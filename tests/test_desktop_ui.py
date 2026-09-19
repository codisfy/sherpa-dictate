import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

from sherpa_app.main import SherpaWindow


class DesktopStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_listening_status_enables_stop_control(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window._apply_status(
            "dictate",
            json.dumps(
                {
                    "state": "listening",
                    "mode": "continuous",
                    "model_name": "parakeet",
                }
            ),
        )

        self.assertFalse(window.dictation_card.start_button.isEnabled())
        self.assertTrue(window.dictation_card.stop_button.isEnabled())
        self.assertTrue(window.tray_stop_dictation.isEnabled())
        window.tray.hide()
        window.deleteLater()

    def test_quit_from_tray_hides_an_open_window_immediately(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window.show()
        self.app.processEvents()
        self.assertTrue(window.isVisible())

        with patch("sherpa_app.main.subprocess.Popen") as popen:
            window.quit_sherpa()

        self.assertFalse(window.isVisible())
        self.assertFalse(window.tray.isVisible())
        self.assertEqual(popen.call_count, 2)
        window.deleteLater()

    def test_dense_pages_scroll_instead_of_clipping(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window.resize(window.minimumSize())
        window.show()
        self.app.processEvents()

        shortcuts = window.findChild(QScrollArea, "shortcutsScroll")
        settings = window.findChild(QScrollArea, "settingsScroll")
        self.assertIsNotNone(shortcuts)
        self.assertIsNotNone(settings)
        self.assertGreater(shortcuts.verticalScrollBar().maximum(), 0)
        self.assertGreater(settings.verticalScrollBar().maximum(), 0)

        window.hide()
        window.tray.hide()
        window.deleteLater()

    def test_saving_settings_reloads_running_dictation_runtime(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window._set_combo_data(window.output_method, "clipboard")

        with (
            patch("sherpa_app.main.save_settings") as save_settings,
            patch.object(window, "run_service") as run_service,
        ):
            window.save_user_settings()

        save_settings.assert_called_once()
        saved = save_settings.call_args.args[0]
        self.assertEqual(saved["dictation"]["output_method"], "clipboard")
        service_calls = [call.args[:2] for call in run_service.call_args_list]
        self.assertIn(("dictate", ["reload-settings"]), service_calls)
        self.assertIn(("read", ["reload-settings"]), service_calls)
        window.tray.hide()
        window.deleteLater()

    def test_engine_restart_runs_immediately_when_dictation_is_idle(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window.dictation_active = False

        with patch.object(window, "run_service") as run_service:
            window._dictation_settings_reloaded("Runtime settings applied", True)

        run_service.assert_called_once()
        self.assertEqual(run_service.call_args.args[:2], ("dictate", ["restart"]))
        self.assertIn("dictate", window.service_restarts_in_progress)
        window.tray.hide()
        window.deleteLater()

    def test_engine_restart_waits_for_active_dictation_to_stop(self) -> None:
        window = SherpaWindow(self.app)
        window.status_timer.stop()
        window.dictation_active = True

        with patch.object(window, "run_service") as run_service:
            window._dictation_settings_reloaded("Runtime settings applied", True)
            run_service.assert_not_called()
            self.assertIn("dictate", window.pending_service_restarts)
            window._apply_status(
                "dictate",
                json.dumps({"state": "idle", "model_name": "parakeet"}),
            )

        self.assertEqual(run_service.call_args.args[:2], ("dictate", ["restart"]))
        self.assertNotIn("dictate", window.pending_service_restarts)
        window.tray.hide()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
