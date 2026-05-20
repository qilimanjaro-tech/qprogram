"""Compiler capability protocol for QProgram.

A platform / compiler that executes :class:`~qprogram.QProgram` programs declares
its **supported feature set** through three orthogonal axes (Vulkan-style, because
flags, numbers, and AST-shape checks have different *shapes of check*):

1. **Capabilities** — flat set of dotted string tokens (``op.play``,
   ``waveform.iq_drag``, ``vendor.<name>.<op>``). Each Operation, Block,
   and Waveform declares the tokens *it* needs through
   :meth:`Operation.required_capabilities`, instance-aware: a ``Play``
   carrying an :class:`IQDrag` returns ``{op.play, waveform.iq,
   waveform.iq_drag}``; a ``Play`` carrying a :class:`Square` returns
   ``{op.play, waveform.single, waveform.square}``.

2. **Limits** — dict of numeric thresholds: ``max_loop_nesting``,
   ``min_wait_duration_ns``, ``max_parallel_loops``. Each profile sets
   defaults, a concrete device can tighten them at runtime.

3. **Predicates** — callables ``(node, ctx) -> Iterable[Diagnostic]`` that
   inspect each AST node with cross-op context. The escape hatch for
   data-flow / context-sensitive checks ("arbitrary sweep is fine for a
   waveform parameter but not at :class:`Wait.duration`").

Vendors register named, hierarchical **profile bundles** via
:func:`register_profile`. A profile combines a capability set, limits, and
predicates; profiles can extend other profiles by name (capabilities and
predicates accumulate; limits inherit then override).

A concrete platform exposes :attr:`PlatformProtocol.capabilities` (resolves a
profile, optionally tightens limits) and :meth:`PlatformProtocol.validate`
(walks the AST once and returns a list of :class:`Diagnostic` objects).

Design lineage: distributed-declaration + centralized-validation comes from
MLIR's SPIR-V dialect availability interfaces. Operand/instance-sensitive
predicates mirror MLIR's ``addDynamicallyLegalOp`` mechanism. The profile
abstraction mirrors QIR profiles (named, hierarchical bundles). The
features/limits/extensions split mirrors Vulkan.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


# ---------------------------------------------------------------------------
# Canonical capability-token registry
# ---------------------------------------------------------------------------
#
# Every dotted token that any in-tree :meth:`required_capabilities` may emit
# is listed here. The set is the single source of truth: profile-bundle
# constructors call :func:`validate_tokens` against it so a typo in a vendor
# package becomes an error at registration time rather than a silent
# acceptance at validate-time. Vendor extensions extend the registry by
# calling :func:`register_capability_tokens`.

_BASE_TOKENS: frozenset[str] = frozenset(
    {
        # op.* — operation presence
        "op.play",
        "op.measure",
        "op.wait",
        "op.sync",
        "op.set_frequency",
        "op.set_phase",
        "op.set_gain",
        "op.reset_phase",
        "op.set_offset",
        "op.set_parameter",
        "op.get_parameter",
        "op.set_crosstalk",
        # block.* — control-flow presence
        "block.block",
        "block.average",
        "block.for_loop",
        "block.loop",
        "block.parallel",
        "block.conditional",
        # waveform.* — channel kinds
        "waveform.single",
        "waveform.iq",
        "waveform.alias",
        # waveform.* — per-class
        "waveform.arbitrary",
        "waveform.chained",
        "waveform.flat_top",
        "waveform.gaussian",
        "waveform.gaussian_drag_correction",
        "waveform.ramp",
        "waveform.snz",
        "waveform.square",
        "waveform.iq_drag",
        "waveform.iq_pair",
        # sweep.* — loop sweep shape
        "sweep.linear",
        "sweep.arbitrary",
        # expr.* — expression node presence
        "expr.constant",
        "expr.variable",
        "expr.measurement_ref",
        "expr.binary_op",
        "expr.unary_op",
        "expr.comparison",
        "expr.logical_and_or",
        "expr.logical_not",
        "expr.where",
        # expr.math.* — per math function
        "expr.math.sin",
        "expr.math.cos",
        "expr.math.tan",
        "expr.math.exp",
        "expr.math.log",
        "expr.math.sqrt",
        "expr.math.abs",
        "expr.math.minimum",
        "expr.math.maximum",
        # measure.returns.* — recognised return tokens
        "measure.returns.iq",
        "measure.returns.raw",
        "measure.returns.state",
    },
)

CAPABILITY_REGISTRY: set[str] = set(_BASE_TOKENS)
"""Mutable token registry. Core tokens are added at import time;
vendor packages extend it via :func:`register_capability_tokens`."""


def register_capability_tokens(*tokens: str) -> None:
    """Register additional capability tokens (vendor extensions).

    Idempotent — duplicate registration is a no-op. Tokens must follow
    the dotted-name convention so they remain readable in error messages
    and in the eventual ``.qp`` format extensions; this function does not
    enforce a syntax (each vendor knows its own namespace) but does flag
    obvious typos like leading/trailing dots or empty segments.
    """
    for token in tokens:
        if not token or token.startswith(".") or token.endswith(".") or ".." in token:
            msg = f"Invalid capability token {token!r} (empty / leading-dot / trailing-dot / doubled dot)"
            raise ValueError(msg)
        CAPABILITY_REGISTRY.add(token)


def validate_tokens(tokens: Iterable[str]) -> None:
    """Raise :class:`ValueError` if any of ``tokens`` is not registered.

    Called by :func:`register_profile` so a profile bundle that mentions
    an unknown token (typo, removed feature) is rejected at registration
    time rather than during validation.
    """
    unknown = [t for t in tokens if t not in CAPABILITY_REGISTRY]
    if unknown:
        msg = (
            f"Unknown capability token(s): {sorted(unknown)}. "
            f"Register via qprogram.protocol.register_capability_tokens before use."
        )
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Waveform class → capability-token dispatch
# ---------------------------------------------------------------------------
#
# Per-class waveform tokens are looked up here rather than declared on each
# waveform class. Keeping the mapping centralized lets the validator stay
# decoupled from the waveform module structure, and lets vendor packages
# register their own waveform classes the same way they register tokens.

WAVEFORM_TOKEN: dict[type, str] = {}
"""Map of waveform class → canonical capability token. Populated lazily by
:func:`_register_builtin_waveform_tokens` on first use to avoid a circular
import; vendor packages call :func:`register_waveform_token` directly."""


def register_waveform_token(cls: type, token: str) -> None:
    """Register a waveform class → token mapping.

    Vendors call this for waveforms they ship. Also automatically registers
    ``token`` in :data:`CAPABILITY_REGISTRY` so a profile that lists the
    token does not have to call both functions.
    """
    WAVEFORM_TOKEN[cls] = token
    register_capability_tokens(token)


def _register_builtin_waveform_tokens() -> None:
    """Populate :data:`WAVEFORM_TOKEN` with the core waveforms.

    Called from inside :func:`waveform_token` on first lookup (lazy) to
    avoid an import cycle between this module and :mod:`qprogram.waveforms`.
    """
    if WAVEFORM_TOKEN:
        return
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

    WAVEFORM_TOKEN.update(
        {
            Arbitrary: "waveform.arbitrary",
            Chained: "waveform.chained",
            FlatTop: "waveform.flat_top",
            Gaussian: "waveform.gaussian",
            GaussianDragCorrection: "waveform.gaussian_drag_correction",
            Ramp: "waveform.ramp",
            Square: "waveform.square",
            SuddenNetZero: "waveform.snz",
            IQDrag: "waveform.iq_drag",
            IQPair: "waveform.iq_pair",
        },
    )


def waveform_token(wf: object) -> str | None:
    """Return the canonical token for a waveform value, or ``None``.

    - String aliases return ``None`` — callers add ``waveform.alias`` directly.
    - Unknown concrete classes (e.g. a vendor-defined waveform whose author
      forgot to register a token) return ``None``; the validator will not
      include any per-class refinement for it. Channel-kind tokens
      (``waveform.single`` / ``waveform.iq``) come from
      :meth:`required_capabilities` directly using ``isinstance`` checks,
      so they remain present even for unregistered classes.

    The parameter is annotated ``object`` because the dispatch is purely
    class-keyed: any instance whose ``type(...)`` is in the registry returns
    a token, and the typical callers pass a ``Waveform`` / ``IQWaveform``
    instance or a string alias. A vendor adding its own waveform class
    registers and queries here without inheriting from the base classes.
    """
    _register_builtin_waveform_tokens()
    if isinstance(wf, str):
        return None
    return WAVEFORM_TOKEN.get(type(wf))


# ---------------------------------------------------------------------------
# Expression → tokens helper
# ---------------------------------------------------------------------------


def expression_tokens(value: object) -> set[str]:
    """Recursively collect capability tokens for an expression value.

    Handles:

    - :class:`Constant` → ``{"expr.constant"}``
    - :class:`Variable` → ``{"expr.variable"}``
    - :class:`BinaryOp` / :class:`UnaryOp` / :class:`Comparison` /
      :class:`LogicalBinaryOp` / :class:`LogicalNot` / :class:`Where` →
      one token + recursion into children
    - :class:`MathFunc` → ``{"expr.math.<name>"}`` + recursion into operands
    - plain ``int`` / ``float`` → ``set()`` (a literal numeric never adds
      anything; the *type* of the parameter is what the caller is asking
      about, not the value)

    The base ``Operation.required_capabilities`` lets each op call this on
    each Expression-typed instance attribute it carries.
    """
    from qprogram.variable import (  # noqa: PLC0415
        BinaryOp,
        Comparison,
        Constant,
        Expression,
        LogicalBinaryOp,
        LogicalNot,
        MathFunc,
        MeasurementRef,
        UnaryOp,
        Variable,
        Where,
    )

    if isinstance(value, Constant):
        return {"expr.constant"}
    if isinstance(value, Variable):
        return {"expr.variable"}
    if isinstance(value, MeasurementRef):
        return {"expr.measurement_ref"}
    if isinstance(value, BinaryOp):
        return {"expr.binary_op"} | expression_tokens(value.left) | expression_tokens(value.right)
    if isinstance(value, UnaryOp):
        return {"expr.unary_op"} | expression_tokens(value.operand)
    if isinstance(value, Comparison):
        return {"expr.comparison"} | expression_tokens(value.left) | expression_tokens(value.right)
    if isinstance(value, LogicalBinaryOp):
        return {"expr.logical_and_or"} | expression_tokens(value.left) | expression_tokens(value.right)
    if isinstance(value, LogicalNot):
        return {"expr.logical_not"} | expression_tokens(value.operand)
    if isinstance(value, Where):
        return (
            {"expr.where"}
            | expression_tokens(value.condition)
            | expression_tokens(value.then)
            | expression_tokens(value.else_)
        )
    if isinstance(value, MathFunc):
        tokens = {f"expr.math.{value.name}"}
        for op in value.operands:
            tokens |= expression_tokens(op)
        return tokens
    if isinstance(value, Expression):  # forward-compat: unknown Expression subclass
        return set()
    return set()


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    """One issue found by the validator.

    ``severity`` is currently always ``"error"``. ``"warning"`` is reserved
    for the future (e.g. a limit-violation that the platform can spill to
    software-driven execution); widening the annotation is the only change
    needed when the fallback story lands.

    ``code`` is a short machine-readable identifier (``missing-capability``,
    ``limit-exceeded``, vendor-defined codes prefixed by the vendor name).
    ``message`` is the human-readable explanation. ``node`` points to the
    offending AST node when one is available (capability-missing diagnostics
    always have one; whole-program checks like total-measurement-count don't).

    ``capability`` is the token that was missing, when applicable.
    ``limit`` is a ``(name, observed_value)`` tuple when a numeric limit was
    exceeded — the threshold itself is in :attr:`CompilerCapabilities.limits`.
    """

    severity: Literal["error"]
    code: str
    message: str
    node: Operation | Block | None = None
    capability: str | None = None
    limit: tuple[str, float] | None = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


# ---------------------------------------------------------------------------
# Predicate + ValidationContext
# ---------------------------------------------------------------------------


@runtime_checkable
class Predicate(Protocol):
    """Per-node validation predicate.

    Called once per visited AST node during :func:`qprogram.validation.validate`,
    with a :class:`ValidationContext` carrying cross-op data-flow facts. Returns
    an iterable of :class:`Diagnostic` — empty when the predicate has nothing
    to say about this node.

    The motivating example: a predicate that flags an :class:`~qprogram.operations.Wait`
    whose ``duration`` is a :class:`Variable` bound by an arbitrary-sweep
    :class:`~qprogram.blocks.Loop`. The predicate needs ``ctx`` to discover
    the binding loop and its sweep kind — facts a per-node ``required_capabilities``
    call cannot see in isolation.
    """

    def __call__(
        self,
        node: Operation | Block,
        ctx: ValidationContext,
    ) -> Iterable[Diagnostic]: ...


SweepKind = Literal["linear", "arbitrary", "averaged"]


class ValidationContext:
    """Read-only view of program-wide data-flow facts, built once per ``validate()`` call.

    Predicates use the queries here to answer "in *this* AST, is X legal?"
    without re-walking the tree. The context is materialised by
    :func:`qprogram.validation.validate` from a single pass over ``program.body``
    and then passed to every predicate; predicates must not mutate it.

    The current surface covers the cases this design targets. New queries
    are added here (not on a wider object) so predicate authors have a
    single, discoverable interface.
    """

    def __init__(  # noqa: PLR0913  # all-keyword constructor for a small data carrier
        self,
        *,
        variable_bindings: Mapping[Variable, Block],
        sweep_kinds: Mapping[Variable, SweepKind],
        max_loop_nesting: int,
        max_parallel_arity: int,
        measurement_count: int,
        measurement_returns: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._variable_bindings = dict(variable_bindings)
        self._sweep_kinds = dict(sweep_kinds)
        self._max_loop_nesting = max_loop_nesting
        self._max_parallel_arity = max_parallel_arity
        self._measurement_count = measurement_count
        self._measurement_returns: dict[str, tuple[str, ...]] = dict(measurement_returns or {})

    def sweep_kind_of(self, var: Variable) -> SweepKind | None:
        """Return how ``var`` is bound, or ``None`` if not bound by any loop.

        - ``"linear"`` — bound by a :class:`~qprogram.blocks.ForLoop`.
        - ``"arbitrary"`` — bound by a :class:`~qprogram.blocks.Loop`
          (numpy-array-driven).
        - ``"averaged"`` — currently unused; reserved for variables that
          are averaged-over rather than swept.
        - ``None`` — variable is not bound by any loop in the program
          (e.g. set externally, or unused inside any loop).
        """
        return self._sweep_kinds.get(var)

    def binding_loop_of(self, var: Variable) -> Block | None:
        """Return the loop block that binds ``var``, or ``None``."""
        return self._variable_bindings.get(var)

    @property
    def max_loop_nesting(self) -> int:
        """Deepest nested-loop count observed in the program (inclusive of Parallel headers)."""
        return self._max_loop_nesting

    @property
    def max_parallel_arity(self) -> int:
        """Largest number of loops in any single :class:`~qprogram.blocks.Parallel` block."""
        return self._max_parallel_arity

    @property
    def measurement_count(self) -> int:
        """Total :class:`~qprogram.operations.operation.MeasurementOperation` instances in the program."""
        return self._measurement_count

    def measurement_returns(self, name: str) -> tuple[str, ...] | None:
        """Return the ``returns`` tuple of the measurement with this name.

        ``None`` if no measurement with that name exists in the program.
        Predicates use this to check that a referenced measurement
        actually requested the data shape they care about (e.g. that a
        ``handle.state`` reference's measurement requested
        ``"state"`` classification).
        """
        return self._measurement_returns.get(name)

    def known_measurement_names(self) -> set[str]:
        """Set of every measurement name in the program."""
        return set(self._measurement_returns)


# ---------------------------------------------------------------------------
# Profile + CompilerCapabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A named bundle of capabilities, limits, and predicates.

    Vendors register one or more profiles via :func:`register_profile`.
    Profiles can extend others by name — capabilities and predicates from
    parent profiles accumulate; limits inherit and may be overridden by
    the child.

    The ``vendor_versions`` field is informational: it records which
    vendor extension versions this profile was designed for, mirroring
    the existing ``.qp`` ``require <vendor> <version>`` line.
    """

    name: str
    version: tuple[int, int, int]
    extends: str | None
    capabilities: frozenset[str]
    limits: Mapping[str, float] = field(default_factory=dict)
    predicates: tuple[Predicate, ...] = ()
    vendor_versions: Mapping[str, tuple[int, int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_tokens(self.capabilities)


@dataclass(frozen=True)
class CompilerCapabilities:
    """The descriptor a :class:`PlatformProtocol` exposes via ``.capabilities``.

    Materialized by :meth:`from_profile`, which walks the profile's
    ``extends`` chain and merges capabilities/predicates (union, in
    parent→child order) and limits (later overrides earlier, so the leaf
    profile's value wins). A live device can also pass
    ``limit_overrides=`` to tighten any merged limit at construction time.

    This is the single object the validator consumes and that users
    introspect. There is no separate "what is advertised" vs "what is
    enforced" surface — same object, both sides.
    """

    profile: str
    version: tuple[int, int, int]
    capabilities: frozenset[str]
    limits: Mapping[str, float]
    predicates: tuple[Predicate, ...]
    vendor_versions: Mapping[str, tuple[int, int, int]]

    def supports(self, token: str) -> bool:
        """Return whether ``token`` is in this descriptor's capability set."""
        return token in self.capabilities

    @classmethod
    def from_profile(
        cls,
        profile_name: str,
        *,
        limit_overrides: Mapping[str, float] | None = None,
        extra_predicates: tuple[Predicate, ...] = (),
    ) -> CompilerCapabilities:
        """Resolve a registered profile (walking ``extends``) into a capability descriptor.

        ``limit_overrides`` — typically supplied by a concrete device that
        knows its hardware is tighter than the profile defaults — replaces
        the corresponding merged-limit values element-wise.

        ``extra_predicates`` — additional predicates the device wants to
        run on top of the profile's. Useful for site-specific constraints
        (e.g. "this rack's instrument F can't do op X concurrently with
        op Y") that don't belong in the vendor-shipped profile.
        """
        profile = resolve_profile(profile_name)
        chain = _profile_chain(profile)
        merged_caps: set[str] = set()
        merged_limits: dict[str, float] = {}
        merged_predicates: list[Predicate] = []
        for p in chain:
            merged_caps |= p.capabilities
            merged_limits.update(p.limits)
            merged_predicates.extend(p.predicates)
        if limit_overrides:
            merged_limits.update(limit_overrides)
        merged_predicates.extend(extra_predicates)
        return cls(
            profile=profile.name,
            version=profile.version,
            capabilities=frozenset(merged_caps),
            limits=dict(merged_limits),
            predicates=tuple(merged_predicates),
            vendor_versions=dict(profile.vendor_versions),
        )


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

PROFILE_REGISTRY: dict[str, Profile] = {}
"""All registered profiles, keyed by ``Profile.name``."""


def register_profile(profile: Profile) -> None:
    """Register a profile by name.

    Raises :class:`ValueError` on duplicate registration (idempotent
    re-registration of the same Profile object is allowed, so import-time
    side-effect modules that load twice don't crash).
    """
    existing = PROFILE_REGISTRY.get(profile.name)
    if existing is profile:
        return
    if existing is not None:
        msg = f"Profile {profile.name!r} is already registered with different content"
        raise ValueError(msg)
    PROFILE_REGISTRY[profile.name] = profile


def resolve_profile(name: str) -> Profile:
    """Look up a profile by name; raise :class:`KeyError` when missing."""
    if name not in PROFILE_REGISTRY:
        available = ", ".join(sorted(PROFILE_REGISTRY)) or "(none registered)"
        msg = f"Unknown profile {name!r}. Available: {available}"
        raise KeyError(msg)
    return PROFILE_REGISTRY[name]


def _profile_chain(profile: Profile) -> list[Profile]:
    """Walk the ``extends`` chain root-first, detecting cycles."""
    seen: set[str] = set()
    chain: list[Profile] = []
    current: Profile | None = profile
    while current is not None:
        if current.name in seen:
            msg = f"Profile inheritance cycle detected at {current.name!r}"
            raise ValueError(msg)
        seen.add(current.name)
        chain.append(current)
        current = resolve_profile(current.extends) if current.extends else None
    chain.reverse()  # root-first so child limits override parent
    return chain


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


# Callable typedef for users authoring predicates without importing Protocol.
PredicateFn = Callable[["Operation | Block", "ValidationContext"], Iterable[Diagnostic]]


__all__ = [
    "CAPABILITY_REGISTRY",
    "PROFILE_REGISTRY",
    "WAVEFORM_TOKEN",
    "CompilerCapabilities",
    "Diagnostic",
    "Predicate",
    "PredicateFn",
    "Profile",
    "SweepKind",
    "ValidationContext",
    "expression_tokens",
    "register_capability_tokens",
    "register_profile",
    "register_waveform_token",
    "resolve_profile",
    "validate_tokens",
    "waveform_token",
]
