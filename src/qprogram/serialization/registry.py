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
"""Registries that drive the ``.qp`` serializer and parser.

The serialization grammar is schema-free: there are no hard-coded keyword lists for operations,
blocks, or sweep sources. Every extension point is a registered entry; the writer and parser
dispatch through the registries instead of ``isinstance`` chains.

The five registries:

- **Operations** (by class + by ``(vendor, name)``) — for write and parse respectively.
- **Blocks** (by name + by class) — keyword-led headers like ``average 1000:`` / ``block:``.
- **Sweep sources** (by class name) — the values a ``for <var> in <Source>(...)`` header binds:
  ``Range``, ``Linspace``, ``Logspace``, ``Values``, ``File``, and the combinators ``Repeat``,
  ``Rotate``, ``Concat``.
- **Waveforms** by class name.
- **Vendor protocol versions** by vendor name.

Vendor extensions register through :func:`register_vendor_operation`,
:func:`register_vendor_block`, and :func:`register_vendor_version` at import time. They additionally
declare a ``qprogram.vendors`` entry point so :func:`try_activate_vendor` can import them on demand
when a ``.qp`` file's ``require`` line names a vendor that is not registered yet.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from qprogram._reserved import RESERVED_VENDOR_NAMES
from qprogram.errors import VendorActivationError

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.sweeps.source import SweepSource
    from qprogram.waveforms import IQWaveform, Waveform

# Why these callback aliases use ``Any``: the registry stores per-subclass callbacks (e.g.
# ``sync_serialize`` takes a concrete ``Sync``, not ``Operation``). Tightening the alias to
# ``Callable[[Operation, ...]]`` would force every registration to cast or widen — ``Any`` lets the
# natural per-class signature survive.
OperationSerializeFn = Callable[[Any, Any], str]
OperationParseFn = Callable[[list[str], Any], Any]
BlockSerializeHeaderFn = Callable[[Any, Any], str]
BlockParseHeaderFn = Callable[[list[str], Any], Any]


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationSpec:
    """Serialization metadata for one operation, core or vendor.

    Attributes:
        name (str): Operation name as it appears in ``.qp``.
        vendor (str | None): Vendor name for the dot-prefix (``<vendor>.<name>``), or ``None`` for
            core.
        cls (type[Operation]): The :class:`Operation` subclass.
        serialize (OperationSerializeFn | None): Optional override; default uses
            ``inspect.signature``-driven serialization.
        parse (OperationParseFn | None): Optional override; default uses
            ``inspect.signature``-driven parsing.
    """

    name: str
    vendor: str | None
    cls: type[Operation]
    serialize: OperationSerializeFn | None = None
    parse: OperationParseFn | None = None

    @property
    def qualified_name(self) -> str:
        """The keyword this operation is written as.

        A core operation keeps its bare ``name``; a vendor operation is dotted as
        ``<vendor>.<name>``.
        """
        return f"{self.vendor}.{self.name}" if self.vendor else self.name


@dataclass(frozen=True)
class BlockSpec:
    """Serialization metadata for a keyword-led control-flow block, core or vendor.

    Sweeps are not registered here — ``for <var> in <Source>(...)`` is driven by the sweep-source
    registry (:func:`register_sweep_source`) instead.

    Attributes:
        name (str): Header keyword as it appears in ``.qp`` (e.g. ``"average"``, ``"block"``),
            without any vendor prefix.
        cls (type[Block]): The :class:`Block` subclass.
        vendor (str | None): Vendor name for the dot-prefix (``<vendor>.<name>``), or ``None`` for a
            core block. Mirrors :attr:`OperationSpec.vendor`, and is what lets the writer emit a
            ``require`` line for a file whose only vendor content is a block.
        serialize_header (BlockSerializeHeaderFn | None): Optional override returning the header text
            without the trailing ``:`` (e.g. ``"average 1000"``). Default emits the bare (qualified)
            keyword.
        parse_header (BlockParseHeaderFn | None): Optional override taking the post-keyword tokens;
            default invokes ``cls()`` with no arguments.
    """

    name: str
    cls: type[Block]
    vendor: str | None = None
    serialize_header: BlockSerializeHeaderFn | None = None
    parse_header: BlockParseHeaderFn | None = None

    @property
    def qualified_name(self) -> str:
        """The keyword this block header is written as.

        A core block keeps its bare ``name``; a vendor block is dotted as ``<vendor>.<name>``, which
        is also the key the block registry stores it under.
        """
        return f"{self.vendor}.{self.name}" if self.vendor else self.name


# ---------------------------------------------------------------------------
# Registry storage
# ---------------------------------------------------------------------------


_operation_specs_by_qualified: dict[tuple[str | None, str], OperationSpec] = {}
_operation_specs_by_class: dict[type, OperationSpec] = {}

_block_specs_by_name: dict[str, BlockSpec] = {}
_block_specs_by_class: dict[type, BlockSpec] = {}

_sweep_source_registry: dict[str, type[SweepSource]] = {}

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

    Re-registering the **same class** under the same ``(vendor, name)`` is allowed (the owner may
    refresh its callbacks; import-time side-effect modules may run twice). Registering a
    **different class** under a taken ``(vendor, name)`` raises — silently replacing another
    package's operation would corrupt every file using that keyword.

    Args:
        name (str): Keyword used in ``.qp`` (``play``, ``measure``, ...). Operation names are
            unrestricted; keyword-led block headers register on the block registry instead.
        cls (type[Operation]): The :class:`Operation` subclass.
        vendor (str | None): Vendor namespace. ``None`` registers a core op (emitted without
            prefix). Cannot be ``"core"`` or any :data:`~qprogram.RESERVED_KEYWORDS`.
        serialize (OperationSerializeFn | None): Optional override; default uses signature-driven
            serialization.
        parse (OperationParseFn | None): Optional override; default uses signature-driven parsing.

    Returns:
        ``cls`` unchanged, so the call can stand in for the class at the point of registration. A
        bare ``@register_operation`` decoration does not work — ``cls`` is the second positional
        parameter, not the first.

    Raises:
        ValueError: If ``vendor`` is reserved, or ``(vendor, name)`` is already registered to a
            different class.
    """
    if vendor is not None and vendor in RESERVED_VENDOR_NAMES:
        msg = (
            f"vendor name {vendor!r} is reserved (see qprogram.RESERVED_KEYWORDS "
            f"plus the 'core' sentinel); pick a different namespace for this "
            f"vendor extension"
        )
        raise ValueError(msg)
    existing = _operation_specs_by_qualified.get((vendor, name))
    if existing is not None and existing.cls is not cls:
        qualified = f"{vendor}.{name}" if vendor else name
        msg = (
            f"operation {qualified!r} is already registered to "
            f"{existing.cls.__module__}.{existing.cls.__qualname__}; refusing to replace it "
            f"with {cls.__module__}.{cls.__qualname__}"
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
    """Register an operation under a vendor namespace.

    Equivalent to ``register_operation(name, cls, vendor=vendor, ...)`` — the spelling vendor
    extension packages use at import time. The optional ``serialize`` / ``parse`` callbacks are
    forwarded unchanged: a vendor shipping a measurement operation passes
    :func:`qprogram.serialization._specs.measurement_op_serialize` and
    :func:`qprogram.serialization._specs.make_measurement_op_parse`, so the parser produces the
    canonical handle instance shared with every :class:`~qprogram.MeasurementRef` naming it.

    Args:
        vendor (str): Vendor namespace the operation is emitted under (``<vendor>.<name>``).
        name (str): Operation keyword within that namespace.
        cls (type[Operation]): The :class:`Operation` subclass.
        serialize (OperationSerializeFn | None): Optional override; default uses signature-driven
            serialization.
        parse (OperationParseFn | None): Optional override; default uses signature-driven parsing.

    Raises:
        ValueError: If ``vendor`` is reserved, or ``(vendor, name)`` is already registered to a
            different class.
    """
    register_operation(name, cls, vendor=vendor, serialize=serialize, parse=parse)


# ---------------------------------------------------------------------------
# Registration — blocks
# ---------------------------------------------------------------------------


def register_block(
    name: str,
    cls: type[Block],
    *,
    vendor: str | None = None,
    serialize_header: BlockSerializeHeaderFn | None = None,
    parse_header: BlockParseHeaderFn | None = None,
) -> type[Block]:
    """Register a keyword-led control-flow block for ``.qp`` serialization.

    The body parser handles indentation and child statements uniformly. Loops are not registered
    here — they use the sweep-source registry instead. Same-class re-registration is allowed;
    claiming a taken keyword with a different class raises.

    The registry is keyed by the **qualified** keyword, so a vendor block is looked up by the same
    dotted token the parser reads off the line (``myplatform.infinite_loop``) and can never collide
    with a core keyword.

    Args:
        name (str): Leading keyword in the block header (``average``, ``block``, ...), without any
            vendor prefix.
        cls (type[Block]): The :class:`Block` subclass.
        vendor (str | None): Vendor namespace. ``None`` registers a core block (emitted without
            prefix). Cannot be ``"core"`` or any :data:`~qprogram.RESERVED_KEYWORDS`. Prefer the
            :func:`register_vendor_block` wrapper from a vendor package.
        serialize_header (BlockSerializeHeaderFn | None): Optional header-text override.
        parse_header (BlockParseHeaderFn | None): Optional header-token override.

    Returns:
        ``cls`` unchanged, so the call can stand in for the class at the point of registration. A
        bare ``@register_block`` decoration does not work — ``cls`` is the second positional
        parameter, not the first.

    Raises:
        ValueError: If ``vendor`` is reserved, or the qualified keyword is already registered to a
            different class.
    """
    if vendor is not None and vendor in RESERVED_VENDOR_NAMES:
        msg = (
            f"vendor name {vendor!r} is reserved (see qprogram.RESERVED_KEYWORDS "
            f"plus the 'core' sentinel); pick a different namespace for this "
            f"vendor extension"
        )
        raise ValueError(msg)
    spec = BlockSpec(
        name=name,
        cls=cls,
        vendor=vendor,
        serialize_header=serialize_header,
        parse_header=parse_header,
    )
    keyword = spec.qualified_name
    existing = _block_specs_by_name.get(keyword)
    if existing is not None and existing.cls is not cls:
        msg = (
            f"block keyword {keyword!r} is already registered to "
            f"{existing.cls.__module__}.{existing.cls.__qualname__}; refusing to replace it"
        )
        raise ValueError(msg)
    _block_specs_by_name[keyword] = spec
    _block_specs_by_class[cls] = spec
    return cls


def register_vendor_block(
    vendor: str,
    name: str,
    cls: type[Block],
    *,
    serialize_header: BlockSerializeHeaderFn | None = None,
    parse_header: BlockParseHeaderFn | None = None,
) -> None:
    """Register a vendor control-flow block — the block analogue of :func:`register_vendor_operation`.

    Equivalent to ``register_block(name, cls, vendor=vendor, ...)``. The block's wire form becomes
    ``<vendor>.<name>:`` followed by an indented suite, and — because the spec records the vendor —
    a program containing the block gets a ``require <vendor> <x.y>`` line even when it contains no
    vendor *operations*.

    A block that repeats its body should also set :attr:`~qprogram.blocks.Block.REPEATS` ``True`` on
    the class so it counts toward ``max_loop_nesting``.

    Args:
        vendor (str): Vendor namespace the block header is emitted under (``<vendor>.<name>:``).
        name (str): Header keyword within that namespace.
        cls (type[Block]): The :class:`Block` subclass.
        serialize_header (BlockSerializeHeaderFn | None): Optional header-text override.
        parse_header (BlockParseHeaderFn | None): Optional header-token override.

    Raises:
        ValueError: If ``vendor`` is reserved, or the qualified keyword is already registered to a
            different class.
    """
    register_block(name, cls, vendor=vendor, serialize_header=serialize_header, parse_header=parse_header)


# ---------------------------------------------------------------------------
# Registration — waveforms, sweep sources, and vendor versions
# ---------------------------------------------------------------------------


def register_waveform(cls: type[Waveform | IQWaveform]) -> type:
    """Register a waveform class for ``.qp`` serialization, keyed by its class name.

    Same-class re-registration is a no-op; registering a different class under an already-taken
    name raises — it would silently change how every existing file parses that constructor.

    Args:
        cls (type[Waveform | IQWaveform]): Waveform class to register. Its ``__name__`` is the
            constructor name on the wire.

    Returns:
        ``cls``, so the function can be used as a decorator.

    Raises:
        ValueError: If ``cls.__name__`` is already registered to a different class.
    """
    existing = _waveform_registry.get(cls.__name__)
    if existing is not None and existing is not cls:
        msg = (
            f"waveform name {cls.__name__!r} is already registered to "
            f"{existing.__module__}.{existing.__qualname__}; rename the class or unregister first"
        )
        raise ValueError(msg)
    _waveform_registry[cls.__name__] = cls
    return cls


def register_sweep_source(cls: type[SweepSource]) -> type[SweepSource]:
    """Register a sweep-source class for ``.qp`` serialization, keyed by its class name.

    The sweep analogue of :func:`register_waveform`, and the whole extension step for a new source:
    once registered, ``for <var> in <ClassName>(...)`` parses and writes itself from the constructor
    signature, exactly as a waveform constructor does.

    Also registers the class's :attr:`~qprogram.sweeps.SweepSource.TOKEN` in the capability registry,
    so a profile may list it without a separate ``register_capability_tokens`` call — mirroring
    :func:`~qprogram.protocol.register_waveform_token`.

    Same-class re-registration is a no-op; registering a different class under an already-taken name
    raises — it would silently change how every existing file parses that constructor.

    Args:
        cls (type[SweepSource]): Sweep-source class to register. Its ``__name__`` is the constructor
            name on the wire, and its ``TOKEN`` is added to the capability registry.

    Returns:
        ``cls``, so the function can be used as a decorator.

    Raises:
        ValueError: If ``cls.__name__`` is already registered to a different class.
    """
    # Imported here rather than at module load, which would be a cycle.
    from qprogram.protocol import register_capability_tokens  # ruff: ignore[import-outside-top-level]

    existing = _sweep_source_registry.get(cls.__name__)
    if existing is not None and existing is not cls:
        msg = (
            f"sweep source name {cls.__name__!r} is already registered to "
            f"{existing.__module__}.{existing.__qualname__}; rename the class or unregister first"
        )
        raise ValueError(msg)
    _sweep_source_registry[cls.__name__] = cls
    register_capability_tokens(cls.TOKEN)
    return cls


def register_vendor_version(vendor: str, version: str) -> None:
    """Record the protocol version of an installed vendor extension.

    Args:
        vendor (str): Vendor name as used in the dot-notation operations. Cannot be ``"core"`` or
            any :data:`~qprogram.RESERVED_KEYWORDS`.
        version (str): Semver string with at least ``major.minor`` integer components. Major.minor
            governs compatibility; patch is informational.

    Raises:
        ValueError: If ``vendor`` is reserved or ``version`` does not parse as ``major.minor``.
    """
    if vendor in RESERVED_VENDOR_NAMES:
        msg = (
            f"vendor name {vendor!r} is reserved (see qprogram.RESERVED_KEYWORDS plus the "
            f"'core' sentinel); pick a different namespace for this vendor extension"
        )
        raise ValueError(msg)
    parts = version.split(".")
    minimum_parts = 2
    if len(parts) < minimum_parts:
        msg = f"vendor version {version!r} must have at least major.minor components"
        raise ValueError(msg)
    try:
        _major, _minor = int(parts[0]), int(parts[1])
    except ValueError as e:
        msg = f"vendor version {version!r} has non-integer major/minor components"
        raise ValueError(msg) from e
    _vendor_versions[vendor] = version


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_operation_spec(vendor: str | None, name: str) -> OperationSpec | None:
    """Return the spec the parser dispatches an operation keyword to.

    Args:
        vendor (str | None): Vendor namespace, or ``None`` for a core operation.
        name (str): Operation keyword within that namespace.

    Returns:
        The registered :class:`OperationSpec`, or ``None`` if the keyword is unknown.
    """
    return _operation_specs_by_qualified.get((vendor, name))


def get_operation_spec_by_class(cls: type) -> OperationSpec | None:
    """Return the spec the writer dispatches an operation instance to.

    Args:
        cls (type): The operation's class. Lookup is exact, not by inheritance.

    Returns:
        The registered :class:`OperationSpec`, or ``None`` if the class is not registered.
    """
    return _operation_specs_by_class.get(cls)


def get_block_spec(name: str) -> BlockSpec | None:
    """Return the spec for a block header keyword.

    Args:
        name (str): The qualified keyword as it appears on the line — ``average`` for a core block,
            ``<vendor>.<name>`` for a vendor one.

    Returns:
        The registered :class:`BlockSpec`, or ``None`` if the keyword is unknown.
    """
    return _block_specs_by_name.get(name)


def get_block_spec_by_class(cls: type) -> BlockSpec | None:
    """Return the spec the writer dispatches a block instance to.

    Args:
        cls (type): The block's class. Lookup is exact, not by inheritance.

    Returns:
        The registered :class:`BlockSpec`, or ``None`` if the class is not registered.
    """
    return _block_specs_by_class.get(cls)


def get_waveform_class(name: str) -> type[Waveform | IQWaveform] | None:
    """Return the waveform class a constructor name reconstructs.

    Args:
        name (str): Constructor name as written in the file (``Gaussian``, ``Arbitrary``, ...).

    Returns:
        The registered waveform class, or ``None`` if no waveform claims that name.
    """
    return _waveform_registry.get(name)


def get_sweep_source_class(name: str) -> type[SweepSource] | None:
    """Return the sweep-source class a constructor name reconstructs.

    Args:
        name (str): Constructor name as written in the file (``Range``, ``Values``, ...).

    Returns:
        The registered sweep-source class, or ``None`` if no source claims that name.
    """
    return _sweep_source_registry.get(name)


def known_sweep_sources() -> set[str]:
    """Return the class name of every registered sweep source.

    The live registry, so a vendor source registered at import time is included immediately. Both
    did-you-mean lists read it: the parser's, for an unknown ``for x in Name(...)`` constructor, and
    the fluent builder's, for an unknown ``sweep(var).from_*`` attribute.

    Returns:
        The class name of every sweep source currently registered.
    """
    return set(_sweep_source_registry)


def get_vendor_version(vendor: str) -> str | None:
    """Return the protocol version an installed vendor extension declared.

    A registered version is also what marks a vendor as active: it is the check
    :func:`try_activate_vendor` makes before and after importing an extension.

    Args:
        vendor (str): Vendor namespace, e.g. ``"qblox"``.

    Returns:
        The semver string the extension registered, or ``None`` if the vendor is not registered.
    """
    return _vendor_versions.get(vendor)


# ---------------------------------------------------------------------------
# Vendor discovery (entry points)
# ---------------------------------------------------------------------------
#
# A `.qp` file lists its vendor dependencies via `require <vendor> <major.minor>`, and the extension
# behind each one registers its namespace/version/operations/profile as an import side effect. A
# vendor package declares
#
#     [project.entry-points."qprogram.vendors"]
#     <vendor> = "<importable_module_that_self_registers>"
#
# and `loads()` imports it on demand, so the caller never has to. That makes a `.qp` file a
# self-contained contract — any environment with the extension *installed* can load it, imported or
# not.

_VENDOR_ENTRY_POINT_GROUP = "qprogram.vendors"
"""Entry-point group vendor packages declare for auto-activation. Each entry point's *name* is the
vendor namespace (e.g. ``qblox``); its *value* is an importable module that self-registers on import
(e.g. ``qprogram_qblox``)."""


@cache
def _vendor_entry_points() -> dict[str, importlib.metadata.EntryPoint]:
    """Discover installed vendor extensions via the ``qprogram.vendors`` entry-point group.

    Memoized — the set of installed distributions is fixed within a process. On a name clash (two
    distributions claiming the same vendor namespace) the first discovered wins; that case is
    pathological, and the rule exists only to make the outcome deterministic. Tests that inject
    entry points should patch this function or call :func:`clear_vendor_discovery_cache`.

    Returns:
        A mapping of vendor namespace to the entry point that activates it.
    """
    found: dict[str, importlib.metadata.EntryPoint] = {}
    for ep in importlib.metadata.entry_points(group=_VENDOR_ENTRY_POINT_GROUP):
        found.setdefault(ep.name, ep)
    return found


def clear_vendor_discovery_cache() -> None:
    """Reset the memoized entry-point scan (for tests that install or patch entry points).

    Tolerant of a monkeypatched ``_vendor_entry_points`` (a plain function has no ``cache_clear``),
    so test teardown can call it regardless of patch/finalizer ordering.
    """
    clearer = getattr(_vendor_entry_points, "cache_clear", None)
    if clearer is not None:
        clearer()


def try_activate_vendor(vendor: str) -> bool:
    """Ensure ``vendor`` is registered, importing its extension package on demand if needed.

    Importing the entry-point target runs the package's registration side effects
    (``register_vendor`` / ``register_vendor_version`` / ``register_vendor_operation`` /
    ``register_profile``). Python caches imports, so repeat calls are cheap and idempotent.

    Args:
        vendor (str): Vendor namespace, e.g. ``"qblox"``.

    Returns:
        ``True`` when the vendor is registered after the call — either it already was, or its
        ``qprogram.vendors`` entry point was found and imported successfully. ``False`` when no
        installed package claims ``vendor``; the caller decides whether that is an error.

    Raises:
        VendorActivationError: If an entry point claims ``vendor`` but its import raises, or it
            imports without registering a protocol version (a packaging bug in the extension).
    """
    if get_vendor_version(vendor) is not None:
        return True
    ep = _vendor_entry_points().get(vendor)
    if ep is None:
        return False
    try:
        ep.load()
    except Exception as e:  # collapse any import-time failure into one clear error
        msg = (
            f"vendor extension for {vendor!r} is installed (entry point {ep.value!r}) but failed "
            f"to import: {type(e).__name__}: {e}"
        )
        raise VendorActivationError(msg) from e
    if get_vendor_version(vendor) is None:
        msg = (
            f"vendor extension for {vendor!r} imported from entry point {ep.value!r} but did not "
            f"register a protocol version; the package must call "
            f"register_vendor_version({vendor!r}, '<x.y.z>') on import"
        )
        raise VendorActivationError(msg)
    return True


# Narrow accessors for callers that want one field of a spec rather than the spec itself.


def get_operation_class(vendor: str, name: str) -> type | None:
    """Return the operation class registered for a vendor keyword.

    Args:
        vendor (str): Vendor namespace.
        name (str): Operation keyword within that namespace.

    Returns:
        The :class:`Operation` subclass, or ``None`` if the keyword is not registered.
    """
    spec = _operation_specs_by_qualified.get((vendor, name))
    return spec.cls if spec is not None else None


def get_operation_vendor_name(cls: type) -> tuple[str, str] | None:
    """Return the vendor coordinates of an operation class.

    Args:
        cls (type): The operation's class.

    Returns:
        The ``(vendor, name)`` pair when ``cls`` is a registered vendor operation, else ``None`` —
        core operations and unregistered classes both answer ``None``.
    """
    spec = _operation_specs_by_class.get(cls)
    if spec is None or spec.vendor is None:
        return None
    return (spec.vendor, spec.name)


def _register_builtin_waveforms() -> None:
    """Populate the waveform registry with the built-in classes."""
    from qprogram.waveforms import (  # ruff: ignore[import-outside-top-level]
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
