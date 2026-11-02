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
    """Gaussian pulse spanning ``num_sigmas`` standard deviations, shifted to start at zero.

    The curve is centred on ``duration / 2`` with standard deviation ``duration / num_sigmas``, so
    ``num_sigmas`` sets the width relative to the pulse rather than in nanoseconds. It is then shifted
    down by its first sample, so the pulse starts at exactly 0, and rescaled so its largest sample
    returns to what it was before the shift: ``amplitude`` when a sample falls on the centre, which at
    resolution 1 means an even duration, and a little under it when the centre falls between two samples.

    Two consequences of that shape are kept on purpose. The centre is ``duration / 2`` rather than the
    middle sample, so the pulse is not symmetric: the first sample is 0 and the last is not. And for an
    ``amplitude`` of 0 or below, the shifted curve's largest sample is its first, which is 0, so the
    rescale is skipped: a negative pulse peaks at ``amplitude * (1 - exp(-num_sigmas**2 / 8))`` rather
    than at ``amplitude``, about 0.86 of it for ``num_sigmas=4``.

    Args:
        amplitude (float | Expression): Height of the peak, reached exactly only for a positive value with
            a sample on the centre (see above). Accepts an [`Expression`][qprogram.Expression].
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
        num_sigmas (float | Expression): Number of standard deviations the pulse spans, so the
            standard deviation is ``duration / num_sigmas``. Larger values give a narrower pulse. Has to
            be non-zero: ``0`` raises `ZeroDivisionError`, and a numpy zero, which is what a sweep binds,
            gives a divide-by-zero `RuntimeWarning` and an all-zero pulse. Accepts an
            [`Expression`][qprogram.Expression].
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        num_sigmas: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.num_sigmas = num_sigmas

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the Gaussian envelope sampled at ``resolution``-ns steps.

        Sample ``k`` is taken at ``k * resolution`` ns, and the centre and width are in nanoseconds, so
        the shape is the same at any resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples, rounded up.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, or ``num_sigmas`` is a symbolic
                expression whose variables are unassigned.
            ZeroDivisionError: If ``num_sigmas`` is a Python ``0`` or ``0.0``.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        num_sigmas = self.num_sigmas.evaluate_or_raise() if isinstance(self.num_sigmas, Expression) else self.num_sigmas
        sigma = duration / num_sigmas
        mu = duration / 2
        x = np.arange(duration / resolution) * resolution
        gaussian = amplitude * np.exp(-0.5 * (x - mu) ** 2 / sigma**2)
        norm = np.amax(gaussian)
        # Shifted to start at 0, so the pulse does not open on a step.
        gaussian -= gaussian[0]
        corr_norm = np.amax(gaussian)
        return gaussian * norm / corr_norm if corr_norm != 0 else gaussian

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
