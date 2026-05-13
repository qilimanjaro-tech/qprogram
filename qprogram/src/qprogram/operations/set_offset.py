from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetOffset(Operation):
    """Set DC offset on one or both signal paths."""

    def __init__(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self.bus = bus
        self.offset_path0 = offset_path0
        self.offset_path1 = offset_path1
