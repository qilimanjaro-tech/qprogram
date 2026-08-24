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
"""Intermediate-frequency modulation of a real envelope onto I and Q."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class Modulated(IQWaveform):
    """IQ pulse formed by intermediate-frequency modulation of a real envelope.

    Produces ``I = envelope · cos(2π·frequency·t + phase)`` and
    ``Q = envelope · sin(2π·frequency·t + phase)``, evaluated at 1-ns resolution. Use this to lift any
    single-channel envelope onto an IQ bus for sideband-modulated drive without writing an
    :class:`IQPair` by hand.

    The materialized I and Q channels are :class:`Arbitrary` waveforms — modulation collapses the
    enclosing envelope's parametric structure to concrete samples.

    Args:
        envelope (Waveform): Underlying single-channel :class:`Waveform` shaping the pulse.
        frequency (float | Expression): Modulation frequency in Hz.
        phase (float | Expression, optional): Phase offset in radians. Defaults to zero.

    Raises:
        TypeError: If ``envelope`` is not a :class:`Waveform` instance.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("envelope",)

    def __init__(
        self,
        envelope: Waveform,
        frequency: float | Expression,
        phase: float | Expression = 0.0,
    ) -> None:
        if not isinstance(envelope, Waveform):
            msg = f"Modulated envelope must be a Waveform, got {type(envelope).__name__}"
            raise TypeError(msg)
        self.envelope = envelope
        self.frequency = frequency
        self.phase = phase

    def _resolved_params(self) -> tuple[float, float]:
        """Return the modulation parameters as concrete floats.

        Returns:
            A ``(frequency, phase)`` pair, with either member evaluated when it is an
            :class:`~qprogram.Expression`.

        Raises:
            UnassignedVariableError: If ``frequency`` or ``phase`` is an expression whose variables are
                still unassigned.
        """
        frequency = self.frequency.evaluate_or_raise() if isinstance(self.frequency, Expression) else self.frequency
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        return float(frequency), float(phase)

    def get_I(self) -> Waveform:
        """Return the in-phase channel.

        Returns:
            An :class:`Arbitrary` waveform holding ``envelope · cos(2π·frequency·t + phase)``, with ``t``
            running over the envelope's samples in seconds at 1-ns steps.

        Raises:
            UnassignedVariableError: If ``frequency``, ``phase``, or any envelope parameter is a symbolic
                expression whose variables are still unassigned.
        """
        env = self.envelope.envelope()
        frequency, phase = self._resolved_params()
        t = np.arange(len(env)) * 1e-9
        return Arbitrary(env * np.cos(2 * np.pi * frequency * t + phase))

    def get_Q(self) -> Waveform:
        """Return the quadrature channel.

        Returns:
            An :class:`Arbitrary` waveform holding ``envelope · sin(2π·frequency·t + phase)``, with ``t``
            running over the envelope's samples in seconds at 1-ns steps.

        Raises:
            UnassignedVariableError: If ``frequency``, ``phase``, or any envelope parameter is a symbolic
                expression whose variables are still unassigned.
        """
        env = self.envelope.envelope()
        frequency, phase = self._resolved_params()
        t = np.arange(len(env)) * 1e-9
        return Arbitrary(env * np.sin(2 * np.pi * frequency * t + phase))

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Modulation is sample-wise, so the length is the underlying envelope's.

        Returns:
            The duration of the underlying envelope in nanoseconds.

        Raises:
            UnassignedVariableError: If the envelope's duration is a symbolic expression whose variables
                are still unassigned.
        """
        return self.envelope.get_duration()
