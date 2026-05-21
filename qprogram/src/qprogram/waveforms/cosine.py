from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Cosine(Waveform):
    """Cosine envelope ``amplitude · cos(2π·frequency·t + phase)``.

    See :class:`Sine` for the analogous sine variant and the same caveats.

    Args:
        amplitude: Peak amplitude.
        duration: Pulse duration in nanoseconds.
        frequency: Oscillation frequency in Hz.
        phase: Phase offset in radians. Defaults to zero.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        frequency: float | Expression,
        phase: float | Expression = 0.0,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.frequency = frequency
        self.phase = phase

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        frequency = self.frequency.evaluate_or_raise() if isinstance(self.frequency, Expression) else self.frequency
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        n_samples = int(duration / resolution)
        t = np.arange(n_samples) * resolution * 1e-9
        return amplitude * np.cos(2 * np.pi * frequency * t + phase)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
