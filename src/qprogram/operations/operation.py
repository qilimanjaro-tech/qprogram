# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The [`Operation`][qprogram.operations.Operation] base class and the measurement-field vocabulary.

The four-method introspection contract (``variables`` / ``buses`` / ``waveforms`` / ``walk``) is shared
with [`Block`][qprogram.blocks.Block]; see the architecture docs for the rationale.

There is deliberately no ``attributes()`` helper for the raw constructor view of an op: ``vars(op)`` and
`inspect` expose exactly that, and an extra method would only repackage information Python already
provides.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from qprogram._structural import ast_eq, ast_hash
from qprogram.errors import ValidationError
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from qprogram.blocks.block import Block
    from qprogram.result import MeasurementHandle


class Operation:
    """Base class for all operations in the QProgram AST.

    Subclasses customize introspection behavior through two class-attribute conventions:

    - `BUS_ATTRS` lists which ``__init__`` parameter names hold bus references. The default
      ``("bus",)`` matches every core op except [`Sync`][qprogram.operations.Sync] (which holds a list
      under ``targets``) and [`Call`][qprogram.operations.Call] (which lists none — buses reach a call
      site only as bound argument values).
    - `WAVEFORM_ATTRS` lists which ``__init__`` parameters carry waveform values. Default empty.

    Equality and hashing are structural; once an instance has been used as a ``set`` / ``dict`` key, do
    not mutate its attributes. Callers like [`QProgram.rebind`][qprogram.QProgram.rebind] that rewrite operations do
    so on a fresh ``deepcopy``.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ()
    BROADCASTS_WHEN_NO_BUS: ClassVar[bool] = False
    """When ``True`` and the op's `BUS_ATTRS` resolve to no buses, the op semantically
    touches *every* bus in the program (e.g. ``Sync(targets=None)``). The validator then routes
    it across all program buses instead of the default-bus profile."""

    AFFECTS_AVERAGING: ClassVar[bool] = False
    """Whether this op participates in what an [`Average`][qprogram.blocks.Average] block averages.

    An ``average`` block repeats its body and accumulates **measurement results**; only the ops
    that produce those results determine whether the averaging itself can run as a real-time
    hardware feature. Ops with ``AFFECTS_AVERAGING = False`` are still validated and still execute
    inside the body — they just don't gate the *Average block's* execution domain (see
    [`qprogram.validation.validate`][qprogram.validate]). Defaults to ``False``; `MeasurementOperation`
    sets it ``True``, so core ``measure`` and vendor ``acquire`` opt in automatically. A vendor
    adding another averaging-relevant op sets it on its own class."""

    def variables(self) -> set[Variable]:
        """Return every [`Variable`][qprogram.Variable] referenced by this op, transitively.

        Walks every public instance attribute, descending into [`Expression`][qprogram.Expression] nodes, waveform
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

        Reads the attributes listed in `BUS_ATTRS`. Plain strings, [`BusRef`][qprogram.BusRef]
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

        Reads the attributes listed in `WAVEFORM_ATTRS`. ``None`` values (from optional waveform
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

        Pairs with `Block.walk`, which recurses through children.
        """
        yield self

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this op needs, in isolation.

        Non-recursive: the validator walks the AST and unions per-node sets. Subclasses override to add
        their identity token (``op.<name>``) plus any state-dependent refinement tokens (waveform kind,
        expression shape, ``measure.fields.<name>`` for the fields a measurement requests, ...).
        """
        return set()

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))


class MeasurementField(StrEnum):
    """Canonical names for the data a measurement can produce.

    Members **are** strings (``MeasurementField.IQ == "iq"`` is ``True``), so they travel unchanged
    through capability tokens, the ``.qp`` writer, and `fields`
    keys. The enum exists for discoverability (type ``MeasurementField.`` and let the editor list
    the options) and for static checking — it is not a new value type.

    Declaration order is the **canonical field order**: `normalize_fields` sorts every
    ``fields`` tuple this way, so ``(IQ, STATE)`` and ``(STATE, IQ)`` build the same AST, hash the
    same, and serialize to the same ``.qp`` line. Vendor-defined fields are plain strings (a vendor
    ships its own `StrEnum` if it wants the same ergonomics) and sort after the core
    members, alphabetically among themselves.

    Attributes:
        STATE: Classified outcome — required to reference ``handle.state`` in a conditional.
        IQ: Demodulated, integrated in-phase/quadrature pair. The default, and the ``data``
            attribute of a [`MeasurementResult`][qprogram.MeasurementResult] whenever it is requested.
        RAW: Raw ADC trace.
    """

    STATE = "state"
    IQ = "iq"
    RAW = "raw"


