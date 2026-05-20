from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation
from qprogram.waveforms.waveform import IQWaveform, Waveform


class Play(Operation):
    """Play a waveform on a bus.

    Args:
        bus: Bus to play on.
        waveform: Either a concrete :class:`~qprogram.waveforms.Waveform` /
            :class:`~qprogram.waveforms.IQWaveform`, or a string alias to be resolved later by
            :meth:`QProgram.with_waveforms`.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self.bus = bus
        self.waveform = waveform

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token  # noqa: PLC0415

        caps = {"op.play"}
        if isinstance(self.waveform, str):
            caps.add("waveform.alias")
        else:
            caps.add("waveform.iq" if isinstance(self.waveform, IQWaveform) else "waveform.single")
            tok = waveform_token(self.waveform)
            if tok is not None:
                caps.add(tok)
        return caps
