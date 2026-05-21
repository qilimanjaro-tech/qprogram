from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Sech(Waveform):
    """Hyperbolic-secant envelope, centred at the midpoint of the duration window.

    The envelope is ``amplitude * sech((t - center) / tau)``. ``sech`` pulses are the canonical
    envelope for chirped adiabatic-passage gates: paired with a quadratic phase ramp they yield
    analytically-solvable population transfer.

    Args:
        amplitude: Peak amplitude (at the midpoint).
        duration: Pulse duration in nanoseconds.
        tau: Width parameter in nanoseconds — analogous to ``sigma`` on :class:`Gaussian`.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        tau: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.tau = tau

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        tau = self.tau.evaluate_or_raise() if isinstance(self.tau, Expression) else self.tau
        n_samples = int(duration / resolution)
        if n_samples == 0:
            return np.zeros(0)
        tau_samples = tau / resolution
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        return amplitude / np.cosh((t - center) / tau_samples)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