class MeasurementOperation(Operation):
    """Marker base for operations that produce a referenceable measurement.

    Why a marker base (rather than duck-typing on the ``handle`` attribute): vendor authors opt in
    deliberately, and tooling that wants to enumerate measurements has a single ``isinstance`` to check.

    Why store the canonical [`MeasurementHandle`][qprogram.MeasurementHandle] rather than a name string: every reference
    to the measurement — the user's variable, the AST node, every [`MeasurementRef`][qprogram.MeasurementRef] in
    conditionals, the value returned by [`QProgram.measurement_handles`][qprogram.QProgram.measurement_handles] —
    becomes the same Python instance, and the runtime writes per-measurement values onto that single object.

    Concrete subclasses **must** set `handle` and `fields`.
    """

    handle: MeasurementHandle
    fields: tuple[str, ...]
    """Requested measurement fields, canonically ordered by `normalize_fields`. Typed as
    ``str`` rather than `MeasurementField` because vendors may register their own field
    names; core fields always compare equal to their enum member."""

    AFFECTS_AVERAGING: ClassVar[bool] = True
    """Measurements are exactly what an ``average`` block accumulates — see
    `Operation.AFFECTS_AVERAGING`."""

    @property
    def name(self) -> str:
        """The measurement name — a proxy for ``self.handle.name``."""
        return self.handle.name

    def required_capabilities(self) -> set[str]:
        """Return one ``measure.fields.<name>`` token per entry in `fields`.

        Concrete subclasses union this with their own identity-token set.
        """
        from qprogram.protocol import measurement_field_token  # ruff: ignore[import-outside-top-level]

        return {measurement_field_token(f) for f in self.fields}


def normalize_fields(value: Iterable[MeasurementField | str]) -> tuple[str, ...]:
    """Coerce a ``fields`` argument into the canonical, sorted ``tuple[str, ...]``.

    Accepts any iterable of `MeasurementField` members or registered field-name strings —
    ``(MeasurementField.IQ, MeasurementField.STATE)`` and ``["iq", "state"]`` are equivalent. A bare
    string is **rejected**: iterating one would yield its characters, and there is no comma-separated
    ``"iq,state"`` spelling.

    Every name is checked against the live capability registry, so a typo raises *here* — at the
    ``measure(...)`` call site — instead of surfacing later as an unsupported-capability diagnostic.
    Vendors widen the accepted set by registering ``measure.fields.<name>`` (see
    [`qprogram.protocol.register_capability_tokens`][qprogram.register_capability_tokens]).

    The result is deduplicated and sorted into canonical order (`MeasurementField`
    declaration order, then vendor names alphabetically), so two measurements requesting the same
    data compare equal, hash equal, and serialize identically regardless of argument order.

    Args:
        value (Iterable[MeasurementField | str]): Iterable of `MeasurementField` members or
            registered field-name strings.

    Returns:
        Canonical, deduplicated, sorted tuple of field names.

    Raises:
        ValidationError: If ``value`` is a bare string or not iterable, if any entry is not a
            string or is empty, if any name is not registered, or if no fields are requested.
    """
    if isinstance(value, str):
        raise ValidationError(_bare_string_message(value))
    try:
        items = list(value)
    except TypeError as e:
        msg = f"`fields` must be an iterable of MeasurementField values, got {value!r}"
        raise ValidationError(msg) from e

    names: list[str] = []
    for item in items:
        if not isinstance(item, str):
            msg = (
                f"`fields` entries must be MeasurementField values or field-name strings, got "
                f"{item!r} of type {type(item).__name__!r}"
            )
            raise ValidationError(msg)
        # ``str(item)`` flattens both StrEnum members and the parser's _QuotedStr marker down to a
        # plain ``str``, so storage is uniform no matter how the caller spelled the field.
        name = str(item)
        if not name:
            msg = "`fields` entries must be non-empty field names"
            raise ValidationError(msg)
        names.append(name)

    if not names:
        msg = (
            f"`fields` must request at least one measurement field; valid values are "
            f"{[str(f) for f in MeasurementField]} (plus any vendor-registered names)"
        )
        raise ValidationError(msg)

    _reject_unknown_fields(names)
    return tuple(sorted(dict.fromkeys(names), key=_field_sort_key))


