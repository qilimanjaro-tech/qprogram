"""Compiler capability protocol for QProgram.

Platforms declare which DSL features they support along three orthogonal axes — capability tokens,
numeric limits, and predicates — and the validator walks an AST once against the resulting descriptor.
See the architecture docs (``docs/developer/capability-protocol.md``) for the design rationale and the
MLIR/Vulkan/QIR lineage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


# Canonical capability-token registry.
# Every dotted token any in-tree ``required_capabilities`` may emit is listed here. Profile bundles
# validate their token sets against this registry at construction time, so a typo in a vendor package
# becomes an error at registration rather than a silent acceptance at validate-time.

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
        "waveform.cosine",
        "waveform.flat_top",
        "waveform.gaussian",
        "waveform.gaussian_drag_correction",
        "waveform.iq_drag",
        "waveform.iq_pair",
        "waveform.iq_rotation",
        "waveform.iq_zero",
        "waveform.modulated",
        "waveform.ramp",
        "waveform.sech",
        "waveform.sine",
        "waveform.snz",
        "waveform.square",
        "waveform.tukey",
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
    """Register vendor-extension capability tokens.

    Idempotent (re-registration is a no-op). Validates the shape of each token but does not enforce
    a namespace policy beyond rejecting empty segments and stray dots — each vendor owns its own
    ``vendor.<name>.*`` prefix.

    Args:
        *tokens: Tokens to register.

    Raises:
        ValueError: If any token is empty, starts/ends with ``.``, or contains ``..``.
    """
    for token in tokens:
        if not token or token.startswith(".") or token.endswith(".") or ".." in token:
            msg = f"Invalid capability token {token!r} (empty / leading-dot / trailing-dot / doubled dot)"
            raise ValueError(msg)
        CAPABILITY_REGISTRY.add(token)


def validate_tokens(tokens: Iterable[str]) -> None:
    """Validate that every token in ``tokens`` is registered.

    Called from :class:`Profile`'s ``__post_init__`` so unknown tokens (typos, removed features) are
    rejected at registration rather than during validation.

    Raises:
        ValueError: If any token is not in :data:`CAPABILITY_REGISTRY`.
    """
    unknown = [t for t in tokens if t not in CAPABILITY_REGISTRY]
    if unknown:
        msg = (
            f"Unknown capability token(s): {sorted(unknown)}. "
            f"Register via qprogram.protocol.register_capability_tokens before use."
        )
        raise ValueError(msg)


# Waveform class → capability-token dispatch.
# Centralised here (rather than declared on each waveform class) so the validator stays decoupled from
# the waveform module structure; vendor packages register their classes through the same API.

WAVEFORM_TOKEN: dict[type, str] = {}
"""Map of waveform class to canonical capability token. Populated lazily on first use to avoid a
circular import; vendor packages extend it via :func:`register_waveform_token`."""


def register_waveform_token(cls: type, token: str) -> None:
    """Register a waveform class → token mapping.

    Also registers ``token`` in :data:`CAPABILITY_REGISTRY` so profiles that list the token don't
    have to call both functions.

    Args:
        cls: Waveform class to register.
        token: Canonical capability token (e.g. ``"waveform.iq_drag"``).
    """
    WAVEFORM_TOKEN[cls] = token
    register_capability_tokens(token)


def _register_builtin_waveform_tokens() -> None:
    """Populate :data:`WAVEFORM_TOKEN` with the built-in waveforms.

    Lazily imported here to break the circular import between this module and :mod:`qprogram.waveforms`.
    """
    if WAVEFORM_TOKEN:
        return
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

    WAVEFORM_TOKEN.update(
        {
            Arbitrary: "waveform.arbitrary",
            Chained: "waveform.chained",
            Cosine: "waveform.cosine",
            FlatTop: "waveform.flat_top",
            Gaussian: "waveform.gaussian",
            GaussianDragCorrection: "waveform.gaussian_drag_correction",
            IQDrag: "waveform.iq_drag",
            IQPair: "waveform.iq_pair",
            IQRotation: "waveform.iq_rotation",
            IQZero: "waveform.iq_zero",
            Modulated: "waveform.modulated",
            Ramp: "waveform.ramp",
            Sech: "waveform.sech",
            Sine: "waveform.sine",
            Square: "waveform.square",
            SuddenNetZero: "waveform.snz",
            Tukey: "waveform.tukey",
        },
    )


def waveform_token(wf: object) -> str | None:
    """Return the canonical capability token for a waveform value, or ``None``.

    String aliases return ``None`` (callers add ``waveform.alias`` directly). Unknown concrete classes
    also return ``None``; the validator simply skips per-class refinement for them. Channel-kind tokens
    (``waveform.single`` / ``waveform.iq``) come from :meth:`Operation.required_capabilities` via
    ``isinstance`` checks, so they remain present even when no per-class token is registered.

    Why ``wf`` is typed ``object``: the dispatch is purely class-keyed, and vendor packages register
    their own classes here without subclassing :class:`Waveform` / :class:`IQWaveform`.
    """
    _register_builtin_waveform_tokens()
    if isinstance(wf, str):
        return None
    return WAVEFORM_TOKEN.get(type(wf))


# ---------------------------------------------------------------------------
# Expression → tokens helper
# ---------------------------------------------------------------------------


def expression_tokens(value: object) -> set[str]:
    """Recursively collect capability tokens contributed by an expression value.

    The returned set always describes the ``Expression`` node type (and operator name for
    :class:`MathFunc`), not the value. Plain numeric literals contribute nothing — they're not
    Expression nodes. Operations call this on each Expression-typed instance attribute they carry.

    Args:
        value: Anything that can appear as an expression operand; non-:class:`Expression` values
            return an empty set.

    Returns:
        Set of capability tokens contributed by the value and its descendants.
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

    Attributes:
        severity: Always ``"error"`` today. ``"warning"`` is reserved for a future fallback story
            (e.g. limit violations a platform can spill to software-driven execution).
        code: Short machine-readable identifier (``"missing-capability"``, ``"limit-exceeded"``, or
            a vendor-prefixed code).
        message: Human-readable explanation.
        node: The offending AST node when one is available. Capability-missing diagnostics always
            have one; whole-program checks (total-measurement-count, ...) do not.
        capability: The token that was missing, when applicable.
        limit: ``(name, observed_value)`` tuple when a numeric limit was exceeded. The threshold
            itself lives in :attr:`CompilerCapabilities.limits`.
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
    """Per-node validation predicate — called once per visited AST node during ``validate()``.

    Receives a :class:`ValidationContext` with cross-op data-flow facts. Returns zero or more
    :class:`Diagnostic` objects. Motivating example: flagging a :class:`Wait` whose ``duration`` is
    bound by an arbitrary-sweep :class:`Loop` — a fact a per-node ``required_capabilities`` call
    can't see in isolation.
    """

    def __call__(
        self,
        node: Operation | Block,
        ctx: ValidationContext,
    ) -> Iterable[Diagnostic]: ...


