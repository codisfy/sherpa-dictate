"""Curated model catalog used by the desktop model manager."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ASR_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    kind: str
    summary: str
    size: str
    url: str
    required_paths: tuple[str, ...]
    backend: str = ""
    language: str = ""

    @property
    def archive_name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


MODELS = (
    ModelSpec(
        id="parakeet",
        name="Parakeet TDT v3",
        kind="asr",
        summary="Best general-purpose accuracy; multilingual, phrase based.",
        size="~640 MB",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
        ),
        required_paths=ASR_FILES,
        backend="offline",
        language="Multilingual",
    ),
    ModelSpec(
        id="parakeet-en",
        name="Parakeet Unified",
        kind="asr",
        summary="Accurate English-only dictation, finalized after each pause.",
        size="~633 MB",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming.tar.bz2"
        ),
        required_paths=ASR_FILES,
        backend="offline",
        language="English",
    ),
    ModelSpec(
        id="nemotron",
        name="Nemotron 3.5 Streaming",
        kind="asr",
        summary="Low-latency streaming dictation with automatic language detection.",
        size="~653 MB",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"
        ),
        required_paths=ASR_FILES,
        backend="online",
        language="Multilingual",
    ),
    ModelSpec(
        id="nemotron-en",
        name="Nemotron Streaming",
        kind="asr",
        summary="Low-latency English-only streaming dictation.",
        size="~633 MB",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2"
        ),
        required_paths=ASR_FILES,
        backend="online",
        language="English",
    ),
    ModelSpec(
        id="kitten-tts",
        name="KittenTTS Nano",
        kind="tts",
        summary="Small local English text-to-speech model with eight voices.",
        size="~45 MB",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
            "kitten-nano-en-v0_8-int8.tar.bz2"
        ),
        required_paths=(
            "model.int8.onnx",
            "voices.bin",
            "tokens.txt",
            "espeak-ng-data",
        ),
        language="English",
    ),
)

MODEL_BY_ID = {model.id: model for model in MODELS}


def legacy_model_path(model_id: str) -> Path:
    if model_id == "kitten-tts":
        return (
            Path.home()
            / ".cache/sherpa-onnx/tts/kitten-nano-en-v0_8-int8"
        )
    directory_names = {
        "parakeet": "parakeet-tdt-0.6b-v3",
        "parakeet-en": "parakeet-unified-en-0.6b",
        "nemotron": "nemotron-3.5-asr-streaming-0.6b",
        "nemotron-en": "nemotron-speech-streaming-en-0.6b",
    }
    return Path.home() / ".cache/openwhispr/parakeet-models" / directory_names[model_id]
