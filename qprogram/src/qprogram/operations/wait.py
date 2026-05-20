from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class Wait(Operation):
    """Idle on ``bus`` for ``duration`` nanoseconds.

    Args:
        bus: Bus to idle on.
        duration: Wait duration in nanoseconds. Accepts an :class:`~qprogram.Expression` for sweeps.
    """

    def __init__(self, bus: str, duration: int | Expression) -> None:
        self.bus = bus
        self.duration = duration

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"op.wait"} | expression_tokens(self.duration)
