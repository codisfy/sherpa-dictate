import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import sherpa_entry
import sherpa_read
from sherpa_app.playback import TtsPlaybackMonitor
from sherpa_read import TtsEngine, client_main, handle_request, parse_arguments, restart_client


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class DrivenOutputStream:
    """Run real playback callbacks on demand to inspect exactly which samples play."""

    def __init__(self, entered, **kwargs):
        self.callback = kwargs["callback"]
        self.finished = kwargs["finished_callback"]
        self.latency = 0.05
        self.entered = entered

    def __enter__(self):
        self.entered.set()
        return self

    def __exit__(self, *args):
        self.finished()

    def render(self, frames):
        output = np.full((frames, 1), -1, dtype=np.float32)
        try:
            self.callback(output, frames, None, None)
        except CallbackStop:
            self.finished()
        except CallbackAbort:
            output.fill(0)
            self.finished()
        return output[:, 0].tolist()


class PausePlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self.temporary.name)
        self.monitor = TtsPlaybackMonitor(self.runtime_dir)
        self.entered = threading.Event()
        self.generated = threading.Event()
        self.stream = None
        self.engine = TtsEngine.__new__(TtsEngine)
        self.engine.lock = threading.Lock()
        self.engine.worker = None
        self.engine.stop_event = None
        self.engine.pause_event = threading.Event()
        self.engine.playback_activity = None
        self.engine.output_latency = 0.0
        self.engine.audio_queue_chunks = 2
        self.engine.output_device = None
        self.engine.speaker_id = 0
        self.engine.speed = 1.0
        self.engine.max_text_characters = 10000
        self.engine.state = "idle"
        self.engine.last_error = ""
        self.engine.config = {"model": "test.onnx"}
        self.engine.tts = SimpleNamespace(sample_rate=48000, generate=self.generate)
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(patch.object(sherpa_read, "RUNTIME_DIR", self.runtime_dir))
        self.patches.enter_context(patch.object(sherpa_read, "np", np, create=True))
        self.patches.enter_context(patch.object(sherpa_read, "sd", SimpleNamespace(
            OutputStream=self.make_stream, CallbackStop=CallbackStop, CallbackAbort=CallbackAbort,
        ), create=True))
        self.patches.enter_context(patch("sherpa_app.playback.time.sleep"))
        self.patches.enter_context(patch("sherpa_read.notify"))

    def tearDown(self) -> None:
        self.engine.stop()
        worker = self.engine.worker
        if self.stream is not None:
            self.stream.finished()
        if worker is not None:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.patches.close()
        self.monitor.close()
        self.temporary.cleanup()

    def make_stream(self, **kwargs):
        self.stream = DrivenOutputStream(self.entered, **kwargs)
        return self.stream

    def generate(self, text, sid, speed, callback):
        samples = np.arange(1, 11, dtype=np.float32)
        callback(samples[:6], 0.6)
        callback(samples[6:], 1.0)
        self.generated.set()
        return SimpleNamespace(samples=samples)

    def start(self):
        self.engine.speak("Read these words")
        self.assertTrue(self.entered.wait(timeout=2))
        self.assertTrue(self.generated.wait(timeout=2))

    def test_repeated_pauses_preserve_partial_chunk_and_queue_positions(self) -> None:
        self.start()
        played = self.stream.render(2)
        for frames in (2, 5):
            response, shutdown = handle_request(self.engine, {"action": "toggle-pause"})
            self.assertEqual(response["state"], "paused")
            self.assertFalse(shutdown)
            self.assertEqual(self.engine.status()["state"], "paused")
            self.assertFalse(self.monitor.active())
            self.assertEqual(self.stream.render(4), [0, 0, 0, 0])
            self.assertEqual(self.stream.render(4), [0, 0, 0, 0])

            response = self.engine.toggle_pause()
            self.assertEqual(response["state"], "speaking")
            self.assertTrue(self.monitor.active())
            played.extend(self.stream.render(frames))

        played.extend(self.stream.render(1))
        self.assertEqual(played, list(range(1, 11)))
        worker = self.engine.worker
        self.assertEqual(self.stream.render(1), [0])
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.engine.state, "idle")
        self.assertFalse(self.monitor.active())
        self.assertEqual(self.engine.last_error, "")

    def test_pause_keeps_new_selection_and_restart_from_discarding_reading(self) -> None:
        self.start()
        self.engine.toggle_pause()
        with self.assertRaisesRegex(RuntimeError, "Already reading"):
            self.engine.speak("A replacement selection")
        with (
            patch("sherpa_read.current_status", return_value=self.engine.status()),
            patch("sherpa_read.start_daemon") as start,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(restart_client(), 1)
        start.assert_not_called()

    def test_stop_while_paused_unblocks_a_full_synthesis_queue(self) -> None:
        self.engine.audio_queue_chunks = 1
        queued = []

        def generate(text, sid, speed, callback):
            for value in range(20):
                if callback(np.full(6, value, dtype=np.float32), 0.0) == 0:
                    break
                queued.append(value)
                self.generated.set()
            return SimpleNamespace(samples=np.ones(6))

        self.engine.tts.generate = generate
        self.start()
        self.engine.toggle_pause()
        self.assertEqual(self.stream.render(10), [0] * 10)
        self.assertEqual(queued, [0])
        worker = self.engine.worker
        self.engine.stop()
        # A late pause shortcut must not restart a reading that is stopping.
        self.assertIn(self.engine.toggle_pause()["state"], {"stopping", "idle"})
        self.stream.render(1)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.engine.state, "idle")
        self.assertFalse(self.monitor.active())
        self.assertEqual(queued, [0])

    def test_pause_before_any_audio_can_resume_and_complete(self) -> None:
        self.start()
        self.engine.toggle_pause()
        self.assertEqual(self.stream.render(4), [0, 0, 0, 0])
        self.engine.toggle_pause()
        self.assertEqual(self.stream.render(10), list(range(1, 11)))

    def test_idle_pause_does_not_start_a_worker(self) -> None:
        self.assertEqual(self.engine.toggle_pause()["state"], "idle")
        self.assertIsNone(self.engine.worker)


