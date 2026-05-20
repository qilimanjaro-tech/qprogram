from __future__ import annotations

import numpy as np

from qprogram.waveforms.waveform import Waveform


class Chained(Waveform):
    """Single-channel waveform built by concatenating other single-channel waveforms in time.

    Args:
        waveforms: Waveforms to play back-to-back, in order.
    """

    def __init__(self, waveforms: list[Waveform]) -> None:
        self.waveforms = waveforms

    def envelope(self, resolution: int = 1) -> np.ndarray:
        return np.concatenate([w.envelope(resolution) for w in self.waveforms])

    def get_duration(self) -> int:
        return sum(w.get_duration() for w in self.waveforms)
