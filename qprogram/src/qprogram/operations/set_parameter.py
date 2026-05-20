from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetParameter(Operation):
    """Set a platform-defined parameter by string name.

    Used for parameters that aren't naturally tied to a bus (e.g. an instrument's clock rate). Targets a
    platform-defined ``alias`` rather than a bus, so :attr:`BUS_ATTRS` is empty.

    Args:
        alias: Platform-defined identifier for the target.
        parameter: Name of the parameter to set.
        value: New value. Accepts an :class:`~qprogram.Expression` for sweeps.
        channel_id: Optional channel index when the alias has multiple channels.
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
