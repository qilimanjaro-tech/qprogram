from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation


class Sync(Operation):
    """Synchronize buses.

    ``targets`` is the list of bus names to sync; ``None`` means sync all
    buses currently active in the program. The field is named ``targets``
    rather than ``buses`` to avoid shadowing :meth:`Operation.buses` on
    the same instance (a list-typed attribute named ``buses`` would
    silently hide the introspection method). The user-facing
    :meth:`QProgram.sync` keeps its ``buses=`` keyword argument; only the
    AST attribute changes name.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("targets",)

    def __init__(self, targets: list[str] | None = None) -> None:
        self.targets = targets
