from __future__ import annotations

import numpy as np
from collections.abc import Sequence

from qprogram.waveforms.waveform import Waveform


class Arbitrary(Waveform):
    """User-provided sample array."""

    def __init__(self, samples: Sequence[float] | np.ndarray) -> None:
        self.samples = np.asarray(samples)

    def envelope(self, resolution: int = 1) -> np.ndarray:  # noqa: ARG002
        return self.samples.copy()

    def get_duration(self) -> int:
        return len(self.samples)
