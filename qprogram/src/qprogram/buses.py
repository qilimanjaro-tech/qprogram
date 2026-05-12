"""Typed bus reference system for QProgram.

Users reference hardware buses (drive lines, readout lines, flux lines, etc.) by name.
Different platforms and qubit types use different naming conventions and expose different
bus types. This module provides a typed, discoverable way to reference buses without
hardcoding strings, while keeping QProgram platform-agnostic at the AST level.

The schema defines *what bus types* each element has and their properties (channel type,
whether they support acquisition, etc.). It does NOT define how many qubits or couplers
exist — the user passes any index and the schema constructs the string.

The key design: ``BusRef`` subclasses ``str``, so it is a string everywhere in the AST,
serialization, and compiler. The typed API is pure user-facing ergonomics.

Typing approach:
- **Presets** (``BusSchema.transmon()``, etc.) return fully typed subclasses with
  explicit properties — IDE autocomplete and mypy work out of the box.
- **Custom schemas** (``add_element()``) use ``__getattr__`` — dynamic, no static typing.
- Users can define their own typed schemas by subclassing (see examples below).
"""

from __future__ import annotations

from typing import ClassVar, Literal, Self

ChannelType = Literal["single", "IQ"]


class BusRef(str):
    """A string that also carries structured bus metadata.

    Subclasses ``str`` so it's fully compatible everywhere a bus name string is expected
    (operations, serialization, compiler, etc.), but also provides metadata attributes
    for tooling, introspection, and validation.

    Attributes:
        element: Element name (e.g. "q", "coupler").
        index: Element index (e.g. 0, (0, 1)).
        kind: Bus kind name (e.g. "drive", "flux", "readout").
        channel: ``"single"`` for real-valued waveforms, ``"IQ"`` for complex I/Q.
        acquires: Whether this bus has an ADC and supports ``measure()`` operations.
        schema: The :class:`BusSchema` instance that produced this ref, or
            ``None`` if the BusRef was constructed manually outside any
            schema. Used by :meth:`~qprogram.QProgram._validate_bus` to
            reject buses that come from a different schema than the one
            attached to the program (which would otherwise serialize fine
            but mean something different on load).
    """

    # Declare slots so a ``str`` subclass can still carry these attributes.
    # An empty ``__slots__ = ()`` would forbid attribute assignment entirely
    # (str has no __dict__).
    __slots__ = ("acquires", "channel", "element", "index", "kind", "schema")

    def __new__(  # noqa: PLR0913  flat constructor — six metadata fields plus the str value
        cls,
        value: str,
        element: str,
        index: int | tuple[int, ...],
        kind: str,
        channel: ChannelType,
        acquires: bool,
        schema: BusSchema | None = None,
    ) -> Self:
        instance = super().__new__(cls, value)
        instance.element = element
        instance.index = index  # type: ignore[assignment]  # slot shadows str.index()
        instance.kind = kind
        instance.channel = channel
        instance.acquires = acquires
        instance.schema = schema
        return instance

    def __getnewargs__(  # ty:ignore[invalid-method-override]
        self,
    ) -> tuple[str, str, int | tuple[int, ...], str, ChannelType, bool, BusSchema | None]:
        """Reconstruction args for ``pickle`` and ``copy.deepcopy``.

        ``str.__reduce_ex__`` only supplies the string value to ``__new__`` by
        default, which would fail here because our ``__new__`` requires the
        metadata fields too. ``schema`` flows through the same path so the
        deepcopied BusRef points to the *deepcopied* schema instance (the
        memo dict shares the same copy across BusRefs).
        """
        return (str(self), self.element, self.index, self.kind, self.channel, self.acquires, self.schema)  # ty:ignore[invalid-return-type, unresolved-attribute]


class BusNaming:
    """Configurable naming convention for bus strings.

    The default pattern produces ``"q0/drive"``, ``"coupler0_1/flux"``, etc.
    Platforms can supply their own pattern, e.g. ``"{kind}_{element}{index}_bus"``
    to produce ``"drive_q0_bus"``. Supported placeholders: ``{element}``,
    ``{index}``, ``{kind}``.
    """

    DEFAULT_PATTERN = "{element}{index}/{kind}"

    def __init__(self, pattern: str = DEFAULT_PATTERN) -> None:
        self.pattern = pattern

    def resolve(self, element: str, index: int | tuple, kind: str) -> str:
        idx_str = "_".join(str(i) for i in index) if isinstance(index, tuple) else str(index)
        return self.pattern.format(element=element, index=idx_str, kind=kind)


# ---------------------------------------------------------------------------
# Internal helpers for dynamic (add_element) schemas
# ---------------------------------------------------------------------------


