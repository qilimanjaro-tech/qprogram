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
"""Typed bus references for QProgram.

The [`BusSchema`][qprogram.BusSchema] API lets users reference buses by typed accessors (e.g. ``schema.q[0].drive``)
rather than raw strings. [`BusRef`][qprogram.BusRef] subclasses `str` so the resulting value is a string
everywhere downstream (AST, serialization, compiler) — the typing is purely ergonomic.

Presets ([`BusSchema.transmon`][qprogram.BusSchema.transmon] and friends) return fully-typed subclasses with IDE
autocomplete; dynamic schemas ([`BusSchema.add_element`][qprogram.BusSchema.add_element]) use ``__getattr__`` and trade
typing for flexibility. See the user guide for examples.
"""

from __future__ import annotations

from typing import ClassVar, Literal, Self

ChannelType = Literal["single", "IQ"]


class BusRef(str):
    """A string that also carries structured bus metadata.

    A ``BusRef`` is a real `str` everywhere downstream — operations, serialization, compiler —
    but exposes metadata attributes for tooling and validation. The ``idx`` attribute is named that way
    rather than ``index`` to avoid shadowing the inherited `str.index` method.

    The constructor takes the resolved bus name as the string value, followed by the metadata fields
    below. Refs normally come from a schema accessor (``schema.q[0].drive``) or from
    `resolve_ref`; constructing one directly is for buses that live outside any schema.

    Attributes:
        element (str): Element name (e.g. ``"q"``, ``"coupler"``).
        idx (int | tuple[int, ...]): Element index (e.g. ``0`` or ``(0, 1)``).
        kind (str): Bus kind name (e.g. ``"drive"``, ``"flux"``, ``"readout"``).
        channel (ChannelType): ``"single"`` for real-valued waveforms, ``"IQ"`` for complex I/Q.
        acquires (bool): ``True`` if the bus has an ADC and supports [`QProgram.measure`][qprogram.QProgram.measure].
        schema (BusSchema | None): The [`BusSchema`][qprogram.BusSchema] that produced this ref, or ``None`` for
            manually-built refs. Used by `QProgram._validate_bus` to reject buses from a
            different schema than the one attached to the program.
    """

    # __slots__ is required for str subclasses to carry attributes; an empty tuple would forbid
    # assignment outright (str has no __dict__).
    __slots__ = ("acquires", "channel", "element", "idx", "kind", "schema")

    element: str
    idx: int | tuple[int, ...]
    kind: str
    channel: ChannelType
    acquires: bool
    schema: BusSchema | None

    def __new__(  # ruff: ignore[too-many-arguments]  flat constructor — six metadata fields plus the str value
        cls,
        value: str,
        element: str,
        idx: int | tuple[int, ...],
        kind: str,
        channel: ChannelType,
        acquires: bool,
        schema: BusSchema | None = None,
    ) -> Self:
        """Build a bus reference whose string content is ``value``.

        The remaining arguments are the metadata fields documented on the class. Leaving ``schema``
        out marks the ref as having no producing schema, which makes
        `QProgram._validate_bus` accept it on a program bound to any schema.

        Returns:
            The new reference, carrying its metadata as attributes.
        """
        instance = super().__new__(cls, value)
        instance.element = element
        instance.idx = idx
        instance.kind = kind
        instance.channel = channel
        instance.acquires = acquires
        instance.schema = schema
        return instance

    def __reduce__(
        self,
    ) -> tuple[type[Self], tuple[str, str, int | tuple[int, ...], str, ChannelType, bool, BusSchema | None]]:
        """Return the reconstruction recipe for `pickle` and `copy.deepcopy`.

        The override is needed because ``str.__reduce_ex__`` passes only the string value to
        ``__new__``, while `BusRef.__new__` requires the metadata fields too. ``schema`` travels
        through the same path, so a deepcopy points at the *deepcopied* schema instance (the memo dict
        shares that one copy across every ref in the program).

        Returns:
            A ``(class, args)`` pair that rebuilds the reference with its metadata intact.
        """
        return (type(self), (str(self), self.element, self.idx, self.kind, self.channel, self.acquires, self.schema))


