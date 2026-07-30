#!/usr/bin/env python3
"""Small local Sherpa-ONNX dictation daemon and command-line client."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
import wave
from collections import deque
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.toml"
MODEL_SELECTION_PATH = PROJECT_DIR / ".active-model"
LOG_PATH = PROJECT_DIR / "dictate.log"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / "sherpa-dictate.sock"

SPOKEN_COMMANDS = {
    "full stop": ".",
    "comma": ",",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "new line": "\n",
    "newline": "\n",
    "new paragraph": "\n\n",
}


def spoken_command_output(text: str) -> str | None:
    """Return a control character when the whole recognized phrase is a command."""
    normalized = text.casefold().strip().strip(".,!?;:")
    return SPOKEN_COMMANDS.get(normalized)


def read_raw_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as config_file:
        return tomllib.load(config_file)


def configured_model_names(raw_config: dict[str, Any]) -> list[str]:
    profiles = raw_config.get("models")
    if not isinstance(profiles, dict) or not profiles:
        return []
    return sorted(str(name) for name in profiles)


def selected_model_name(raw_config: dict[str, Any]) -> str:
    selected = str(raw_config.get("active_model", "default"))
    try:
        saved_selection = MODEL_SELECTION_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        saved_selection = ""
    if saved_selection:
        selected = saved_selection
    return selected


def load_config() -> dict[str, Any]:
    raw_config = read_raw_config()
    profiles = raw_config.get("models")

    if isinstance(profiles, dict) and profiles:
        model_name = selected_model_name(raw_config)
        if model_name not in profiles:
            choices = ", ".join(configured_model_names(raw_config))
            raise ValueError(f"Unknown model profile '{model_name}'; choose one of: {choices}")
        common_config = {
            key: value
            for key, value in raw_config.items()
            if key not in {"active_model", "models"}
        }
        profile = profiles[model_name]
        if not isinstance(profile, dict):
            raise ValueError(f"Model profile '{model_name}' must be a TOML table")
        config = {**common_config, **profile, "model_name": model_name}
    else:
        config = {**raw_config, "model_name": "default", "backend": "online"}

    model_dir = Path(config["model_dir"]).expanduser()
    required = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
    missing = [str(model_dir / name) for name in required if not (model_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing model files:\n" + "\n".join(missing))

    config["model_dir"] = model_dir
    backend = str(config.get("backend", "online")).lower()
    if backend not in {"online", "offline"}:
        raise ValueError("Model backend must be 'online' or 'offline'")
    config["backend"] = backend
    return config


def configure_logging() -> None:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class DictationEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model_name = str(config.get("model_name", "default"))
        self.model_dir: Path = config["model_dir"]
        self.backend = str(config.get("backend", "online"))
        self.model_type = str(config.get("model_type", "nemo_transducer"))
        self.language = str(config.get("language", "auto"))
        self.input_sample_rate = int(config.get("input_sample_rate", 48000))
        self.audio_device = config.get("audio_device") or None
        self.num_threads = int(config.get("num_threads", 4))
        self.paste_delay = int(config.get("paste_delay_ms", 180)) / 1000
        self.output_method = str(config.get("output_method", "type")).lower()
        self.typing_key_delay = int(config.get("typing_key_delay_ms", 2))
        self.typing_key_hold = int(config.get("typing_key_hold_ms", 2))
        self.clipboard_sensitive = bool(config.get("clipboard_sensitive", True))
        self.clipboard_shortcut = str(
            config.get("clipboard_shortcut", "ctrl_shift_v")
        ).lower()
        self.clipboard_clear_delay = (
            int(config.get("clipboard_clear_after_paste_ms", 500)) / 1000
        )
        self.silence_seconds = int(config.get("silence_ms", 900)) / 1000
        self.pre_roll_seconds = int(config.get("pre_roll_ms", 300)) / 1000
        self.min_speech_seconds = int(config.get("min_speech_ms", 250)) / 1000
        self.max_phrase_seconds = float(config.get("max_phrase_seconds", 30))
        self.speech_threshold = float(config.get("speech_threshold", 0.012))
        self.append_space = bool(config.get("append_space_after_phrase", True))
        self.spoken_punctuation = bool(config.get("spoken_punctuation", True))
        if self.output_method not in {"type", "clipboard"}:
            raise ValueError("output_method must be 'type' or 'clipboard'")
        if self.clipboard_shortcut not in {"ctrl_v", "ctrl_shift_v"}:
            raise ValueError("clipboard_shortcut must be 'ctrl_v' or 'ctrl_shift_v'")

        logging.info(
            "Loading model profile=%s backend=%s from %s",
            self.model_name,
            self.backend,
            self.model_dir,
        )
        model_files = {
            "encoder": str(self.model_dir / "encoder.int8.onnx"),
            "decoder": str(self.model_dir / "decoder.int8.onnx"),
            "joiner": str(self.model_dir / "joiner.int8.onnx"),
            "tokens": str(self.model_dir / "tokens.txt"),
            "num_threads": self.num_threads,
            "provider": "cpu",
        }
        if self.backend == "online":
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                **model_files
            )
        else:
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                **model_files,
                model_type=self.model_type,
            )
        logging.info("Model profile=%s loaded", self.model_name)

        self.listening = False
        self.mode = "idle"
        self.continuous_paste = True
        self.input_stream: sd.InputStream | None = None
        self.recognition_stream: Any = None
        self.audio_queue: queue.Queue[np.ndarray] | None = None
        self.stop_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.worker_result = ""
        self.worker_error: BaseException | None = None
        self.overflowed = False
        self.phrases_pasted = 0
        self.paste_warning = ""

    def _new_recognition_stream(self) -> Any:
        if self.backend == "offline":
            return []

        stream = self.recognizer.create_stream()
        if self.language:
            stream.set_option("language", self.language)
        return stream

    def _accept_and_decode(self, stream: Any, samples: np.ndarray) -> None:
        if self.backend == "offline":
            stream.append(samples.copy())
            return

        stream.accept_waveform(self.input_sample_rate, samples)
        while self.recognizer.is_ready(stream):
            self.recognizer.decode_stream(stream)

    def _finalize_stream(self, stream: Any) -> str:
        if self.backend == "offline":
            if not stream:
                return ""
            offline_stream = self.recognizer.create_stream()
            samples = np.concatenate(stream)
            offline_stream.accept_waveform(self.input_sample_rate, samples)
            self.recognizer.decode_stream(offline_stream)
            return offline_stream.result.text.strip()

        tail = np.zeros(int(0.3 * self.input_sample_rate), dtype=np.float32)
        self._accept_and_decode(stream, tail)
        stream.input_finished()
        while self.recognizer.is_ready(stream):
            self.recognizer.decode_stream(stream)
        return self.recognizer.get_result(stream).strip()

    def _send_text(self, text: str) -> dict[str, Any]:
        return send_text_to_active_window(
            text=text,
            method=self.output_method,
            paste_delay=self.paste_delay,
            typing_key_delay=self.typing_key_delay,
            typing_key_hold=self.typing_key_hold,
            clipboard_sensitive=self.clipboard_sensitive,
            clipboard_shortcut=self.clipboard_shortcut,
            clipboard_clear_delay=self.clipboard_clear_delay,
        )

    def _decode_worker(self) -> None:
        assert self.audio_queue is not None
        assert self.stop_event is not None
        assert self.recognition_stream is not None

        try:
            while not self.stop_event.is_set() or not self.audio_queue.empty():
                try:
                    samples = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                self._accept_and_decode(self.recognition_stream, samples)

            self.worker_result = self._finalize_stream(self.recognition_stream)
        except BaseException as error:
            self.worker_error = error
            logging.exception("Recognition worker failed")

    def _continuous_decode_worker(self) -> None:
        assert self.audio_queue is not None
        assert self.stop_event is not None

        pre_roll: deque[np.ndarray] = deque()
        pre_roll_samples = 0
        pre_roll_limit = int(self.pre_roll_seconds * self.input_sample_rate)
        silence_limit = int(self.silence_seconds * self.input_sample_rate)
        min_speech_samples = int(self.min_speech_seconds * self.input_sample_rate)
        max_phrase_samples = int(self.max_phrase_seconds * self.input_sample_rate)
        stream: Any = None
        phrase_samples = 0
        voiced_samples = 0
        silent_samples = 0
        transcripts: list[str] = []
        output_started = False
        last_output_character = ""

        def finish_phrase() -> None:
            nonlocal stream, phrase_samples, voiced_samples, silent_samples
            nonlocal output_started, last_output_character
            if stream is None:
                return

            text = self._finalize_stream(stream)
            if text and voiced_samples >= min_speech_samples:
                transcripts.append(text)
                if self.continuous_paste:
                    command_text = spoken_command_output(text) if self.spoken_punctuation else None
                    if command_text is not None:
                        if command_text in ".!?" and command_text == last_output_character:
                            output_text = ""
                        else:
                            output_text = command_text
                    else:
                        needs_space = (
                            output_started
                            and self.append_space
                            and last_output_character not in {"", "\n"}
                        )
                        output_text = (" " if needs_space else "") + text

                    paste_result = (
                        self._send_text(output_text)
                        if output_text
                        else {"pasted": True, "warning": "", "input_method": "none"}
                    )
                    if paste_result.get("pasted"):
                        self.phrases_pasted += 1
                        if output_text:
                            output_started = True
                            last_output_character = output_text[-1]
                    if paste_result.get("warning"):
                        self.paste_warning = str(paste_result["warning"])
                logging.info(
                    "Continuous phrase finalized; transcription length=%d output_method=%s",
                    len(text),
                    self.output_method if self.continuous_paste else "disabled",
                )
            else:
                logging.info(
                    "Continuous phrase discarded; transcription length=%d voiced_ms=%d",
                    len(text),
                    round(voiced_samples * 1000 / self.input_sample_rate),
                )

            stream = None
            phrase_samples = 0
            voiced_samples = 0
            silent_samples = 0

        try:
            while not self.stop_event.is_set() or not self.audio_queue.empty():
                try:
                    samples = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
                is_speech = rms >= self.speech_threshold

                if stream is None:
                    pre_roll.append(samples)
                    pre_roll_samples += samples.size
                    while pre_roll and pre_roll_samples > pre_roll_limit:
                        pre_roll_samples -= pre_roll.popleft().size

                    if not is_speech:
                        continue

                    stream = self._new_recognition_stream()
                    for buffered_samples in pre_roll:
                        self._accept_and_decode(stream, buffered_samples)
                        phrase_samples += buffered_samples.size
                    pre_roll.clear()
                    pre_roll_samples = 0
                    voiced_samples = samples.size
                    silent_samples = 0
                    continue

                self._accept_and_decode(stream, samples)
                phrase_samples += samples.size
                if is_speech:
                    voiced_samples += samples.size
                    silent_samples = 0
                else:
                    silent_samples += samples.size

                if silent_samples >= silence_limit or phrase_samples >= max_phrase_samples:
                    finish_phrase()
                    pre_roll.clear()
                    pre_roll_samples = 0

            finish_phrase()
            self.worker_result = "\n".join(transcripts)
        except BaseException as error:
            self.worker_error = error
            logging.exception("Continuous recognition worker failed")

    def _audio_callback(
        self,
        input_data: np.ndarray,
        _frames: int,
        _time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.warning("Audio callback status: %s", status)
        if self.audio_queue is None:
            return

        samples = np.asarray(input_data[:, 0], dtype=np.float32).copy()
        try:
            self.audio_queue.put_nowait(samples)
        except queue.Full:
            self.overflowed = True
            logging.error("Audio queue overflow; a chunk was dropped")

    def _start(self, mode: str, paste: bool = True) -> dict[str, Any]:
        if self.listening:
            return {
                "ok": True,
                "state": "listening",
                "mode": self.mode,
                "model_name": self.model_name,
                "message": f"Already listening in {self.mode} mode",
            }

        self.audio_queue = queue.Queue(maxsize=200)
        self.stop_event = threading.Event()
        self.recognition_stream = self._new_recognition_stream() if mode == "manual" else None
        self.worker_result = ""
        self.worker_error = None
        self.overflowed = False
        self.phrases_pasted = 0
        self.paste_warning = ""
        self.continuous_paste = paste
        worker_target = self._decode_worker if mode == "manual" else self._continuous_decode_worker
        self.worker = threading.Thread(target=worker_target, name="recognizer", daemon=True)
        self.worker.start()

        try:
            self.input_stream = sd.InputStream(
                samplerate=self.input_sample_rate,
                blocksize=int(self.input_sample_rate * 0.1),
                device=self.audio_device,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self.input_stream.start()
        except BaseException:
            self.stop_event.set()
            self.worker.join(timeout=5)
            self.input_stream = None
            raise

        self.listening = True
        self.mode = mode
        logging.info("Recording started in %s mode", mode)
        message = "Continuous listening" if mode == "continuous" else "Listening"
        return {
            "ok": True,
            "state": "listening",
            "mode": mode,
            "model_name": self.model_name,
            "message": message,
        }

    def start(self) -> dict[str, Any]:
        return self._start("manual")

    def start_continuous(self, paste: bool = True) -> dict[str, Any]:
        return self._start("continuous", paste=paste)

    def stop(self, paste: bool = True) -> dict[str, Any]:
        if not self.listening:
            return {"ok": True, "state": "idle", "message": "Not listening", "text": ""}

        assert self.stop_event is not None
        assert self.worker is not None

        previous_mode = self.mode
        self.listening = False
        if previous_mode == "continuous" and not paste:
            self.continuous_paste = False
        if self.input_stream is not None:
            self.input_stream.stop()
            self.input_stream.close()
            self.input_stream = None

        self.stop_event.set()
        self.worker.join(timeout=120)
        if self.worker.is_alive():
            raise TimeoutError("Timed out while finalizing transcription")
        if self.worker_error is not None:
            raise RuntimeError(f"Transcription failed: {self.worker_error}")

        text = self.worker_result
        warning = "Audio overflowed; some speech may be missing" if self.overflowed else ""
        paste_result = {"pasted": False, "warning": warning}
        if previous_mode == "manual" and paste and text:
            paste_result = self._send_text(text)
            if warning and paste_result.get("warning"):
                paste_result["warning"] = f"{warning}; {paste_result['warning']}"
            elif warning:
                paste_result["warning"] = warning
        elif previous_mode == "continuous":
            paste_result = {
                "pasted": self.phrases_pasted > 0,
                "phrases_pasted": self.phrases_pasted,
                "warning": self.paste_warning or warning,
            }

        self.mode = "idle"
        logging.info(
            "Recording stopped; mode=%s transcription length=%d phrases_pasted=%d",
            previous_mode,
            len(text),
            self.phrases_pasted,
        )
        if previous_mode == "continuous":
            message = f"Continuous dictation stopped; {self.phrases_pasted} phrase(s) pasted"
        else:
            message = "Transcription complete" if text else "No speech recognized"
        return {
            "ok": True,
            "state": "idle",
            "mode": previous_mode,
            "message": message,
            "text": text,
            **paste_result,
        }

    def toggle(self) -> dict[str, Any]:
        return self.stop(paste=True) if self.listening else self.start()

    def toggle_continuous(self, paste: bool = True) -> dict[str, Any]:
        return self.stop(paste=paste) if self.listening else self.start_continuous(paste=paste)

    def transcribe_wave(self, filename: str) -> dict[str, Any]:
        if self.listening:
            raise RuntimeError("Stop microphone dictation before transcribing a file")

        audio_path = Path(filename).expanduser().resolve()
        with wave.open(str(audio_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw_audio = wav_file.readframes(frame_count)

        if sample_width != 2:
            raise ValueError(f"Only 16-bit PCM WAV is supported for this test command; got {sample_width * 8}-bit")

        samples = np.frombuffer(raw_audio, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)

        stream = self.recognizer.create_stream()
        if self.backend == "offline":
            stream.accept_waveform(sample_rate, samples)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
        else:
            if self.language:
                stream.set_option("language", self.language)
            chunk_size = max(1, int(sample_rate * 0.1))
            for offset in range(0, samples.size, chunk_size):
                stream.accept_waveform(sample_rate, samples[offset : offset + chunk_size])
                while self.recognizer.is_ready(stream):
                    self.recognizer.decode_stream(stream)

            stream.accept_waveform(
                sample_rate,
                np.zeros(int(0.3 * sample_rate), dtype=np.float32),
            )
            stream.input_finished()
            while self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)
            text = self.recognizer.get_result(stream).strip()
        return {"ok": True, "state": "idle", "message": "File transcribed", "text": text}

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": "listening" if self.listening else "idle",
            "mode": self.mode,
            "message": f"Listening ({self.mode})" if self.listening else "Ready",
            "model_name": self.model_name,
            "model": str(self.model_dir),
            "backend": self.backend,
            "language": self.language,
            "phrases_pasted": self.phrases_pasted,
        }


def copy_to_clipboard(text: str, sensitive: bool = True) -> None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        command = ["wl-copy", "--trim-newline"]
        if sensitive:
            command.append("--sensitive")
        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            timeout=10,
        )
        return

    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text,
            text=True,
            check=True,
            timeout=10,
        )
        return

    raise RuntimeError("Neither wl-copy nor xclip is available")


def clear_clipboard() -> None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        subprocess.run(
            ["wl-copy", "--clear"],
            check=True,
            timeout=10,
        )
        return

    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input="",
            text=True,
            check=True,
            timeout=10,
        )


def send_paste_shortcut(shortcut: str) -> tuple[bool, str]:
    if shortcut == "ctrl_shift_v":
        ydotool_keys = ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
        xdotool_keys = "ctrl+shift+v"
    else:
        ydotool_keys = ["29:1", "47:1", "47:0", "29:0"]
        xdotool_keys = "ctrl+v"

    if shutil.which("ydotool"):
        environment = os.environ.copy()
        environment.setdefault("YDOTOOL_SOCKET", str(RUNTIME_DIR / ".ydotool_socket"))
        result = subprocess.run(
            ["ydotool", "key", *ydotool_keys],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        logging.warning("ydotool paste failed: %s", result.stderr.strip())

    if shutil.which("xdotool"):
        result = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", xdotool_keys],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        logging.warning("xdotool paste failed: %s", result.stderr.strip())

    return False, "Text was copied, but automatic paste failed"


def paste_into_active_window(
    text: str,
    delay: float,
    sensitive: bool,
    shortcut: str,
    clear_delay: float,
) -> dict[str, Any]:
    copy_to_clipboard(text, sensitive=sensitive)
    time.sleep(delay)
    pasted, warning = send_paste_shortcut(shortcut)

    if clear_delay >= 0:
        time.sleep(clear_delay)
        try:
            clear_clipboard()
        except BaseException as error:
            clear_warning = f"Could not clear clipboard: {error}"
            warning = f"{warning}; {clear_warning}" if warning else clear_warning
            logging.warning(clear_warning)

    return {"pasted": pasted, "warning": warning, "input_method": "clipboard"}


def type_into_active_window(
    text: str,
    key_delay: int,
    key_hold: int,
) -> tuple[bool, str]:
    if not shutil.which("ydotool"):
        return False, "ydotool is unavailable"

    environment = os.environ.copy()
    environment.setdefault("YDOTOOL_SOCKET", str(RUNTIME_DIR / ".ydotool_socket"))
    result = subprocess.run(
        [
            "ydotool",
            "type",
            f"--key-delay={key_delay}",
            f"--key-hold={key_hold}",
            "--file=-",
        ],
        input=text,
        text=True,
        env=environment,
        capture_output=True,
        timeout=max(10, len(text) // 10),
    )
    if result.returncode == 0:
        return True, ""

    detail = result.stderr.strip() or f"ydotool exited with status {result.returncode}"
    logging.warning("ydotool direct typing failed: %s", detail)
    return False, detail


def send_text_to_active_window(
    text: str,
    method: str,
    paste_delay: float,
    typing_key_delay: int,
    typing_key_hold: int,
    clipboard_sensitive: bool,
    clipboard_shortcut: str,
    clipboard_clear_delay: float,
) -> dict[str, Any]:
    if method == "type":
        typed, warning = type_into_active_window(
            text,
            key_delay=typing_key_delay,
            key_hold=typing_key_hold,
        )
        if typed:
            return {"pasted": True, "warning": "", "input_method": "type"}
        logging.warning("Falling back to clipboard paste after direct typing failure")
    else:
        warning = ""

    try:
        result = paste_into_active_window(
            text,
            delay=paste_delay,
            sensitive=clipboard_sensitive,
            shortcut=clipboard_shortcut,
            clear_delay=clipboard_clear_delay,
        )
    except BaseException as error:
        clipboard_warning = f"Clipboard paste failed: {error}"
        combined = f"{warning}; {clipboard_warning}" if warning else clipboard_warning
        return {"pasted": False, "warning": combined, "input_method": "failed"}

    if warning and result.get("warning"):
        result["warning"] = f"{warning}; {result['warning']}"
    elif warning:
        result["warning"] = warning
    return result


def notify(summary: str, body: str = "") -> None:
    if not shutil.which("notify-send"):
        return
    command = ["notify-send", "--app-name=Sherpa Dictation", summary]
    if body:
        command.append(body)
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def handle_request(engine: DictationEngine, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    action = request.get("action", "toggle")
    if action == "toggle":
        return engine.toggle(), False
    if action == "continuous":
        return engine.toggle_continuous(paste=bool(request.get("paste", True))), False
    if action == "start":
        return engine.start(), False
    if action == "stop":
        return engine.stop(paste=bool(request.get("paste", True))), False
    if action == "status":
        return engine.status(), False
    if action == "transcribe":
        return engine.transcribe_wave(str(request["filename"])), False
    if action == "quit":
        if engine.listening:
            engine.stop(paste=False)
        return {"ok": True, "state": "stopped", "message": "Daemon stopped"}, True
    raise ValueError(f"Unknown action: {action}")


def daemon_main() -> int:
    global np, sherpa_onnx, sd
    import numpy as np
    import sherpa_onnx
    import sounddevice as sd

    configure_logging()
    os.umask(0o077)
    config = load_config()
    engine = DictationEngine(config)
    should_stop = False

    if SOCKET_PATH.exists():
        try:
            SOCKET_PATH.unlink()
        except OSError:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    SOCKET_PATH.chmod(0o600)
    server.listen(5)
    server.settimeout(0.5)
    logging.info("Daemon ready on %s", SOCKET_PATH)

    def request_shutdown(_signum: int, _frame: Any) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        while not should_stop:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue

            with connection:
                try:
                    payload = b""
                    while b"\n" not in payload:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        payload += chunk
                    request = json.loads(payload.split(b"\n", 1)[0].decode())
                    response, requested_stop = handle_request(engine, request)
                    should_stop = should_stop or requested_stop
                except BaseException as error:
                    logging.exception("Request failed")
                    response = {"ok": False, "state": "error", "message": str(error)}

                connection.sendall(json.dumps(response).encode() + b"\n")
    finally:
        if engine.listening:
            try:
                engine.stop(paste=False)
            except BaseException:
                logging.exception("Failed to stop recording during shutdown")
        server.close()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        logging.info("Daemon exited")

    return 0


def request_daemon(payload: dict[str, Any], timeout: float = 180) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode() + b"\n")
        response_data = b""
        while b"\n" not in response_data:
            chunk = client.recv(65536)
            if not chunk:
                break
            response_data += chunk
    finally:
        client.close()

    if not response_data:
        raise RuntimeError("The dictation daemon closed the connection without a response")
    return json.loads(response_data.split(b"\n", 1)[0].decode())


def start_daemon() -> None:
    log_file = LOG_PATH.open("a")
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "daemon"],
        cwd=PROJECT_DIR,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        close_fds=True,
    )
    log_file.close()

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if SOCKET_PATH.exists():
            try:
                response = request_daemon({"action": "status"}, timeout=2)
                if response.get("ok"):
                    return
            except (ConnectionError, OSError, RuntimeError):
                pass
        time.sleep(0.2)
    raise TimeoutError(f"Daemon did not start; inspect {LOG_PATH}")


def send_with_autostart(payload: dict[str, Any], autostart: bool = True) -> dict[str, Any]:
    try:
        return request_daemon(payload)
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, OSError):
        if not autostart:
            raise
        start_daemon()
        return request_daemon(payload)


def model_client(requested_model: str | None) -> int:
    raw_config = read_raw_config()
    names = configured_model_names(raw_config)
    if not names:
        print("Error: config.toml does not define named model profiles", file=sys.stderr)
        return 1

    current = selected_model_name(raw_config)
    if requested_model is None:
        loaded = ""
        if SOCKET_PATH.exists():
            try:
                status = request_daemon({"action": "status"}, timeout=3)
                loaded_name = status.get("model_name", "unknown")
                loaded = f"; loaded: {loaded_name}"
            except (ConnectionError, OSError, RuntimeError):
                loaded = "; daemon is not responding"
        print(f"Selected model: {current}{loaded}")
        print("Available models: " + ", ".join(names))
        return 0

    name_lookup = {name.casefold(): name for name in names}
    selected = name_lookup.get(requested_model.casefold())
    if selected is None:
        print(
            f"Error: unknown model '{requested_model}'; choose one of: {', '.join(names)}",
            file=sys.stderr,
        )
        return 1

    if SOCKET_PATH.exists():
        try:
            status = request_daemon({"action": "status"}, timeout=3)
            if status.get("model_name") != selected:
                request_daemon({"action": "quit"}, timeout=180)
                deadline = time.monotonic() + 5
                while SOCKET_PATH.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
        except (ConnectionError, OSError, RuntimeError):
            pass

    temporary_path = MODEL_SELECTION_PATH.with_suffix(".tmp")
    temporary_path.write_text(selected + "\n", encoding="utf-8")
    os.replace(temporary_path, MODEL_SELECTION_PATH)
    notify("Sherpa model selected", f"{selected}; it will load on the next dictation")
    print(f"Selected model: {selected}")
    print("It will load on the next dictation.")
    return 0


def client_main(arguments: argparse.Namespace) -> int:
    if arguments.command == "daemon":
        return daemon_main()
    if arguments.command == "model":
        return model_client(arguments.name)

    if arguments.command == "status" and not SOCKET_PATH.exists():
        print("Daemon is not running")
        return 1
    if arguments.command == "quit" and not SOCKET_PATH.exists():
        print("Daemon is not running")
        return 0

    payload: dict[str, Any] = {"action": arguments.command}
    if arguments.command == "transcribe":
        payload["filename"] = str(Path(arguments.filename).expanduser().resolve())
    elif arguments.command in {"stop", "continuous"}:
        payload["paste"] = not arguments.no_paste

    try:
        response = send_with_autostart(
            payload,
            autostart=arguments.command not in {"status", "quit"},
        )
    except BaseException as error:
        notify("Dictation error", str(error))
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not response.get("ok"):
        message = response.get("message", "Unknown error")
        notify("Dictation error", message)
        print(f"Error: {message}", file=sys.stderr)
        return 1

    state = response.get("state")
    if state == "listening":
        model_name = response.get("model_name", "model")
        if response.get("mode") == "continuous":
            notify(
                f"Continuous dictation: {model_name}",
                "Pause after each phrase; press the shortcut to finish",
            )
        else:
            notify(f"Listening: {model_name}", "Press the shortcut again to transcribe")
    elif arguments.command in {"toggle", "continuous", "stop"}:
        warning = response.get("warning", "")
        notify(response.get("message", "Transcription complete"), warning)
    elif arguments.command == "quit":
        notify("Sherpa Dictation stopped")

    text = response.get("text")
    if text:
        print(text)
    else:
        print(response.get("message", "OK"))
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Sherpa-ONNX dictation")
    subparsers = parser.add_subparsers(dest="command")
    for command in ("toggle", "start", "status", "quit", "daemon"):
        subparsers.add_parser(command)
    continuous = subparsers.add_parser(
        "continuous",
        help="Toggle continuous dictation; pauses finalize and paste each phrase",
    )
    continuous.add_argument("--no-paste", action="store_true", help="Recognize without pasting")
    stop = subparsers.add_parser("stop")
    stop.add_argument("--no-paste", action="store_true", help="Finalize without pasting")
    transcribe = subparsers.add_parser("transcribe", help="Transcribe a 16-bit PCM WAV file")
    transcribe.add_argument("filename")
    model = subparsers.add_parser("model", help="Show or select the ASR model")
    model.add_argument("name", nargs="?", help="Model profile name")

    arguments = parser.parse_args()
    if arguments.command is None:
        arguments.command = "toggle"
    return arguments


if __name__ == "__main__":
    raise SystemExit(client_main(parse_arguments()))
