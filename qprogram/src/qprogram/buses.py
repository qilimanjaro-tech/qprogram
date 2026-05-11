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

from dataclasses import dataclass
from typing import Literal, Self

ChannelType = Literal["single", "IQ"]


@dataclass(frozen=True)
class BusInfo:
    """Properties of a bus type within an element.

    Attributes:
        channel: "single" for real-valued waveforms, "IQ" for complex I/Q waveforms.
        acquires: Whether this bus has an ADC and supports ``measure()`` operations.
    """

    channel: ChannelType = "single"
    acquires: bool = False

    def __repr__(self) -> str:
        parts: list[str] = [self.channel]
        if self.acquires:
            parts.append("acquires")
        return f"BusInfo({', '.join(parts)})"


# Convenience constants for common bus configurations
IQ = BusInfo(channel="IQ")
IQ_ACQUIRES = BusInfo(channel="IQ", acquires=True)
SINGLE = BusInfo(channel="single")


class BusRef(str):
    """A string that also carries structured bus metadata.

    Subclasses ``str`` so it's fully compatible everywhere a bus name string is expected
    (operations, serialization, compiler, etc.), but also provides metadata attributes
    for tooling, introspection, and validation.

    Attributes:
        element: Element name (e.g. "q", "coupler").
        index: Element index (e.g. 0, (0, 1)).
        type: Bus type name (e.g. "drive", "flux").
        info: BusInfo with channel type, acquires flag, etc.
    """

    # Declare slots so a ``str`` subclass can still carry these attributes.
    # An empty ``__slots__ = ()`` would forbid attribute assignment entirely
    # (str has no __dict__).
    __slots__ = ("element", "index", "info", "type")

    def __new__(
        cls,
        value: str,
        element: str = "",
        index: int | tuple = 0,
        type: str = "",  # noqa: A002  shadowing builtin is intentional — attribute is named `type`
        info: BusInfo | None = None,
    ) -> Self:
        instance = super().__new__(cls, value)
        instance.element = element
        instance.index = index
        instance.type = type
        instance.info = info or BusInfo()
        return instance

    @property
    def channel_type(self) -> ChannelType:
        return self.info.channel  # ty:ignore[unresolved-attribute]

    @property
    def acquires(self) -> bool:
        return self.info.acquires  # ty:ignore[unresolved-attribute]


class BusNaming:
    """Configurable naming convention for bus strings.

    The default pattern produces ``"q0/drive"``, ``"coupler0_1/flux"``, etc.
    Platforms can supply their own pattern, e.g. ``"{bus_type}_{element}{index}_bus"``
    to produce ``"drive_q0_bus"``.
    """

    DEFAULT_PATTERN = "{element}{index}/{bus_type}"

    def __init__(self, pattern: str = DEFAULT_PATTERN) -> None:
        self.pattern = pattern

    def resolve(self, element: str, index: int | tuple, bus_type: str) -> str:
        idx_str = "_".join(str(i) for i in index) if isinstance(index, tuple) else str(index)
        return self.pattern.format(element=element, index=idx_str, bus_type=bus_type)


# ---------------------------------------------------------------------------
# Internal helpers for dynamic (add_element) schemas
# ---------------------------------------------------------------------------


class ElementSchema:
    """Describes a type of element and its available bus types (for dynamic schemas)."""

    def __init__(self, name: str, buses: dict[str, BusInfo], naming: BusNaming) -> None:
        self.name = name
        self.buses = buses
        self.naming = naming

    @property
    def bus_names(self) -> list[str]:
        return list(self.buses.keys())


class _DynamicElementAccessor:
    """Dynamic bus accessor — uses __getattr__, no static typing."""

    def __init__(self, schema: ElementSchema, index: int | tuple) -> None:
        self._schema = schema
        self._index = index

    def __getattr__(self, bus_type: str) -> BusRef:
        if bus_type.startswith("_"):
            raise AttributeError(bus_type)
        if bus_type not in self._schema.buses:
            available = ", ".join(self._schema.bus_names)
            msg = f"'{self._schema.name}' has no bus '{bus_type}'. Available: {available}"
            raise AttributeError(msg)
        info = self._schema.buses[bus_type]
        raw = self._schema.naming.resolve(self._schema.name, self._index, bus_type)
        return BusRef(raw, element=self._schema.name, index=self._index, type=bus_type, info=info)

    def __repr__(self) -> str:
        buses = ", ".join(f".{b}({info})" for b, info in self._schema.buses.items())
        return f"{self._schema.name}[{self._index}] ({buses})"


class _DynamicElementFactory:
    """Dynamic element factory — subscriptable, no static typing."""

    def __init__(self, schema: ElementSchema) -> None:
        self._schema = schema

    def __getitem__(self, index: int | tuple) -> _DynamicElementAccessor:
        return _DynamicElementAccessor(self._schema, index)

    def __repr__(self) -> str:
        buses = ", ".join(f"{b}: {info}" for b, info in self._schema.buses.items())
        return f"ElementFactory('{self._schema.name}', buses={{{buses}}})"


# ---------------------------------------------------------------------------
# Typed element accessors for presets (IDE autocomplete works)
# ---------------------------------------------------------------------------


