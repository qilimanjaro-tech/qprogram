from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetFrequency(Operation):
    """Set NCO/oscillator frequency (Hz)."""

    def __init__(self, bus: str, frequency: float | Expression) -> None:
        self.bus = bus
        self.frequency = frequency

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"op.set_frequency"} | expression_tokens(self.frequency)