class ElementSchema:
    """Describes a type of element and its available bus types (for dynamic schemas).

    ``buses`` maps each bus name (e.g. ``"drive"``, ``"readout"``) to a
    ``(channel, acquires)`` tuple — the same data that lives on the
    eventual :class:`BusRef`, just declared once per element kind.
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
        return list(self.buses.keys())


class _DynamicElementAccessor:
    """Dynamic bus accessor — uses __getattr__, no static typing."""

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
            index=self._index,
            kind=kind,
            channel=channel,
            acquires=acquires,
            schema=self._parent,
        )

    def __repr__(self) -> str:
        buses = ", ".join(f".{b}({info})" for b, info in self._schema.buses.items())
        return f"{self._schema.name}[{self._index}] ({buses})"


class _DynamicElementFactory:
    """Dynamic element factory — subscriptable, no static typing."""

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
    """Base for typed element accessors. Subclasses add bus properties."""

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
            index=self._index,
            kind=kind,
            channel=channel,
            acquires=acquires,
            schema=self._parent,
        )

    def __repr__(self) -> str:
        return f"{self._element}[{self._index}]"


class _TypedElementFactory:
    """Base for typed element factories. Subclasses specify the accessor type."""

    _accessor_cls: type[_TypedElementAccessor]

    def __init__(self, element: str, naming: BusNaming, parent: BusSchema) -> None:
        self._element = element
        self._naming = naming
        self._parent = parent

    def __getitem__(self, index: int) -> _TypedElementAccessor:
        return self._accessor_cls(self._element, index, self._naming, self._parent)


# --- Transmon qubit buses: drive (IQ), readout (IQ, acquires) ---


class TransmonQubitBuses(_TypedElementAccessor):
    """Bus accessor for transmon qubits: drive, readout."""

    @property
    def drive(self) -> BusRef:
        """Drive line (IQ channel)."""
        return self._ref("drive", "IQ")

    @property
    def readout(self) -> BusRef:
        """Readout line (IQ channel, acquires)."""
        return self._ref("readout", "IQ", acquires=True)


class TransmonQubitFactory(_TypedElementFactory):
    """Factory for transmon qubit bus accessors."""

    _accessor_cls = TransmonQubitBuses

    def __getitem__(self, index: int) -> TransmonQubitBuses:
        return TransmonQubitBuses(self._element, index, self._naming, self._parent)


# --- Flux-tunable transmon qubit buses: drive, readout, flux ---


class FluxTunableTransmonQubitBuses(TransmonQubitBuses):
    """Bus accessor for flux-tunable transmon qubits: drive, readout, flux."""

    @property
    def flux(self) -> BusRef:
        """Flux line (single channel)."""
        return self._ref("flux", "single")


class FluxTunableTransmonQubitFactory(_TypedElementFactory):
    _accessor_cls = FluxTunableTransmonQubitBuses

    def __getitem__(self, index: int) -> FluxTunableTransmonQubitBuses:
        return FluxTunableTransmonQubitBuses(self._element, index, self._naming, self._parent)


# --- Fluxonium qubit buses: drive, readout, flux_x, flux_z ---


class FluxoniumQubitBuses(TransmonQubitBuses):
    """Bus accessor for fluxonium qubits: drive, readout, flux_x, flux_z."""

    @property
    def flux_x(self) -> BusRef:
        """Flux X line (single channel)."""
        return self._ref("flux_x", "single")

    @property
    def flux_z(self) -> BusRef:
        """Flux Z line (single channel)."""
        return self._ref("flux_z", "single")


class FluxoniumQubitFactory(_TypedElementFactory):
    _accessor_cls = FluxoniumQubitBuses

    def __getitem__(self, index: int) -> FluxoniumQubitBuses:
        return FluxoniumQubitBuses(self._element, index, self._naming, self._parent)


# --- Coupler buses: flux ---


class CouplerBuses(_TypedElementAccessor):
    """Bus accessor for couplers: flux."""

    @property
    def flux(self) -> BusRef:
        """Coupler flux line (single channel)."""
        return self._ref("flux", "single")


class CouplerFactory(_TypedElementFactory):
    _accessor_cls = CouplerBuses

    def __getitem__(self, index: int | tuple) -> CouplerBuses:
        return CouplerBuses(self._element, index, self._naming, self._parent)


# ---------------------------------------------------------------------------
# BusSchema: main class
# ---------------------------------------------------------------------------


class BusSchema:
    """Declares the bus types for each element kind on a chip.

    There are two ways to use BusSchema:

    1. **Presets** — return fully typed subclasses with IDE autocomplete::

        schema = BusSchema.transmon()
        schema.q[0].drive  # IDE knows this is BusRef, shows .drive and .readout
        schema.q[0].readout  # autocomplete works

    2. **Dynamic** — use ``add_element()`` for custom topologies (no static typing)::

        schema = BusSchema()
        schema.add_element("q", buses={
            "drive":   ("IQ", False),
            "readout": ("IQ", True),
        })
        schema.q[0].drive  # works at runtime, but IDE doesn't know about .q

    3. **Custom typed** — subclass for your own qubit types (see example below).

    Each :class:`~qprogram.QProgram` carries at most one ``BusSchema`` (set via
    its constructor). The ``.qp`` writer reads ``program.schema`` to decide
    how to emit each bus reference; there is no per-bus back-pointer.

    Attributes:
        KIND: Class-level identifier (e.g. ``"transmon"``). Built-in presets
            set this; user subclasses should set their own. Used by the ``.qp``
            writer to emit a one-liner ``schema: <KIND>`` for known presets.
    """

    KIND: ClassVar[str] = ""

    def __init__(self, naming: BusNaming | None = None) -> None:
        self._naming = naming or BusNaming()
        self._elements: dict[str, ElementSchema] = {}

    @property
    def naming(self) -> BusNaming:
        return self._naming

    @property
    def elements(self) -> dict[str, ElementSchema]:
        """Read-only view of registered element schemas (for serialization)."""
        return self._elements

    def add_element(self, name: str, buses: dict[str, tuple[ChannelType, bool]]) -> None:
        """Register an element type with its bus types and properties (dynamic, untyped).

        ``buses`` maps each bus name to a ``(channel, acquires)`` tuple, e.g.
        ``{"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)}``.

        For typed schemas, subclass BusSchema and add properties instead.
        """
        self._elements[name] = ElementSchema(name=name, buses=buses, naming=self._naming)

    def __getattr__(self, name: str) -> _DynamicElementFactory:
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
    # Presets — return fully typed subclasses
    # ------------------------------------------------------------------

    @classmethod
    def transmon(cls, naming: BusNaming | None = None) -> TransmonSchema:
        """Schema for fixed-frequency transmon qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires).
        """
        return TransmonSchema(naming=naming)

    @classmethod
    def transmon_coupled(cls, naming: BusNaming | None = None) -> TransmonCoupledSchema:
        """Schema for fixed-frequency transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires).
        Coupler buses: ``flux`` (single).
        """
        return TransmonCoupledSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon(cls, naming: BusNaming | None = None) -> FluxTunableTransmonSchema:
        """Schema for flux-tunable transmon qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).
        """
        return FluxTunableTransmonSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon_coupled(cls, naming: BusNaming | None = None) -> FluxTunableTransmonCoupledSchema:
        """Schema for flux-tunable transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).
        Coupler buses: ``flux`` (single).
        """
        return FluxTunableTransmonCoupledSchema(naming=naming)

    @classmethod
    def fluxonium(cls, naming: BusNaming | None = None) -> FluxoniumSchema:
        """Schema for fluxonium qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires),
        ``flux_x`` (single), ``flux_z`` (single).
        """
        return FluxoniumSchema(naming=naming)

    @classmethod
    def fluxonium_coupled(cls, naming: BusNaming | None = None) -> FluxoniumCoupledSchema:
        """Schema for fluxonium qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires),
        ``flux_x`` (single), ``flux_z`` (single).
        Coupler buses: ``flux`` (single).
        """
        return FluxoniumCoupledSchema(naming=naming)


# ---------------------------------------------------------------------------
# Typed schema classes for presets
# ---------------------------------------------------------------------------


class TransmonSchema(BusSchema):
    """Typed schema for transmon qubits. IDE sees ``q`` with full autocomplete."""

    KIND = "transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})

    @property
    def q(self) -> TransmonQubitFactory:
        return TransmonQubitFactory("q", self._naming, self)


class TransmonCoupledSchema(TransmonSchema):
    """Typed schema for transmon qubits + couplers. Adds ``c`` property."""

    KIND = "transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)


class FluxTunableTransmonSchema(BusSchema):
    """Typed schema for flux-tunable transmon qubits."""

    KIND = "flux_tunable_transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)})

    @property
    def q(self) -> FluxTunableTransmonQubitFactory:
        return FluxTunableTransmonQubitFactory("q", self._naming, self)


class FluxTunableTransmonCoupledSchema(FluxTunableTransmonSchema):
    """Typed schema for flux-tunable transmon qubits + couplers."""

    KIND = "flux_tunable_transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)


class FluxoniumSchema(BusSchema):
    """Typed schema for fluxonium qubits."""

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
        return FluxoniumQubitFactory("q", self._naming, self)


class FluxoniumCoupledSchema(FluxoniumSchema):
    """Typed schema for fluxonium qubits + couplers."""

    KIND = "fluxonium_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)


# Registry of built-in preset schemas, keyed by KIND. The ``.qp`` parser uses
# this to instantiate a preset from a one-liner ``schema: <kind>`` declaration.
_BUILTIN_PRESETS: dict[str, type[BusSchema]] = {
    TransmonSchema.KIND: TransmonSchema,
    TransmonCoupledSchema.KIND: TransmonCoupledSchema,
    FluxTunableTransmonSchema.KIND: FluxTunableTransmonSchema,
    FluxTunableTransmonCoupledSchema.KIND: FluxTunableTransmonCoupledSchema,
    FluxoniumSchema.KIND: FluxoniumSchema,
    FluxoniumCoupledSchema.KIND: FluxoniumCoupledSchema,
}


def get_preset_class(kind: str) -> type[BusSchema] | None:
    """Return the built-in preset class registered under ``kind``, or ``None``."""
    return _BUILTIN_PRESETS.get(kind)


def is_builtin_preset(schema: BusSchema) -> bool:
    """Return ``True`` if ``schema`` is an instance of a built-in preset class."""
    return type(schema) in _BUILTIN_PRESETS.values()
