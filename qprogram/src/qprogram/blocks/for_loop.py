from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    from qprogram.variable import Variable


class ForLoop(Block):
    """Parametric loop: start, stop, step."""

    def __init__(self, variable: Variable, start: float, stop: float, step: float) -> None:
        super().__init__()
        self.variable = variable
        self.start = start
        self.stop = stop
        self.step = step
