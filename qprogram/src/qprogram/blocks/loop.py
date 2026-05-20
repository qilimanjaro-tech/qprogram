from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    import numpy.typing as npt

    from qprogram.variable import Variable


class Loop(Block):
    """Arbitrary-sweep block: iterate ``variable`` through the given sequence of values.

    Use this when the sweep points don't fit a linear ``start/stop/step`` pattern (log scales, calibrated
    points, etc.). For linear sweeps prefer :class:`ForLoop` — some platforms can compile linear sweeps
    more efficiently.

    Args:
        variable: The :class:`~qprogram.Variable` rebound on each iteration.
        values: Sequence of values to iterate through. Anything ``np.asarray`` accepts.
    """

    def __init__(self, variable: Variable, values: npt.ArrayLike) -> None:
        super().__init__()
        self.variable = variable
        self.values = np.asarray(values)

    def variables(self) -> set[Variable]:
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        return {"block.loop", "sweep.arbitrary"}
