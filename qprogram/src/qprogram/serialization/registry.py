"""Registries that drive the ``.qp`` serializer and parser.

The serialization grammar is schema-free: there are no hard-coded keyword lists for operations,
blocks, or sweep generators. Every extension point is a registered entry; the writer and parser
dispatch through the registries instead of ``isinstance`` chains.

The five registries:

- **Operations** (by class + by ``(vendor, name)``) — for write and parse respectively.
- **Blocks** (by name + by class) — keyword-led headers like ``average 1000:`` / ``block:``.
- **Sweep generators** (by name + by class) — sources for ``for <var> in <source>``; built-ins
  ``range``, ``values``, and ``file``.
- **Waveforms** by class name.
- **Vendor protocol versions** by vendor name.

Vendor extensions register through :func:`register_vendor_operation` and
:func:`register_vendor_version` at import time.
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

# Why these callback aliases use ``Any``: the registry stores per-subclass callbacks (e.g.
# ``sync_serialize`` takes a concrete ``Sync``, not ``Operation``). Tightening the alias to
# ``Callable[[Operation, ...]]`` would force every registration to cast or widen — ``Any`` lets the
# natural per-class signature survive.
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
    """Serialization metadata for one operation, core or vendor.

    Attributes:
        name: Operation name as it appears in ``.qp``.
        vendor: Vendor name for the dot-prefix (``<vendor>.<name>``), or ``None`` for core.
        cls: The :class:`Operation` subclass.
        serialize: Optional override; default uses ``inspect.signature``-driven serialization.
        parse: Optional override; default uses ``inspect.signature``-driven parsing.
    """

    name: str
    vendor: str | None
    cls: type[Operation]
    serialize: OperationSerializeFn | None = None
    parse: OperationParseFn | None = None

    @property
    def qualified_name(self) -> str:
        """Return ``name`` for core ops or ``"<vendor>.<name>"`` for vendor ops."""
        return f"{self.vendor}.{self.name}" if self.vendor else self.name


@dataclass(frozen=True)
class BlockSpec:
    """Serialization metadata for a keyword-led control-flow block.

    Loop-like blocks (``for var in <source>``) live in :class:`SweepGeneratorSpec` instead.

    Attributes:
        name: Header keyword as it appears in ``.qp`` (e.g. ``"average"``, ``"block"``).
        cls: The :class:`Block` subclass.
        serialize_header: Optional override returning the header text without the trailing ``:``
            (e.g. ``"average 1000"``). Default emits the bare keyword.
        parse_header: Optional override taking the post-keyword tokens; default invokes ``cls()`` with
            no arguments.
    """

    name: str
    cls: type[Block]
    serialize_header: BlockSerializeHeaderFn | None = None
    parse_header: BlockParseHeaderFn | None = None


@dataclass(frozen=True)
class SweepGeneratorSpec:
    """Serialization metadata for a sweep source used in ``for <var> in <gen>``.

    Why ``write`` may be ``None``: parse-only sources (e.g. ``file("path.npy")``) load into a
    :class:`Loop` whose values are written back as a ``[...]`` literal via the ``values`` generator.
    The file path is not preserved on the AST, so the source has no write side and doesn't appear in
    the by-class lookup.

    Attributes:
        name: Generator name in ``.qp`` (e.g. ``"range"``, ``"values"``).
        block_cls: The :class:`Block` subclass produced and consumed by this generator.
        parse: Builds a block instance from textual arguments.
        write: Reconstructs textual arguments from a block instance, or ``None`` for parse-only.
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

    Re-registering the same class overwrites the previous entry but leaves the old ``(vendor, name)``
    key in the by-qualified map — useful for hot-reload, surprising otherwise.

    Args:
        name: Keyword used in ``.qp`` (``play``, ``measure``, ...). Operation names are unrestricted;
            future block keywords like ``if`` register on the block registry instead.
        cls: The :class:`Operation` subclass.
        vendor: Vendor namespace. ``None`` registers a core op (emitted without prefix). Cannot be
            ``"core"`` or any :data:`~qprogram.RESERVED_KEYWORDS`.
        serialize: Optional override; default uses signature-driven serialization.
        parse: Optional override; default uses signature-driven parsing.

    Returns:
        ``cls``, so the function can be used as a decorator.

    Raises:
        ValueError: If ``vendor`` is reserved.
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
    """Register a keyword-led control-flow block for ``.qp`` serialization.

    The body parser handles indentation and child statements uniformly. Loops are not registered
    here — they use the sweep-generator registry instead.

    Args:
        name: Leading keyword in the block header (``average``, ``block``, ...).
        cls: The :class:`Block` subclass.
        serialize_header: Optional header-text override.
        parse_header: Optional header-token override.

    Returns:
        ``cls``, so the function can be used as a decorator.
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
    """Register a sweep source for the ``for <var> in <name>(...)`` grammar.

    Two generators may share a block class — typically one canonical writer and one or more
    parse-only siblings. For example, ``values`` writes :class:`Loop` as ``[...]``; ``file`` parses
    ``file("...")`` into a :class:`Loop` but writes back through ``values``.

    Args:
        name: Generator name (``range``, ``values``, ``file``, ...).
        cls: Loop block class produced (:class:`ForLoop` or :class:`Loop`).
        parse: Receives ``(variable, args_text, parse_ctx)`` and returns the constructed block.
        write: Receives ``(block, write_ctx)`` and returns the textual generator expression. ``None``
            for parse-only generators — they don't own the write side of their block class.

    Returns:
        ``cls``, so the function can be used as a decorator.
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
    """Decorator that registers a waveform class for ``.qp`` serialization, keyed by class name."""
    _waveform_registry[cls.__name__] = cls
    return cls