class PauseCommandTests(unittest.TestCase):
    def test_shortcut_and_cli_route_to_the_toggle_without_capturing_text(self) -> None:
        with patch("sherpa_entry._run_engine", return_value=0) as run:
            self.assertEqual(sherpa_entry.main(["action", "tts-pause-toggle"]), 0)
        run.assert_called_once_with("read", ["toggle-pause"])

        with (
            patch("sherpa_read.SOCKET_PATH") as socket,
            patch("sherpa_read.request_daemon", return_value={"ok": True}) as request,
            patch("sherpa_read.report_response", return_value=0),
            patch("sherpa_read.get_selected_text") as capture,
            patch("sherpa_read.start_daemon") as start,
        ):
            socket.exists.return_value = True
            self.assertEqual(client_main(parse_arguments(["toggle-pause"])), 0)
        request.assert_called_once_with({"action": "toggle-pause"})
        capture.assert_not_called()
        start.assert_not_called()

    def test_toggle_without_a_daemon_is_a_noop(self) -> None:
        with (
            patch("sherpa_read.SOCKET_PATH") as socket,
            patch("sherpa_read.request_daemon") as request,
            patch("sherpa_read.start_daemon") as start,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            socket.exists.return_value = False
            self.assertEqual(client_main(parse_arguments(["toggle-pause"])), 0)
        request.assert_not_called()
        start.assert_not_called()

    def test_read_selection_stops_an_existing_paused_reading(self) -> None:
        with (
            patch("sherpa_read.current_status", return_value={"state": "paused"}),
            patch("sherpa_read.request_daemon", return_value={"ok": True}) as request,
            patch("sherpa_read.report_response", return_value=0),
            patch("sherpa_read.get_selected_text") as capture,
        ):
            self.assertEqual(client_main(parse_arguments(["selection"])), 0)
        request.assert_called_once_with({"action": "stop"})
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