SweepKind = Literal["linear", "arbitrary", "averaged"]


class ValidationContext:
    """Read-only view of program-wide data-flow facts, built once per ``validate()`` call.

    Predicates use the queries here to answer "in *this* AST, is X legal?" without re-walking the
    tree. New queries are added here so predicate authors have a single, discoverable surface;
    predicates must treat the context as immutable.
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
        """Return how ``var`` is loop-bound.

        Returns:
            ``"linear"`` for :class:`ForLoop`, ``"arbitrary"`` for :class:`Loop`, ``"averaged"`` for
            future reserve, or ``None`` if not loop-bound (set externally or unused).
        """
        return self._sweep_kinds.get(var)

    def binding_loop_of(self, var: Variable) -> Block | None:
        """Return the loop block that binds ``var``, or ``None`` if it has no binding."""
        return self._variable_bindings.get(var)

    @property
    def max_loop_nesting(self) -> int:
        """Deepest nested-loop count observed in the program (Parallel headers counted)."""
        return self._max_loop_nesting

    @property
    def max_parallel_arity(self) -> int:
        """Largest ``len(parallel.loops)`` observed across any :class:`Parallel` block."""
        return self._max_parallel_arity

    @property
    def measurement_count(self) -> int:
        """Total number of :class:`MeasurementOperation` instances in the program."""
        return self._measurement_count

    def measurement_returns(self, name: str) -> tuple[str, ...] | None:
        """Return the ``returns`` tuple of the named measurement, or ``None`` if it doesn't exist.

        Predicates use this to check that a referenced measurement requested the data shape they
        care about — e.g. that a ``handle.state`` reference's source measurement requested
        ``"state"`` classification.
        """
        return self._measurement_returns.get(name)

    def known_measurement_names(self) -> set[str]:
        """Return the set of every measurement name in the program."""
        return set(self._measurement_returns)


# ---------------------------------------------------------------------------
# Profile + CompilerCapabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A named, versioned bundle of capabilities, limits, and predicates.

    Vendors register one or more profiles via :func:`register_profile`. Profiles may :attr:`extends`
    another by name — capabilities and predicates accumulate (parent → child), limits inherit and may
    be overridden by the child.

    Attributes:
        name: Unique profile name (e.g. ``"myvendor-default-v1"``).
        version: Profile version as ``(major, minor, patch)``.
        extends: Name of a parent profile, or ``None``.
        capabilities: Set of capability tokens this profile advertises.
        limits: Numeric thresholds; the validator silently ignores unrecognised keys so future
            limits can be declared without breaking older validators.
        predicates: Tuple of :class:`Predicate` callables.
        vendor_versions: Informational record of which vendor extension versions this profile was
            designed for; mirrors the ``.qp`` ``require <vendor> <version>`` line.
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
    """The capability descriptor that :class:`PlatformProtocol` exposes via ``.capabilities``.

    Materialized by :meth:`from_profile`, which walks ``extends`` and merges parent → child:
    capabilities/predicates union, limits replace. A live device may pass ``limit_overrides=`` to
    further tighten any limit. The same object is what the validator consumes and what users
    introspect — there is no separate "advertised vs. enforced" surface.

    Attributes:
        profile: Name of the source profile.
        version: Source profile version.
        capabilities: Merged set of capability tokens.
        limits: Merged numeric limits.
        predicates: Merged tuple of :class:`Predicate` callables.
        vendor_versions: Merged vendor-extension version expectations.
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
        """Resolve a registered profile and merge it into a capability descriptor.

        Args:
            profile_name: Name of a profile previously passed to :func:`register_profile`.
            limit_overrides: Per-limit replacements for the merged values — typically supplied by a
                device that knows its hardware is tighter than the profile defaults.
            extra_predicates: Site-specific predicates run on top of the profile's. Useful for
                rack-level constraints that don't belong in the vendor-shipped profile.

        Returns:
            The materialised :class:`CompilerCapabilities`.

        Raises:
            KeyError: If ``profile_name`` is not registered.
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
    """Register a profile in :data:`PROFILE_REGISTRY` under its name.

    Idempotent for the *same* Profile object — useful for import-time side-effect modules that may
    load twice. Re-registering a different profile under an existing name raises.

    Raises:
        ValueError: If a different profile is already registered under ``profile.name``.
    """
    existing = PROFILE_REGISTRY.get(profile.name)
    if existing is profile:
        return
    if existing is not None:
        msg = f"Profile {profile.name!r} is already registered with different content"
        raise ValueError(msg)
    PROFILE_REGISTRY[profile.name] = profile


def resolve_profile(name: str) -> Profile:
    """Look up a profile by name.

    Raises:
        KeyError: If ``name`` is not registered. The message lists the currently-known names.
    """
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


# Callable alias for users authoring predicates without pulling Protocol into scope.
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