def register_vendor_version(vendor: str, version: str) -> None:
    """Record the protocol version of an installed vendor extension.

    Args:
        vendor: Vendor name as used in the dot-notation operations.
        version: Semver string. Major.minor governs compatibility; patch is informational.
    """
    _vendor_versions[vendor] = version


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_operation_spec(vendor: str | None, name: str) -> OperationSpec | None:
    """Return the spec for ``(vendor, name)``, or ``None`` if no operation is registered."""
    return _operation_specs_by_qualified.get((vendor, name))


def get_operation_spec_by_class(cls: type) -> OperationSpec | None:
    """Return the spec for an :class:`Operation` subclass, or ``None`` if it's not registered."""
    return _operation_specs_by_class.get(cls)


def get_block_spec(name: str) -> BlockSpec | None:
    """Return the spec for the block keyword ``name``, or ``None`` if none is registered."""
    return _block_specs_by_name.get(name)


def get_block_spec_by_class(cls: type) -> BlockSpec | None:
    """Return the spec for a :class:`Block` subclass, or ``None`` if it's not registered."""
    return _block_specs_by_class.get(cls)


def get_sweep_generator_spec(name: str) -> SweepGeneratorSpec | None:
    """Return the spec for the sweep generator ``name``, or ``None``."""
    return _sweep_generator_specs_by_name.get(name)


def get_sweep_generator_spec_by_class(cls: type) -> SweepGeneratorSpec | None:
    """Return the writing-side spec owning ``cls``, or ``None`` if no generator has registered a writer."""
    return _sweep_generator_specs_by_class.get(cls)


def get_waveform_class(name: str) -> type[Waveform | IQWaveform] | None:
    """Return the waveform class registered under ``name``, or ``None``."""
    return _waveform_registry.get(name)


def get_vendor_version(vendor: str) -> str | None:
    """Return the registered protocol version of an installed vendor, or None."""
    return _vendor_versions.get(vendor)


# Legacy compatibility shims — direct callers should prefer the new APIs above.


def get_operation_class(vendor: str, name: str) -> type | None:
    """Return the :class:`Operation` subclass for ``(vendor, name)``, or ``None`` if not registered."""
    spec = _operation_specs_by_qualified.get((vendor, name))
    return spec.cls if spec is not None else None


def get_operation_vendor_name(cls: type) -> tuple[str, str] | None:
    """Return ``(vendor, name)`` for ``cls`` when it is a registered vendor op, else ``None``."""
    spec = _operation_specs_by_class.get(cls)
    if spec is None or spec.vendor is None:
        return None
    return (spec.vendor, spec.name)


def _register_builtin_waveforms() -> None:
    """Populate the waveform registry with the built-in classes."""
    from qprogram.waveforms import (  # noqa: PLC0415
        Arbitrary,
        Chained,
        Cosine,
        FlatTop,
        Gaussian,
        GaussianDragCorrection,
        IQDrag,
        IQPair,
        IQRotation,
        IQZero,
        Modulated,
        Ramp,
        Sech,
        Sine,
        Square,
        SuddenNetZero,
        Tukey,
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
        Sine,
        Cosine,
        Tukey,
        Sech,
        IQPair,
        IQDrag,
        Modulated,
        IQRotation,
        IQZero,
    ]:
        _waveform_registry[cls.__name__] = cls


_register_builtin_waveforms()
