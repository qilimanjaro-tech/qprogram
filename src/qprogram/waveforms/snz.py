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

from typing import cast

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class SuddenNetZero(Waveform):
    """Sudden Net Zero (SNZ) flux pulse for fast, leakage-suppressed CZ gates.

    The pulse is ``+amplitude`` for its first half, one sample at ``b * amplitude``, ``t_phi`` samples at
    zero, one sample at ``-b * amplitude``, then ``-amplitude`` for its second half (Negîrneac et al.,
    [arXiv:2008.07411](https://arxiv.org/abs/2008.07411)). The halves mirror each other, so the integrated
    flux is zero for any ``b`` as long as ``0 <= t_phi <= duration - 2``. Each half lasts
    ``(duration - t_phi - 2) / 2`` ns.

    Two conventions differ from the paper's: ``t_phi`` counts only the zeros between the two ``b``
    samples, so the paper's ``t_φ`` is ``t_phi + 2``, and ``b`` is a fraction of ``amplitude`` rather
    than an amplitude of its own.

    ``t_phi`` is not range-checked. Above ``duration - 2`` it raises (see `envelope`). A negative
    ``t_phi`` with ``duration - t_phi`` even raises nothing, and the pulse is no longer net zero: the
    halves overlap the ``b`` samples, so ``duration=10, t_phi=-2`` sums to ``-1.5 * amplitude``.

    Args:
        amplitude (float | Expression): Amplitude of the two halves, positive first. Accepts an
            [`Expression`][qprogram.Expression].
        duration (int | Expression): Total pulse duration in nanoseconds. ``duration - t_phi`` has to be
            even for the halves to come out equal. Accepts an [`Expression`][qprogram.Expression].
        b (float | Expression): Height of the single samples either side of the zero hold, as a fraction
            of ``amplitude``. Accepts an [`Expression`][qprogram.Expression].
        t_phi (int | Expression): Length of the zero hold between the two ``b`` samples, in nanoseconds,
            from 0 to ``duration - 2``. Accepts an [`Expression`][qprogram.Expression], whose value has to be an ``int`` as well. A
            float raises, ``4.0`` included, and every sweep source binds floats, so ``t_phi`` cannot be
            swept.
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

        Only the two halves are converted to samples: ``t_phi`` and the two ``b`` samples are counted in
        samples, so at a resolution other than 1 the pieces rarely add up to the pulse. Above a small
        ``t_phi`` (3 at resolution 2, 2 at resolutions 3 and 4, 1 at resolution 8) every duration raises
        ``ValueError``. Where it does not raise, the zero hold lasts ``t_phi * resolution`` ns and each
        ``b`` sample lasts ``resolution`` ns.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D float array of ``duration / resolution`` samples, rounded to the nearest integer.

        Raises:
            ValueError: If the two halves do not come out equal, which at resolution 1 means
                ``duration - t_phi`` is odd, or if ``t_phi`` is ``duration`` or more.
            IndexError: If ``t_phi`` is a float rather than an ``int``, or is ``duration - 1``.
            UnassignedVariableError: If a parameter is an [`Expression`][qprogram.Expression] whose variables
                have no value.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        b = self.b.evaluate_or_raise() if isinstance(self.b, Expression) else self.b
        # Deliberately not converted with int(): a float t_phi raises IndexError rather than being truncated.
        t_phi = cast("int", self.t_phi.evaluate_or_raise() if isinstance(self.t_phi, Expression) else self.t_phi)
        half = int((duration - 2 - t_phi) / 2 / resolution)

        envelope = np.zeros(round(duration / resolution))
        envelope[:half] = amplitude * np.ones(half)
        envelope[half] = b * amplitude
        envelope[half + 1 + t_phi] = -b * amplitude
        try:
            envelope[half + 2 + t_phi :] = -amplitude * np.ones(half)
        except ValueError as error:
            msg = (
                f"SuddenNetZero(duration={duration}, t_phi={t_phi}) does not split into equal halves at "
                f"resolution {resolution}: the positive half has {half} samples and the negative half would "
                f"have {len(envelope[half + 2 + t_phi :])}. At resolution 1, duration - t_phi has to be even."
            )
            raise ValueError(msg) from error
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
