from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetOffset(Operation):
    """Set DC offset on one or both signal paths of a bus.

    Args:
        bus: Bus whose DC offset to set.
        offset_path0: Offset on path 0 (the only path for single-channel buses, I for IQ buses).
        offset_path1: Offset on path 1 (Q for IQ buses). ``None`` leaves the path's offset unchanged.
    """

    def __init__(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self.bus = bus
        self.offset_path0 = offset_path0
        self.offset_path1 = offset_path1

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        caps = {"op.set_offset"} | expression_tokens(self.offset_path0)
        if self.offset_path1 is not None:
            caps |= expression_tokens(self.offset_path1)
        return caps
