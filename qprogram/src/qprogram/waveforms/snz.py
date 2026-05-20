from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class SuddenNetZero(Waveform):
    """Sudden Net Zero (SNZ) pulse for fast, leakage-suppressed two-qubit gates.

    The shape is a positive square segment, a zero hold of width ``t_phi``, then a negative square segment
    scaled by ``b``. The positive and negative areas cancel so the net integrated flux is zero.

    Args:
        amplitude: Amplitude of the positive segment. Accepts an :class:`~qprogram.Expression`.
        duration: Total pulse duration in nanoseconds. Accepts an :class:`~qprogram.Expression`.
        b: Ratio of negative-to-positive amplitudes (typically near 1.0). Accepts an
            :class:`~qprogram.Expression`.
        t_phi: Width of the zero hold between the two segments in nanoseconds. Accepts an
            :class:`~qprogram.Expression`.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        b: float | Expression,
        t_phi: int | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.b = b
        self.t_phi = t_phi

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        b = self.b.evaluate_or_raise() if isinstance(self.b, Expression) else self.b
        t_phi = self.t_phi.evaluate_or_raise() if isinstance(self.t_phi, Expression) else self.t_phi
        n_samples = int(duration / resolution)
        t_phi_samples = int(t_phi / resolution)
        half = (n_samples - t_phi_samples) // 2

        envelope = np.zeros(n_samples)
        envelope[:half] = amplitude
        envelope[half : half + t_phi_samples] = 0.0
        envelope[half + t_phi_samples :] = -amplitude * b
        return envelope

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
