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
"""In-plane rotation of an existing IQ pulse."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQRotation(IQWaveform):
    """An existing :class:`IQWaveform` rotated in the I/Q plane by ``phase`` radians.

    Applies the 2x2 rotation::

        I_out = I * cos(phase) - Q * sin(phase)
        Q_out = I * sin(phase) + Q * cos(phase)

    Useful for virtual-Z gates and for applying a software-side phase offset to a calibrated pulse
    without resampling the envelope. Materializes both channels as :class:`Arbitrary` waveforms; for
    purely-symbolic rotation, prefer carrying the phase through the underlying envelope's parameters.

    Args:
        base (IQWaveform): The :class:`IQWaveform` to rotate.
        phase (float | Expression): Rotation angle in radians.

    Raises:
        TypeError: If ``base`` is not an :class:`IQWaveform` instance.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("base",)

    def __init__(self, base: IQWaveform, phase: float | Expression) -> None:
        if not isinstance(base, IQWaveform):
            msg = f"IQRotation base must be an IQWaveform, got {type(base).__name__}"
            raise TypeError(msg)
        self.base = base
        self.phase = phase

    def _resolved_phase(self) -> float:
        """Return the rotation angle as a concrete float.

        Returns:
            The value of ``phase``, evaluated when it is an :class:`~qprogram.Expression`.

        Raises:
            UnassignedVariableError: If ``phase`` is an expression whose variables are still unassigned.
        """
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        return float(phase)

    def get_I(self) -> Waveform:
        """Return the rotated in-phase channel.

        Returns:
            An :class:`Arbitrary` waveform holding ``I·cos(phase) - Q·sin(phase)``, sampled from the base
            channels at 1-ns steps.

        Raises:
            UnassignedVariableError: If ``phase`` or any parameter of the base channels is a symbolic
                expression whose variables are still unassigned.
        """
        phase = self._resolved_phase()
        i_env = self.base.get_I().envelope()
        q_env = self.base.get_Q().envelope()
        return Arbitrary(i_env * np.cos(phase) - q_env * np.sin(phase))

    def get_Q(self) -> Waveform:
        """Return the rotated quadrature channel.

        Returns:
            An :class:`Arbitrary` waveform holding ``I·sin(phase) + Q·cos(phase)``, sampled from the base
            channels at 1-ns steps.

        Raises:
            UnassignedVariableError: If ``phase`` or any parameter of the base channels is a symbolic
                expression whose variables are still unassigned.
        """
        phase = self._resolved_phase()
        i_env = self.base.get_I().envelope()
        q_env = self.base.get_Q().envelope()
        return Arbitrary(i_env * np.sin(phase) + q_env * np.cos(phase))

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        A rotation mixes the two channels sample by sample, so the length is the base waveform's.

        Returns:
            The duration of the base waveform in nanoseconds.

        Raises:
            UnassignedVariableError: If the base waveform's duration is a symbolic expression whose
                variables are still unassigned.
        """
        return self.base.get_duration()
