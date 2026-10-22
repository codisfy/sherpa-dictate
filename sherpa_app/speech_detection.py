"""Local speech classification, separate from phrase timing and recognition."""

from pathlib import Path

import numpy as np
import sherpa_onnx
import soxr


MODEL_PATH = Path(__file__).resolve().parent / "assets" / "silero_vad.onnx"
SAMPLE_RATE = 16000


class SpeechDetector:
    """Classify microphone chunks with a small, bundled Silero VAD model.

    The segmenter owns pause/minimum-speech timing. Keep the model's own
    smoothing short so it does not add another full pause before insertion.
    A fresh session resets both the recurrent model and the resampler.
    """

    def __init__(self, input_sample_rate: int) -> None:
        if not MODEL_PATH.is_file():
            raise FileNotFoundError(
                "Bundled speech detector is missing. Reinstall Sherpa: "
                f"{MODEL_PATH}"
            )
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(MODEL_PATH)
        config.silero_vad.threshold = 0.5
        config.silero_vad.window_size = 512
        config.silero_vad.min_speech_duration = 0.01
        config.silero_vad.min_silence_duration = 0.01
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        config.provider = "cpu"
        if not config.validate():
            raise ValueError("Speech detector configuration validation failed")
        self.model = sherpa_onnx.VadModel.create(config)
        self.window_size = self.model.window_size()
        self.resampler = (
            soxr.ResampleStream(input_sample_rate, SAMPLE_RATE, 1, quality="LQ")
            if input_sample_rate != SAMPLE_RATE
            else None
        )
        self.reset()

    def reset(self) -> None:
        self.model.reset()
        if self.resampler is not None:
            self.resampler.clear()
        self.pending = np.empty(0, dtype=np.float32)
        self.last_is_speech = False

    def is_speech(self, samples: np.ndarray) -> bool:
        samples = np.asarray(samples, dtype=np.float32)
        if self.resampler is not None:
            samples = self.resampler.resample_chunk(samples)
        self.pending = np.concatenate((self.pending, samples))
        processed = self.pending.size // self.window_size * self.window_size
        if not processed:
            return self.last_is_speech

        speech = False
        for offset in range(0, processed, self.window_size):
            # Always advance the model, including after a positive frame.
            detected = self.model.is_speech(
                self.pending[offset : offset + self.window_size]
            )
            speech = speech or detected
        self.pending = self.pending[processed:].copy()
        self.last_is_speech = speech
        return speech
