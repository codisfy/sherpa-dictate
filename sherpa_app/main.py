"""PySide6 desktop and system-tray application for Sherpa."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QProcess, QRectF, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from .catalog import MODELS, MODEL_BY_ID, ModelSpec
from .model_manager import download_model, installed_model_path, is_model_complete
from .settings import default_settings, load_settings, save_settings
from .wayland_shortcuts import WaylandShortcutPortal, is_wayland_session


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENTRY_SCRIPT = PROJECT_DIR / "sherpa_entry.py"
ICON_PATH = PROJECT_DIR / "assets" / "sherpa.svg"
SPIN_UP_ICON_PATH = PROJECT_DIR / "assets" / "spin-up.svg"
SPIN_DOWN_ICON_PATH = PROJECT_DIR / "assets" / "spin-down.svg"


class ModelDownloadThread(QThread):
    progress_changed = Signal(int, int)
    installed = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, spec: ModelSpec, storage: Path) -> None:
        super().__init__()
        self.spec = spec
        self.storage = storage
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            path = download_model(
                self.spec,
                self.storage,
                progress=lambda received, total: self.progress_changed.emit(
                    received, total
                ),
                cancelled=lambda: self._cancelled,
            )
            self.installed.emit(self.spec.id, str(path))
        except BaseException as error:
            self.failed.emit(self.spec.id, str(error))


class ToggleSwitch(QCheckBox):
    """Small accessible toggle that renders consistently across Linux themes."""

    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setAccessibleName(accessible_name)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#168652") if self.isChecked() else QColor("#aab4ae"))
        painter.drawRoundedRect(QRectF(1, 3, 44, 20), 10, 10)
        painter.setBrush(QColor("#ffffff"))
        knob_x = 25 if self.isChecked() else 5
        painter.drawEllipse(QRectF(knob_x, 5, 16, 16))


class ServiceCard(QFrame):
    activated = Signal()
    stopped = Signal()

    def __init__(self, title: str, description: str, start_label: str) -> None:
        super().__init__()
        self.setObjectName("serviceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.status_label = QLabel("Not running")
        self.status_label.setObjectName("muted")
        heading.addWidget(title_label)
        heading.addStretch()
        heading.addWidget(self.status_dot)
        heading.addWidget(self.status_label)
        layout.addLayout(heading)

        description_label = QLabel(description)
        description_label.setWordWrap(True)
        description_label.setObjectName("muted")
        layout.addWidget(description_label)

        buttons = QHBoxLayout()
        self.start_button = QPushButton(start_label)
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.activated.emit)
        self.stop_button.clicked.connect(self.stopped.emit)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch()
        layout.addLayout(buttons)

    def set_state(self, active: bool, detail: str) -> None:
        self.status_dot.setProperty("active", active)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)
        self.status_label.setText(detail)
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(active)


class SherpaWindow(QMainWindow):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = load_settings()
        self.processes: set[QProcess] = set()
        self.status_pending: set[str] = set()
        self.download_thread: ModelDownloadThread | None = None
        self.model_rows: dict[str, dict[str, Any]] = {}
        self.dictation_active = False
        self.reader_active = False
        self._close_notice_shown = False
        self._quitting = False
        self._quit_started = False
        self.shortcut_portal: WaylandShortcutPortal | None = None

        self.setWindowTitle("Sherpa")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(820, 650)
        self.setMinimumSize(700, 540)
        self._build_ui()
        self._build_tray()
        self._apply_style()
        self._setup_wayland_shortcuts()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(2500)
        QTimer.singleShot(100, self.refresh_status)

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 22, 26, 24)

        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(38, 38))
        title_box = QVBoxLayout()
        title = QLabel("Sherpa")
        title.setObjectName("appTitle")
        subtitle = QLabel("Private, local voice tools")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        brand.addWidget(logo)
        brand.addLayout(title_box)
        brand.addStretch()
        outer.addLayout(brand)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setMinimumWidth(410)
        self.tabs.addTab(self._build_home_tab(), "Home")
        self.tabs.addTab(self._build_models_tab(), "Models")
        self.tabs.addTab(self._build_shortcuts_tab(), "Shortcuts")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        outer.addWidget(self.tabs)
        self.setCentralWidget(root)

    def _build_home_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 24, 4, 4)
        layout.setSpacing(18)

        intro = QLabel("Speak naturally anywhere, or listen to selected text.")
        intro.setObjectName("sectionTitle")
        layout.addWidget(intro)

        cards = QGridLayout()
        cards.setSpacing(16)
        self.dictation_card = ServiceCard(
            "Dictation",
            "Listens continuously and types each phrase into the focused application.",
            "Start dictation",
        )
        self.reader_card = ServiceCard(
            "Text to speech",
            "Reads the text currently selected in another application.",
            "Read selection",
        )
        self.dictation_card.activated.connect(
            lambda: self.run_service("dictate", ["continuous"])
        )
        self.dictation_card.stopped.connect(
            lambda: self.run_service("dictate", ["stop"])
        )
        self.reader_card.activated.connect(
            lambda: self.run_service("read", ["selection"])
        )
        self.reader_card.stopped.connect(
            lambda: self.run_service("read", ["stop"])
        )
        cards.addWidget(self.dictation_card, 0, 0)
        cards.addWidget(self.reader_card, 0, 1)
        layout.addLayout(cards)

        self.activity_label = QLabel("Ready")
        self.activity_label.setObjectName("activity")
        self.activity_label.setWordWrap(True)
        layout.addWidget(self.activity_label)
        layout.addStretch()
        return page

    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 18, 4, 4)
        layout.setSpacing(12)

        storage_row = QHBoxLayout()
        storage_row.addWidget(QLabel("Download location"))
        self.storage_label = QLabel(str(self.settings["model_storage_dir"]))
        self.storage_label.setObjectName("pathLabel")
        self.storage_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        storage_row.addWidget(self.storage_label, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_storage_directory)
        storage_row.addWidget(choose)
        layout.addLayout(storage_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        contents = QWidget()
        self.models_layout = QVBoxLayout(contents)
        self.models_layout.setContentsMargins(0, 8, 0, 8)
        self.models_layout.setSpacing(10)
        for spec in MODELS:
            self._add_model_row(spec)
        self.models_layout.addStretch()
        scroll.setWidget(contents)
        layout.addWidget(scroll, 1)

        self.download_progress = QProgressBar()
        self.download_progress.setVisible(False)
        self.download_progress.setTextVisible(True)
        layout.addWidget(self.download_progress)
        return page

    def _add_model_row(self, spec: ModelSpec) -> None:
        frame = QFrame()
        frame.setObjectName("modelRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 15, 18, 15)

        text = QVBoxLayout()
        heading = QLabel(spec.name)
        heading.setObjectName("modelTitle")
        details = QLabel(
            f"{spec.language} · {spec.size} · {spec.backend.title() if spec.backend else 'TTS'}"
        )
        details.setObjectName("muted")
        summary = QLabel(spec.summary)
        summary.setObjectName("muted")
        summary.setWordWrap(True)
        text.addWidget(heading)
        text.addWidget(details)
        text.addWidget(summary)
        layout.addLayout(text, 1)

        status = QLabel()
        status.setMinimumWidth(75)
        download = QPushButton("Download")
        download.clicked.connect(lambda _checked=False, model=spec: self.download(model))
        layout.addWidget(status)
        layout.addWidget(download)

        use: QPushButton | None = None
        if spec.kind == "asr":
            use = QPushButton("Use")
            use.clicked.connect(
                lambda _checked=False, model_id=spec.id: self.select_model(model_id)
            )
            layout.addWidget(use)
        self.models_layout.addWidget(frame)
        self.model_rows[spec.id] = {
            "status": status,
            "download": download,
            "use": use,
        }
        self.refresh_model_rows()

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setContentsMargins(8, 24, 8, 8)
        form = QFormLayout()
        form.setHorizontalSpacing(30)
        form.setVerticalSpacing(16)

        dictation = self.settings.get("dictation", {})
        tts = self.settings.get("tts", {})

        self.input_device = QLineEdit(str(dictation.get("audio_device", "")))
        self.input_device.setPlaceholderText("System default, device name, or index")

        self.output_device = QLineEdit(str(tts.get("output_device", "")))
        self.output_device.setPlaceholderText("System default, device name, or index")

        self.num_threads = QSpinBox()
        self.num_threads.setRange(1, 64)
        self.num_threads.setValue(int(dictation.get("num_threads", 8)))

        self.output_method = QComboBox()
        self.output_method.addItem("Direct typing", "type")
        self.output_method.addItem("Clipboard paste", "clipboard")
        self._set_combo_data(self.output_method, dictation.get("output_method", "type"))

        self.silence_ms = QSpinBox()
        self.silence_ms.setRange(300, 5000)
        self.silence_ms.setSingleStep(100)
        self.silence_ms.setSuffix(" ms")
        self.silence_ms.setValue(int(dictation.get("silence_ms", 1500)))

        self.speech_threshold = QDoubleSpinBox()
        self.speech_threshold.setRange(0.001, 0.1)
        self.speech_threshold.setDecimals(3)
        self.speech_threshold.setSingleStep(0.001)
        self.speech_threshold.setValue(float(dictation.get("speech_threshold", 0.012)))

        self.speaker_id = QSpinBox()
        self.speaker_id.setRange(0, 7)
        self.speaker_id.setValue(int(tts.get("speaker_id", 2)))

        self.tts_speed = QDoubleSpinBox()
        self.tts_speed.setRange(0.5, 3.0)
        self.tts_speed.setSingleStep(0.1)
        self.tts_speed.setValue(float(tts.get("speed", 1.4)))

        self.start_minimized = ToggleSwitch("Start in the system tray")
        self.start_minimized.setChecked(bool(self.settings.get("start_minimized", False)))

        self.spoken_punctuation = ToggleSwitch("Enable spoken punctuation")
        self.spoken_punctuation.setChecked(
            bool(dictation.get("spoken_punctuation", True))
        )

        form.addRow("Microphone", self.input_device)
        form.addRow("Audio output", self.output_device)
        form.addRow("Recognition CPU threads", self.num_threads)
        form.addRow("Text insertion", self.output_method)
        form.addRow("Pause before transcription", self.silence_ms)
        form.addRow("Microphone sensitivity threshold", self.speech_threshold)
        form.addRow("TTS voice", self.speaker_id)
        form.addRow("TTS speed", self.tts_speed)
        form.addRow(
            "Spoken punctuation",
            self._toggle_field(self.spoken_punctuation, "Convert commands such as “new line”"),
        )
        form.addRow(
            "On launch",
            self._toggle_field(self.start_minimized, "Start in the system tray"),
        )
        layout.addLayout(form)

        note = QLabel(
            "Audio remains on this device. Changed engine settings apply the next time "
            "the corresponding background service starts."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        save = QPushButton("Save settings")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save_user_settings)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self.reset_user_settings)
        buttons.addWidget(save)
        buttons.addWidget(reset)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        return self._scrollable_page(page, "settingsScroll")

    def _build_shortcuts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setContentsMargins(4, 22, 4, 4)
        layout.setSpacing(12)

        heading = QLabel("Global keyboard shortcuts")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        portal_row = QFrame()
        portal_row.setObjectName("serviceCard")
        portal_layout = QHBoxLayout(portal_row)
        portal_layout.setContentsMargins(16, 14, 16, 14)
        portal_text = QVBoxLayout()
        portal_title = QLabel("Wayland shortcut integration")
        portal_title.setObjectName("modelTitle")
        self.shortcut_portal_status = QLabel(
            "Checking whether this desktop supports automatic registration…"
        )
        self.shortcut_portal_status.setObjectName("muted")
        self.shortcut_portal_status.setWordWrap(True)
        self.shortcut_portal_status.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        portal_text.addWidget(portal_title)
        portal_text.addWidget(self.shortcut_portal_status)
        self.configure_shortcuts_button = QPushButton("Configure shortcuts…")
        self.configure_shortcuts_button.setEnabled(False)
        self.configure_shortcuts_button.clicked.connect(
            self.configure_wayland_shortcuts
        )
        portal_layout.addLayout(portal_text, 1)
        portal_layout.addWidget(self.configure_shortcuts_button)
        layout.addWidget(portal_row)

        explanation = QLabel(
            "On supported Wayland desktops, Sherpa registers these actions with the "
            "system automatically. The commands below remain available as a fallback "
            "for X11 and desktops without the portal."
        )
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        explanation.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(explanation)

        actions = (
            ("Toggle continuous dictation", "dictation-toggle"),
            ("Start continuous dictation", "dictation-start"),
            ("Stop dictation", "dictation-stop"),
            ("Toggle manual dictation", "dictation-manual-toggle"),
            ("Read selected text", "read-selection"),
            ("Stop text to speech", "tts-stop"),
        )
        for title, action in actions:
            row = QFrame()
            row.setObjectName("modelRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(16, 12, 16, 12)
            label = QLabel(title)
            label.setMinimumWidth(190)
            command = QLabel(self._shortcut_command(action))
            command.setObjectName("pathLabel")
            command.setMinimumWidth(0)
            command.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            command.setTextInteractionFlags(Qt.TextSelectableByMouse)
            command.setToolTip(command.text())
            copy = QPushButton("Copy")
            copy.clicked.connect(
                lambda _checked=False, value=command.text(): self.copy_shortcut(value)
            )
            row_layout.addWidget(label)
            row_layout.addWidget(command, 1)
            row_layout.addWidget(copy)
            layout.addWidget(row)
        layout.addStretch()
        return self._scrollable_page(page, "shortcutsScroll")

    @staticmethod
    def _scrollable_page(page: QWidget, object_name: str) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _setup_wayland_shortcuts(self) -> None:
        if not is_wayland_session():
            self.shortcut_portal_status.setText(
                "Automatic registration is available in a Wayland session. Use the "
                "commands below with your desktop’s shortcut settings on X11."
            )
            return
        portal = WaylandShortcutPortal(self)
        self.shortcut_portal = portal
        portal.status_changed.connect(self._shortcut_portal_status_changed)
        portal.shortcut_activated.connect(self.run_shortcut_action)
        portal.start()

    def _shortcut_portal_status_changed(
        self, message: str, can_configure: bool
    ) -> None:
        self.shortcut_portal_status.setText(message)
        self.configure_shortcuts_button.setEnabled(can_configure)

    def configure_wayland_shortcuts(self) -> None:
        if self.shortcut_portal is not None:
            self.shortcut_portal.configure_shortcuts()

    def run_shortcut_action(self, action: str) -> None:
        commands = {
            "dictation-toggle": ("dictate", ["continuous"]),
            "dictation-start": ("dictate", ["continuous-start"]),
            "dictation-stop": ("dictate", ["stop"]),
            "dictation-manual-toggle": ("dictate", ["toggle"]),
            "read-selection": ("read", ["selection"]),
            "tts-stop": ("read", ["stop"]),
        }
        command = commands.get(action)
        if command is None:
            return
        service, arguments = command
        self.run_service(service, arguments)

    @staticmethod
    def _shortcut_command(action: str) -> str:
        executable = (
            Path(sys.executable)
            if getattr(sys, "frozen", False)
            else PROJECT_DIR / "sherpa"
        )
        return shlex.join([str(executable), "action", action])

    def copy_shortcut(self, command: str) -> None:
        QApplication.clipboard().setText(command)
        self.activity_label.setText(f"Copied shortcut command: {command}")
        self.tray.showMessage(
            "Sherpa", "Shortcut command copied", QSystemTrayIcon.Information, 2000
        )

    @staticmethod
    def _toggle_field(toggle: ToggleSwitch, text: str) -> QWidget:
        field = QWidget()
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        label = QLabel(text)
        label.setBuddy(toggle)
        layout.addWidget(toggle)
        layout.addWidget(label)
        layout.addStretch()
        return field

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(ICON_PATH)), self)
        self.tray.setToolTip("Sherpa · Ready")
        menu = QMenu()
        self.tray_start_dictation = QAction("Start dictation", self)
        self.tray_stop_dictation = QAction("Stop dictation", self)
        self.tray_read = QAction("Read selected text", self)
        self.tray_stop_reading = QAction("Stop reading", self)
        show_action = QAction("Open Sherpa", self)
        quit_action = QAction("Quit Sherpa", self)

        self.tray_start_dictation.triggered.connect(
            lambda: self.run_service("dictate", ["continuous"])
        )
        self.tray_stop_dictation.triggered.connect(
            lambda: self.run_service("dictate", ["stop"])
        )
        self.tray_read.triggered.connect(lambda: self.run_service("read", ["selection"]))
        self.tray_stop_reading.triggered.connect(
            lambda: self.run_service("read", ["stop"])
        )
        show_action.triggered.connect(self.show_from_tray)
        quit_action.triggered.connect(self.quit_sherpa)

        menu.addAction(self.tray_start_dictation)
        menu.addAction(self.tray_stop_dictation)
        menu.addSeparator()
        menu.addAction(self.tray_read)
        menu.addAction(self.tray_stop_reading)
        menu.addSeparator()
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_from_tray()
            if reason == QSystemTrayIcon.Trigger
            else None
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.app.setQuitOnLastWindowClosed(False)

    def _apply_style(self) -> None:
        stylesheet = """
            QWidget { font-family: Inter, Cantarell, sans-serif; font-size: 14px; color: #18221d; background: transparent; }
            QMainWindow { background: #f5f7f5; }
            #appTitle { font-size: 24px; font-weight: 700; color: #102219; }
            #sectionTitle { font-size: 19px; font-weight: 650; }
            #cardTitle, #modelTitle { font-size: 16px; font-weight: 650; }
            #muted { color: #66736c; }
            #pathLabel { color: #536159; padding: 7px 10px; min-height: 22px; background: #e9eeeb; border-radius: 7px; }
            #serviceCard, #modelRow { background: #ffffff; border: 1px solid #dde5df; border-radius: 12px; }
            #statusDot { color: #a7b0ab; }
            #statusDot[active="true"] { color: #18a466; }
            #activity { padding: 12px 14px; color: #536159; background: #e9eeeb; border-radius: 8px; }
            QPushButton { background: #ffffff; border: 1px solid #cbd5ce; border-radius: 8px; padding: 8px 14px; min-height: 20px; }
            QPushButton:hover { background: #edf3ef; border-color: #9eafa4; }
            QPushButton:disabled { color: #9ca59f; background: #f0f2f1; }
            #primaryButton { color: white; background: #167c4a; border-color: #167c4a; font-weight: 600; }
            #primaryButton:hover { background: #116a3e; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { padding: 10px 18px; margin-top: 10px; color: #657169; border: none; }
            QTabBar::tab:selected { color: #116a3e; border-bottom: 2px solid #167c4a; font-weight: 600; }
            QComboBox, QLineEdit { background: white; border: 1px solid #cbd5ce; border-radius: 7px; padding: 7px; min-height: 22px; }
            QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #cbd5ce; border-radius: 7px; padding: 7px 34px 7px 9px; min-height: 22px; }
            QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 27px; background: #f3f6f4; border-left: 1px solid #d5ddd8; border-bottom: 1px solid #d5ddd8; border-top-right-radius: 7px; }
            QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 27px; background: #f3f6f4; border-left: 1px solid #d5ddd8; border-bottom-right-radius: 7px; }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #e4ece7; }
            QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed, QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed { background: #d8e4dc; }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@SPIN_UP@); width: 12px; height: 12px; }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@SPIN_DOWN@); width: 12px; height: 12px; }
            QProgressBar { border: 1px solid #cbd5ce; border-radius: 7px; text-align: center; background: white; }
            QProgressBar::chunk { background: #22a766; border-radius: 6px; }
            """
        self.setStyleSheet(
            stylesheet.replace("@SPIN_UP@", SPIN_UP_ICON_PATH.as_posix()).replace(
                "@SPIN_DOWN@", SPIN_DOWN_ICON_PATH.as_posix()
            )
        )

    def run_service(
        self,
        service: str,
        arguments: list[str],
        completed: Callable[[str], None] | None = None,
        quiet: bool = False,
    ) -> None:
        process = QProcess(self)
        self.processes.add(process)
        if not quiet:
            self.activity_label.setText(f"Working: {service} {' '.join(arguments)}…")

        def finished(exit_code: int, _status: QProcess.ExitStatus) -> None:
            stdout = bytes(process.readAllStandardOutput()).decode(errors="replace").strip()
            stderr = bytes(process.readAllStandardError()).decode(errors="replace").strip()
            self.processes.discard(process)
            process.deleteLater()
            message = stdout or stderr
            if completed:
                completed(message)
            elif not quiet:
                self.activity_label.setText(message or ("Done" if exit_code == 0 else "Failed"))
                if exit_code != 0 and message:
                    self.tray.showMessage("Sherpa", message, QSystemTrayIcon.Warning, 5000)
            if not quiet:
                QTimer.singleShot(150, self.refresh_status)

        process.finished.connect(finished)
        if getattr(sys, "frozen", False):
            process.start(sys.executable, ["engine", service, *arguments])
        else:
            process.start(
                sys.executable,
                [str(ENTRY_SCRIPT), "engine", service, *arguments],
            )

    def refresh_status(self) -> None:
        for service in ("dictate", "read"):
            if service in self.status_pending:
                continue
            self.status_pending.add(service)
            self.run_service(
                service,
                ["status"],
                lambda output, name=service: self._status_finished(name, output),
                True,
            )

    def _status_finished(self, service: str, output: str) -> None:
        self.status_pending.discard(service)
        self._apply_status(service, output)

    def _apply_status(self, service: str, output: str) -> None:
        try:
            status = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            status = {}

        if service == "dictate":
            self.dictation_active = status.get("state") == "listening"
            detail = (
                f"Listening · {status.get('model_name', 'model')}"
                if self.dictation_active
                else "Not running"
            )
            self.dictation_card.set_state(self.dictation_active, detail)
            self.tray_start_dictation.setEnabled(not self.dictation_active)
            self.tray_stop_dictation.setEnabled(self.dictation_active)
        else:
            self.reader_active = status.get("state") in {"speaking", "stopping"}
            detail = "Reading" if self.reader_active else "Not running"
            self.reader_card.set_state(self.reader_active, detail)
            self.tray_read.setEnabled(not self.reader_active)
            self.tray_stop_reading.setEnabled(self.reader_active)

        states = []
        if self.dictation_active:
            states.append("dictating")
        if self.reader_active:
            states.append("reading")
        self.tray.setToolTip("Sherpa · " + (" and ".join(states).title() if states else "Ready"))

    def refresh_model_rows(self) -> None:
        active_model = str(load_settings().get("active_model") or "")
        if not active_model:
            try:
                active_model = (PROJECT_DIR / ".active-model").read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                active_model = "parakeet"
        storage = Path(str(load_settings()["model_storage_dir"])).expanduser()
        for spec in MODELS:
            row = self.model_rows.get(spec.id)
            if not row:
                continue
            installed_path = installed_model_path(spec)
            installed = is_model_complete(spec, installed_path)
            target_path = storage / spec.id
            installed_in_target = installed and installed_path.resolve() == target_path.resolve()
            row["status"].setText("Installed" if installed else "Not installed")
            row["status"].setToolTip(str(installed_path) if installed else "")
            if installed_in_target:
                row["download"].setText("Installed")
            elif installed:
                row["download"].setText("Install here")
            else:
                row["download"].setText("Download")
            row["download"].setEnabled(
                not installed_in_target and self.download_thread is None
            )
            if row["use"] is not None:
                row["use"].setText("Active" if spec.id == active_model else "Use")
                row["use"].setEnabled(installed and spec.id != active_model)

    def choose_storage_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose model download location",
            str(Path(str(self.settings["model_storage_dir"])).expanduser()),
        )
        if not selected:
            return
        self.settings["model_storage_dir"] = selected
        save_settings(self.settings)
        self.storage_label.setText(selected)
        self.refresh_model_rows()

    def download(self, spec: ModelSpec) -> None:
        if self.download_thread is not None:
            return
        storage = Path(str(load_settings()["model_storage_dir"]))
        self.download_progress.setVisible(True)
        self.download_progress.setRange(0, 0)
        self.download_progress.setFormat(f"Downloading {spec.name}…")
        thread = ModelDownloadThread(spec, storage)
        self.download_thread = thread
        thread.progress_changed.connect(
            lambda received, total: self._update_download_progress(spec, received, total)
        )
        thread.installed.connect(self._download_installed)
        thread.failed.connect(self._download_failed)
        thread.finished.connect(thread.deleteLater)
        self.refresh_model_rows()
        thread.start()

    def _update_download_progress(self, spec: ModelSpec, received: int, total: int) -> None:
        if total > 0:
            self.download_progress.setRange(0, 1000)
            self.download_progress.setValue(round(received * 1000 / total))
            self.download_progress.setFormat(
                f"{spec.name} · {received / 1024 / 1024:.0f} of {total / 1024 / 1024:.0f} MB"
            )
        else:
            self.download_progress.setRange(0, 0)

    def _download_installed(self, model_id: str, path: str) -> None:
        name = MODEL_BY_ID[model_id].name
        self.download_thread = None
        self.download_progress.setVisible(False)
        self.activity_label.setText(f"Installed {name} in {path}")
        self.tray.showMessage("Model installed", name, QSystemTrayIcon.Information, 4000)
        self.refresh_model_rows()

    def _download_failed(self, model_id: str, error: str) -> None:
        self.download_thread = None
        self.download_progress.setVisible(False)
        self.refresh_model_rows()
        if not self._quitting:
            QMessageBox.critical(self, f"Could not download {MODEL_BY_ID[model_id].name}", error)

    def select_model(self, model_id: str) -> None:
        spec = MODEL_BY_ID[model_id]
        if not is_model_complete(spec, installed_model_path(spec)):
            QMessageBox.information(self, "Model not installed", "Download this model first.")
            return
        self.run_service(
            "dictate",
            ["model", model_id],
            lambda output: self._model_selected(model_id, output),
        )

    def _model_selected(self, model_id: str, output: str) -> None:
        self.activity_label.setText(output or f"Selected {MODEL_BY_ID[model_id].name}")
        self.refresh_model_rows()

    def save_user_settings(self) -> None:
        latest = load_settings()
        latest["dictation"] = {
            **latest.get("dictation", {}),
            "audio_device": self._device_value(self.input_device.text()),
            "num_threads": self.num_threads.value(),
            "output_method": self.output_method.currentData(),
            "silence_ms": self.silence_ms.value(),
            "speech_threshold": self.speech_threshold.value(),
            "spoken_punctuation": self.spoken_punctuation.isChecked(),
        }
        latest["tts"] = {
            **latest.get("tts", {}),
            "output_device": self._device_value(self.output_device.text()),
            "speaker_id": self.speaker_id.value(),
            "speed": self.tts_speed.value(),
        }
        latest["start_minimized"] = self.start_minimized.isChecked()
        save_settings(latest)
        self.settings = latest
        self.activity_label.setText("Settings saved. They apply when each service next starts.")

    def reset_user_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset settings?",
            "Reset app and engine preferences to their defaults? Downloaded models will not be deleted.",
        )
        if answer != QMessageBox.Yes:
            return

        current = load_settings()
        defaults = default_settings()
        defaults["model_paths"] = dict(current.get("model_paths", {}))
        save_settings(defaults)
        self.settings = defaults

        self.input_device.clear()
        self.output_device.clear()
        self.num_threads.setValue(8)
        self._set_combo_data(self.output_method, "type")
        self.silence_ms.setValue(1500)
        self.speech_threshold.setValue(0.012)
        self.speaker_id.setValue(2)
        self.tts_speed.setValue(1.4)
        self.spoken_punctuation.setChecked(True)
        self.start_minimized.setChecked(False)
        self.storage_label.setText(str(defaults["model_storage_dir"]))
        self.refresh_model_rows()
        self.activity_label.setText("Settings reset to defaults. Downloaded models were kept.")

    @staticmethod
    def _device_value(text: str) -> str | int:
        value = text.strip()
        return int(value) if value.isdecimal() else value

    def show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._close_notice_shown:
                self.tray.showMessage(
                    "Sherpa is still running",
                    "Use the tray icon to open or quit the app.",
                    QSystemTrayIcon.Information,
                    3500,
                )
                self._close_notice_shown = True
        else:
            event.ignore()
            self.quit_sherpa()

    def quit_sherpa(self) -> None:
        if self.download_thread is not None:
            self._quitting = True
            self.download_thread.finished.connect(self._finish_quit)
            self.download_thread.cancel()
            self.hide()
            self.tray.setToolTip("Sherpa · Cancelling download…")
            return
        self._finish_quit()

    def _finish_quit(self) -> None:
        if self._quit_started:
            return
        self._quit_started = True
        self._quitting = True
        self.status_timer.stop()
        self.hide()
        for service in ("dictate", "read"):
            if getattr(sys, "frozen", False):
                command = [sys.executable, "engine", service, "quit"]
            else:
                command = [
                    sys.executable,
                    str(ENTRY_SCRIPT),
                    "engine",
                    service,
                    "quit",
                ]
            subprocess.Popen(
                command,
                cwd=PROJECT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.tray.hide()
        portal = self.shortcut_portal
        if portal is None:
            self.app.quit()
            return
        portal.finished.connect(self.app.quit)
        portal.stop()
        if not portal.isRunning():
            self.app.quit()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    QApplication.setApplicationName("Sherpa")
    QApplication.setOrganizationName("Sherpa")
    QApplication.setOrganizationDomain("sherpa.io")
    QApplication.setDesktopFileName("io.sherpa.Sherpa")
    app = QApplication([sys.argv[0]])
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = SherpaWindow(app)
    minimized = "--minimized" in arguments or load_settings().get("start_minimized")
    if not minimized or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
