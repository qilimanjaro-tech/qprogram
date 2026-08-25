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
"""Rectangular single-channel waveform."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Square(Waveform):
    """Constant-amplitude rectangular pulse.

    Args:
        amplitude (float | Expression): Pulse amplitude. Accepts an [`Expression`][qprogram.Expression] to be
            swept by an enclosing loop.
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
    """

    def __init__(self, amplitude: float | Expression, duration: int | Expression) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the rectangular envelope sampled at ``resolution``-ns steps.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D array of length ``duration / resolution``, every sample at ``amplitude``. The dtype
            follows ``amplitude``, so an integer amplitude yields an integer array.

        Raises:
            UnassignedVariableError: If a parameter is an [`Expression`][qprogram.Expression] whose variables
                have no value.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return np.full(int(duration / resolution), amplitude)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is an [`Expression`][qprogram.Expression] whose variables
                have no value.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
