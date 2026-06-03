from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQZero(IQWaveform):
    """Lift a real-valued :class:`Waveform` onto an IQ bus with a zero Q channel.

    Convenience wrapper for ``IQPair(I=envelope, Q=Square(0.0, duration))``. Useful when a calibrated
    single-channel pulse has to drive an IQ-typed bus without rewriting the rest of the program.

    Args:
        envelope: Single-channel :class:`Waveform` placed on the I channel.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("envelope",)

    def __init__(self, envelope: Waveform) -> None:
        if not isinstance(envelope, Waveform):
            msg = f"IQZero envelope must be a Waveform, got {type(envelope).__name__}"
            raise TypeError(msg)
        self.envelope = envelope

    def get_I(self) -> Waveform:
        return self.envelope

    def get_Q(self) -> Waveform:
        return Arbitrary(np.zeros(self.envelope.get_duration()))

    def get_duration(self) -> int:
        return self.envelope.get_duration()
