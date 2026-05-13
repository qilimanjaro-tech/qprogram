from __future__ import annotations

from qprogram.operations.operation import Operation


class ResetPhase(Operation):
    """Reset NCO phase to zero."""

    def __init__(self, bus: str) -> None:
        self.bus = bus
