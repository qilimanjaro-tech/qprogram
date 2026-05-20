"""Registries that drive the ``.qp`` serializer and parser.

The serialization layer is intentionally schema-free in its grammar: there are
no hard-coded keyword lists for operations, blocks, or sweep generators. Every
extension point — core operations, vendor operations, control-flow blocks,
sweep sources for ``for ... in <generator>`` — is a registered entry here. The
writer and parser dispatch through these registries instead of through
``isinstance`` chains or per-keyword branches.

Three registries cover the language surface:

- :data:`_operation_specs_by_class` / ``_by_qualified`` — operations, keyed both
  by class (for serialization) and by ``(vendor, name)`` (for parsing). The
  ``vendor`` slot is ``None`` for core operations.
- :data:`_block_specs_by_name` / ``_by_class`` — control-flow blocks whose
  header is a single keyword followed by optional arguments
  (``average 1000:``, ``block:``). Loops live in a separate registry below
  because their header is keyword-led by ``for`` and parameterised by a sweep
  source.
- :data:`_sweep_generator_specs_by_name` / ``_by_class`` — sweep sources used
  inside ``for <var> in <generator>``. Maps both directions: name → spec for
  parsing, block class → spec for writing. Built-in entries are ``range``
  (produces :class:`ForLoop`), ``values`` (the ``[...]`` literal, produces
  :class:`Loop`), and ``file`` (produces :class:`Loop`).

Two additional registries — waveforms (keyed by class name) and vendor
protocol versions — remain as they were; the new APIs build on top.

Vendor extensions continue to use :func:`register_vendor_operation` and
:func:`register_vendor_version` from their own ``__init__.py``; nothing about
their integration changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from qprogram._reserved import RESERVED_VENDOR_NAMES

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.waveforms import IQWaveform, Waveform

# Callback signatures are intentionally type-erased: the registry stores
# callbacks for concrete subclasses (e.g. ``sync_serialize`` takes ``Sync``,
# not ``Operation``) and the writer/parser is the only call site. Typing
# them as ``Callable[[Operation, Any], str]`` would force every registration
# to cast or accept a wider base; ``Any`` lets the natural per-class signature
# survive and keeps callbacks readable.
OperationSerializeFn = Callable[[Any, Any], str]
OperationParseFn = Callable[[list[str], Any], Any]
BlockSerializeHeaderFn = Callable[[Any, Any], str]
BlockParseHeaderFn = Callable[[list[str], Any], Any]
SweepParseFn = Callable[[Any, str, Any], Any]
SweepWriteFn = Callable[[Any, Any], str]


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationSpec:
    """Serialization metadata for one operation (core or vendor).

    ``vendor`` is ``None`` for core operations; otherwise it is the vendor
    name used in the dot-prefix on the wire (``qblox.acquire``). ``serialize``
    and ``parse`` may be ``None``, in which case the default callbacks based
    on ``inspect.signature`` are used.
    """

    name: str
    vendor: str | None
    cls: type[Operation]
    serialize: OperationSerializeFn | None = None
    parse: OperationParseFn | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.vendor}.{self.name}" if self.vendor else self.name


@dataclass(frozen=True)
class BlockSpec:
    """Serialization metadata for a control-flow block whose header is keyword-led.

    Loop-like blocks (``for var in <generator>``) are not registered here —
    they live in the sweep generator registry, which maps both names (for
    parsing) and classes (for writing).

    ``serialize_header`` takes the block and a write context and returns the
    header text *without* the trailing ``:`` (e.g. ``"average 1000"``). The
    default emits the bare keyword.

    ``parse_header`` takes the tokens after the keyword and a parse context
    and returns a freshly-constructed block. The default invokes ``cls()``
    with no arguments.
    """

    name: str
    cls: type[Block]
    serialize_header: BlockSerializeHeaderFn | None = None
    parse_header: BlockParseHeaderFn | None = None


@dataclass(frozen=True)
class SweepGeneratorSpec:
    """Serialization metadata for a sweep source used in ``for <var> in <gen>``.

    ``parse`` builds a block from the textual arguments of the generator;
    ``write`` reconstructs those arguments from the block instance. The link
    between the directions is the block class — exactly one sweep generator
    owns each loop block class on the write side.

    ``write`` may be ``None`` for parse-only generators that don't have a
    natural serialized form. For example, ``file("path.npy")`` reads values
    into a :class:`Loop`, but the loaded values are then written back as a
    ``[...]`` literal via the ``values`` generator — the file path is not
    retained on the AST. Such generators contribute their parse side only and
    do not appear in the by-class lookup.
    """

    name: str
    block_cls: type[Block]
    parse: SweepParseFn
    write: SweepWriteFn | None = None


# ---------------------------------------------------------------------------
# Registry storage
# ---------------------------------------------------------------------------


_operation_specs_by_qualified: dict[tuple[str | None, str], OperationSpec] = {}
_operation_specs_by_class: dict[type, OperationSpec] = {}

_block_specs_by_name: dict[str, BlockSpec] = {}
_block_specs_by_class: dict[type, BlockSpec] = {}

_sweep_generator_specs_by_name: dict[str, SweepGeneratorSpec] = {}
_sweep_generator_specs_by_class: dict[type, SweepGeneratorSpec] = {}

# Maps waveform class name -> class
_waveform_registry: dict[str, type[Waveform | IQWaveform]] = {}

# Maps vendor name -> declared protocol version (semver string, e.g. "0.1.0")
_vendor_versions: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Registration — operations
# ---------------------------------------------------------------------------


def register_operation(
    name: str,
    cls: type[Operation],
    *,
    vendor: str | None = None,
    serialize: OperationSerializeFn | None = None,
    parse: OperationParseFn | None = None,
) -> type[Operation]:
    """Register an operation for ``.qp`` serialization.

    ``name`` is the keyword used in the file (``play``, ``measure``, …). For
    vendor extensions, pass ``vendor="qblox"`` and the file writes
    ``qblox.<name>``. ``serialize`` and ``parse`` are optional overrides; when
    omitted, the default signature-driven callbacks handle the operation.

    The same class can only be registered once — registering a second time
    overwrites the previous entry, which is what you want for hot-reload but
    note that the *old* (vendor, name) key remains in the by-qualified map.

    ``vendor`` cannot be ``"core"`` or any of
    :data:`~qprogram.RESERVED_KEYWORDS`. Core operations register with
    ``vendor=None`` (the default) and emit unprefixed on the wire; the
    ``"core"`` string is reserved as a sentinel and the keyword set is
    reserved against namespace collisions with future syntax. Operation
    *names* themselves are unrestricted — future block keywords like
    ``if`` register on the block registry, not here.
    """
    if vendor is not None and vendor in RESERVED_VENDOR_NAMES:
        msg = (
            f"vendor name {vendor!r} is reserved (see qprogram.RESERVED_KEYWORDS "
            f"plus the 'core' sentinel); pick a different namespace for this "
            f"vendor extension"
        )
        raise ValueError(msg)
    spec = OperationSpec(name=name, vendor=vendor, cls=cls, serialize=serialize, parse=parse)
    _operation_specs_by_qualified[(vendor, name)] = spec
    _operation_specs_by_class[cls] = spec
    return cls


def register_vendor_operation(
    vendor: str,
    name: str,
    cls: type[Operation],
    *,
    serialize: OperationSerializeFn | None = None,
    parse: OperationParseFn | None = None,
) -> None:
    """Convenience wrapper used by vendor extension packages.

    Equivalent to ``register_operation(name, cls, vendor=vendor, ...)``.
    Optional ``serialize`` / ``parse`` callbacks are forwarded — vendors
    that ship measurement ops, for example, pass
    :func:`qprogram.serialization._specs.measurement_op_parse` here so
    the parser produces a canonical handle instance shared with any
    :class:`~qprogram.MeasurementRef`.
    """
    register_operation(name, cls, vendor=vendor, serialize=serialize, parse=parse)


# ---------------------------------------------------------------------------
# Registration — blocks
# ---------------------------------------------------------------------------


def register_block(
    name: str,
    cls: type[Block],
    *,
    serialize_header: BlockSerializeHeaderFn | None = None,
    parse_header: BlockParseHeaderFn | None = None,
) -> type[Block]:
    """Register a control-flow block for ``.qp`` serialization.

    ``name`` is the leading keyword in the block header (``average``,
    ``block``). The body parser handles indentation and child statements
    uniformly across all registered blocks. Loops are *not* registered here —
    they use the sweep generator registry instead.
    """
    spec = BlockSpec(name=name, cls=cls, serialize_header=serialize_header, parse_header=parse_header)
    _block_specs_by_name[name] = spec
    _block_specs_by_class[cls] = spec
    return cls


# ---------------------------------------------------------------------------
# Registration — sweep generators
# ---------------------------------------------------------------------------


def register_sweep_generator(
    name: str,
    cls: type[Block],
    *,
    parse: SweepParseFn,
    write: SweepWriteFn | None = None,
) -> type[Block]:
    """Register a sweep source for ``for <var> in <name>(...)``.

    ``parse`` receives the declared variable, the textual argument string
    (between parentheses), and a parse context, and returns a
    fully-constructed loop block (e.g. a :class:`ForLoop` or :class:`Loop`).

    ``write`` receives that same block and a write context and returns the
    textual generator expression (e.g. ``"range(0, 1, 0.01)"``). It may be
    ``None`` for parse-only generators (see :class:`SweepGeneratorSpec`); in
    that case the generator is not registered as the writer for its block
    class, leaving room for another generator to own the write side.

    Two generators may share a block class: typically one with a ``write``
    function (the canonical write form) and one or more parse-only siblings.
    For example, ``values`` writes ``Loop`` as ``[...]``; ``file`` parses
    ``file("...")`` into a ``Loop`` but writes back through ``values``.
    """
    spec = SweepGeneratorSpec(name=name, block_cls=cls, parse=parse, write=write)
    _sweep_generator_specs_by_name[name] = spec
    if write is not None:
        _sweep_generator_specs_by_class[cls] = spec
    return cls


# ---------------------------------------------------------------------------
# Registration — waveforms and vendor versions (unchanged)
# ---------------------------------------------------------------------------


def register_waveform(cls: type[Waveform | IQWaveform]) -> type:
    """Decorator to register a waveform type for serialization."""
    _waveform_registry[cls.__name__] = cls
    return cls


def register_vendor_version(vendor: str, version: str) -> None:
    """Register the protocol version of an installed vendor extension.

    Args:
        vendor: Vendor name as used in the dot-notation operations (e.g. "qblox").
        version: Semver string (e.g. "0.1.0"). Major.minor is what counts for
            compatibility; patch is informational.
    """
    _vendor_versions[vendor] = version


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_operation_spec(vendor: str | None, name: str) -> OperationSpec | None:
    return _operation_specs_by_qualified.get((vendor, name))


def get_operation_spec_by_class(cls: type) -> OperationSpec | None:
    return _operation_specs_by_class.get(cls)


def get_block_spec(name: str) -> BlockSpec | None:
    return _block_specs_by_name.get(name)


def get_block_spec_by_class(cls: type) -> BlockSpec | None:
    return _block_specs_by_class.get(cls)


def get_sweep_generator_spec(name: str) -> SweepGeneratorSpec | None:
    return _sweep_generator_specs_by_name.get(name)


def get_sweep_generator_spec_by_class(cls: type) -> SweepGeneratorSpec | None:
    return _sweep_generator_specs_by_class.get(cls)


def get_waveform_class(name: str) -> type[Waveform | IQWaveform] | None:
    return _waveform_registry.get(name)


def get_vendor_version(vendor: str) -> str | None:
    """Return the registered protocol version of an installed vendor, or None."""
    return _vendor_versions.get(vendor)


# ---------------------------------------------------------------------------
# Legacy compatibility shims (kept for direct callers; prefer the new APIs)
# ---------------------------------------------------------------------------


def get_operation_class(vendor: str, name: str) -> type | None:
    spec = _operation_specs_by_qualified.get((vendor, name))
    return spec.cls if spec is not None else None


def get_operation_vendor_name(cls: type) -> tuple[str, str] | None:
    spec = _operation_specs_by_class.get(cls)
    if spec is None or spec.vendor is None:
        return None
    return (spec.vendor, spec.name)


# ---------------------------------------------------------------------------
# Built-in waveforms
# ---------------------------------------------------------------------------


def _register_builtin_waveforms() -> None:
    from qprogram.waveforms import (  # noqa: PLC0415
        Arbitrary,
        Chained,
        FlatTop,
        Gaussian,
        GaussianDragCorrection,
        IQDrag,
        IQPair,
        Ramp,
        Square,
        SuddenNetZero,
    )

    for cls in [
        Square,
        Gaussian,
        GaussianDragCorrection,
        Ramp,
        FlatTop,
        SuddenNetZero,
        Arbitrary,
        Chained,
        IQPair,
        IQDrag,
    ]:
        _waveform_registry[cls.__name__] = cls


_register_builtin_waveforms()
