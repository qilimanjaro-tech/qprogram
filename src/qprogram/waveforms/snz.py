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
"""Sudden Net Zero flux waveform."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class SuddenNetZero(Waveform):
    """Sudden Net Zero (SNZ) pulse for fast, leakage-suppressed two-qubit gates.

    The shape is a positive square segment, a zero hold of width ``t_phi``, then a negative square segment
    scaled by ``b``. The two segments are meant to cancel, leaving zero net integrated flux: the
    cancellation is exact when ``b`` is 1 and the samples left over after the hold divide evenly between
    the segments, and ``b`` is detuned from 1 to null whatever residual the flux line adds.

    Args:
        amplitude (float | Expression): Amplitude of the positive segment. Accepts an
            [`Expression`][qprogram.Expression].
        duration (int | Expression): Total pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
        b (float | Expression): Ratio of negative-to-positive amplitudes (typically near 1.0). Accepts an
            [`Expression`][qprogram.Expression].
        t_phi (int | Expression): Width of the zero hold between the two segments in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        b: float | Expression,
        t_phi: int | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.b = b
        self.t_phi = t_phi

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the SNZ envelope sampled at ``resolution``-ns steps.

        The two square segments split what is left of the pulse once the zero hold is taken out, rounding
        the positive segment down — so the negative segment carries the extra sample when that remainder is
        an odd number of samples.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D float array of length ``duration / resolution``.

        Raises:
            UnassignedVariableError: If a parameter is an [`Expression`][qprogram.Expression] whose variables
                have no value.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        b = self.b.evaluate_or_raise() if isinstance(self.b, Expression) else self.b
        t_phi = self.t_phi.evaluate_or_raise() if isinstance(self.t_phi, Expression) else self.t_phi
        n_samples = int(duration / resolution)
        t_phi_samples = int(t_phi / resolution)
        half = (n_samples - t_phi_samples) // 2

        envelope = np.zeros(n_samples)
        envelope[:half] = amplitude
        envelope[half : half + t_phi_samples] = 0.0
        envelope[half + t_phi_samples :] = -amplitude * b
        return envelope

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
