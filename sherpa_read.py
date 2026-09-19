#!/usr/bin/env python3
"""Read selected or supplied text with a local sherpa-onnx TTS model."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

from sherpa_app.settings import load_settings, resolve_model_path, user_data_dir


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.toml"
LOG_PATH = user_data_dir() / "logs" / "read.log"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / "sherpa-read.sock"
LINE_BREAK = r"(?:\r\n|[\n\r\v\f\x85\u2028\u2029])"


def clean_selected_text(text: str) -> str:
    """Remove layout artifacts that make copied document text sound unnatural."""
    text = text.replace("\u200b", "").replace("\u00ad", "")
    text = re.sub(rf"[-\u2010\u2011][ \t]*{LINE_BREAK}", "", text)
    text = re.sub(LINE_BREAK, " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _capture_text(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _read_primary_selection() -> str:
    if shutil.which("wl-paste"):
        return _capture_text(["wl-paste", "--primary", "--no-newline"])
    if shutil.which("xclip"):
        return _capture_text(["xclip", "-o", "-selection", "primary"])
    return ""


def _read_clipboard() -> str:
    if shutil.which("wl-paste"):
        return _capture_text(["wl-paste", "--no-newline"])
    if shutil.which("xclip"):
        return _capture_text(["xclip", "-o", "-selection", "clipboard"])
    return ""


def _copy_selection() -> bool:
    try:
        if shutil.which("ydotool"):
            environment = os.environ.copy()
            environment.setdefault(
                "YDOTOOL_SOCKET", str(RUNTIME_DIR / ".ydotool_socket")
            )
            result = subprocess.run(
                ["ydotool", "key", "29:1", "46:1", "46:0", "29:0"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            return result.returncode == 0

        if shutil.which("xdotool"):
            result = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+c"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

    return False


def get_selected_text(
    shortcut_release_delay: float = 0.25,
    clipboard_update_delay: float = 0.20,
) -> str:
    """Return the desktop selection, using Ctrl+C as a Wayland fallback."""
    text = _read_primary_selection()
    if text:
        return clean_selected_text(text)

    time.sleep(shortcut_release_delay)
    if not _copy_selection():
        return ""
    time.sleep(clipboard_update_delay)
    return clean_selected_text(_read_clipboard())


def load_tts_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    raw_tts = raw_config.get("tts")
    if not isinstance(raw_tts, dict):
        raise ValueError("config.toml must contain a [tts] table")

    config = dict(raw_tts)
    tts_settings = load_settings().get("tts", {})
    if isinstance(tts_settings, dict):
        config.update(tts_settings)
    model_dir = resolve_model_path(
        "kitten-tts", Path(str(config.get("model_dir", "")))
    )
    if not str(config.get("model_dir", "")).strip():
        raise ValueError("tts.model_dir must not be empty")

    model_file = str(config.get("model_file", "model.int8.onnx"))
    files = {
        "model": model_dir / model_file,
        "voices": model_dir / str(config.get("voices_file", "voices.bin")),
        "tokens": model_dir / str(config.get("tokens_file", "tokens.txt")),
        "data_dir": model_dir / str(config.get("data_dir", "espeak-ng-data")),
    }
    missing = [
        str(path)
        for name, path in files.items()
        if not (path.is_dir() if name == "data_dir" else path.is_file())
    ]
    if missing:
        raise FileNotFoundError("Missing KittenTTS model files:\n" + "\n".join(missing))

    config.update(files)
    config["model_dir"] = model_dir
    config["num_threads"] = max(1, int(config.get("num_threads", 2)))
    config["speaker_id"] = int(config.get("speaker_id", 0))
    config["speed"] = float(config.get("speed", 1.0))
    config["max_text_characters"] = int(config.get("max_text_characters", 50000))
    config["audio_queue_chunks"] = max(1, int(config.get("audio_queue_chunks", 8)))
    config["output_device"] = config.get("output_device") or None
    if config["speed"] <= 0:
        raise ValueError("tts.speed must be greater than zero")
    if config["max_text_characters"] <= 0:
        raise ValueError("tts.max_text_characters must be greater than zero")
    return config


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class TtsEngine:
    """Keep a TTS model loaded and synthesize/play one utterance at a time."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.speaker_id = int(config["speaker_id"])
        self.speed = float(config["speed"])
        self.output_device = config["output_device"]
        self.audio_queue_chunks = int(config["audio_queue_chunks"])
        self.max_text_characters = int(config["max_text_characters"])
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.state = "idle"
        self.last_error = ""

        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kitten=sherpa_onnx.OfflineTtsKittenModelConfig(
                    model=str(config["model"]),
                    voices=str(config["voices"]),
                    tokens=str(config["tokens"]),
                    data_dir=str(config["data_dir"]),
                ),
                num_threads=int(config["num_threads"]),
                provider="cpu",
            ),
            max_num_sentences=1,
        )
        if not tts_config.validate():
            raise ValueError("KittenTTS configuration validation failed")

        logging.info("Loading KittenTTS model from %s", config["model"])
        self.tts = sherpa_onnx.OfflineTts(tts_config)
        if not 0 <= self.speaker_id < self.tts.num_speakers:
            raise ValueError(
                f"tts.speaker_id must be between 0 and {self.tts.num_speakers - 1}"
            )
        logging.info(
            "KittenTTS loaded; sample_rate=%d speakers=%d sid=%d speed=%.2f",
            self.tts.sample_rate,
            self.tts.num_speakers,
            self.speaker_id,
            self.speed,
        )

    def _run(self, text: str, stop_event: threading.Event) -> None:
        with self.lock:
            audio_queue_chunks = self.audio_queue_chunks
            output_device = self.output_device
            speaker_id = self.speaker_id
            speed = self.speed
        audio_queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=audio_queue_chunks
        )
        generation_done = threading.Event()
        playback_done = threading.Event()
        current_chunk: np.ndarray | None = None
        current_offset = 0

        def generation_callback(samples: np.ndarray, _progress: float) -> int:
            if stop_event.is_set():
                return 0
            while not stop_event.is_set():
                try:
                    audio_queue.put(np.asarray(samples, dtype=np.float32), timeout=0.1)
                    return 1
                except queue.Full:
                    continue
            return 0

        def playback_callback(
            output: np.ndarray,
            frames: int,
            _time_info: Any,
            status: sd.CallbackFlags,
        ) -> None:
            nonlocal current_chunk, current_offset
            if status:
                logging.warning("TTS output callback status: %s", status)
            if stop_event.is_set():
                raise sd.CallbackAbort

            output.fill(0)
            written = 0
            while written < frames:
                if current_chunk is None:
                    try:
                        current_chunk = audio_queue.get_nowait()
                        current_offset = 0
                    except queue.Empty:
                        if generation_done.is_set():
                            raise sd.CallbackStop
                        return

                available = current_chunk.size - current_offset
                count = min(frames - written, available)
                output[written : written + count, 0] = current_chunk[
                    current_offset : current_offset + count
                ]
                written += count
                current_offset += count
                if current_offset == current_chunk.size:
                    current_chunk = None
                    current_offset = 0

        started = time.monotonic()
        try:
            with sd.OutputStream(
                samplerate=self.tts.sample_rate,
                blocksize=1024,
                device=output_device,
                channels=1,
                dtype="float32",
                callback=playback_callback,
                finished_callback=playback_done.set,
            ):
                audio = self.tts.generate(
                    text,
                    sid=speaker_id,
                    speed=speed,
                    callback=generation_callback,
                )
                generation_done.set()
                if not stop_event.is_set():
                    playback_done.wait()

            if not stop_event.is_set() and len(audio.samples) == 0:
                raise RuntimeError("KittenTTS generated no audio")
            logging.info(
                "Reading %s; characters=%d elapsed=%.3f",
                "stopped" if stop_event.is_set() else "completed",
                len(text),
                time.monotonic() - started,
            )
        except BaseException as error:
            if stop_event.is_set():
                logging.info("Reading stopped after %.3f seconds", time.monotonic() - started)
            else:
                self.last_error = str(error)
                logging.exception("Reading failed")
                notify("Reading error", str(error))
        finally:
            generation_done.set()
            playback_done.set()
            with self.lock:
                if self.worker is threading.current_thread():
                    self.state = "idle"
                    self.worker = None
                    self.stop_event = None

    def speak(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("No text to read")
        with self.lock:
            max_text_characters = self.max_text_characters
        if len(text) > max_text_characters:
            raise ValueError(
                f"Selected text has {len(text)} characters; the configured maximum is "
                f"{max_text_characters}"
            )

        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("Already reading; stop the current text first")
            stop_event = threading.Event()
            worker = threading.Thread(
                target=self._run,
                args=(text, stop_event),
                name="tts-reader",
                daemon=True,
            )
            self.stop_event = stop_event
            self.worker = worker
            self.state = "speaking"
            self.last_error = ""
            worker.start()

        return {
            "ok": True,
            "state": "speaking",
            "message": "Reading selected text",
            "characters": len(text),
        }

    def stop(self) -> dict[str, Any]:
        with self.lock:
            worker = self.worker
            stop_event = self.stop_event
            if worker is None or not worker.is_alive():
                self.state = "idle"
                return {"ok": True, "state": "idle", "message": "Not reading"}
            assert stop_event is not None
            stop_event.set()
            self.state = "stopping"
        return {"ok": True, "state": "stopping", "message": "Reading stopped"}

    def reload_runtime_settings(self) -> dict[str, Any]:
        config = load_tts_config()
        if not 0 <= int(config["speaker_id"]) < self.tts.num_speakers:
            raise ValueError(
                f"tts.speaker_id must be between 0 and {self.tts.num_speakers - 1}"
            )
        restart_keys = (
            "model",
            "voices",
            "tokens",
            "data_dir",
            "num_threads",
        )
        restart_required = [
            key for key in restart_keys if config.get(key) != self.config.get(key)
        ]
        with self.lock:
            self.speaker_id = int(config["speaker_id"])
            self.speed = float(config["speed"])
            self.output_device = config["output_device"]
            self.audio_queue_chunks = int(config["audio_queue_chunks"])
            self.max_text_characters = int(config["max_text_characters"])
            state = self.state
        self.config = config
        message = "Runtime text-to-speech settings applied"
        if restart_required:
            message += "; restart required for " + ", ".join(restart_required)
        return {
            "ok": True,
            "state": state,
            "message": message,
            "restart_required": restart_required,
        }

    def shutdown(self) -> None:
        self.stop()
        with self.lock:
            worker = self.worker
        if worker is not None:
            worker.join(timeout=10)

    def status(self) -> dict[str, Any]:
        with self.lock:
            worker_alive = self.worker is not None and self.worker.is_alive()
            state = self.state if worker_alive else "idle"
            if not worker_alive:
                self.state = "idle"
        return {
            "ok": True,
            "state": state,
            "message": "Reading" if state == "speaking" else state.capitalize(),
            "model": str(self.config["model"]),
            "speaker_id": self.speaker_id,
            "speed": self.speed,
            "sample_rate": self.tts.sample_rate,
            "last_error": self.last_error,
        }


def notify(summary: str, body: str = "") -> None:
    if not shutil.which("notify-send"):
        return
    command = ["notify-send", "--app-name=Sherpa Reader", summary]
    if body:
        command.append(body)
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def handle_request(engine: TtsEngine, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    action = str(request.get("action", "status"))
    if action == "speak":
        return engine.speak(str(request.get("text", ""))), False
    if action == "stop":
        return engine.stop(), False
    if action == "status":
        return engine.status(), False
    if action == "reload-settings":
        return engine.reload_runtime_settings(), False
    if action == "quit":
        engine.shutdown()
        return {"ok": True, "state": "stopped", "message": "Reader stopped"}, True
    raise ValueError(f"Unknown action: {action}")


def daemon_main() -> int:
    global np, sd, sherpa_onnx
    import numpy as np
    import sherpa_onnx
    import sounddevice as sd

    configure_logging()
    os.umask(0o077)
    engine = TtsEngine(load_tts_config())
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
    logging.info("Reader daemon ready on %s", SOCKET_PATH)

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
                    logging.exception("Reader request failed")
                    response = {"ok": False, "state": "error", "message": str(error)}
                connection.sendall(json.dumps(response).encode() + b"\n")
    finally:
        engine.shutdown()
        server.close()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        logging.info("Reader daemon exited")
    return 0


def request_daemon(payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
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
        raise RuntimeError("The reader daemon closed the connection without a response")
    return json.loads(response_data.split(b"\n", 1)[0].decode())


def start_daemon() -> None:
    load_tts_config()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("a")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "engine", "read", "daemon"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "daemon"]
    process = subprocess.Popen(
        command,
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
        if process.poll() is not None:
            raise RuntimeError(f"Reader daemon failed to start; inspect {LOG_PATH}")
        if SOCKET_PATH.exists():
            try:
                response = request_daemon({"action": "status"}, timeout=2)
                if response.get("ok"):
                    return
            except (ConnectionError, OSError, RuntimeError):
                pass
        time.sleep(0.2)
    process.terminate()
    raise TimeoutError(f"Reader daemon did not start; inspect {LOG_PATH}")


def send_with_autostart(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return request_daemon(payload)
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, OSError):
        start_daemon()
        return request_daemon(payload)


def current_status() -> dict[str, Any] | None:
    if not SOCKET_PATH.exists():
        return None
    try:
        return request_daemon({"action": "status"}, timeout=2)
    except (ConnectionError, OSError, RuntimeError):
        return None


def report_response(response: dict[str, Any], command: str) -> int:
    if not response.get("ok"):
        message = str(response.get("message", "Unknown error"))
        notify("Reading error", message)
        print(f"Error: {message}", file=sys.stderr)
        return 1

    if command == "speak":
        notify("Sherpa Reader", "Press the shortcut again to stop")
    elif command == "stop":
        notify("Sherpa Reader", "Reading stopped")
    print(response.get("message", "OK"))
    return 0


def restart_client() -> int:
    status = current_status()
    if status and status.get("state") in {"speaking", "stopping"}:
        print("Error: stop text-to-speech before restarting its background service", file=sys.stderr)
        return 1
    if status is not None:
        request_daemon({"action": "quit"}, timeout=15)
        deadline = time.monotonic() + 5
        while SOCKET_PATH.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if SOCKET_PATH.exists():
            raise TimeoutError("Reader daemon did not stop before restart")
    start_daemon()
    print("Text-to-speech background service restarted")
    return 0


def client_main(arguments: argparse.Namespace) -> int:
    if arguments.command == "daemon":
        return daemon_main()
    if arguments.command == "restart":
        try:
            return restart_client()
        except BaseException as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    if arguments.command == "status":
        status = current_status()
        if status is None:
            print("Reader daemon is not running")
            return 1
        print(json.dumps(status, indent=2))
        return 0

    if arguments.command == "quit":
        if not SOCKET_PATH.exists():
            print("Reader daemon is not running")
            return 0
        try:
            return report_response(request_daemon({"action": "quit"}), "quit")
        except BaseException as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    if arguments.command == "reload-settings":
        if not SOCKET_PATH.exists():
            print("Reader daemon is not running; settings will be used when reading starts")
            return 0
        try:
            return report_response(
                request_daemon({"action": "reload-settings"}),
                "reload-settings",
            )
        except BaseException as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    if arguments.command == "stop":
        if not SOCKET_PATH.exists():
            print("Reader daemon is not running")
            return 0
        try:
            return report_response(request_daemon({"action": "stop"}), "stop")
        except BaseException as error:
            notify("Reading error", str(error))
            print(f"Error: {error}", file=sys.stderr)
            return 1

    if arguments.command == "selection":
        status = current_status()
        if status and status.get("state") in {"speaking", "stopping"}:
            return report_response(request_daemon({"action": "stop"}), "stop")
        text = get_selected_text()
    else:
        text = arguments.text if arguments.text is not None else sys.stdin.read()
        text = clean_selected_text(text)

    if not text:
        notify("Sherpa Reader", "No selected text found")
        print("No selected text found.", file=sys.stderr)
        return 1

    try:
        response = send_with_autostart({"action": "speak", "text": text})
    except BaseException as error:
        notify("Reading error", str(error))
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return report_response(response, "speak")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Sherpa text-to-speech reader")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("selection", help="Toggle reading of the desktop selection")
    speak = subparsers.add_parser("speak", help="Read an argument, or stdin when omitted")
    speak.add_argument("text", nargs="?")
    for command in (
        "stop",
        "status",
        "quit",
        "daemon",
        "reload-settings",
        "restart",
    ):
        subparsers.add_parser(command)

    arguments = parser.parse_args(argv)
    if arguments.command is None:
        arguments.command = "selection"
    return arguments


if __name__ == "__main__":
    raise SystemExit(client_main(parse_arguments()))
