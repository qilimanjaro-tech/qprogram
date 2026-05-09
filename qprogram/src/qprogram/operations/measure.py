from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.waveforms.waveform import IQWaveform


class Measure(Operation):
    """Play a readout pulse and acquire the result."""

    def __init__(self, bus: str, waveform: IQWaveform | str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.save_adc = save_adc
