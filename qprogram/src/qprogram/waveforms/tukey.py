from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Tukey(Waveform):
    """Tukey window: rectangular pulse with cosine-tapered edges.

    The ``alpha`` parameter controls the fraction of the pulse occupied by the rising and falling
    edges combined: ``alpha=0`` produces a pure rectangle, ``alpha=1`` produces a Hann window, and
    intermediate values yield a flat top of width ``(1 - alpha) * duration`` with cosine ramps on
    each side of width ``(alpha / 2) * duration``. Matches the ``alpha`` parameter of
    :func:`scipy.signal.windows.tukey`.

    Cheaper to compile than :class:`FlatTop` (no ``erf`` evaluation) and commonly used as a smoothing
    window in pulse calibration.

    Args:
        amplitude: Peak amplitude (achieved across the flat region).
        duration: Total pulse duration in nanoseconds.
        alpha: Fraction of the duration occupied by the combined rise + fall, in ``[0, 1]``.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        alpha: float | Expression = 0.5,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.alpha = alpha

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        alpha = self.alpha.evaluate_or_raise() if isinstance(self.alpha, Expression) else self.alpha

        n = int(duration / resolution)
        if n <= 1:
            return np.full(max(n, 0), amplitude)
        if alpha <= 0:
            return np.full(n, amplitude)
        if alpha >= 1:
            # Hann window
            t = np.arange(n)
            return amplitude * 0.5 * (1 - np.cos(2 * np.pi * t / (n - 1)))

        t = np.arange(n)
        half_alpha = alpha * (n - 1) / 2
        out = np.full(n, float(amplitude))
        rising = t < half_alpha
        falling = t > (n - 1) - half_alpha
        out[rising] = amplitude * 0.5 * (1 + np.cos(np.pi * (t[rising] / half_alpha - 1)))
        out[falling] = amplitude * 0.5 * (1 + np.cos(np.pi * ((t[falling] - (n - 1)) / half_alpha + 1)))
        return out

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
