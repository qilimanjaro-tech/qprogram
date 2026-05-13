"""Operation base class and the introspection contract for AST nodes.

Every :class:`Operation` (and every :class:`~qprogram.blocks.Block`) exposes a
small uniform read-only interface so analyzers, linters, compilers, and
diagram tools can walk the program without ``isinstance`` ladders or
duck-typed attribute hunting.

The contract is four methods:

- :meth:`Operation.variables` — every :class:`Variable` referenced by this op,
  transitively through expressions and waveform parameters.
- :meth:`Operation.buses` — every bus name the op touches.
- :meth:`Operation.waveforms` — every concrete waveform or string alias the
  op carries.
- :meth:`Operation.walk` — a generator yielding the op itself; ``Block`` 's
  override recurses through children.

Subclasses get all four for free. The only thing concrete ops opt into is
two class-attribute *conventions* that describe their data shape: which
``__init__`` parameter names hold buses and which hold waveforms. Defaults
match the common case so most ops set nothing.

A consumer that wants the raw constructor-parameter view of an op can call
``vars(op)`` or use :mod:`inspect` directly. We deliberately don't ship an
``attributes()`` helper for this: it would add a method to the contract that
no existing analyzer or serializer uses, and it would repackage information
Python already exposes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from qprogram.blocks.block import Block


class Operation:
    """Base class for all operations in the QProgram AST.

    Provides the introspection contract every concrete operation
    automatically satisfies. Subclasses customise behaviour through two
    class-attribute conventions:

    - :attr:`BUS_ATTRS` lists which ``__init__`` parameter names hold bus
      references. The default ``("bus",)`` matches every core op except
      :class:`~qprogram.operations.Sync` (which holds a list under
      ``buses``) and :class:`~qprogram.operations.SetParameter` (no bus).
      Set to ``()`` for ops without any bus, or to a tuple of names for
      ops with multiple (``ActiveReset`` has ``bus`` and ``control_bus``).

    - :attr:`WAVEFORM_ATTRS` lists which ``__init__`` parameters carry
      :class:`Waveform`/`IQWaveform` instances or string aliases. Default
      is empty; ops that play or measure waveforms declare it.

    Both class attributes are local to the operation class — vendor authors
    set them next to ``__init__`` and the rest of the introspection works
    without further declaration.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ()

    def variables(self) -> set[Variable]:
        """Return every :class:`Variable` referenced by this op, transitively.

        Walks every public instance attribute and gathers the variables
        from inside Expressions, waveform parameters, and nested lists.
        The default works for every op whose data lives on public attrs;
        ops with unusual shapes (data hidden in private fields, computed
        lazily, etc.) can override.
        """
        out: set[Variable] = set()
        for name, value in vars(self).items():
            if name.startswith("_"):
                continue
            out |= _collect_variables(value)
        return out

    def buses(self) -> set[str]:
        """Return every bus name this op touches.

        Reads from the attributes listed in :attr:`BUS_ATTRS`. Plain
        strings, :class:`~qprogram.BusRef` instances (which subclass
        ``str``), and lists of either are all collected. Empty when
        :attr:`BUS_ATTRS` is ``()``.
        """
        out: set[str] = set()
        for attr_name in self.BUS_ATTRS:
            value = getattr(self, attr_name, None)
            if isinstance(value, str):
                out.add(value)
            elif isinstance(value, list):
                out.update(v for v in value if isinstance(v, str))
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Return every waveform (concrete or string alias) the op carries.

        Reads from the attributes listed in :attr:`WAVEFORM_ATTRS`.
        :data:`None` values are skipped (some optional waveform params).
        """
        out: set[Waveform | IQWaveform | str] = set()
        for attr_name in self.WAVEFORM_ATTRS:
            value = getattr(self, attr_name, None)
            if value is not None:
                out.add(value)
        return out

    def walk(self) -> Iterator[Operation | Block]:
        """Yield ``self``; operations are AST leaves.

        Pairs with :meth:`Block.walk`, which recurses through children.
        Together they let callers do ``for node in program.body.walk()``
        and traverse the whole tree without writing recursion.
        """
        yield self


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


def normalize_returns(value: str | Iterable[str]) -> tuple[str, ...]:
    """Coerce a ``returns`` argument into the canonical ``tuple[str, ...]``.

    Accepts the three input shapes users naturally reach for:

    - a comma-separated string — ``"iq,raw"`` — tokenised on commas.
    - any iterable of strings — ``["iq", "raw"]`` / ``("iq",)``.
    - a single string token — ``"iq"`` (degenerates to a one-element tuple).

    Empty entries (from doubled commas or whitespace tokens) are dropped;
    a fully-empty input raises :class:`ValueError` so the field never ends
    up as an empty tuple silently. The canonical tuple form is used for
    storage, equality, and the ``.qp`` serializer's comma-joined output
    (see the writer's ``serialize_value``).

    No restriction on string contents at this layer — platforms decide
    which return-type strings they recognise (``"iq"``, ``"raw"``,
    ``"state"``, …) and raise their own error for unsupported ones.
    """
    parts = (
        [p.strip() for p in value.split(",")]
        if isinstance(value, str)
        else [str(p).strip() for p in value]
    )
    cleaned = [p for p in parts if p]
    if not cleaned:
        msg = "`returns` must specify at least one return type"
        raise ValueError(msg)
    return tuple(cleaned)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _collect_variables(value: object) -> set[Variable]:
    """Recursively gather every :class:`Variable` reachable from ``value``.

    Used by :meth:`Operation.variables` to walk arbitrary attribute
    contents. Handles the four shapes that appear inside an operation:

    - :class:`Variable` — direct reference, add it.
    - :class:`Expression` — delegate to its ``variables()`` (which
      already does the recursive walk over BinaryOp/Comparison/MathFunc/…).
    - :class:`Waveform` / :class:`IQWaveform` — descend into the
      waveform's own public attributes; any variables in its constructor
      arguments come along.
    - ``list`` / ``tuple`` — walk each element.

    Anything else (strings, ints, bools, ``None``, BusRefs) contributes
    nothing and is skipped. This keeps the helper tolerant of the
    arbitrary shapes vendor extensions might invent without forcing them
    to override :meth:`Operation.variables`.
    """
    if isinstance(value, Variable):
        return {value}
    if isinstance(value, Expression):
        return value.variables()
    if isinstance(value, (Waveform, IQWaveform)):
        out: set[Variable] = set()
        for name, wf_attr in vars(value).items():
            if not name.startswith("_"):
                out |= _collect_variables(wf_attr)
        return out
    if isinstance(value, (list, tuple)):
        out = set()
        for item in value:
            out |= _collect_variables(item)
        return out
    return set()
