"""Operation base class.

The four-method introspection contract (``variables`` / ``buses`` / ``waveforms`` / ``walk``) is shared
with :class:`~qprogram.blocks.Block`; see the architecture docs for the rationale.

We deliberately don't ship an ``attributes()`` helper for the raw constructor view of an op — callers can
use ``vars(op)`` or :mod:`inspect` directly; an extra method would repackage information Python already
exposes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram._structural import ast_eq, ast_hash
from qprogram.errors import ValidationError
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from qprogram.blocks.block import Block
    from qprogram.result import MeasurementHandle


class Operation:
    """Base class for all operations in the QProgram AST.

    Subclasses customise introspection behaviour through two class-attribute conventions:

    - :attr:`BUS_ATTRS` lists which ``__init__`` parameter names hold bus references. The default
      ``("bus",)`` matches every core op except :class:`~qprogram.operations.Sync` (which holds a list
      under ``targets``) and :class:`~qprogram.operations.SetParameter` / ``GetParameter`` /
      ``SetCrosstalk`` (no bus).
    - :attr:`WAVEFORM_ATTRS` lists which ``__init__`` parameters carry waveform values. Default empty.

    Equality and hashing are structural; once an instance has been used as a ``set`` / ``dict`` key, do
    not mutate its attributes. Callers like :meth:`QProgram.with_bus_mapping` that rewrite operations do
    so on a fresh ``deepcopy``.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ()

    def variables(self) -> set[Variable]:
        """Return every :class:`Variable` referenced by this op, transitively.

        Walks every public instance attribute, descending into :class:`Expression` nodes, waveform
        parameters, and nested lists. Ops with data hidden in private fields or computed lazily can
        override this method.
        """
        out: set[Variable] = set()
        for name, value in vars(self).items():
            if name.startswith("_"):
                continue
            out |= _collect_variables(value)
        return out

    def buses(self) -> set[str]:
        """Return every bus name this op touches.

        Reads the attributes listed in :attr:`BUS_ATTRS`. Plain strings, :class:`~qprogram.BusRef`
        instances, and lists of either are collected.
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

        Reads the attributes listed in :attr:`WAVEFORM_ATTRS`. ``None`` values (from optional waveform
        params) are skipped.
        """
        out: set[Waveform | IQWaveform | str] = set()
        for attr_name in self.WAVEFORM_ATTRS:
            value = getattr(self, attr_name, None)
            if value is not None:
                out.add(value)
        return out

    def walk(self) -> Iterator[Operation | Block]:
        """Yield ``self`` — operations are AST leaves.

        Pairs with :meth:`Block.walk`, which recurses through children.
        """
        yield self

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this op needs, in isolation.

        Non-recursive: the validator walks the AST and unions per-node sets. Subclasses override to add
        their identity token (``op.<name>``) plus any state-dependent refinement tokens (waveform kind,
        expression shape, return-type tokens for measurements, ...).
        """
        return set()

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))


class MeasurementOperation(Operation):
    """Marker base for operations that produce a referenceable measurement.

    Why a marker base (rather than duck-typing on the ``handle`` attribute): vendor authors opt in
    deliberately, and tooling that wants to enumerate measurements has a single ``isinstance`` to check.

    Why store the canonical :class:`~qprogram.MeasurementHandle` rather than a name string: every
    reference to the measurement — the user's variable, the AST node, every :class:`MeasurementRef` in
    conditionals, the value returned by :meth:`QProgram.measurement_handles` — becomes the same Python
    instance, and the runtime writes per-measurement values onto that single object.

    Concrete subclasses **must** set :attr:`handle` and :attr:`returns`.
    """

    handle: MeasurementHandle
    returns: tuple[str, ...]

    @property
    def name(self) -> str:
        """Return the measurement name. Proxy for ``self.handle.name``."""
        return self.handle.name

    def required_capabilities(self) -> set[str]:
        """Return one ``measure.returns.<token>`` per token in :attr:`returns`.

        Concrete subclasses union this with their own identity-token set.
        """
        return {f"measure.returns.{t}" for t in self.returns}


def normalize_returns(value: str | Iterable[str]) -> tuple[str, ...]:
    """Coerce a ``returns`` argument into the canonical ``tuple[str, ...]``.

    Accepts a comma-separated string (``"iq,raw"``), an iterable of strings (``["iq", "raw"]``), or a
    single token (``"iq"``). Empty entries from doubled commas or whitespace tokens are dropped.

    The canonical tuple is used for storage, equality, and the ``.qp`` writer's comma-joined output. No
    restriction on string contents at this layer — platforms decide which return-type strings they
    recognise.

    Args:
        value: Comma-separated string, iterable of strings, or a single string token.

    Returns:
        Canonical tuple of return-type tokens, with empty entries removed.

    Raises:
        ValidationError: If no non-empty tokens remain after cleaning.
    """
    parts = [p.strip() for p in value.split(",")] if isinstance(value, str) else [str(p).strip() for p in value]
    cleaned = [p for p in parts if p]
    if not cleaned:
        msg = "`returns` must specify at least one return type"
        raise ValidationError(msg)
    return tuple(cleaned)


def _collect_variables(value: object) -> set[Variable]:
    """Recursively gather every :class:`Variable` reachable from ``value``.

    Used by :meth:`Operation.variables`. Tolerates the arbitrary attribute shapes vendor extensions
    might invent: anything that isn't a Variable, Expression, Waveform, or list/tuple is skipped.
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
