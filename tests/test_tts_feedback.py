import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import sherpa_dictate
import sherpa_read
from sherpa_app.playback import TtsPlaybackMonitor, tts_playback
from sherpa_dictate import DictationEngine, _QUEUE_END
from sherpa_read import TtsEngine


class TtsFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self.temporary.name)
        self.monitor = TtsPlaybackMonitor(self.runtime_dir)
        self.tail = patch("sherpa_app.playback.time.sleep")
        self.tail.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.monitor.close)
        self.addCleanup(self.tail.stop)
        self.numpy = patch.object(sherpa_dictate, "np", np, create=True)
        self.numpy.start()
        self.addCleanup(self.numpy.stop)

    def make_engine(self, mode: str) -> DictationEngine:
        engine = DictationEngine.__new__(DictationEngine)
        engine.settings_lock = threading.Lock()
        engine._apply_runtime_settings({
            "silence_ms": 200,
            "pre_roll_ms": 100,
            "first_phrase_pre_roll_ms": 100,
            "min_speech_ms": 100,
            "speech_threshold": 0.5,
        })
        engine.input_sample_rate = 10
        engine.audio_queue = queue.Queue()
        engine.phrase_queue = queue.Queue()
        engine.stop_event = threading.Event()
        engine.playback_monitor = self.monitor
        engine.mode = mode
        engine.worker_error = None
        return engine

    def capture(self, engine: DictationEngine, amplitude: float) -> None:
        engine._audio_callback(np.full((1, 1), amplitude, dtype=np.float32), 1, None, None)

    def test_manual_capture_ignores_playback_and_resumes(self) -> None:
        engine = self.make_engine("manual")
        self.capture(engine, 1)
        with tts_playback(self.runtime_dir):
            self.capture(engine, 9)
        self.capture(engine, 2)

        self.assertEqual(engine.audio_queue.qsize(), 2)
        self.assertEqual(engine.audio_queue.get().tolist(), [1])
        self.assertEqual(engine.audio_queue.get().tolist(), [2])

    def test_continuous_capture_preserves_user_phrases_without_playback_in_pre_roll(self) -> None:
        engine = self.make_engine("continuous")
        self.capture(engine, 1)
        with tts_playback(self.runtime_dir):
            for _ in range(3):
                self.capture(engine, 9)
        self.capture(engine, 2)
        self.capture(engine, 0)
        self.capture(engine, 0)
        engine.stop_event.set()
        engine._continuous_segment_worker()

        first = engine.phrase_queue.get_nowait()
        second = engine.phrase_queue.get_nowait()
        self.assertEqual(np.concatenate(first.chunks).tolist(), [1, 0, 0])
        self.assertEqual(np.concatenate(second.chunks).tolist(), [2, 0, 0])
        self.assertIs(engine.phrase_queue.get_nowait(), _QUEUE_END)
        self.assertIsNone(engine.worker_error)

    def test_disabling_and_reenabling_protection_during_playback(self) -> None:
        for mode in ("manual", "continuous"):
            with self.subTest(mode=mode):
                engine = self.make_engine(mode)
                self.assertTrue(engine.ignore_tts_playback)
                with tts_playback(self.runtime_dir):
                    engine._apply_runtime_settings({"ignore_tts_playback": False})
                    self.capture(engine, 9)
                    engine._apply_runtime_settings({"ignore_tts_playback": True})
                    self.capture(engine, 8)
                self.assertEqual(engine.audio_queue.get_nowait().tolist(), [9])
                if mode == "continuous":
                    self.assertEqual(engine.audio_queue.get_nowait().tolist(), [0])
                self.assertTrue(engine.audio_queue.empty())

    def test_reader_holds_activity_until_output_closes_including_stop_and_error(self) -> None:
        for outcome in ("completed", "stopped", "failed"):
            with self.subTest(outcome=outcome):
                stop_event = threading.Event()
                engine = TtsEngine.__new__(TtsEngine)
                engine.lock = threading.Lock()
                engine.audio_queue_chunks = 8
                engine.output_device = None
                engine.speaker_id = 0
                engine.speed = 1
                engine.worker = threading.current_thread()
                engine.state = "speaking"
                engine.stop_event = stop_event
                engine.pause_event = threading.Event()
                engine.playback_activity = None
                engine.output_latency = 0.0
                engine.last_error = ""
                monitor = self.monitor

                class OutputStream:
                    def __init__(self, **kwargs):
                        self.finished = kwargs["finished_callback"]
                        self.latency = 0.0

                    def __enter__(self):
                        self_active = monitor.active()
                        if not self_active:
                            raise AssertionError("Playback must be guarded before output opens")
                        return self

                    def __exit__(self, *args):
                        if not monitor.active():
                            raise AssertionError("Playback must stay guarded until output closes")

                def generate(*args, **kwargs):
                    self.assertTrue(monitor.active())
                    if outcome == "failed":
                        raise RuntimeError("synthesis failed")
                    if outcome == "stopped":
                        stop_event.set()
                    stream.finished()
                    return SimpleNamespace(samples=np.ones(1))

                def make_output(**kwargs):
                    nonlocal stream
                    stream = OutputStream(**kwargs)
                    return stream

                stream = None
                engine.tts = SimpleNamespace(sample_rate=48000, generate=generate)
                with (
                    patch.object(sherpa_read, "RUNTIME_DIR", self.runtime_dir),
                    patch.object(sherpa_read, "sd", SimpleNamespace(OutputStream=make_output), create=True),
                    patch("sherpa_read.notify"),
                    patch("sherpa_read.logging.exception"),
                ):
                    engine._run("Read this", stop_event)

                self.assertFalse(self.monitor.active())
                self.assertEqual(engine.state, "idle")
                self.assertIsNone(engine.worker)
                self.assertEqual(engine.last_error, "synthesis failed" if outcome == "failed" else "")

    def test_reader_crash_releases_activity_across_processes(self) -> None:
        code = (
            "from pathlib import Path\n"
            "import sys\n"
            "from sherpa_app.playback import tts_playback\n"
            "with tts_playback(Path(sys.argv[1])):\n"
            "    print('ready', flush=True)\n"
            "    sys.stdin.read()\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(self.runtime_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertTrue(self.monitor.active())
            process.kill()
            process.communicate(timeout=5)
            self.assertFalse(self.monitor.active())
            # The file remains, but cannot cause a stale muted state.
            self.assertTrue((self.runtime_dir / "sherpa-tts-playback.lock").exists())
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_acoustic_tail_is_guarded_after_an_error(self) -> None:
        def check_tail(seconds):
            self.assertEqual(seconds, 0.3)
            self.assertTrue(self.monitor.active())

        with patch("sherpa_app.playback.time.sleep", side_effect=check_tail) as sleep:
            with self.assertRaisesRegex(RuntimeError, "output failed"):
                with tts_playback(self.runtime_dir):
                    raise RuntimeError("output failed")
            sleep.assert_called_once()
        self.assertFalse(self.monitor.active())

    def test_dictation_resumes_during_pause_and_is_protected_before_resume(self) -> None:
        engine = self.make_engine("manual")
        with tts_playback(self.runtime_dir) as activity:
            self.capture(engine, 9)
            activity.pause(output_latency=0.05)
            self.capture(engine, 1)
            activity.resume()
            self.capture(engine, 8)
        self.capture(engine, 2)
        self.assertEqual(engine.audio_queue.get_nowait().tolist(), [1])
        self.assertEqual(engine.audio_queue.get_nowait().tolist(), [2])
        self.assertTrue(engine.audio_queue.empty())


if __name__ == "__main__":
    unittest.main()
