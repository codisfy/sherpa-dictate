import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import sherpa_dictate
from sherpa_dictate import DictationEngine


class ContinuousPipelineTests(unittest.TestCase):
    def make_engine(self) -> DictationEngine:
        engine = DictationEngine.__new__(DictationEngine)
        engine.input_sample_rate = 10
        engine.silence_seconds = 0.2
        engine.pre_roll_seconds = 0.1
        engine.first_phrase_pre_roll_seconds = 0.1
        engine.min_speech_seconds = 0.1
        engine.max_phrase_seconds = 10
        engine.speech_threshold = 0.5
        engine.audio_queue = queue.Queue()
        engine.phrase_queue = queue.Queue()
        engine.output_queue = queue.Queue()
        engine.stop_event = threading.Event()
        engine.settings_lock = threading.Lock()
        engine.spoken_punctuation = True
        engine.append_space = True
        engine.output_method = "type"
        engine.continuous_paste = True
        engine.phrases_pasted = 0
        engine.paste_warning = ""
        engine.worker_result = ""
        engine.worker_error = None
        return engine

    def test_slow_typing_does_not_block_recording_or_recognition(self) -> None:
        sherpa_dictate.np = np
        engine = self.make_engine()
        typing_started = threading.Event()
        allow_typing_to_finish = threading.Event()
        second_phrase_recognized = threading.Event()
        recognized = 0
        typed: list[str] = []

        def recognize(_phrase: object) -> str:
            nonlocal recognized
            recognized += 1
            if recognized == 2:
                second_phrase_recognized.set()
            return f"phrase {recognized}"

        def send_text(text: str) -> dict[str, object]:
            typed.append(text)
            if len(typed) == 1:
                typing_started.set()
                self.assertTrue(allow_typing_to_finish.wait(timeout=2))
            return {"pasted": True, "warning": ""}

        engine._recognize_continuous_phrase = recognize  # type: ignore[method-assign]
        engine._send_text = send_text  # type: ignore[method-assign]
        workers = [
            threading.Thread(target=engine._continuous_output_worker),
            threading.Thread(target=engine._continuous_recognition_worker),
            threading.Thread(target=engine._continuous_segment_worker),
        ]
        for worker in workers:
            worker.start()

        speech = np.ones(1, dtype=np.float32)
        silence = np.zeros(1, dtype=np.float32)
        for chunk in (speech, silence, silence):
            engine.audio_queue.put(chunk)
        self.assertTrue(typing_started.wait(timeout=2))

        for chunk in (speech, silence, silence):
            engine.audio_queue.put(chunk)
        engine.stop_event.set()

        self.assertTrue(second_phrase_recognized.wait(timeout=2))
        self.assertEqual(typed, ["phrase 1"])
        allow_typing_to_finish.set()
        for worker in workers:
            worker.join(timeout=2)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertIsNone(engine.worker_error)
        self.assertEqual(typed, ["phrase 1", " phrase 2"])
        self.assertEqual(engine.worker_result, "phrase 1\nphrase 2")

    def test_runtime_output_method_reload_does_not_require_restart(self) -> None:
        engine = DictationEngine.__new__(DictationEngine)
        engine.settings_lock = threading.Lock()
        engine.listening = True
        engine.model_name = "parakeet"
        engine.model_dir = Path("/models/parakeet")
        engine.backend = "offline"
        engine.model_type = "nemo_transducer"
        engine.input_sample_rate = 48000
        engine.num_threads = 8
        engine.output_method = "type"

        config = {
            "model_name": "parakeet",
            "model_dir": Path("/models/parakeet"),
            "backend": "offline",
            "model_type": "nemo_transducer",
            "input_sample_rate": 48000,
            "num_threads": 8,
            "output_method": "clipboard",
        }
        with patch("sherpa_dictate.load_config", return_value=config):
            result = engine.reload_runtime_settings()

        self.assertEqual(engine.output_method, "clipboard")
        self.assertEqual(result["state"], "listening")
        self.assertEqual(result["restart_required"], [])


if __name__ == "__main__":
    unittest.main()
