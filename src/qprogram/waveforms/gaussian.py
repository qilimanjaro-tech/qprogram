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
"""Gaussian pulse envelope."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Gaussian(Waveform):
    """Gaussian-shaped pulse, peaked at the midpoint of the duration window.

    The envelope is not truncation-corrected: the tails are clipped wherever the window ends, so the
    first and last samples sit at whatever amplitude the Gaussian reaches there rather than at zero.

    Args:
        amplitude (float | Expression): Peak amplitude. Accepts an :class:`~qprogram.Expression`.
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            :class:`~qprogram.Expression`.
        sigma (float | Expression): Standard deviation in nanoseconds. The truncation ratio
            ``duration / sigma`` controls how steeply the tails are clipped at the window edges. Accepts an
            :class:`~qprogram.Expression`.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        sigma: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.sigma = sigma

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the Gaussian envelope sampled at ``resolution``-ns steps.

        The peak sits at the center of the sample window, so an even sample count straddles it between
        the two middle samples and the largest sample falls a little below ``amplitude``. ``sigma`` is
        converted to samples, which keeps the shape the same at any resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, or ``sigma`` is a symbolic expression
                whose variables are unassigned.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        sigma = self.sigma.evaluate_or_raise() if isinstance(self.sigma, Expression) else self.sigma
        n_samples = int(duration / resolution)
        sigma_samples = sigma / resolution
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        return amplitude * np.exp(-0.5 * ((t - center) / sigma_samples) ** 2)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration, truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
