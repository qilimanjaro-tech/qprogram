from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Square(Waveform):
    """Constant-amplitude rectangular pulse.

    Args:
        amplitude: Pulse amplitude. Accepts an :class:`~qprogram.Expression` to be swept by an enclosing loop.
        duration: Pulse duration in nanoseconds. Accepts an :class:`~qprogram.Expression`.
    """

    def __init__(self, amplitude: float | Expression, duration: int | Expression) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return np.full(int(duration / resolution), amplitude)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
