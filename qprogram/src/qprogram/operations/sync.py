from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation


class Sync(Operation):
    """Synchronise buses to a common time reference.

    The user-facing :meth:`QProgram.sync` exposes a ``buses=`` keyword; the AST attribute is named
    ``targets`` to avoid shadowing :meth:`Operation.buses` (a list attribute named ``buses`` would
    silently hide the introspection method).

    Args:
        targets: Bus names to sync, or ``None`` to sync every bus currently active in the program.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("targets",)

    def __init__(self, targets: list[str] | None = None) -> None:
        self.targets = targets

    def required_capabilities(self) -> set[str]:
        return {"op.sync"}
