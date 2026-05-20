"""Typed bus references for QProgram.

The :class:`BusSchema` API lets users reference buses by typed accessors (e.g. ``schema.q[0].drive``)
rather than raw strings. :class:`BusRef` subclasses :class:`str` so the resulting value is a string
everywhere downstream (AST, serialization, compiler) — the typing is purely ergonomic.

Presets (:meth:`BusSchema.transmon` and friends) return fully-typed subclasses with IDE autocomplete;
dynamic schemas (:meth:`BusSchema.add_element`) use ``__getattr__`` and trade typing for flexibility.
See the user guide for examples.
"""

from __future__ import annotations

from typing import ClassVar, Literal, Self

ChannelType = Literal["single", "IQ"]


class BusRef(str):
    """A string that also carries structured bus metadata.

    A ``BusRef`` is a real :class:`str` everywhere downstream — operations, serialization, compiler —
    but exposes metadata attributes for tooling and validation. The ``idx`` attribute is named that way
    rather than ``index`` to avoid shadowing the inherited :meth:`str.index` method.

    Attributes:
        element: Element name (e.g. ``"q"``, ``"coupler"``).
        idx: Element index (e.g. ``0`` or ``(0, 1)``).
        kind: Bus kind name (e.g. ``"drive"``, ``"flux"``, ``"readout"``).
        channel: ``"single"`` for real-valued waveforms, ``"IQ"`` for complex I/Q.
        acquires: ``True`` if the bus has an ADC and supports :meth:`QProgram.measure`.
        schema: The :class:`BusSchema` that produced this ref, or ``None`` for manually-built refs. Used
            by :meth:`QProgram._validate_bus` to reject buses from a different schema than the one
            attached to the program.
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

    def __new__(  # noqa: PLR0913  flat constructor — six metadata fields plus the str value
        cls,
        value: str,
        element: str,
        idx: int | tuple[int, ...],
        kind: str,
        channel: ChannelType,
        acquires: bool,
        schema: BusSchema | None = None,
    ) -> Self:
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
        """Return the reconstruction recipe for :mod:`pickle` and :func:`copy.deepcopy`.

        Why this override exists: ``str.__reduce_ex__`` only passes the string value to ``__new__``, but
        our ``__new__`` requires the metadata fields too. ``schema`` flows through the same path so a
        deepcopy points to the *deepcopied* schema instance (the memo dict shares the same copy
        across BusRefs).
        """
        return (type(self), (str(self), self.element, self.idx, self.kind, self.channel, self.acquires, self.schema))


class BusNaming:
    """Configurable bus-name format string.

    The default ``"{element}{index}/{kind}"`` produces ``"q0/drive"``, ``"coupler0_1/flux"``, etc.
    Platforms with entrenched naming conventions can supply their own, e.g.
    ``"{kind}_{element}{index}_bus"`` for ``"drive_q0_bus"``.

    Args:
        pattern: Format string. Supported placeholders: ``{element}``, ``{index}``, ``{kind}``.
    """

    DEFAULT_PATTERN = "{element}{index}/{kind}"

    def __init__(self, pattern: str = DEFAULT_PATTERN) -> None:
        self.pattern = pattern

    def resolve(self, element: str, index: int | tuple, kind: str) -> str:
        """Format a bus name from its component pieces, joining tuple indices with underscores."""
        idx_str = "_".join(str(i) for i in index) if isinstance(index, tuple) else str(index)
        return self.pattern.format(element=element, index=idx_str, kind=kind)


# ---------------------------------------------------------------------------
# Internal helpers for dynamic (add_element) schemas
# ---------------------------------------------------------------------------


class ElementSchema:
    """Describes an element type and its available bus kinds — used by dynamic schemas.

    Args:
        name: Element name (e.g. ``"q"``).
        buses: Mapping of bus kind to ``(channel, acquires)``. Same data that ends up on the resulting
            :class:`BusRef`, declared once per element kind.
        naming: The naming convention used when resolving :class:`BusRef` strings.
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
        """Return the registered bus kind names."""
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
    """Base class for typed element factories. Concrete subclasses specify :attr:`_accessor_cls`."""

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
        """Return the drive bus (IQ channel)."""
        return self._ref("drive", "IQ")

    @property
    def readout(self) -> BusRef:
        """Return the readout bus (IQ channel, acquires)."""
        return self._ref("readout", "IQ", acquires=True)


class TransmonQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning :class:`TransmonQubitBuses` instances."""

    _accessor_cls = TransmonQubitBuses

    def __getitem__(self, index: int) -> TransmonQubitBuses:
        return TransmonQubitBuses(self._element, index, self._naming, self._parent)


class FluxTunableTransmonQubitBuses(TransmonQubitBuses):
    """Typed accessor for flux-tunable transmon qubit buses (``drive``, ``readout``, ``flux``)."""

    @property
    def flux(self) -> BusRef:
        """Return the flux bus (single channel)."""
        return self._ref("flux", "single")


class FluxTunableTransmonQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning :class:`FluxTunableTransmonQubitBuses` instances."""

    _accessor_cls = FluxTunableTransmonQubitBuses

    def __getitem__(self, index: int) -> FluxTunableTransmonQubitBuses:
        return FluxTunableTransmonQubitBuses(self._element, index, self._naming, self._parent)


class FluxoniumQubitBuses(TransmonQubitBuses):
    """Typed accessor for fluxonium qubit buses (``drive``, ``readout``, ``flux_x``, ``flux_z``)."""

    @property
    def flux_x(self) -> BusRef:
        """Return the X-axis flux bus (single channel)."""
        return self._ref("flux_x", "single")

    @property
    def flux_z(self) -> BusRef:
        """Return the Z-axis flux bus (single channel)."""
        return self._ref("flux_z", "single")


class FluxoniumQubitFactory(_TypedElementFactory):
    """Subscriptable factory returning :class:`FluxoniumQubitBuses` instances."""

    _accessor_cls = FluxoniumQubitBuses

    def __getitem__(self, index: int) -> FluxoniumQubitBuses:
        return FluxoniumQubitBuses(self._element, index, self._naming, self._parent)


class CouplerBuses(_TypedElementAccessor):
    """Typed accessor for coupler buses (``flux``)."""

    @property
    def flux(self) -> BusRef:
        """Return the coupler flux bus (single channel)."""
        return self._ref("flux", "single")


class CouplerFactory(_TypedElementFactory):
    """Subscriptable factory returning :class:`CouplerBuses` instances. Indices may be tuples."""

    _accessor_cls = CouplerBuses

    def __getitem__(self, index: int | tuple) -> CouplerBuses:
        return CouplerBuses(self._element, index, self._naming, self._parent)


# ---------------------------------------------------------------------------
# BusSchema: main class
# ---------------------------------------------------------------------------


