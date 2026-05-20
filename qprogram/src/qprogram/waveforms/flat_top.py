from __future__ import annotations

from math import erf

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class FlatTop(Waveform):
    """Rectangular pulse with erf-smoothed rising and falling edges.

    Useful for fast-acting flat pulses where instantaneous square edges would inject high-frequency content
    outside the control electronics' bandwidth.

    Args:
        amplitude: Pulse amplitude. Accepts an :class:`~qprogram.Expression`.
        duration: Total pulse duration in nanoseconds (including the smoothed edges). Accepts an
            :class:`~qprogram.Expression`.
        smooth_duration: Length of each edge (rise and fall) in nanoseconds. Accepts an
            :class:`~qprogram.Expression`.
        buffer: Optional padding added on both sides of the pulse in nanoseconds.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        smooth_duration: int | Expression,
        buffer: int = 0,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.smooth_duration = smooth_duration
        self.buffer = buffer

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        smooth_duration = (
            self.smooth_duration.evaluate_or_raise()
            if isinstance(self.smooth_duration, Expression)
            else self.smooth_duration
        )
        n_samples = int(duration / resolution)
        smooth = int(smooth_duration / resolution)
        t = np.arange(n_samples)
        rise = 0.5 * (1 + np.vectorize(erf)((t - smooth) / (smooth / 3)))
        fall = 0.5 * (1 + np.vectorize(erf)((n_samples - 1 - smooth - t) / (smooth / 3)))
        return amplitude * rise * fall

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
