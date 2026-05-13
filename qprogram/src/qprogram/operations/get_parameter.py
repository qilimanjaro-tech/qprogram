from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Variable


class GetParameter(Operation):
    """Read a platform parameter into a variable.

    Operates on a platform-defined ``alias`` rather than a bus; declares
    :attr:`BUS_ATTRS` as empty so :meth:`Operation.buses` correctly skips it.
    The result variable is captured into :attr:`variable` and surfaces
    through the default :meth:`Operation.variables` walker.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variable: Variable, alias: str, parameter: str, channel_id: int | None = None) -> None:
        self.variable = variable
        self.alias = alias
        self.parameter = parameter
        self.channel_id = channel_id