class BusNaming:
    """Configurable bus-name format string.

    The default ``"{element}{index}/{kind}"`` produces ``"q0/drive"``, ``"coupler0_1/flux"``, etc.
    Platforms with entrenched naming conventions can supply their own, e.g.
    ``"{kind}_{element}{index}_bus"`` for ``"drive_q0_bus"``.

    Args:
        pattern (str): Format string. Supported placeholders: ``{element}``, ``{index}``, ``{kind}``.
    """

    DEFAULT_PATTERN = "{element}{index}/{kind}"

    def __init__(self, pattern: str = DEFAULT_PATTERN) -> None:
        self.pattern = pattern

    def resolve(self, element: str, index: int | tuple, kind: str) -> str:
        """Format a bus name from its component pieces.

        Args:
            element (str): Element name, substituted for ``{element}``.
            index (int | tuple): Element index, substituted for ``{index}``. A tuple is joined with
                underscores, so ``(0, 1)`` becomes ``0_1``.
            kind (str): Bus kind name, substituted for ``{kind}``.

        Returns:
            The bus name produced by this naming's pattern.

        Raises:
            KeyError: If the pattern names a placeholder other than ``{element}``, ``{index}`` or
                ``{kind}``.
            ValueError: If the pattern is not a well-formed format string, or applies a format
                specification the substituted text cannot satisfy — every piece is substituted as
                text, so ``{index:d}`` fails.
            IndexError: If the pattern uses a positional placeholder such as ``{0}``; the three
                pieces are supplied by keyword only.
        """
        idx_str = "_".join(str(i) for i in index) if isinstance(index, tuple) else str(index)
        return self.pattern.format(element=element, index=idx_str, kind=kind)


# ---------------------------------------------------------------------------
# Internal helpers for dynamic (add_element) schemas
# ---------------------------------------------------------------------------


class ElementSchema:
    """An element type and the bus kinds it exposes.

    Every schema records one of these per element registered through
    [`BusSchema.add_element`][qprogram.BusSchema.add_element], whether the schema is a typed preset or built
    dynamically; the ``.qp`` writer emits the ``schema:`` block from them.

    Args:
        name (str): Element name (e.g. ``"q"``).
        buses (dict[str, tuple[ChannelType, bool]]): Mapping of bus kind to ``(channel, acquires)``.
            Same data that ends up on the resulting [`BusRef`][qprogram.BusRef], declared once per element kind.
        naming (BusNaming): The naming convention used when resolving [`BusRef`][qprogram.BusRef] strings.
    """

    def __init__(
        self,
        name: str,
        buses: dict[str, tuple[ChannelType, bool]],
        naming: BusNaming,
    ) -> None:
        self.name = name
        self.buses = buses
        self.naming = naming

    @property
    def bus_names(self) -> list[str]:
        """The registered bus kind names, in declaration order."""
        return list(self.buses.keys())


class _DynamicElementAccessor:
    """Bus accessor for dynamically-built schemas — resolves bus kinds via ``__getattr__``."""

    def __init__(self, schema: ElementSchema, index: int | tuple, parent: BusSchema) -> None:
        self._schema = schema
        self._index = index
        self._parent = parent

    def __getattr__(self, kind: str) -> BusRef:
        if kind.startswith("_"):
            raise AttributeError(kind)
        if kind not in self._schema.buses:
            available = ", ".join(self._schema.bus_names)
            msg = f"'{self._schema.name}' has no bus '{kind}'. Available: {available}"
            raise AttributeError(msg)
        channel, acquires = self._schema.buses[kind]
        raw = self._schema.naming.resolve(self._schema.name, self._index, kind)
        return BusRef(
            raw,
            element=self._schema.name,
            idx=self._index,
            kind=kind,
            channel=channel,
            acquires=acquires,
            schema=self._parent,
        )

    def __repr__(self) -> str:
        buses = ", ".join(f".{b}({info})" for b, info in self._schema.buses.items())
        return f"{self._schema.name}[{self._index}] ({buses})"


class _DynamicElementFactory:
    """Subscriptable element factory for dynamically-built schemas."""

    def __init__(self, schema: ElementSchema, parent: BusSchema) -> None:
        self._schema = schema
        self._parent = parent

    def __getitem__(self, index: int | tuple) -> _DynamicElementAccessor:
        return _DynamicElementAccessor(self._schema, index, self._parent)

    def __repr__(self) -> str:
        buses = ", ".join(f"{b}: {info}" for b, info in self._schema.buses.items())
        return f"ElementFactory('{self._schema.name}', buses={{{buses}}})"


