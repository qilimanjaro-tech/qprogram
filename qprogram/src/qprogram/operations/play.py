from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.waveforms.waveform import IQWaveform, Waveform


class Play(Operation):
    """Play a waveform on a bus."""

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self.bus = bus
        self.waveform = waveform
