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
"""Single-channel envelope lifted onto an IQ bus."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQZero(IQWaveform):
    """A real-valued [`Waveform`][qprogram.waveforms.Waveform] presented on an IQ bus with a silent Q channel.

    Convenience wrapper for ``IQPair(I=envelope, Q=Square(0.0, duration))``. Useful when a calibrated
    single-channel pulse has to drive an IQ-typed bus without rewriting the rest of the program.

    Args:
        envelope (Waveform): Single-channel [`Waveform`][qprogram.waveforms.Waveform] placed on the I channel.

    Raises:
        TypeError: If ``envelope`` is not a [`Waveform`][qprogram.waveforms.Waveform] instance.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("envelope",)

    def __init__(self, envelope: Waveform) -> None:
        if not isinstance(envelope, Waveform):
            msg = f"IQZero envelope must be a Waveform, got {type(envelope).__name__}"
            raise TypeError(msg)
        self.envelope = envelope

    def get_I(self) -> Waveform:
        """Return the in-phase channel.

        Returns:
            The wrapped envelope, unchanged.
        """
        return self.envelope

    def get_Q(self) -> Waveform:
        """Return the silent quadrature channel.

        Returns:
            An [`Arbitrary`][qprogram.waveforms.Arbitrary] waveform of zeros, one sample per nanosecond of the envelope's duration.

        Raises:
            UnassignedVariableError: If the envelope's duration is a symbolic expression whose variables
                are still unassigned.
        """
        return Arbitrary(np.zeros(self.envelope.get_duration()))

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration of the wrapped envelope in nanoseconds.

        Raises:
            UnassignedVariableError: If the envelope's duration is a symbolic expression whose variables
                are still unassigned.
        """
        return self.envelope.get_duration()
