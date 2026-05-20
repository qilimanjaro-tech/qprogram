from __future__ import annotations

from qprogram.operations.operation import Operation


class ResetPhase(Operation):
    """Reset the NCO phase on a bus to zero.

    Args:
        bus: Bus whose oscillator phase to reset.
    """

    def __init__(self, bus: str) -> None:
        self.bus = bus

    def required_capabilities(self) -> set[str]:
        return {"op.reset_phase"}
