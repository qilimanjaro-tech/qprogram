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

from math import erf

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class FlatTop(Waveform):
    """Rectangular pulse with erf-smoothed rising and falling edges.

    Useful for fast-acting flat pulses where instantaneous square edges would inject high-frequency content
    outside the control electronics' bandwidth.

    Each edge is an error function of width ``smooth_duration / 3``: the rise crosses half amplitude
    ``smooth_duration`` ns into the pulse and is flat to within a part in 10⁵ by twice that, with the fall
    mirrored about the pulse center. A ``duration`` that is not comfortably longer than
    ``2 * smooth_duration`` therefore never reaches full amplitude.

    Args:
        amplitude (float | Expression): Pulse amplitude. Accepts an :class:`~qprogram.Expression`.
        duration (int | Expression): Pulse duration in nanoseconds, including the smoothed edges but
            excluding the ``buffer`` padding. Accepts an :class:`~qprogram.Expression`.
        smooth_duration (int | Expression): Length of each edge (rise and fall) in nanoseconds. Accepts an
            :class:`~qprogram.Expression`.
        buffer (int, optional): Zero-amplitude padding added on **each** side of the pulse, in nanoseconds.
            The total duration is ``duration + 2 * buffer``.
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

        The rising and falling error functions are multiplied together rather than spliced, which keeps the
        envelope smooth even when the two edges overlap. Padding is emitted as ``buffer / resolution`` zero
        samples on each side.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``(duration + 2 * buffer) / resolution`` samples.

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
        n_samples = int(duration / resolution)
        smooth = int(smooth_duration / resolution)
        t = np.arange(n_samples)
        rise = 0.5 * (1 + np.vectorize(erf)((t - smooth) / (smooth / 3)))
        fall = 0.5 * (1 + np.vectorize(erf)((n_samples - 1 - smooth - t) / (smooth / 3)))
        pulse = amplitude * rise * fall
        if self.buffer:
            pad = np.zeros(int(self.buffer / resolution))
            return np.concatenate([pad, pulse, pad])
        return pulse

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds, padding included.

        Returns:
            ``duration + 2 * buffer``, with ``duration`` truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration) + 2 * self.buffer
