import queue
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import soxr

import sherpa_dictate
from sherpa_dictate import DictationEngine, _QUEUE_END
from sherpa_app.speech_detection import MODEL_PATH, SAMPLE_RATE, SpeechDetector


FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / name), "rb") as audio:
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(
            np.float32
        ) / 32768


class SpeechDetectionTests(unittest.TestCase):
    def decisions(self, detector: SpeechDetector, samples: np.ndarray, rate: int) -> list[bool]:
        block = rate // 10
        return [
            detector.is_speech(samples[offset : offset + block])
            for offset in range(0, samples.size, block)
        ]

    def test_real_model_accepts_speech_and_short_commands_at_microphone_rates(self) -> None:
        for rate in (16000, 44100, 48000):
            for name in ("vad-speech.wav", "vad-command.wav"):
                with self.subTest(rate=rate, fixture=name):
                    samples = read_fixture(name)
                    if rate != SAMPLE_RATE:
                        samples = soxr.resample(samples, SAMPLE_RATE, rate)
                    detector = SpeechDetector(rate)
                    decisions = self.decisions(detector, samples, rate)
                    self.assertGreaterEqual(sum(decisions), 3)

    def test_loud_noise_and_hum_do_not_count_as_speech(self) -> None:
        for rate in (16000, 44100, 48000):
            noise = np.random.default_rng(1).normal(0, 0.06, rate * 3).astype(np.float32)
            hum = (0.1 * np.sin(2 * np.pi * 120 * np.arange(rate * 3) / rate)).astype(
                np.float32
            )
            for label, samples in (("noise", noise), ("hum", hum)):
                with self.subTest(rate=rate, sound=label):
                    detector = SpeechDetector(rate)
                    self.assertFalse(any(self.decisions(detector, samples, rate)))

    def test_speech_in_noise_is_detected_and_following_noise_releases_it(self) -> None:
        rate = 48000
        speech = soxr.resample(read_fixture("vad-speech.wav"), SAMPLE_RATE, rate)
        rng = np.random.default_rng(2)
        speech += rng.normal(0, 0.015, speech.size).astype(np.float32)
        detector = SpeechDetector(rate)
        self.assertTrue(any(self.decisions(detector, speech, rate)))
        noise = rng.normal(0, 0.06, rate).astype(np.float32)
        # Allow the recurrent detector and resampler to settle after speech.
        self.assertFalse(any(self.decisions(detector, noise, rate)[3:]))

    def test_reset_discards_recurrent_state_and_partial_frames(self) -> None:
        detector = SpeechDetector(48000)
        speech = soxr.resample(read_fixture("vad-speech.wav"), SAMPLE_RATE, 48000)
        first_pass = self.decisions(detector, speech, 48000)
        detector.is_speech(np.ones(73, dtype=np.float32))
        detector.reset()
        self.assertEqual(detector.pending.size, 0)
        self.assertFalse(detector.last_is_speech)
        self.assertEqual(self.decisions(detector, speech, 48000), first_pass)

    def test_a_minute_of_background_noise_does_not_poison_the_next_utterance(self) -> None:
        detector = SpeechDetector(48000)
        noise = np.random.default_rng(3).normal(0, 0.06, 48000 * 60).astype(np.float32)
        self.assertFalse(any(self.decisions(detector, noise, 48000)))
        speech = soxr.resample(read_fixture("vad-command.wav"), SAMPLE_RATE, 48000)
        self.assertGreaterEqual(sum(self.decisions(detector, speech, 48000)), 3)
        self.assertLess(detector.pending.size, detector.window_size)

    def test_real_speech_followed_by_loud_noise_finishes_without_the_30_second_wait(self) -> None:
        rate = 48000
        engine = DictationEngine.__new__(DictationEngine)
        engine.settings_lock = threading.Lock()
        engine._apply_runtime_settings({"silence_ms": 300})
        engine.input_sample_rate = rate
        engine.speech_detector = SpeechDetector(rate)
        engine.audio_queue = queue.Queue()
        engine.phrase_queue = queue.Queue()
        engine.stop_event = threading.Event()
        engine.worker_error = None
        speech = soxr.resample(read_fixture("vad-speech.wav"), SAMPLE_RATE, rate)
        noise = np.random.default_rng(1).normal(0, 0.06, rate * 2).astype(np.float32)
        samples = np.concatenate((speech, noise))
        for offset in range(0, samples.size, rate // 10):
            engine.audio_queue.put(samples[offset : offset + rate // 10])
        engine.stop_event.set()
        with patch.object(sherpa_dictate, "np", np, create=True):
            engine._continuous_segment_worker()

        phrase = engine.phrase_queue.get_nowait()
        self.assertIsNone(engine.worker_error)
        self.assertTrue(phrase.first)
        # Preserve the start of the utterance, then finalize soon after it ends
        # despite the following noise remaining above the volume threshold.
        np.testing.assert_array_equal(
            np.concatenate(phrase.chunks)[:rate // 10], speech[:rate // 10]
        )
        self.assertLess(phrase.sample_count, speech.size + rate)
        self.assertIs(engine.phrase_queue.get_nowait(), _QUEUE_END)

    def test_partial_frames_are_retained_and_every_complete_frame_is_processed(self) -> None:
        detector = SpeechDetector(SAMPLE_RATE)
        detector.model = Mock()
        detector.model.is_speech.side_effect = [True, False, False]
        self.assertFalse(detector.is_speech(np.zeros(100, dtype=np.float32)))
        self.assertEqual(detector.model.is_speech.call_count, 0)
        self.assertTrue(detector.is_speech(np.zeros(1000, dtype=np.float32)))
        self.assertEqual(detector.model.is_speech.call_count, 2)
        self.assertEqual(detector.pending.size, 76)
        self.assertFalse(detector.is_speech(np.zeros(436, dtype=np.float32)))
        self.assertEqual(detector.model.is_speech.call_count, 3)

    def test_missing_model_reports_an_actionable_error(self) -> None:
        with patch("sherpa_app.speech_detection.MODEL_PATH", MODEL_PATH / "missing"):
            with self.assertRaisesRegex(FileNotFoundError, "Reinstall Sherpa"):
                SpeechDetector(SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
