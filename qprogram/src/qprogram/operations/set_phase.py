from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class SetPhase(Operation):
    def __init__(self, bus: str, phase: float | Expression) -> None:
        self.bus = bus
        self.phase = phase

    def get_variables(self) -> set[Variable]:
        if isinstance(self.phase, Expression):
            return self.phase.variables()
        return set()
