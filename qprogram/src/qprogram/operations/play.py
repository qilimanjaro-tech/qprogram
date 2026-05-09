from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform


class Play(Operation):
    """Play a waveform on a bus."""

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self.bus = bus
        self.waveform = waveform

    def get_variables(self) -> set[Variable]:
        # Waveform parameters might be expressions (which contain variables)
        variables: set[Variable] = set()
        if isinstance(self.waveform, (Waveform, IQWaveform)):
            for val in vars(self.waveform).values():
                if isinstance(val, Expression):
                    variables |= val.variables()
        return variables
