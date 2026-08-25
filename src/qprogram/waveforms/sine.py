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
"""Sinusoidal single-channel waveform."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Sine(Waveform):
    """Sinusoidal envelope ``amplitude · sin(2π·frequency·t + phase)``.

    Useful for parametric drives, sideband cooling tones, and as a building block for amplitude-modulated
    waveforms. The envelope does not taper to zero at the endpoints; pair with a window function
    (e.g. [`Tukey`][qprogram.waveforms.Tukey]) when continuous-wave artifacts matter.

    Args:
        amplitude (float | Expression): Peak amplitude.
        duration (int | Expression): Pulse duration in nanoseconds.
        frequency (float | Expression): Oscillation frequency in Hz.
        phase (float | Expression, optional): Phase offset in radians. Defaults to zero.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        frequency: float | Expression,
        phase: float | Expression = 0.0,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.frequency = frequency
        self.phase = phase

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the sine envelope sampled at ``resolution``-ns steps.

        The sample times are expressed in seconds, so ``frequency`` is read as a frequency in Hz.

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
        frequency = self.frequency.evaluate_or_raise() if isinstance(self.frequency, Expression) else self.frequency
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        n_samples = int(duration / resolution)
        t = np.arange(n_samples) * resolution * 1e-9
        return amplitude * np.sin(2 * np.pi * frequency * t + phase)

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
