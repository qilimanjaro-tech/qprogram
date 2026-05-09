from __future__ import annotations

from qprogram.operations.operation import Operation


class ResetPhase(Operation):
    def __init__(self, bus: str) -> None:
        self.bus = bus
