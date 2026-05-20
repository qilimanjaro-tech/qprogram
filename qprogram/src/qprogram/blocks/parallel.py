from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block

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
        loops: The :class:`ForLoop` / :class:`Loop` instances to advance in lockstep.
    """

    def __init__(self, loops: Iterable[ForLoop | Loop]) -> None:
        super().__init__()
        self.loops: list[ForLoop | Loop] = list(loops)

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
