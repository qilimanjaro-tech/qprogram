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
"""Tukey-windowed single-channel waveform."""

from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class Tukey(Waveform):
    """Tukey window: rectangular pulse with cosine-tapered edges.

    The ``alpha`` parameter controls the fraction of the pulse occupied by the rising and falling
    edges combined: ``alpha=0`` produces a pure rectangle, ``alpha=1`` produces a Hann window, and
    intermediate values yield a flat top of width ``(1 - alpha) * duration`` with cosine ramps on
    each side of width ``(alpha / 2) * duration``. Matches the ``alpha`` parameter of
    :func:`scipy.signal.windows.tukey`.

    Cheaper to compile than :class:`FlatTop` (no ``erf`` evaluation) and commonly used as a smoothing
    window in pulse calibration.

    Args:
        amplitude (float | Expression): Peak amplitude (achieved across the flat region).
        duration (int | Expression): Total pulse duration in nanoseconds.
        alpha (float | Expression, optional): Fraction of the duration occupied by the combined rise +
            fall, in ``[0, 1]``. Defaults to ``0.5``.
    """

    def __init__(
        self,
        amplitude: float | Expression,
        duration: int | Expression,
        alpha: float | Expression = 0.5,
    ) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.alpha = alpha

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the Tukey-windowed envelope sampled at ``resolution``-ns steps.

        Three cases skip the taper arithmetic: a window of fewer than two samples and ``alpha <= 0`` are
        flat at ``amplitude``, and ``alpha >= 1`` is a full Hann window. Otherwise the taper spans
        ``alpha * (n - 1) / 2`` samples at each end, where ``n`` is the sample count.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D array of length ``duration / resolution``. The tapered forms are float; the two flat
            forms take their dtype from ``amplitude``, so an integer amplitude yields an integer array.

        Raises:
            UnassignedVariableError: If a parameter is an :class:`~qprogram.Expression` whose variables
                have no value.
        """
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        alpha = self.alpha.evaluate_or_raise() if isinstance(self.alpha, Expression) else self.alpha

        n = int(duration / resolution)
        if n <= 1:
            return np.full(max(n, 0), amplitude)
        if alpha <= 0:
            return np.full(n, amplitude)
        if alpha >= 1:
            # The taper spans the whole pulse, leaving no flat top: a Hann window.
            t = np.arange(n)
            return amplitude * 0.5 * (1 - np.cos(2 * np.pi * t / (n - 1)))

        t = np.arange(n)
        half_alpha = alpha * (n - 1) / 2
        out = np.full(n, float(amplitude))
        rising = t < half_alpha
        falling = t > (n - 1) - half_alpha
        out[rising] = amplitude * 0.5 * (1 + np.cos(np.pi * (t[rising] / half_alpha - 1)))
        out[falling] = amplitude * 0.5 * (1 + np.cos(np.pi * ((t[falling] - (n - 1)) / half_alpha + 1)))
        return out

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration truncated to a whole number of nanoseconds.

        Raises:
            UnassignedVariableError: If ``duration`` is an :class:`~qprogram.Expression` whose variables
                have no value.
        """
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
