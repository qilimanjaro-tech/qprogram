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
    """First-order DRAG correction to a [`Gaussian`][qprogram.waveforms.Gaussian], the Q channel of a DRAG pulse.

    On its own, this waveform is rarely emitted directly; it is the Q-channel partner produced by
    `get_Q`.

    The envelope is ``-drag_coefficient * (t - duration / 2) / sigma**2`` times the
    [`Gaussian`][qprogram.waveforms.Gaussian] envelope with the same parameters, with ``t`` and
    ``sigma = duration / num_sigmas`` in nanoseconds (Motzoi et al., [arXiv:0901.0534](https://arxiv.org/abs/0901.0534),
    eq. 10). That Gaussian is the shifted and rescaled one, so the correction is the derivative of the
    unshifted curve times the shifted one, not exactly the derivative of the I channel: it starts at 0
    where the derivative does not, and its peak is about a fifth lower at ``num_sigmas=4`` (7% at 5).

    The duration, and the meaning of ``amplitude``, ``duration``, and ``num_sigmas``, are inherited
    unchanged, so the correction always spans the same window as the Gaussian it partners.

    Args:
        amplitude (float | Expression): Peak amplitude of the underlying Gaussian, which a negative value
            does not reach (see [`Gaussian`][qprogram.waveforms.Gaussian]). Accepts an
            [`Expression`][qprogram.Expression].
        duration (int | Expression): Pulse duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
        num_sigmas (float | Expression): Number of standard deviations the underlying Gaussian spans. Has
            to be non-zero, as for [`Gaussian`][qprogram.waveforms.Gaussian]. Accepts an
            [`Expression`][qprogram.Expression].
        drag_coefficient (float | Expression): Weight on the derivative term, in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression].
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        num_sigmas: float | Expression,
        drag_coefficient: float | Expression,
    ) -> None:
        super().__init__(amplitude=amplitude, duration=duration, num_sigmas=num_sigmas)
        self.drag_coefficient = drag_coefficient

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the DRAG correction sampled at ``resolution``-ns steps.

        The derivative is taken with respect to time in nanoseconds, so the correction is the same at
        any resolution, as the Gaussian is. It crosses zero at ``duration / 2``.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per nanosecond.

        Returns:
            A 1-D float array of ``duration / resolution`` samples, rounded up.

        Raises:
            UnassignedVariableError: If ``amplitude``, ``duration``, ``num_sigmas``, or
                ``drag_coefficient`` is a symbolic expression whose variables are unassigned.
            ZeroDivisionError: If ``num_sigmas`` is a Python ``0`` or ``0.0``.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        num_sigmas = self.num_sigmas.evaluate_or_raise() if isinstance(self.num_sigmas, Expression) else self.num_sigmas
        drag_coefficient = (
            self.drag_coefficient.evaluate_or_raise()
            if isinstance(self.drag_coefficient, Expression)
            else self.drag_coefficient
        )
        sigma = duration / num_sigmas
        mu = duration / 2
        x = np.arange(duration / resolution) * resolution
        return (-1 * drag_coefficient * (x - mu) / sigma**2) * super().envelope(resolution=resolution)
