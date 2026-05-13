from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.blocks.for_loop import ForLoop
    from qprogram.blocks.loop import Loop
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


class Parallel(Block):
    """Run multiple loops concurrently. Created via the | operator on loop contexts.

    A :class:`Parallel` has a small structural quirk: its **loop headers**
    (the ``ForLoop`` / ``Loop`` instances composed via ``|``) live in
    ``self.loops`` rather than in ``self._elements``. The body operations
    that run under all those loops jointly live in ``_elements`` as usual.
    The introspection overrides below thread the loops back into the union
    so analyzers see the bound loop variables and the headers themselves
    when walking.
    """

    def __init__(self, loops: list[ForLoop | Loop]) -> None:
        super().__init__()
        self.loops = loops

    def variables(self) -> set[Variable]:
        """Include each composed loop's bound variable plus child variables."""
        out = super().variables()
        for lp in self.loops:
            out |= lp.variables()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        """Yield self, each composed loop header (as a leaf), then body children."""
        yield self
        for lp in self.loops:
            yield from lp.walk()
        for el in self._elements:
            yield from el.walk()
