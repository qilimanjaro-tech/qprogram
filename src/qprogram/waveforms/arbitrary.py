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
"""Waveform defined by an explicit array of samples."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.waveforms.waveform import Waveform

if TYPE_CHECKING:
    from collections.abc import Sequence


class Arbitrary(Waveform):
    """Waveform defined by a user-provided 1-D sample array.

    The ``resolution`` argument to `envelope` is ignored — the samples are taken as-is, one per ns.

    An array that is already an `numpy.ndarray` is adopted rather than copied, so a caller holding
    a reference to it must not mutate it: waveforms are values, compared and hashed structurally.
    `envelope` hands back a copy for the same reason.

    Args:
        samples (Sequence[float] | np.ndarray): 1-D sequence (list, tuple, or ``np.ndarray``) of sample
            values. Converted with `numpy.asarray`, so the stored dtype follows the input.
    """

    def __init__(self, samples: Sequence[float] | np.ndarray) -> None:
        self.samples = np.asarray(samples)

    def envelope(self, resolution: int = 1) -> np.ndarray:  # ruff: ignore[unused-method-argument]
        """Return a copy of the stored samples.

        Args:
            resolution (int, optional): Ignored. Accepted so the signature matches `Waveform.envelope`; the
                stored samples are the envelope already, one per nanosecond.

        Returns:
            A copy of the sample array, so writing to it cannot alter the waveform.
        """
        return self.samples.copy()

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The number of samples, one sample spanning one nanosecond.
        """
        return len(self.samples)
