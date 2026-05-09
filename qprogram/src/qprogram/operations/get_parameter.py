from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Variable


class GetParameter(Operation):
    """Read a platform parameter into a variable."""

    def __init__(self, variable: Variable, alias: str, parameter: str, channel_id: int | None = None) -> None:
        self.variable = variable
        self.alias = alias
        self.parameter = parameter
        self.channel_id = channel_id

    def get_variables(self) -> set[Variable]:
        return {self.variable}
