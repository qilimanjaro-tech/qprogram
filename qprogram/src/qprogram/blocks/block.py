"""Generic block container with the AST introspection contract.

A :class:`Block` is a sequential container of operations and nested blocks.
Beyond holding children, it implements the same four-method introspection
interface as :class:`~qprogram.operations.Operation` so the entire AST can
be walked through a single uniform API. Loop subclasses (``ForLoop``,
``Loop``, ``Average``, ``Parallel``) override the relevant pieces to expose
their own data — loop variables, sweep parameters, shot counts — via direct
instance attributes (``ForLoop.variable``, ``Average.shots``, …) rather than
a generic dict accessor.
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
    """Generic sequential container for operations and nested blocks."""

    def __init__(self) -> None:
        self._elements: list[Block | Operation] = []

    @property
    def elements(self) -> list[Block | Operation]:
        return self._elements

    def append(self, element: Block | Operation) -> None:
        self._elements.append(element)

    # -- introspection ------------------------------------------------------

    def variables(self) -> set[Variable]:
        """Union of variables across all child elements.

        Loop subclasses override to also include the variable they bind
        (the loop counter).
        """
        out: set[Variable] = set()
        for el in self._elements:
            out |= el.variables()
        return out

    def buses(self) -> set[str]:
        """Union of bus references across all child elements."""
        out: set[str] = set()
        for el in self._elements:
            out |= el.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Union of waveform references across all child elements."""
        out: set[Waveform | IQWaveform | str] = set()
        for el in self._elements:
            out |= el.waveforms()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        """Pre-order traversal: yield this block, then each child recursively.

        Pairs with :meth:`Operation.walk` (which yields just the op).
        Together they let callers do ``for node in program.body.walk()`` to
        visit every node in declaration order without writing recursion.
        """
        yield self
        for el in self._elements:
            yield from el.walk()

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens *this* block needs, in isolation.

        Mirrors :meth:`~qprogram.operations.operation.Operation.required_capabilities`:
        each subclass returns its identity token plus any refinement
        tokens. The validator walks via :meth:`walk` and unions per-node
        sets — children's tokens are picked up when the walk visits them,
        not by recursing here.

        The base implementation returns ``{"block.block"}``; subclasses
        like :class:`~qprogram.blocks.ForLoop` override to add their own
        identity and sweep-shape tokens.
        """
        return {"block.block"}

    # -- structural equality and hash ---------------------------------------
    #
    # Two blocks are equal iff they are of the same concrete class and
    # carry equivalent attribute data — *including* their children, which
    # live on the private ``_elements`` list. ``vars(self)`` picks that up
    # automatically; the shared :mod:`qprogram._structural` helpers then
    # walk the list element-wise and recurse into each child (which is
    # itself an :class:`Operation` or :class:`Block` with its own
    # structural ``__eq__`` / ``__hash__``).
    #
    # Hash is consistent with equality. Mutation contract: once a block
    # has been used as a ``set`` / ``dict`` key or its hash has been
    # cached, do not :meth:`append` to it or otherwise mutate. Programs
    # under construction (operations still being appended) should be
    # treated as not-yet-hashable; freezing happens implicitly when the
    # caller stops mutating.

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))
