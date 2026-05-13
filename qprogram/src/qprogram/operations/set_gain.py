from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetGain(Operation):
    """Set output gain."""

    def __init__(self, bus: str, gain: float | Expression) -> None:
        self.bus = bus
        self.gain = gain