class _TypedElementAccessor:
    """Base for typed element accessors. Subclasses add bus properties."""

    def __init__(self, element: str, index: int | tuple, naming: BusNaming) -> None:
        self._element = element
        self._index = index
        self._naming = naming

    def _ref(self, bus_type: str, info: BusInfo) -> BusRef:
        raw = self._naming.resolve(self._element, self._index, bus_type)
        return BusRef(raw, element=self._element, index=self._index, type=bus_type, info=info)

    def __repr__(self) -> str:
        return f"{self._element}[{self._index}]"


class _TypedElementFactory:
    """Base for typed element factories. Subclasses specify the accessor type."""

    _accessor_cls: type[_TypedElementAccessor]

    def __init__(self, element: str, naming: BusNaming) -> None:
        self._element = element
        self._naming = naming

    def __getitem__(self, index: int) -> _TypedElementAccessor:
        return self._accessor_cls(self._element, index, self._naming)


# --- Transmon qubit buses: drive (IQ), readout (IQ, acquires) ---


class TransmonQubitBuses(_TypedElementAccessor):
    """Bus accessor for transmon qubits: drive, readout."""

    @property
    def drive(self) -> BusRef:
        """Drive line (IQ channel)."""
        return self._ref("drive", IQ)

    @property
    def readout(self) -> BusRef:
        """Readout line (IQ channel, acquires)."""
        return self._ref("readout", IQ_ACQUIRES)


class TransmonQubitFactory(_TypedElementFactory):
    """Factory for transmon qubit bus accessors."""

    _accessor_cls = TransmonQubitBuses

    def __getitem__(self, index: int) -> TransmonQubitBuses:
        return TransmonQubitBuses(self._element, index, self._naming)


# --- Flux-tunable transmon qubit buses: drive, readout, flux ---


class FluxTunableTransmonQubitBuses(TransmonQubitBuses):
    """Bus accessor for flux-tunable transmon qubits: drive, readout, flux."""

    @property
    def flux(self) -> BusRef:
        """Flux line (single channel)."""
        return self._ref("flux", SINGLE)


class FluxTunableTransmonQubitFactory(_TypedElementFactory):
    _accessor_cls = FluxTunableTransmonQubitBuses

    def __getitem__(self, index: int) -> FluxTunableTransmonQubitBuses:
        return FluxTunableTransmonQubitBuses(self._element, index, self._naming)


# --- Fluxonium qubit buses: drive, readout, flux_x, flux_z ---


class FluxoniumQubitBuses(TransmonQubitBuses):
    """Bus accessor for fluxonium qubits: drive, readout, flux_x, flux_z."""

    @property
    def flux_x(self) -> BusRef:
        """Flux X line (single channel)."""
        return self._ref("flux_x", SINGLE)

    @property
    def flux_z(self) -> BusRef:
        """Flux Z line (single channel)."""
        return self._ref("flux_z", SINGLE)


class FluxoniumQubitFactory(_TypedElementFactory):
    _accessor_cls = FluxoniumQubitBuses

    def __getitem__(self, index: int) -> FluxoniumQubitBuses:
        return FluxoniumQubitBuses(self._element, index, self._naming)


# --- Coupler buses: flux ---


class CouplerBuses(_TypedElementAccessor):
    """Bus accessor for couplers: flux."""

    @property
    def flux(self) -> BusRef:
        """Coupler flux line (single channel)."""
        return self._ref("flux", SINGLE)


class CouplerFactory(_TypedElementFactory):
    _accessor_cls = CouplerBuses

    def __getitem__(self, index: int | tuple) -> CouplerBuses:
        return CouplerBuses(self._element, index, self._naming)


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
        schema.add_element("q", buses={"drive": IQ, "readout": IQ_ACQUIRES})
        schema.q[0].drive  # works at runtime, but IDE doesn't know about .q

    3. **Custom typed** — subclass for your own qubit types (see example below).
    """

    def __init__(self, naming: BusNaming | None = None) -> None:
        self._naming = naming or BusNaming()
        self._elements: dict[str, ElementSchema] = {}

    def add_element(self, name: str, buses: dict[str, BusInfo]) -> None:
        """Register an element type with its bus types and properties (dynamic, untyped).

        For typed schemas, subclass BusSchema and add properties instead.
        """
        self._elements[name] = ElementSchema(name=name, buses=buses, naming=self._naming)

    def __getattr__(self, name: str) -> _DynamicElementFactory:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._elements:
            return _DynamicElementFactory(self._elements[name])
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

    @property
    def q(self) -> TransmonQubitFactory:
        return TransmonQubitFactory("q", self._naming)


class TransmonCoupledSchema(TransmonSchema):
    """Typed schema for transmon qubits + couplers. Adds ``c`` property."""

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming)


class FluxTunableTransmonSchema(BusSchema):
    """Typed schema for flux-tunable transmon qubits."""

    @property
    def q(self) -> FluxTunableTransmonQubitFactory:
        return FluxTunableTransmonQubitFactory("q", self._naming)


class FluxTunableTransmonCoupledSchema(FluxTunableTransmonSchema):
    """Typed schema for flux-tunable transmon qubits + couplers."""

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming)


class FluxoniumSchema(BusSchema):
    """Typed schema for fluxonium qubits."""

    @property
    def q(self) -> FluxoniumQubitFactory:
        return FluxoniumQubitFactory("q", self._naming)


class FluxoniumCoupledSchema(FluxoniumSchema):
    """Typed schema for fluxonium qubits + couplers."""

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming)
