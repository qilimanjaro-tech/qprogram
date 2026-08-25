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
"""Waveform concatenation — single-channel envelopes played back-to-back."""

from __future__ import annotations

import numpy as np

from qprogram.waveforms.waveform import Waveform


class Chained(Waveform):
    """Single-channel waveform built by concatenating other single-channel waveforms in time.

    `Waveform.__add__` builds these and flattens as it goes, so ``a + b + c`` yields one
    three-element chain rather than nested pairs. Every child is sampled at whatever resolution the
    chain is asked for, and the chain's duration is the sum of the children's.

    Args:
        waveforms (list[Waveform]): Waveforms to play back-to-back, in order.
    """

    def __init__(self, waveforms: list[Waveform]) -> None:
        self.waveforms = waveforms

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the children's envelopes concatenated in order.

        Args:
            resolution (int, optional): Sample period in nanoseconds, passed through to every child.

        Returns:
            A 1-D array holding each child's envelope in turn.

        Raises:
            ValueError: If the chain holds no waveforms, leaving nothing to concatenate.
            UnassignedVariableError: If a child's envelope depends on a variable that has no value.
        """
        return np.concatenate([w.envelope(resolution) for w in self.waveforms])

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The sum of the children's durations, or zero for an empty chain.

        Raises:
            UnassignedVariableError: If a child's duration is a symbolic expression whose variables are
                unassigned.
        """
        return sum(w.get_duration() for w in self.waveforms)