# ---------------------------------------------------------------------------
# Typed element accessors for presets (IDE autocomplete works)
# ---------------------------------------------------------------------------


class _TypedElementAccessor:
    """Base class for typed element accessors. Concrete subclasses add per-bus ``@property`` accessors."""

    def __init__(self, element: str, index: int | tuple, naming: BusNaming, parent: BusSchema) -> None:
        self._element = element
        self._index = index
        self._naming = naming
        self._parent = parent

    def _ref(self, kind: str, channel: ChannelType, *, acquires: bool = False) -> BusRef:
        raw = self._naming.resolve(self._element, self._index, kind)
        return BusRef(
            raw,
            element=self._element,
            idx=self._index,
            kind=kind,
            channel=channel,
            acquires=acquires,
            schema=self._parent,
        )

    def __repr__(self) -> str:
        return f"{self._element}[{self._index}]"


class _TypedElementFactory:
    """Base class for typed element factories. Concrete subclasses specify `_accessor_cls`."""

    _accessor_cls: type[_TypedElementAccessor]

    def __init__(self, element: str, naming: BusNaming, parent: BusSchema) -> None:
        self._element = element
        self._naming = naming
        self._parent = parent

    def __getitem__(self, index: int) -> _TypedElementAccessor:
        return self._accessor_cls(self._element, index, self._naming, self._parent)


# --- Transmon qubit buses: drive (IQ), readout (IQ, acquires) ---


class TransmonQubitBuses(_TypedElementAccessor):
    """Typed accessor for transmon qubit buses (``drive``, ``readout``)."""

    @property
    def drive(self) -> BusRef:
        """The drive bus (IQ channel)."""
        return self._ref("drive", "IQ")

    @property
    def readout(self) -> BusRef:
        """The readout bus (IQ channel, acquires)."""
        return self._ref("readout", "IQ", acquires=True)


class TransmonQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning `TransmonQubitBuses` instances."""

    _accessor_cls = TransmonQubitBuses

    def __getitem__(self, index: int) -> TransmonQubitBuses:
        return TransmonQubitBuses(self._element, index, self._naming, self._parent)


class FluxTunableTransmonQubitBuses(TransmonQubitBuses):
    """Typed accessor for flux-tunable transmon qubit buses (``drive``, ``readout``, ``flux``)."""

    @property
    def flux(self) -> BusRef:
        """The flux bus (single channel)."""
        return self._ref("flux", "single")


class FluxTunableTransmonQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning `FluxTunableTransmonQubitBuses` instances."""

    _accessor_cls = FluxTunableTransmonQubitBuses

    def __getitem__(self, index: int) -> FluxTunableTransmonQubitBuses:
        return FluxTunableTransmonQubitBuses(self._element, index, self._naming, self._parent)


class FluxoniumQubitBuses(TransmonQubitBuses):
    """Typed accessor for fluxonium qubit buses (``drive``, ``readout``, ``flux_x``, ``flux_z``)."""

    @property
    def flux_x(self) -> BusRef:
        """The X-axis flux bus (single channel)."""
        return self._ref("flux_x", "single")

    @property
    def flux_z(self) -> BusRef:
        """The Z-axis flux bus (single channel)."""
        return self._ref("flux_z", "single")


class FluxoniumQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning `FluxoniumQubitBuses` instances."""

    _accessor_cls = FluxoniumQubitBuses

    def __getitem__(self, index: int) -> FluxoniumQubitBuses:
        return FluxoniumQubitBuses(self._element, index, self._naming, self._parent)


class CouplerBuses(_TypedElementAccessor):
    """Typed accessor for coupler buses (``flux``)."""

    @property
    def flux(self) -> BusRef:
        """The coupler flux bus (single channel)."""
        return self._ref("flux", "single")


class CouplerFactory(_TypedElementFactory):
    """Subscriptable factory returning `CouplerBuses` instances. Indices may be tuples."""

    _accessor_cls = CouplerBuses

    def __getitem__(self, index: int | tuple) -> CouplerBuses:
        return CouplerBuses(self._element, index, self._naming, self._parent)


# ---------------------------------------------------------------------------
# BusSchema: main class
# ---------------------------------------------------------------------------


def _coerce_to_schema_instance(value: object) -> BusSchema | None:
    """Return ``value`` as a [`BusSchema`][qprogram.BusSchema] instance, or ``None`` if it isn't one.

    Accepts a [`BusSchema`][qprogram.BusSchema] instance (returned as-is) or a [`BusSchema`][qprogram.BusSchema]
    *subclass* (instantiated with default naming). Anything else returns ``None`` so the ``+`` operators can yield
    ``NotImplemented`` and let Python raise the usual ``TypeError``. Used by both the instance-level `BusSchema.__add__`
    and the class-level `_BusSchemaMeta.__add__` so ``A + B`` behaves the same whether ``A`` / ``B`` are schema classes
    or instances.

    Args:
        value (object): The operand to coerce.

    Returns:
        A schema instance, or ``None`` when ``value`` is neither a schema nor a schema subclass.
    """
    if isinstance(value, BusSchema):
        return value
    if isinstance(value, type) and issubclass(value, BusSchema):
        return value()
    return None


class _BusSchemaMeta(type):
    """Metaclass that lets two schema *classes* be combined with ``+``.

    Defining ``+`` between classes (``FluxTunableTransmonSchema + RFSwitchSchema``) requires the
    operator to live on the metaclass — ``__add__`` on the class body only governs instances. This
    mirrors `BusSchema.__add__` so the class form and the instance form
    (``FluxTunableTransmonSchema() + RFSwitchSchema()``) produce the *same* result: a new combined
    [`BusSchema`][qprogram.BusSchema] instance. The metaclass adds nothing else, so construction, ``isinstance`` and
    ``issubclass`` are unaffected.
    """

    def __add__(cls, other: object) -> BusSchema:
        """Combine two schema classes into one dynamic schema instance.

        Args:
            other (object): Right-hand operand: a [`BusSchema`][qprogram.BusSchema] instance or subclass.

        Returns:
            A combined schema instance, or ``NotImplemented`` for an operand that is neither.

        Raises:
            ValueError: If the two schemas disagree on naming, or define the same element name with
                different buses.
        """
        left = _coerce_to_schema_instance(cls)
        right = _coerce_to_schema_instance(other)
        if left is None or right is None:
            return NotImplemented  # type: ignore[return-value]
        return BusSchema.combine(left, right)


class BusSchema(metaclass=_BusSchemaMeta):
    """The bus kinds each element kind on a chip exposes.

    Three construction modes:

    1. **Presets** — `transmon`, `fluxonium`, etc. return fully-typed subclasses with IDE
       autocomplete.
    2. **Dynamic** — instantiate [`BusSchema`][qprogram.BusSchema] directly and call `add_element` for custom
       topologies. Bus access via ``schema.q[0].drive`` works at runtime but has no static type.
    3. **Custom typed** — subclass [`BusSchema`][qprogram.BusSchema] to expose your own typed accessors; see the user
       guide for the template.

    Schemas **compose**: ``schema_a + schema_b`` (or `combine` for three or more, or for naming
    control) returns a new schema with the union of both element families. Either operand may be a
    schema instance or a schema class, e.g. ``FluxTunableTransmonSchema + RFSwitchSchema``. The result
    is a plain (dynamic) [`BusSchema`][qprogram.BusSchema] — runtime access like ``combined.q[0].drive`` works, but it
    carries no static typing (the same trade-off as `add_element`). Build refs from the
    *combined* schema, not the originals, so their `BusRef.schema` back-pointer matches the
    schema you attach to a program.

    Each [`QProgram`][qprogram.QProgram] holds at most one schema — passed at construction, or adopted
    from the first schema-backed ref the program sees. The ``.qp`` writer reads ``program.schema`` —
    not the individual refs' `BusRef.schema` back-pointers — both to emit the ``schema:`` block
    and to decide that a bus renders as an ``element[idx].kind`` path. A ref's own back-pointer
    records which schema produced it, which is what lets a program refuse a ref from a foreign schema
    and what routes the bus to its per-bus capability profile.

    Args:
        naming (BusNaming | None): Naming convention for the bus strings this schema resolves. When
            omitted, a default [`BusNaming`][qprogram.BusNaming] is used.

    Attributes:
        KIND (str): Class-level identifier set by built-in presets (``"transmon"``, ``"fluxonium"``,
            ...). Informational; user subclasses may set their own. A combined schema keeps the base
            ``""``.
    """

    KIND: ClassVar[str] = ""

    def __init__(self, naming: BusNaming | None = None) -> None:
        self._naming = naming or BusNaming()
        self._elements: dict[str, ElementSchema] = {}

    @property
    def naming(self) -> BusNaming:
        """The [`BusNaming`][qprogram.BusNaming] this schema resolves bus strings through."""
        return self._naming

    @property
    def elements(self) -> dict[str, ElementSchema]:
        """The registered element schemas, keyed by element name."""
        return self._elements

    def add_element(self, name: str, buses: dict[str, tuple[ChannelType, bool]]) -> None:
        """Register an element type and its available bus kinds.

        For statically-typed schemas, subclass [`BusSchema`][qprogram.BusSchema] and expose ``@property`` accessors
        rather than using this method.

        Args:
            name (str): Element name (e.g. ``"q"``, ``"resonator"``). Registering a name twice
                replaces the earlier declaration.
            buses (dict[str, tuple[ChannelType, bool]]): Mapping of bus kind to
                ``(channel, acquires)``. For example::

                {"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)}
        """
        self._elements[name] = ElementSchema(name=name, buses=buses, naming=self._naming)

    def __getattr__(self, name: str) -> _DynamicElementFactory:
        """Resolve ``schema.<element>`` to a subscriptable factory for that element's buses.

        This is the dynamic access path: it answers for every element registered through
        `add_element`, which is why a combined or dynamically-built schema supports
        ``schema.q[0].drive`` without any typed accessor.

        Args:
            name (str): Element name to look up.

        Returns:
            A factory that yields a bus accessor when subscripted with an element index.

        Raises:
            AttributeError: If ``name`` names no registered element, in which case the message lists
                the ones that are registered; or if it starts with an underscore, so that attribute
                probing by `copy` and `pickle` is not mistaken for an element lookup.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._elements:
            return _DynamicElementFactory(self._elements[name], self)
        available = ", ".join(self._elements.keys())
        msg = f"No element '{name}' in schema. Available: {available}"
        raise AttributeError(msg)

    def __repr__(self) -> str:
        parts = []
        for name, schema in self._elements.items():
            buses = ", ".join(f"{b}: {info}" for b, info in schema.buses.items())
            parts.append(f"{name}({buses})")
        return f"BusSchema({', '.join(parts)})"

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def __add__(self, other: object) -> BusSchema:
        """Combine this schema with another via ``schema_a + schema_b``.

        Args:
            other (object): Right-hand operand: a [`BusSchema`][qprogram.BusSchema] instance or subclass.

        Returns:
            A new combined schema (see `combine`), or ``NotImplemented`` for an unrelated
            operand, which makes Python raise the usual ``TypeError``.

        Raises:
            ValueError: If the two schemas disagree on naming, or define the same element name with
                different buses.
        """
        right = _coerce_to_schema_instance(other)
        if right is None:
            return NotImplemented  # type: ignore[return-value]
        return BusSchema.combine(self, right)

    @staticmethod
    def combine(*schemas: BusSchema, naming: BusNaming | None = None) -> BusSchema:
        """Merge schemas into a new dynamic [`BusSchema`][qprogram.BusSchema].

        The result holds the **union** of every input schema's elements, so passing a single schema
        yields a dynamic copy of it. It is a plain
        [`BusSchema`][qprogram.BusSchema] (not a typed subclass), so ``combined.q[0].drive`` resolves at runtime but
        without static typing — the same trade-off as building a schema with `add_element`.
        The ``+`` operator (`__add__`, and the class-level form via the metaclass) delegates
        here; use ``combine`` directly when joining three or more schemas in one call or when you need
        to pick the naming convention explicitly.

        Args:
            *schemas (BusSchema): The schemas to merge (at least one). Each must be a
                [`BusSchema`][qprogram.BusSchema] instance.
            naming (BusNaming | None): Naming convention for the combined schema. When omitted,
                every input schema must share the same naming pattern (a combined schema can carry
                only one) — they usually do, since the default is universal. Pass an explicit
                ``naming`` to resolve a clash.

        Returns:
            A new [`BusSchema`][qprogram.BusSchema] whose ``elements`` are the union of the inputs'.

        Raises:
            ValueError: If no schemas are given, if the inputs disagree on naming and none is given,
                or if two inputs define the *same* element name with *different* buses (an ambiguous
                merge — rename one element). Re-declaring an identical element is allowed (idempotent).
        """
        if not schemas:
            msg = "BusSchema.combine() requires at least one schema"
            raise ValueError(msg)
        if naming is None:
            patterns = {s.naming.pattern for s in schemas}
            if len(patterns) > 1:
                msg = (
                    f"cannot combine schemas with different naming patterns {sorted(patterns)}; "
                    f"pass naming=BusNaming(...) to BusSchema.combine() to choose one explicitly"
                )
                raise ValueError(msg)
            naming = BusNaming(next(iter(patterns)))
        combined = BusSchema(naming=naming)
        for schema in schemas:
            for name, element in schema.elements.items():
                existing = combined.elements.get(name)
                if existing is not None:
                    if existing.buses != element.buses:
                        msg = (
                            f"cannot combine schemas: element {name!r} is defined differently "
                            f"({existing.buses} vs {element.buses}); rename one element before combining"
                        )
                        raise ValueError(msg)
                    continue  # identical element already merged — idempotent
                combined.add_element(name, dict(element.buses))
        return combined

    # ------------------------------------------------------------------
    # Presets — return fully typed subclasses
    # ------------------------------------------------------------------

    @classmethod
    def transmon(cls, naming: BusNaming | None = None) -> TransmonSchema:
        """Return a schema for fixed-frequency transmon qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing a ``q`` qubit accessor.
        """
        return TransmonSchema(naming=naming)

    @classmethod
    def transmon_coupled(cls, naming: BusNaming | None = None) -> TransmonCoupledSchema:
        """Return a schema for fixed-frequency transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires). Coupler buses: ``flux`` (single).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing ``q`` qubit and ``c`` coupler accessors.
        """
        return TransmonCoupledSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon(cls, naming: BusNaming | None = None) -> FluxTunableTransmonSchema:
        """Return a schema for flux-tunable transmon qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing a ``q`` qubit accessor.
        """
        return FluxTunableTransmonSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon_coupled(cls, naming: BusNaming | None = None) -> FluxTunableTransmonCoupledSchema:
        """Return a schema for flux-tunable transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).
        Coupler buses: ``flux`` (single).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing ``q`` qubit and ``c`` coupler accessors.
        """
        return FluxTunableTransmonCoupledSchema(naming=naming)

    @classmethod
    def fluxonium(cls, naming: BusNaming | None = None) -> FluxoniumSchema:
        """Return a schema for fluxonium qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux_x`` (single), ``flux_z`` (single).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing a ``q`` qubit accessor.
        """
        return FluxoniumSchema(naming=naming)

    @classmethod
    def fluxonium_coupled(cls, naming: BusNaming | None = None) -> FluxoniumCoupledSchema:
        """Return a schema for fluxonium qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux_x`` (single), ``flux_z`` (single).
        Coupler buses: ``flux`` (single).

        Args:
            naming (BusNaming | None): Custom naming convention for the resolved bus strings.

        Returns:
            A typed schema exposing ``q`` qubit and ``c`` coupler accessors.
        """
        return FluxoniumCoupledSchema(naming=naming)


# ---------------------------------------------------------------------------
# Typed schema classes for presets
# ---------------------------------------------------------------------------


class TransmonSchema(BusSchema):
    """Typed schema for fixed-frequency transmon qubits — exposes a typed ``q`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})

    @property
    def q(self) -> TransmonQubitFactory:
        """The qubit factory: ``schema.q[0]`` exposes that qubit's ``drive`` and ``readout`` buses."""
        return TransmonQubitFactory("q", self._naming, self)


class TransmonCoupledSchema(TransmonSchema):
    """Typed schema for transmon qubits plus couplers — adds a typed ``c`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        """The coupler factory: ``schema.c[0, 1]`` exposes that coupler's ``flux`` bus."""
        return CouplerFactory("c", self._naming, self)


class FluxTunableTransmonSchema(BusSchema):
    """Typed schema for flux-tunable transmon qubits — exposes a typed ``q`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "flux_tunable_transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)})

    @property
    def q(self) -> FluxTunableTransmonQubitFactory:
        """The qubit factory: ``schema.q[0]`` exposes that qubit's ``drive``, ``readout`` and ``flux`` buses."""
        return FluxTunableTransmonQubitFactory("q", self._naming, self)


class FluxTunableTransmonCoupledSchema(FluxTunableTransmonSchema):
    """Typed schema for flux-tunable transmon qubits plus couplers — adds a typed ``c`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "flux_tunable_transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        """The coupler factory: ``schema.c[0, 1]`` exposes that coupler's ``flux`` bus."""
        return CouplerFactory("c", self._naming, self)


class FluxoniumSchema(BusSchema):
    """Typed schema for fluxonium qubits — exposes a typed ``q`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "fluxonium"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element(
            "q",
            {
                "drive": ("IQ", False),
                "readout": ("IQ", True),
                "flux_x": ("single", False),
                "flux_z": ("single", False),
            },
        )

    @property
    def q(self) -> FluxoniumQubitFactory:
        """The qubit factory: ``schema.q[0]`` exposes that qubit's drive, readout and two flux buses."""
        return FluxoniumQubitFactory("q", self._naming, self)


class FluxoniumCoupledSchema(FluxoniumSchema):
    """Typed schema for fluxonium qubits plus couplers — adds a typed ``c`` accessor.

    Args:
        naming (BusNaming | None): Custom naming convention for the resolved bus strings.
    """

    KIND = "fluxonium_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        """The coupler factory: ``schema.c[0, 1]`` exposes that coupler's ``flux`` bus."""
        return CouplerFactory("c", self._naming, self)


# ---------------------------------------------------------------------------
# Structural re-resolution — shared by the parser and QProgram.rebind
# ---------------------------------------------------------------------------


def resolve_ref(schema: BusSchema, element: str, index: int | tuple[int, ...], kind: str) -> BusRef:
    """Re-resolve an ``(element, index, kind)`` coordinate into a typed [`BusRef`][qprogram.BusRef].

    The single source of truth for turning a structural bus coordinate into a [`BusRef`][qprogram.BusRef] under
    ``schema``'s naming. Used both when loading ``element[i].kind`` paths
    (`_resolve_bus_path`) and when porting a program to
    new indices or a new schema ([`rebind`][qprogram.QProgram.rebind]).

    Args:
        schema (BusSchema): The schema to resolve the coordinate against; it becomes the ref's
            `BusRef.schema` back-pointer.
        element (str): Element name, as registered on ``schema``.
        index (int | tuple[int, ...]): Element index, a tuple for multi-index elements such as
            couplers.
        kind (str): Bus kind name declared for that element.

    Returns:
        A typed [`BusRef`][qprogram.BusRef] whose string form follows ``schema``'s naming pattern.

    Raises:
        AttributeError: If ``element`` is not an element of ``schema`` or ``kind`` is not one of that
            element's bus kinds.
        KeyError: If ``schema``'s naming pattern names a placeholder other than ``{element}``,
            ``{index}`` or ``{kind}``.
        ValueError: If ``schema``'s naming pattern is not a well-formed format string.
        IndexError: If ``schema``'s naming pattern uses a positional placeholder such as ``{0}``.
    """
    factory = getattr(schema, element)
    accessor = factory[index]
    return getattr(accessor, kind)


def naming_substituted_schema(schema: BusSchema, naming: BusNaming) -> BusSchema:
    """Return a dynamic copy of ``schema`` with every element re-declared under ``naming``.

    Used by [`rebind`][qprogram.QProgram.rebind] for naming-only ports: the structural element/bus content
    is preserved but [`BusRef`][qprogram.BusRef] strings (and the serialized ``schema:`` block) adopt the new
    pattern. The result is a plain (untyped) [`BusSchema`][qprogram.BusSchema] — the same trade-off as
    [`BusSchema.combine`][qprogram.BusSchema.combine].

    Args:
        schema (BusSchema): The schema whose elements and bus kinds are carried over.
        naming (BusNaming): The naming convention the copy resolves bus strings through.

    Returns:
        A new dynamic [`BusSchema`][qprogram.BusSchema] with the same elements under the given naming.
    """
    new_schema = BusSchema(naming=naming)
    for name, element in schema.elements.items():
        new_schema.add_element(name, dict(element.buses))
    return new_schema
