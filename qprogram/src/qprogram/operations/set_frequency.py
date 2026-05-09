from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class SetFrequency(Operation):
    """Set NCO/oscillator frequency (Hz)."""

    def __init__(self, bus: str, frequency: float | Expression) -> None:
        self.bus = bus
        self.frequency = frequency

    def get_variables(self) -> set[Variable]:
        if isinstance(self.frequency, Expression):
            return self.frequency.variables()
        return set()
