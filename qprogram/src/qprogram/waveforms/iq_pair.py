from __future__ import annotations

from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQPair(IQWaveform):
    """Pairs two single-channel waveforms as I and Q."""

    def __init__(self, I: Waveform, Q: Waveform) -> None:
        if not isinstance(I, Waveform) or not isinstance(Q, Waveform):
            msg = "I and Q must be Waveform instances"
            raise TypeError(msg)
        self.I = I
        self.Q = Q

    def get_I(self) -> Waveform:
        return self.I

    def get_Q(self) -> Waveform:
        return self.Q

    def get_duration(self) -> int:
        return self.I.get_duration()
