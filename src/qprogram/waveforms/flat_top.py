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
"""Flat-topped pulse with error-function edges."""

from __future__ import annotations

import math

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class FlatTop(Waveform):
    """Rectangular pulse with erf-smoothed rising and falling edges.

    Useful for fast-acting flat pulses where instantaneous square edges would inject high-frequency content
    outside the control electronics' bandwidth.

    The envelope is the sum of a rising and a falling error function, each of width ``smooth_duration / 4``:

    ``amplitude / 2 * (erf((t + 1/2 - buffer - smooth_duration / 2) / (smooth_duration / 4))
    + erf((duration - 1/2 - t - buffer - smooth_duration / 2) / (smooth_duration / 4)))``

    with ``t`` the sample time in nanoseconds, so each sample is read half a nanosecond after it starts.
    On that ``t + 1/2`` scale, the rise is at 0.2% of ``amplitude`` at ``buffer``, crosses half amplitude
    at ``buffer + smooth_duration / 2``, and reaches 99.8% at ``buffer + smooth_duration``. The fall
    mirrors it at the other end, so the flat top spans ``duration - 2 * (buffer + smooth_duration)`` ns,
    and a ``duration`` shorter than ``2 * (buffer + smooth_duration)`` never reaches full amplitude.

    Two consequences of that formula are kept on purpose. Nothing checks that the edges fit: once
    ``duration - 2 * buffer`` is shorter than ``smooth_duration``, the two error functions sum to less
    than zero and the whole pulse has the opposite sign to ``amplitude``, and when the two are equal the
    pulse is zero. And the pulse only starts and ends at 0 when ``buffer`` leaves room before the rise:
    with ``buffer=0`` the first and last samples are 0.55% of ``amplitude`` for ``smooth_duration=10``,
    and more for shorter edges (8% for ``smooth_duration=2``).

    Args:
        amplitude (float | Expression): Pulse amplitude. Accepts an [`Expression`][qprogram.Expression].
        duration (int | Expression): Pulse duration in nanoseconds, edges and ``buffer`` included. Accepts an
            [`Expression`][qprogram.Expression].
        smooth_duration (int | Expression): Time each edge takes to go from 0.2% to 99.8% of ``amplitude``, in
            nanoseconds. Accepts an [`Expression`][qprogram.Expression].
        buffer (int, optional): Time before the rise starts and after the fall ends, on **each** side, in
            nanoseconds. It sits inside ``duration``: it moves the edges inwards and leaves the duration as
            it is.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        smooth_duration: int | Expression,
        buffer: int = 0,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.smooth_duration = smooth_duration
        self.buffer = buffer

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the smoothed rectangular envelope sampled at ``resolution``-ns steps.

        The edges are placed in nanoseconds, so the shape is the same at any resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples, rounded up.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, or ``smooth_duration`` is a symbolic
                expression whose variables are unassigned.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        smooth_duration = (
            self.smooth_duration.evaluate_or_raise()
            if isinstance(self.smooth_duration, Expression)
            else self.smooth_duration
        )
        erf = np.vectorize(math.erf, otypes=[float])
        x = np.arange(-duration / 2 + 0.5, duration / 2 + 0.5, resolution)
        half = duration / 2 - self.buffer
        return 0.5 * amplitude * (erf(4 * (x + half) / smooth_duration - 2) - erf(4 * (x - half) / smooth_duration + 2))

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration, truncated to a whole number of nanoseconds. ``buffer`` is inside it.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
