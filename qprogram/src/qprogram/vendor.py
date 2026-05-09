from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.operations.operation import Operation
    from qprogram.qprogram import QProgram


class VendorNamespace:
    """Base class for vendor operation namespaces.

    Vendors subclass this and add typed methods that instantiate
    Operation subclasses and append them to the program.
    """

    def __init__(self, program: QProgram) -> None:
        self._program = program

    def _append(self, operation: Operation) -> None:
        """Append an operation to the program's active block."""
        self._program._active_block.append(operation)  # noqa: SLF001
