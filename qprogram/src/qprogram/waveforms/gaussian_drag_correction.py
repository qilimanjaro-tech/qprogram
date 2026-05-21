from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian


class GaussianDragCorrection(Gaussian):
    """Derivative-of-Gaussian envelope used as the Q-channel of a DRAG pulse.

    On its own, this waveform is rarely emitted directly; it is the Q-channel partner produced by
    :class:`~qprogram.waveforms.IQDrag.get_Q`.

    Args:
        amplitude: Peak amplitude of the underlying Gaussian. Accepts an :class:`~qprogram.Expression`.
        duration: Pulse duration in nanoseconds. Accepts an :class:`~qprogram.Expression`.
        sigma: Standard deviation of the underlying Gaussian in nanoseconds.
        beta: DRAG scaling (``β`` in the Motzoi et al. parameterisation). Multiplicative weight on the
            derivative term.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        sigma: float | Expression,
        beta: float | Expression,
    ) -> None:
        super().__init__(amplitude=amplitude, duration=duration, sigma=sigma)
        self.beta = beta

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        sigma = self.sigma.evaluate_or_raise() if isinstance(self.sigma, Expression) else self.sigma
        beta = self.beta.evaluate_or_raise() if isinstance(self.beta, Expression) else self.beta
        n_samples = int(duration / resolution)
        sigma_samples = sigma / resolution
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        gaussian = amplitude * np.exp(-0.5 * ((t - center) / sigma_samples) ** 2)
        return beta * -(t - center) / (sigma_samples**2) * gaussian
