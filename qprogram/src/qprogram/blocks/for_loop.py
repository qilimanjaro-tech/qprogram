from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    from qprogram.variable import Variable


class ForLoop(Block):
    """Linear sweep block: iterate ``variable`` from ``start`` to ``stop`` in increments of ``step``.

    Args:
        variable: The :class:`~qprogram.Variable` rebound on each iteration.
        start: First value of the sweep (inclusive).
        stop: Final value of the sweep (inclusive).
        step: Increment between consecutive iterations.
    """

    def __init__(self, variable: Variable, start: float, stop: float, step: float) -> None:
        super().__init__()
        self.variable = variable
        self.start = start
        self.stop = stop
        self.step = step

    def variables(self) -> set[Variable]:
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        return {"block.for_loop", "sweep.linear"}
