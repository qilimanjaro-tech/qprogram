from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class Wait(Operation):
    """Idle for a given duration (ns)."""

    def __init__(self, bus: str, duration: int | Expression) -> None:
        self.bus = bus
        self.duration = duration

    def get_variables(self) -> set[Variable]:
        if isinstance(self.duration, Expression):
            return self.duration.variables()
        return set()
