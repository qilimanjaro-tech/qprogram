from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetParameter(Operation):
    """Set a platform parameter (string-based, not Enum).

    Operates on a platform-defined ``alias`` rather than a bus; declares
    :attr:`BUS_ATTRS` as empty so :meth:`Operation.buses` correctly skips it.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

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

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"op.set_parameter"} | expression_tokens(self.value)
