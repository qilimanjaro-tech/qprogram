from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    from qprogram.variable import Variable


class Loop(Block):
    """Loop over an arbitrary array of values."""

    def __init__(self, variable: Variable, values: np.ndarray) -> None:
        super().__init__()
        self.variable = variable
        self.values = np.asarray(values)
