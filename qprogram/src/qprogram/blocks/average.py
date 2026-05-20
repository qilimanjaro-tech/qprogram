from __future__ import annotations

from qprogram.blocks.block import Block


class Average(Block):
    """Repeat the contained block ``shots`` times and average measurement results across iterations.

    Args:
        shots: Number of times to execute the block body.
    """

    def __init__(self, shots: int) -> None:
        super().__init__()
        self.shots = shots

    def required_capabilities(self) -> set[str]:
        return {"block.average"}
