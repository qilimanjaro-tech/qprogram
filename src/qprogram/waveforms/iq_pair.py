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
"""IQ pulse assembled from two single-channel envelopes."""

from __future__ import annotations

from qprogram.errors import UnassignedVariableError, ValidationError
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQPair(IQWaveform):
    """An IQ waveform assembled from two independent single-channel envelopes.

    Useful when the desired I and Q envelopes don't fit one of the named DRAG-style classes — for example,
    a square-on-I, zero-on-Q readout pulse.

    Args:
        I (Waveform): In-phase channel.
        Q (Waveform): Quadrature channel. Must have the same duration as ``I``.

    Raises:
        TypeError: If either argument is not a :class:`Waveform` instance.
        ValidationError: If the two channels have different (concretely-known) durations. The
            check is best-effort — symbolic durations whose variables are still unassigned are
            accepted here and left to the platform compiler to verify once values are bound.
    """

    def __init__(self, I: Waveform, Q: Waveform) -> None:
        if not isinstance(I, Waveform) or not isinstance(Q, Waveform):
            msg = "I and Q must be Waveform instances"
            raise TypeError(msg)
        try:
            i_duration, q_duration = I.get_duration(), Q.get_duration()
        except UnassignedVariableError:
            i_duration = q_duration = None  # symbolic durations — defer the check
        if i_duration != q_duration:
            msg = f"IQPair channels must have equal durations; got I={i_duration} ns, Q={q_duration} ns"
            raise ValidationError(msg)
        self.I = I
        self.Q = Q

    def get_I(self) -> Waveform:
        """Return the in-phase channel.

        Returns:
            The waveform given as ``I``, unchanged.
        """
        return self.I

    def get_Q(self) -> Waveform:
        """Return the quadrature channel.

        Returns:
            The waveform given as ``Q``, unchanged.
        """
        return self.Q

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Both channels are required to share a duration, so the I channel answers for the pair.

        Returns:
            The duration of the I channel in nanoseconds.

        Raises:
            UnassignedVariableError: If the I channel's duration is a symbolic expression whose
                variables are still unassigned.
        """
        return self.I.get_duration()
