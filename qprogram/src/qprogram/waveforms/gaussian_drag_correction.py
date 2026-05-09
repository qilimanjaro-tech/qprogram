from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian


class GaussianDragCorrection(Gaussian):
    """Derivative of Gaussian (DRAG Q component)."""

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        num_sigmas: float | Expression,
        drag_coefficient: float | Expression,
    ) -> None:
        super().__init__(amplitude=amplitude, duration=duration, num_sigmas=num_sigmas)
        self.drag_coefficient = drag_coefficient

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        num_sigmas = self.num_sigmas.evaluate_or_raise() if isinstance(self.num_sigmas, Expression) else self.num_sigmas
        drag_coefficient = (
            self.drag_coefficient.evaluate_or_raise()
            if isinstance(self.drag_coefficient, Expression)
            else self.drag_coefficient
        )
        n_samples = int(duration / resolution)
        sigma = n_samples / (2 * num_sigmas)
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        gaussian = amplitude * np.exp(-0.5 * ((t - center) / sigma) ** 2)
        return drag_coefficient * -(t - center) / (sigma**2) * gaussian
