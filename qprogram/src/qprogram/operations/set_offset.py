from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class SetOffset(Operation):
    def __init__(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self.bus = bus
        self.offset_path0 = offset_path0
        self.offset_path1 = offset_path1

    def get_variables(self) -> set[Variable]:
        variables: set[Variable] = set()
        if isinstance(self.offset_path0, Expression):
            variables |= self.offset_path0.variables()
        if isinstance(self.offset_path1, Expression):
            variables |= self.offset_path1.variables()
        return variables
