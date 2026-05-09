from __future__ import annotations

from qprogram.operations.operation import Operation


class Sync(Operation):
    """Synchronize buses."""

    def __init__(self, buses: list[str] | None = None) -> None:
        self.buses = buses
