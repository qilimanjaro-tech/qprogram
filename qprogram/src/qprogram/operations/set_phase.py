from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetPhase(Operation):
    """Set the NCO phase on a bus.

    Args:
        bus: Bus whose oscillator phase to set.
        phase: Phase in radians. Accepts an :class:`~qprogram.Expression`.
    """

    def __init__(self, bus: str, phase: float | Expression) -> None:
        self.bus = bus
        self.phase = phase

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"op.set_phase"} | expression_tokens(self.phase)
