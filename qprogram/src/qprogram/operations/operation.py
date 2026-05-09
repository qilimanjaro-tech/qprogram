from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.variable import Variable


class Operation:
    """Base class for all operations in the QProgram AST."""

    def get_variables(self) -> set[Variable]:
        """Return all Variable instances referenced by this operation."""
        return set()
