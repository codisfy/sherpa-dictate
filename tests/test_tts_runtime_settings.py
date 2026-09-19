import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sherpa_read import TtsEngine


class TtsRuntimeSettingsTests(unittest.TestCase):
    def test_voice_speed_and_device_reload_without_rebuilding_model(self) -> None:
        engine = TtsEngine.__new__(TtsEngine)
        engine.lock = threading.Lock()
        engine.tts = SimpleNamespace(num_speakers=8)
        engine.state = "idle"
        engine.config = {
            "model": "model.onnx",
            "voices": "voices.bin",
            "tokens": "tokens.txt",
            "data_dir": "espeak-ng-data",
            "num_threads": 2,
        }
        config = {
            **engine.config,
            "speaker_id": 5,
            "speed": 1.8,
            "output_device": "speakers",
            "audio_queue_chunks": 12,
            "max_text_characters": 10000,
        }

        with patch("sherpa_read.load_tts_config", return_value=config):
            result = engine.reload_runtime_settings()

        self.assertEqual(result["restart_required"], [])
        self.assertEqual(engine.speaker_id, 5)
        self.assertEqual(engine.speed, 1.8)
        self.assertEqual(engine.output_device, "speakers")


if __name__ == "__main__":
    unittest.main()
