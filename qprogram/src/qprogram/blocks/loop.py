from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError

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

    Raises:
        ValidationError: If ``values`` is empty or not 1-D.
    """

    def __init__(self, variable: Variable, values: npt.ArrayLike) -> None:
        super().__init__()
        array = np.asarray(values)
        if array.ndim != 1:
            msg = f"Loop values must be a 1-D sequence, got a {array.ndim}-D array"
            raise ValidationError(msg)
        if array.size == 0:
            msg = "Loop values must be non-empty (an empty sweep never executes its body)"
            raise ValidationError(msg)
        self.variable = variable
        self.values = array

    def num_iterations(self) -> int:
        """Number of sweep points — one per element of :attr:`values`."""
        return int(self.values.size)

    def variables(self) -> set[Variable]:
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        return {"block.loop", "sweep.arbitrary"}
