from __future__ import annotations

from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQPair(IQWaveform):
    """Compose two :class:`Waveform` instances into an :class:`IQWaveform`.

    Useful when the desired I and Q envelopes don't fit one of the named DRAG-style classes — for example,
    a square-on-I, zero-on-Q readout pulse.

    Args:
        I: In-phase channel.
        Q: Quadrature channel. Must have the same duration as ``I``.

    Raises:
        TypeError: If either argument is not a :class:`Waveform` instance.
    """

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
