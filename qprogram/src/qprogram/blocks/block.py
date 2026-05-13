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
