from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Expression, Variable


class SetParameter(Operation):
    """Set a platform parameter (string-based, not Enum)."""

    def __init__(
        self,
        alias: str,
        parameter: str,
        value: float | Expression,
        channel_id: int | None = None,
    ) -> None:
        self.alias = alias
        self.parameter = parameter
        self.value = value
        self.channel_id = channel_id

    def get_variables(self) -> set[Variable]:
        if isinstance(self.value, Expression):
            return self.value.variables()
        return set()
