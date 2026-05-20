from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Variable


class GetParameter(Operation):
    """Read a platform-defined parameter into a :class:`~qprogram.Variable` at runtime.

    Operates on a platform-defined ``alias`` rather than a bus, so :attr:`BUS_ATTRS` is empty.

    Args:
        variable: Destination :class:`~qprogram.Variable` for the read value.
        alias: Platform-defined identifier for the target.
        parameter: Name of the parameter to read.
        channel_id: Optional channel index when the alias has multiple channels.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variable: Variable, alias: str, parameter: str, channel_id: int | None = None) -> None:
        self.variable = variable
        self.alias = alias
        self.parameter = parameter
        self.channel_id = channel_id

    def required_capabilities(self) -> set[str]:
        return {"op.get_parameter"}
