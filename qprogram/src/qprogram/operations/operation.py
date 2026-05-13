from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.variable import Variable


class Operation:
    """Base class for all operations in the QProgram AST."""

    def get_variables(self) -> set[Variable]:
        """Return all Variable instances referenced by this operation."""
        return set()


class MeasurementOperation(Operation):
    """Marker base for operations that produce a referenceable measurement.

    Concrete subclasses (``Measure`` in core; ``Acquire`` in qprogram-qblox;
    any future vendor measurement op) **must** expose a ``name: str``
    instance attribute. The runtime and the result API rely on this
    contract — :class:`~qprogram.QProgramResult.get` looks up by name —
    and :class:`~qprogram.QProgram` walks the AST for instances of this
    class to allocate fresh names and to surface :meth:`measurement_handles`.

    A marker base (rather than duck-typing on a ``name`` attribute) keeps
    the contract explicit: vendor authors opt in deliberately, and tooling
    that wants to enumerate measurements has a single ``isinstance`` to
    check.
    """

    name: str  # subclasses must set this