class BusSchema:
    """Declares the bus types for each element kind on a chip.

    Three construction modes:

    1. **Presets** — :meth:`transmon`, :meth:`fluxonium`, etc. return fully-typed subclasses with IDE
       autocomplete.
    2. **Dynamic** — instantiate :class:`BusSchema` directly and call :meth:`add_element` for custom
       topologies. Bus access via ``schema.q[0].drive`` works at runtime but has no static type.
    3. **Custom typed** — subclass :class:`BusSchema` to expose your own typed accessors; see the user
       guide for the template.

    Each :class:`~qprogram.QProgram` holds at most one schema, passed at construction. The ``.qp``
    writer reads ``program.schema`` to format bus references; there is no per-bus back-pointer.

    Attributes:
        KIND: Class-level identifier set by built-in presets (``"transmon"``, ``"fluxonium"``, ...).
            Used by the ``.qp`` writer when emitting the schema header. User subclasses should set
            their own.
    """

    KIND: ClassVar[str] = ""

    def __init__(self, naming: BusNaming | None = None) -> None:
        self._naming = naming or BusNaming()
        self._elements: dict[str, ElementSchema] = {}

    @property
    def naming(self) -> BusNaming:
        """Return the :class:`BusNaming` used by this schema."""
        return self._naming

    @property
    def elements(self) -> dict[str, ElementSchema]:
        """Return a read-only view of the registered element schemas."""
        return self._elements

    def add_element(self, name: str, buses: dict[str, tuple[ChannelType, bool]]) -> None:
        """Register an element type and its available bus kinds.

        For statically-typed schemas, subclass :class:`BusSchema` and expose ``@property`` accessors
        rather than using this method.

        Args:
            name: Element name (e.g. ``"q"``, ``"resonator"``).
            buses: Mapping of bus kind to ``(channel, acquires)``. For example::

                {"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)}
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

        Args:
            naming: Optional custom naming convention.
        """
        return TransmonSchema(naming=naming)

    @classmethod
    def transmon_coupled(cls, naming: BusNaming | None = None) -> TransmonCoupledSchema:
        """Schema for fixed-frequency transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires). Coupler buses: ``flux`` (single).

        Args:
            naming: Optional custom naming convention.
        """
        return TransmonCoupledSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon(cls, naming: BusNaming | None = None) -> FluxTunableTransmonSchema:
        """Schema for flux-tunable transmon qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).

        Args:
            naming: Optional custom naming convention.
        """
        return FluxTunableTransmonSchema(naming=naming)

    @classmethod
    def flux_tunable_transmon_coupled(cls, naming: BusNaming | None = None) -> FluxTunableTransmonCoupledSchema:
        """Schema for flux-tunable transmon qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux`` (single).
        Coupler buses: ``flux`` (single).

        Args:
            naming: Optional custom naming convention.
        """
        return FluxTunableTransmonCoupledSchema(naming=naming)

    @classmethod
    def fluxonium(cls, naming: BusNaming | None = None) -> FluxoniumSchema:
        """Schema for fluxonium qubits (no couplers).

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux_x`` (single), ``flux_z`` (single).

        Args:
            naming: Optional custom naming convention.
        """
        return FluxoniumSchema(naming=naming)

    @classmethod
    def fluxonium_coupled(cls, naming: BusNaming | None = None) -> FluxoniumCoupledSchema:
        """Schema for fluxonium qubits with couplers.

        Qubit buses: ``drive`` (IQ), ``readout`` (IQ, acquires), ``flux_x`` (single), ``flux_z`` (single).
        Coupler buses: ``flux`` (single).

        Args:
            naming: Optional custom naming convention.
        """
        return FluxoniumCoupledSchema(naming=naming)


# ---------------------------------------------------------------------------
# Typed schema classes for presets
# ---------------------------------------------------------------------------


class TransmonSchema(BusSchema):
    """Typed schema for fixed-frequency transmon qubits — exposes a typed ``q`` accessor."""

    KIND = "transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})

    @property
    def q(self) -> TransmonQubitFactory:
        return TransmonQubitFactory("q", self._naming, self)


class TransmonCoupledSchema(TransmonSchema):
    """Typed schema for transmon qubits plus couplers — adds a typed ``c`` accessor."""

    KIND = "transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)


class FluxTunableTransmonSchema(BusSchema):
    """Typed schema for flux-tunable transmon qubits — exposes a typed ``q`` accessor."""

    KIND = "flux_tunable_transmon"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True), "flux": ("single", False)})

    @property
    def q(self) -> FluxTunableTransmonQubitFactory:
        return FluxTunableTransmonQubitFactory("q", self._naming, self)


class FluxTunableTransmonCoupledSchema(FluxTunableTransmonSchema):
    """Typed schema for flux-tunable transmon qubits plus couplers."""

    KIND = "flux_tunable_transmon_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)


class FluxoniumSchema(BusSchema):
    """Typed schema for fluxonium qubits — exposes a typed ``q`` accessor."""

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
    """Typed schema for fluxonium qubits plus couplers."""

    KIND = "fluxonium_coupled"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("c", {"flux": ("single", False)})

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming, self)
