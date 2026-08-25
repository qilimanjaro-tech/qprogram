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
"""Linear ramp between two amplitudes."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Ramp(Waveform):
    """Linearly-interpolated ramp between two amplitudes.

    Args:
        from_amplitude (float | Expression): Starting amplitude. Accepts an [`Expression`][qprogram.Expression].
        to_amplitude (float | Expression): Ending amplitude. Accepts an [`Expression`][qprogram.Expression].
        duration (int | Expression): Ramp duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
    """

    def __init__(
        self,
        from_amplitude: float | Expression,
        to_amplitude: float | Expression,
        duration: int | Expression,
    ) -> None:
        self.from_amplitude = from_amplitude
        self.to_amplitude = to_amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the ramp sampled at ``resolution``-ns steps.

        Samples run linearly from ``from_amplitude`` to ``to_amplitude``, so the step between samples is
        set by the sample count rather than by ``resolution`` alone. Both endpoints are included once the
        window holds at least two samples: a one-sample window yields ``from_amplitude`` alone, and a
        window that holds less than one sample yields an empty array.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples running linearly from
            ``from_amplitude`` to ``to_amplitude``.

        Raises:
            UnassignedVariableError: If either amplitude or the duration is a symbolic expression whose
                variables are still unassigned.
            ValueError: If the duration is negative, which asks for a negative number of samples.
        """
        from_amplitude = (
            self.from_amplitude.evaluate_or_raise()
            if isinstance(self.from_amplitude, Expression)
            else self.from_amplitude
        )
        to_amplitude = (
            self.to_amplitude.evaluate_or_raise() if isinstance(self.to_amplitude, Expression) else self.to_amplitude
        )
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        n_samples = int(duration / resolution)
        return np.linspace(from_amplitude, to_amplitude, n_samples)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The ramp duration, truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are still
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
