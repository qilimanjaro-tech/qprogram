from __future__ import annotations

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian
from qprogram.waveforms.gaussian_drag_correction import GaussianDragCorrection
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQDrag(IQWaveform):
    """DRAG (Derivative Removal by Adiabatic Gate) pulse.

    The I channel carries a :class:`Gaussian`; the Q channel carries the derivative correction
    (:class:`GaussianDragCorrection`) scaled by ``drag_coefficient``. Suppresses leakage to the |2⟩ state
    during single-qubit rotations on weakly-anharmonic transmons.

    Args:
        amplitude: Peak amplitude of the I-channel Gaussian. Accepts an :class:`~qprogram.Expression`.
        duration: Pulse duration in nanoseconds. Accepts an :class:`~qprogram.Expression`.
        num_sigmas: Window width of the I-channel Gaussian in standard deviations.
        drag_coefficient: Calibration parameter setting Q-channel weight. Typically a small (<0.5) value
            tuned per qubit.
    """

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
