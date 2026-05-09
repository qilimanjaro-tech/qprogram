from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class SetGain(Operation):
    def __init__(self, bus: str, gain: float | Expression) -> None:
        self.bus = bus
        self.gain = gain

    def get_variables(self) -> set[Variable]:
        if isinstance(self.gain, Expression):
            return self.gain.variables()
        return set()
