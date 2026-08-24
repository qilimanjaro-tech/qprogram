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
"""Hyperbolic-secant pulse envelope."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Sech(Waveform):
    """Hyperbolic-secant envelope, centered at the midpoint of the duration window.

    The envelope is ``amplitude * sech((t - center) / tau)``. ``sech`` pulses are the canonical
    envelope for chirped adiabatic-passage gates: paired with a quadratic phase ramp they yield
    analytically-solvable population transfer.

    Args:
        amplitude (float | Expression): Peak amplitude (at the midpoint).
        duration (int | Expression): Pulse duration in nanoseconds.
        tau (float | Expression): Width parameter in nanoseconds — analogous to ``sigma`` on
            :class:`Gaussian`.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        tau: float | Expression,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.tau = tau

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the pulse envelope sampled at ``resolution``-ns steps.

        The peak sits at the center of the sample window, and ``tau`` is scaled into samples so the shape
        is independent of ``resolution``. A window that holds less than one sample yields an empty array.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, or ``tau`` is a symbolic expression
                whose variables are still unassigned.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        tau = self.tau.evaluate_or_raise() if isinstance(self.tau, Expression) else self.tau
        n_samples = int(duration / resolution)
        if n_samples == 0:
            return np.zeros(0)
        tau_samples = tau / resolution
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        return amplitude / np.cosh((t - center) / tau_samples)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The pulse duration, truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is a symbolic expression whose variables are still
                unassigned.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
