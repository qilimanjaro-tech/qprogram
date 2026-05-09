from __future__ import annotations

from qprogram.blocks.block import Block


class Average(Block):
    """Repeat and average results."""

    def __init__(self, shots: int) -> None:
        super().__init__()
        self.shots = shots
