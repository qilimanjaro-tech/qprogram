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

    The I channel carries a [`Gaussian`][qprogram.waveforms.Gaussian]; the Q channel carries the derivative correction
    ([`GaussianDragCorrection`][qprogram.waveforms.GaussianDragCorrection]) scaled by ``drag_coefficient``. Suppresses
    leakage to the |2⟩ state during single-qubit rotations on weakly-anharmonic transmons.

    The two channels are built on demand from the four stored parameters, so they cannot drift out of step
    with each other or with `get_duration`.

    Args:
        amplitude (float | Expression): Peak amplitude of the I-channel Gaussian, which a negative value
            does not reach (see [`Gaussian`][qprogram.waveforms.Gaussian]). Accepts an
            [`Expression`][qprogram.Expression].
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
        num_sigmas (float | Expression): Number of standard deviations the I-channel Gaussian spans.
            Accepts an [`Expression`][qprogram.Expression].
        drag_coefficient (float | Expression): Weight on the Q-channel derivative correction, tuned per
            qubit. Accepts an [`Expression`][qprogram.Expression].
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
        """Return the in-phase component as a single-channel [`Waveform`][qprogram.waveforms.Waveform].

        Returns:
            A fresh [`Gaussian`][qprogram.waveforms.Gaussian] carrying this pulse's amplitude, duration, and
            num_sigmas.
        """
        return Gaussian(amplitude=self.amplitude, duration=self.duration, num_sigmas=self.num_sigmas)

    def get_Q(self) -> Waveform:
        """Return the quadrature component as a single-channel [`Waveform`][qprogram.waveforms.Waveform].

        Returns:
            A fresh [`GaussianDragCorrection`][qprogram.waveforms.GaussianDragCorrection] carrying this pulse's parameters
            together with ``drag_coefficient``.
        """
        return GaussianDragCorrection(
            amplitude=self.amplitude,
            duration=self.duration,
            num_sigmas=self.num_sigmas,
            drag_coefficient=self.drag_coefficient,
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
