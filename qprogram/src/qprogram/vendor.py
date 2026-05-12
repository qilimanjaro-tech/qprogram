from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.buses import BusRef

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
        """Append an operation to the program's active block.

        Before appending, any BusRef-typed attributes on the operation are
        run through ``QProgram._validate_bus`` so vendor ops can't sneak in a
        bus from a different schema. Plain-string attributes are ignored
        (they aren't buses) and BusRefs without metadata pass through.
        """
        for value in vars(operation).values():
            if isinstance(value, BusRef):
                self._program._validate_bus(value)  # noqa: SLF001
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, BusRef):
                        self._program._validate_bus(item)  # noqa: SLF001
        self._program._active_block.append(operation)  # noqa: SLF001
