# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""DRAG pulse — a Gaussian on I, its derivative correction on Q."""

from __future__ import annotations

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian
from qprogram.waveforms.gaussian_drag_correction import GaussianDragCorrection
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQDrag(IQWaveform):
    """DRAG (Derivative Removal by Adiabatic Gate) pulse.

    The I channel carries a :class:`Gaussian`; the Q channel carries the derivative correction
    (:class:`GaussianDragCorrection`) scaled by ``beta``. Suppresses leakage to the |2⟩ state during
    single-qubit rotations on weakly-anharmonic transmons.

    The two channels are built on demand from the four stored parameters, so they cannot drift out of step
    with each other or with :meth:`get_duration`.

    Args:
        amplitude (float | Expression): Peak amplitude of the I-channel Gaussian. Accepts an
            :class:`~qprogram.Expression`.
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            :class:`~qprogram.Expression`.
        sigma (float | Expression): Standard deviation of the I-channel Gaussian in nanoseconds.
        beta (float | Expression): DRAG scaling (``β`` in the Motzoi et al. parameterization). Typically a
            small (< 0.5) value tuned per qubit.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        sigma: float | Expression,
        beta: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.sigma = sigma
        self.beta = beta

    def get_I(self) -> Waveform:
        """Return the in-phase component as a single-channel :class:`Waveform`.

        Returns:
            A fresh :class:`~qprogram.waveforms.Gaussian` carrying this pulse's amplitude, duration, and
            sigma.
        """
        return Gaussian(amplitude=self.amplitude, duration=self.duration, sigma=self.sigma)

    def get_Q(self) -> Waveform:
        """Return the quadrature component as a single-channel :class:`Waveform`.

        Returns:
            A fresh :class:`~qprogram.waveforms.GaussianDragCorrection` carrying this pulse's parameters
            together with ``beta``.
        """
        return GaussianDragCorrection(
            amplitude=self.amplitude,
            duration=self.duration,
            sigma=self.sigma,
            beta=self.beta,
        )

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration, truncated to a whole number of nanoseconds. Both channels span it.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
