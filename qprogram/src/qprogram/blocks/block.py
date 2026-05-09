from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.operations.operation import Operation


class Block:
    """Generic sequential container for operations and nested blocks."""

    def __init__(self) -> None:
        self._elements: list[Block | Operation] = []

    @property
    def elements(self) -> list[Block | Operation]:
        return self._elements

    def append(self, element: Block | Operation) -> None:
        self._elements.append(element)
