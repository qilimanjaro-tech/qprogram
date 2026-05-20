from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    import numpy.typing as npt

    from qprogram.variable import Variable


class Loop(Block):
    """Loop over an arbitrary array of values."""

    def __init__(self, variable: Variable, values: npt.ArrayLike) -> None:
        super().__init__()
        self.variable = variable
        self.values = np.asarray(values)

    def variables(self) -> set[Variable]:
        """Include the loop-counter variable on top of the children's vars."""
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        return {"block.loop", "sweep.arbitrary"}
