from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Ramp(Waveform):
    """Linearly-interpolated ramp between two amplitudes.

    Args:
        from_amplitude: Starting amplitude. Accepts an :class:`~qprogram.Expression`.
        to_amplitude: Ending amplitude. Accepts an :class:`~qprogram.Expression`.
        duration: Ramp duration in nanoseconds. Accepts an :class:`~qprogram.Expression`.
    """

    def __init__(
        self,
        from_amplitude: float | Expression,
        to_amplitude: float | Expression,
        duration: int | Expression,
    ) -> None:
        self.from_amplitude = from_amplitude
        self.to_amplitude = to_amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        from_amplitude = (
            self.from_amplitude.evaluate_or_raise()
            if isinstance(self.from_amplitude, Expression)
            else self.from_amplitude
        )
        to_amplitude = (
            self.to_amplitude.evaluate_or_raise() if isinstance(self.to_amplitude, Expression) else self.to_amplitude
        )
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        n_samples = int(duration / resolution)
        return np.linspace(from_amplitude, to_amplitude, n_samples)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
