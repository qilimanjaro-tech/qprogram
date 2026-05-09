from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    from qprogram.blocks.for_loop import ForLoop
    from qprogram.blocks.loop import Loop


class Parallel(Block):
    """Run multiple loops concurrently. Created via the | operator on loop contexts."""

    def __init__(self, loops: list[ForLoop | Loop]) -> None:
        super().__init__()
        self.loops = loops
