from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from qprogram.blocks.for_loop import ForLoop
    from qprogram.blocks.loop import Loop
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


class Parallel(Block):
    """Compose multiple loops to advance their iterators in lockstep, sharing one body.

    Created via the ``|`` operator on loop contexts (``with for_loop(a, ...) | for_loop(b, ...) as p:``).
    Note the structural quirk: composed loop headers live on ``self.loops``, *not* in ``self._elements``.
    Body operations live in ``_elements`` as usual. The introspection overrides below thread loop
    variables back into the unioned views so analyzers see them.

    Args:
        loops: The :class:`ForLoop` / :class:`Loop` instances to advance in lockstep. At least
            two, and all with the same number of iterations.

    Raises:
        ValidationError: On fewer than two loops or mismatched iteration counts.
    """

    def __init__(self, loops: Iterable[ForLoop | Loop]) -> None:
        super().__init__()
        loop_list: list[ForLoop | Loop] = list(loops)
        if len(loop_list) < 2:
            msg = f"Parallel requires at least two loops, got {len(loop_list)}"
            raise ValidationError(msg)
        iteration_counts = [lp.num_iterations() for lp in loop_list]
        if len(set(iteration_counts)) > 1:
            described = ", ".join(
                f"{type(lp).__name__}({lp.variable.id!r}): {n}"
                for lp, n in zip(loop_list, iteration_counts, strict=True)
            )
            msg = f"parallel loops must have the same number of iterations to advance in lockstep; got {described}"
            raise ValidationError(msg)
        self.loops: list[ForLoop | Loop] = loop_list

    def variables(self) -> set[Variable]:
        out = super().variables()
        for lp in self.loops:
            out |= lp.variables()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        yield self
        for lp in self.loops:
            yield from lp.walk()
        for el in self._elements:
            yield from el.walk()

    def required_capabilities(self) -> set[str]:
        return {"block.parallel"}
