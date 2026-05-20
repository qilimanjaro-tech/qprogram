"""Base :class:`Block` container.

Blocks satisfy the same introspection contract as :class:`~qprogram.operations.Operation` so the entire
AST walks through a single uniform API. See the architecture docs for the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable
    from qprogram.waveforms.waveform import IQWaveform, Waveform


class Block:
    """Generic sequential container for operations and nested blocks.

    The base ``Block`` is what :meth:`QProgram.block` produces — an unstructured grouping with no extra
    semantics. Loop blocks (:class:`~qprogram.blocks.ForLoop`, :class:`~qprogram.blocks.Loop`,
    :class:`~qprogram.blocks.Average`, :class:`~qprogram.blocks.Parallel`) and
    :class:`~qprogram.blocks.Conditional` subclass it to add structure the runtime understands.

    Equality and hashing are structural (same class + same children); once a block has been used as a
    ``set`` / ``dict`` key, do not :meth:`append` to it.
    """

    def __init__(self) -> None:
        self._elements: list[Block | Operation] = []

    @property
    def elements(self) -> list[Block | Operation]:
        """Return the contained operations and sub-blocks in declaration order."""
        return self._elements

    def append(self, element: Block | Operation) -> None:
        """Append an operation or sub-block to the end of this block."""
        self._elements.append(element)

    def variables(self) -> set[Variable]:
        """Return every :class:`~qprogram.Variable` referenced by any child.

        Loop subclasses override to also include the loop-counter variable they bind.
        """
        out: set[Variable] = set()
        for el in self._elements:
            out |= el.variables()
        return out

    def buses(self) -> set[str]:
        """Return every bus name referenced by any child."""
        out: set[str] = set()
        for el in self._elements:
            out |= el.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Return every waveform (concrete or string alias) referenced by any child."""
        out: set[Waveform | IQWaveform | str] = set()
        for el in self._elements:
            out |= el.waveforms()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        """Yield this block, then each descendant in pre-order.

        Pairs with :meth:`Operation.walk` (which yields just the leaf) so callers can write
        ``for node in program.body.walk():`` without recursion.
        """
        yield self
        for el in self._elements:
            yield from el.walk()

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this block needs, in isolation.

        Non-recursive by design — the validator handles child tokens via :meth:`walk` and unions
        per-node sets. Recursing here would double-count. Subclasses override to add their own identity
        token (``block.<name>``) and refinement tokens (sweep shape, etc.).
        """
        return {"block.block"}

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))
