# Speech detector fixtures

These synthetic voices were generated locally with Sherpa-ONNX 1.13.4 and
KittenTTS nano en v0.8 int8, speaker 2, speed 1.0. No microphone audio was used.
Both files are mono, 16 kHz, signed 16-bit PCM WAV, resampled using Python-SoXR.

- `vad-speech.wav`: “This is a speech detection test.”
- `vad-command.wav`: “Comma.”

The fixtures check that noise rejection still admits speech and short spoken
punctuation commands without downloading or synthesizing audio during tests.
