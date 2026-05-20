from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.waveforms.waveform import Waveform

if TYPE_CHECKING:
    from collections.abc import Sequence


class Arbitrary(Waveform):
    """Waveform defined by a user-provided 1-D sample array.

    The ``resolution`` argument to :meth:`envelope` is ignored — the samples are taken as-is, one per ns.

    Args:
        samples: 1-D sequence (list, tuple, or ``np.ndarray``) of sample values.
    """

    def __init__(self, samples: Sequence[float] | np.ndarray) -> None:
        self.samples = np.asarray(samples)

    def envelope(self, resolution: int = 1) -> np.ndarray:  # noqa: ARG002
        return self.samples.copy()

    def get_duration(self) -> int:
        return len(self.samples)
