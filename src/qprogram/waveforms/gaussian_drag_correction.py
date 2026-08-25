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
"""Derivative-of-Gaussian correction envelope for DRAG pulses."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.gaussian import Gaussian


class GaussianDragCorrection(Gaussian):
    """Derivative-of-Gaussian envelope used as the Q-channel of a DRAG pulse.

    On its own, this waveform is rarely emitted directly; it is the Q-channel partner produced by
    `get_Q`.

    Only the envelope differs from [`Gaussian`][qprogram.waveforms.Gaussian]: the duration, and the meaning of
    ``amplitude``, ``duration``, and ``sigma``, are inherited unchanged, so the correction always spans the
    same window as the Gaussian it partners.

    Args:
        amplitude (float | Expression): Peak amplitude of the underlying Gaussian. Accepts an
            [`Expression`][qprogram.Expression].
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
        sigma (float | Expression): Standard deviation of the underlying Gaussian in nanoseconds.
        beta (float | Expression): DRAG scaling (``β`` in the Motzoi et al. parameterization).
            Multiplicative weight on the derivative term.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        sigma: float | Expression,
        beta: float | Expression,
    ) -> None:
        super().__init__(amplitude=amplitude, duration=duration, sigma=sigma)
        self.beta = beta

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the scaled Gaussian derivative sampled at ``resolution``-ns steps.

        The derivative is taken with respect to sample index rather than time, so the correction's
        amplitude scales with ``resolution`` while the underlying Gaussian's does not. It is antisymmetric
        about the pulse center, where it crosses zero.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, ``sigma``, or ``beta`` is a symbolic
                expression whose variables are unassigned.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        sigma = self.sigma.evaluate_or_raise() if isinstance(self.sigma, Expression) else self.sigma
        beta = self.beta.evaluate_or_raise() if isinstance(self.beta, Expression) else self.beta
        n_samples = int(duration / resolution)
        sigma_samples = sigma / resolution
        center = (n_samples - 1) / 2
        t = np.arange(n_samples)
        gaussian = amplitude * np.exp(-0.5 * ((t - center) / sigma_samples) ** 2)
        return beta * -(t - center) / (sigma_samples**2) * gaussian