def _bare_string_message(value: str) -> str:
    """Build the error message that rewrites a bare ``fields="iq,state"`` into ``fields=("iq", "state")``.

    The comma-separated names in the rejected string are turned into the tuple literal the caller
    should have passed, so the fix is visible in the error itself.

    Args:
        value (str): The bare string the caller passed as ``fields``.

    Returns:
        The error message, ending in the suggested ``fields=(...)`` literal.
    """
    parts = [p.strip() for p in value.split(",")]
    suggestion = ", ".join(f'"{p}"' for p in parts if p)
    literal = f"({suggestion},)" if len(parts) == 1 else f"({suggestion})"
    return (
        f"`fields` must be an iterable of MeasurementField values, not the bare string {value!r} — "
        f"iterating a string yields its characters, and the comma-separated form is not accepted. "
        f"Pass a tuple: fields={literal}"
    )


_CORE_FIELD_ORDER: dict[str, int] = {name: i for i, name in enumerate(MeasurementField)}
"""Canonical sort position of each core field, taken from `MeasurementField`'s declaration
order. Keys are enum members; plain-string lookups hit them because ``StrEnum`` members hash and
compare as their values."""


def _field_sort_key(name: str) -> tuple[int, int, str]:
    """Return the sort key placing core fields first in declaration order, then vendor fields.

    The key defines a *total* order over any name set, which is what makes the canonical tuple
    independent of the order the caller passed.

    Args:
        name (str): A measurement field name, core or vendor-registered.

    Returns:
        A ``(group, core_index, vendor_name)`` triple: core fields sort as
        ``(0, declaration position, "")``, vendor fields as ``(1, 0, name)``.
    """
    index = _CORE_FIELD_ORDER.get(name)
    return (0, index, "") if index is not None else (1, 0, name)


def _reject_unknown_fields(names: Sequence[str]) -> None:
    """Raise on any field name that isn't a registered ``measure.fields.<name>`` capability token.

    Args:
        names (Sequence[str]): The field names to check against the live capability registry.

    Raises:
        ValidationError: On the first unknown name, with a close-match suggestion when there is one.
    """
    from qprogram.protocol import known_measurement_fields  # ruff: ignore[import-outside-top-level]

    known = known_measurement_fields()
    unknown = [n for n in names if n not in known]
    if not unknown:
        return
    import difflib  # ruff: ignore[import-outside-top-level]  # only paid on the error path

    close = difflib.get_close_matches(unknown[0], sorted(known), n=1)
    hint = f" Did you mean {close[0]!r}?" if close else ""
    msg = (
        f"unknown measurement field(s) {unknown!r}.{hint} Known fields: {sorted(known)}. A vendor "
        f"extension adds its own by registering `measure.fields.<name>` via "
        f"qprogram.protocol.register_capability_tokens."
    )
    raise ValidationError(msg)


def _collect_variables(value: object) -> set[Variable]:
    """Recursively gather every [`Variable`][qprogram.Variable] reachable from ``value``.

    Used by `Operation.variables`. Tolerates the arbitrary attribute shapes vendor extensions
    might invent: anything that isn't a Variable, Expression, Waveform, or list/tuple is skipped.

    Args:
        value (object): Any operation attribute value — a variable, an expression tree, a waveform
            whose parameters may be symbolic, a list or tuple of those, or something unrelated.

    Returns:
        Every variable found, deduplicated; an empty set for values that hold none.
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
