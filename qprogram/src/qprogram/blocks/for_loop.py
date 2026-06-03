from __future__ import annotations

import math
from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from qprogram.variable import Variable


def _require_finite_number(value: object, *, name: str) -> float:
    """Reject non-numeric, boolean, and non-finite sweep bounds.

    A ``bool`` is an ``int`` subclass but never a meaningful bound; ``inf``/``nan`` would create
    a sweep that can neither execute nor round-trip through ``.qp``.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        msg = f"ForLoop {name} must be an int or float, got {type(value).__name__}"
        raise ValidationError(msg)
    if not math.isfinite(value):
        msg = f"ForLoop {name} must be finite, got {value!r}"
        raise ValidationError(msg)
    return value


class ForLoop(Block):
    """Linear sweep block: iterate ``variable`` from ``start`` to ``stop`` in increments of ``step``.

    Args:
        variable: The :class:`~qprogram.Variable` rebound on each iteration.
        start: First value of the sweep (inclusive).
        stop: Final value of the sweep (inclusive).
        step: Increment between consecutive iterations.

    Raises:
        ValidationError: If any bound is non-numeric or non-finite, if ``step`` is zero (an
            infinite sweep), or if ``step`` points away from ``stop`` (an empty sweep).
    """

    def __init__(self, variable: Variable, start: float, stop: float, step: float) -> None:
        super().__init__()
        start = _require_finite_number(start, name="start")
        stop = _require_finite_number(stop, name="stop")
        step = _require_finite_number(step, name="step")
        if step == 0:
            msg = "ForLoop step must be non-zero (a zero step never reaches stop)"
            raise ValidationError(msg)
        if (stop - start) * step < 0:
            msg = (
                f"ForLoop step {step!r} moves away from stop ({start!r} -> {stop!r}); "
                f"flip the step sign or swap the bounds"
            )
            raise ValidationError(msg)
        self.variable = variable
        self.start = start
        self.stop = stop
        self.step = step

    def num_iterations(self) -> int:
        """Number of sweep points, with ``start`` and ``stop`` both inclusive.

        Computed as ``round((stop - start) / step) + 1`` — the rounding absorbs floating-point
        division noise for ranges like ``(0.0, 1.0, 0.01)``.
        """
        return round((self.stop - self.start) / self.step) + 1

    def variables(self) -> set[Variable]:
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        return {"block.for_loop", "sweep.linear"}
