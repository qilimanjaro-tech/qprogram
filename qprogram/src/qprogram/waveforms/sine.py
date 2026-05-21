from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Sine(Waveform):
    """Sinusoidal envelope ``amplitude · sin(2π·frequency·t + phase)``.

    Useful for parametric drives, sideband cooling tones, and as a building block for amplitude-modulated
    waveforms. The envelope does not taper to zero at the endpoints; pair with a window function
    (e.g. :class:`Tukey`) when continuous-wave artefacts matter.

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
        return amplitude * np.sin(2 * np.pi * frequency * t + phase)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
