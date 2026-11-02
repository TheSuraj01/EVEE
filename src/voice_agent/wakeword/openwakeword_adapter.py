"""openWakeWord behind the WakeWordDetector protocol.

ONNX inference: there is no tflite runtime for Windows. The model loads lazily, and
pipeline frames (30 ms) are regrouped into the model's native 80 ms int16 chunks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: 80 ms at 16 kHz — openWakeWord's native step.
CHUNK_SAMPLES = 1280


class OpenWakeWordDetector:
    def __init__(
        self,
        model_name: str,
        threshold: float,
        load: Callable[[str], Any] | None = None,
    ) -> None:
        self._name = model_name
        self._threshold = threshold
        self._load = load or load_openwakeword
        self._model: Any = None
        self._pending = np.empty(0, dtype=np.int16)

    def warm_up(self) -> None:
        """Load the model now, so the first wake word isn't delayed by it."""
        self._get_model()

    def process(self, frame: np.ndarray) -> bool:
        pcm = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16)
        self._pending = np.concatenate((self._pending, pcm))
        fired = False
        while self._pending.size >= CHUNK_SAMPLES:
            chunk = self._pending[:CHUNK_SAMPLES]
            self._pending = self._pending[CHUNK_SAMPLES:]
            scores = self._get_model().predict(chunk)
            fired = fired or max(scores.values(), default=0.0) >= self._threshold
        return fired

    def reset(self) -> None:
        """Forget buffered audio and the model's rolling state, so the tail of the last
        command can't re-trigger it."""
        self._pending = np.empty(0, dtype=np.int16)
        if self._model is not None:
            self._model.reset()

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = self._load(self._name)
        return self._model


def missing_model_files(model_name: str) -> list[str]:  # pragma: no cover - needs the lib
    """The ONNX files `model_name` needs that are not on disk yet (wake model + features)."""
    import openwakeword

    pretrained = map(Path, openwakeword.get_pretrained_model_paths("onnx"))
    wake = [path for path in pretrained if model_name in path.name]
    features = [
        Path(m["model_path"]).with_suffix(".onnx") for m in openwakeword.FEATURE_MODELS.values()
    ]
    return [str(path) for path in wake + features if not path.exists()]


def load_openwakeword(model_name: str) -> Any:  # pragma: no cover - needs the model files
    """Download only when files are missing (first run); any other load error surfaces as is.
    An unknown model name finds no files to fetch, and Model() then says so clearly."""
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    missing = missing_model_files(model_name)
    if missing:
        log.info("wake word files missing (%d); downloading them once", len(missing))
        download_models([model_name])
    return Model(wakeword_models=[model_name], inference_framework="onnx")
