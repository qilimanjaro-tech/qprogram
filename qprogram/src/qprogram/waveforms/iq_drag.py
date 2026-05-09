from __future__ import annotations

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian
from qprogram.waveforms.gaussian_drag_correction import GaussianDragCorrection
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQDrag(IQWaveform):
    """DRAG pulse: Gaussian I + GaussianDragCorrection Q."""

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        num_sigmas: float | Expression,
        drag_coefficient: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.num_sigmas = num_sigmas
        self.drag_coefficient = drag_coefficient

    def get_I(self) -> Waveform:
        return Gaussian(amplitude=self.amplitude, duration=self.duration, num_sigmas=self.num_sigmas)

    def get_Q(self) -> Waveform:
        return GaussianDragCorrection(
            amplitude=self.amplitude,
            duration=self.duration,
            num_sigmas=self.num_sigmas,
            drag_coefficient=self.drag_coefficient,
        )

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
