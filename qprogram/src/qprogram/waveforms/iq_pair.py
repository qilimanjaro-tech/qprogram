from __future__ import annotations

from qprogram.errors import UnassignedVariableError, ValidationError
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
        ValidationError: If the two channels have different (concretely-known) durations. The
            check is best-effort — symbolic durations whose variables are still unassigned are
            accepted here and left to the platform compiler to verify once values are bound.
    """

    def __init__(self, I: Waveform, Q: Waveform) -> None:
        if not isinstance(I, Waveform) or not isinstance(Q, Waveform):
            msg = "I and Q must be Waveform instances"
            raise TypeError(msg)
        try:
            i_duration, q_duration = I.get_duration(), Q.get_duration()
        except UnassignedVariableError:
            i_duration = q_duration = None  # symbolic durations — defer the check
        if i_duration != q_duration:
            msg = f"IQPair channels must have equal durations; got I={i_duration} ns, Q={q_duration} ns"
            raise ValidationError(msg)
        self.I = I
        self.Q = Q

    def get_I(self) -> Waveform:
        return self.I

    def get_Q(self) -> Waveform:
        return self.Q

    def get_duration(self) -> int:
        return self.I.get_duration()
