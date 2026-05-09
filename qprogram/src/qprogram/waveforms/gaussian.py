from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Gaussian(Waveform):
    """Gaussian-shaped pulse."""

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        num_sigmas: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.num_sigmas = num_sigmas

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        num_sigmas = self.num_sigmas.evaluate_or_raise() if isinstance(self.num_sigmas, Expression) else self.num_sigmas
        n_samples = int(duration / resolution)
        sigma = n_samples / (2 * num_sigmas)
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        return amplitude * np.exp(-0.5 * ((t - center) / sigma) ** 2)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
