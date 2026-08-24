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
"""Compiler capability protocol for QProgram.

Platforms declare which DSL features they support along three orthogonal axes — capability tokens,
numeric limits, and predicates — split into a **per-bus** grain (drive, readout, flux, ...) and a
**platform-wide** grain (control flow, expressions, bus-less ops). Each grain is further split into
**rt** and **host** halves so the validator can reason about real-time vs host-side execution.
See the architecture docs (``docs/developer/capability-protocol.md``) for the design rationale and the
MLIR/Vulkan/QIR lineage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.buses import BusRef
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


Domain = Literal["rt", "host"]
"""Execution domain for an AST node — real-time hardware sequencer (``"rt"``) or host-side
orchestration on the lab server (``"host"``)."""

BusSelector = tuple[str, str]
"""``(element_kind, bus_kind)`` key into :attr:`PlatformCapabilities.bus`. ``("q", "drive")`` selects
every transmon drive bus on the platform."""


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
        # block.* — control-flow presence
        "block.block",
        "block.average",
        "block.sweep",
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
        # sweep.<kind> — how compilable the sweep's values are. A claim about shape, not content:
        # "linear" means exactly start + step * i, which a sequencer can run from a loop register.
        "sweep.linear",
        "sweep.arbitrary",
        # sweep.<source> — per-source-class, mirroring waveform.<class>. A platform that can generate
        # a source natively declares its token; one that can't omits it and the validator says so
        # rather than the platform silently materializing the points into a table.
        "sweep.range",
        "sweep.values",
        "sweep.linspace",
        "sweep.logspace",
        "sweep.file",
        "sweep.repeat",
        "sweep.rotate",
        "sweep.concat",
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
        # measure.fields.* — recognized measurement fields
        "measure.fields.iq",
        "measure.fields.raw",
        "measure.fields.state",
    },
)

CAPABILITY_REGISTRY: set[str] = set(_BASE_TOKENS)
"""Mutable token registry. Core tokens are added at import time;
vendor packages extend it via :func:`register_capability_tokens`."""

MEASUREMENT_FIELD_TOKEN_PREFIX = "measure.fields."  # ruff: ignore[hardcoded-password-string]  # a capability-token namespace, not a secret
"""Token namespace for measurement fields. The registry is the single source of truth for which
field names ``normalize_fields`` accepts, so a vendor that registers
``measure.fields.<name>`` immediately widens the DSL's ``fields=`` vocabulary."""


def measurement_field_token(field: str) -> str:
    """Return the capability token for a measurement field name (``"iq"`` → ``"measure.fields.iq"``).

    Args:
        field (str): A measurement field name, core or vendor-registered.

    Returns:
        The token a profile lists to advertise support for that field.
    """
    return f"{MEASUREMENT_FIELD_TOKEN_PREFIX}{field}"


def known_measurement_fields() -> set[str]:
    """Return every currently registered measurement field name (core plus vendor).

    Derived from :data:`CAPABILITY_REGISTRY` rather than from :class:`MeasurementField`, so vendor
    fields are included the moment their token is registered. Used by
    :func:`qprogram.operations.operation.normalize_fields` to reject typos at the ``measure(...)``
    call site, and by editor tooling to offer completions.

    Returns:
        Every field name that currently has a ``measure.fields.<name>`` token registered.
    """
    return {
        t.removeprefix(MEASUREMENT_FIELD_TOKEN_PREFIX)
        for t in CAPABILITY_REGISTRY
        if t.startswith(MEASUREMENT_FIELD_TOKEN_PREFIX)
    }


def register_capability_tokens(*tokens: str) -> None:
    """Register vendor-extension capability tokens.

    Idempotent (re-registration is a no-op). Validates the shape of each token but does not enforce
    a namespace policy beyond rejecting empty segments and stray dots — each vendor owns its own
    ``vendor.<name>.*`` prefix.

    Args:
        *tokens (str): Tokens to register.

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

    Called from :class:`Profile`'s ``__post_init__``, so an unknown token — a typo, or a feature this
    build does not have — is rejected at registration rather than during validation.

    Args:
        tokens (Iterable[str]): Capability tokens to check.

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
# Centralized here (rather than declared on each waveform class) so the validator stays decoupled from
# the waveform module structure; vendor packages register their classes through the same API.

WAVEFORM_TOKEN: dict[type, str] = {}
"""Map of waveform class to canonical capability token. Populated lazily on first use to avoid a
circular import; vendor packages extend it via :func:`register_waveform_token`."""


def register_waveform_token(cls: type, token: str) -> None:
    """Register a waveform class → token mapping.

    Also registers ``token`` in :data:`CAPABILITY_REGISTRY` so profiles that list the token don't
    have to call both functions.

    Args:
        cls (type): Waveform class to register.
        token (str): Canonical capability token (e.g. ``"waveform.iq_drag"``).

    Raises:
        ValueError: If ``token`` is empty, starts or ends with ``.``, or contains ``..``.
    """
    WAVEFORM_TOKEN[cls] = token
    register_capability_tokens(token)


def _register_builtin_waveform_tokens() -> None:
    """Populate :data:`WAVEFORM_TOKEN` with the built-in waveforms.

    Lazily imported here to break the circular import between this module and :mod:`qprogram.waveforms`.
    """
    if WAVEFORM_TOKEN:
        return
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
    also return ``None``; the validator skips per-class refinement for them. Channel-kind tokens
    (``waveform.single`` / ``waveform.iq``) come from :meth:`Operation.required_capabilities` via
    ``isinstance`` checks, so they remain present even when no per-class token is registered.

    Args:
        wf (object): The waveform value to classify. Typed ``object`` because the dispatch is purely
            class-keyed, and vendor packages register their own classes here without subclassing
            :class:`~qprogram.waveforms.Waveform` / :class:`~qprogram.waveforms.IQWaveform`.

    Returns:
        The token registered for the value's class, or ``None`` for a string alias or an
        unregistered class.
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
        value (object): Anything that can appear as an expression operand. A value that is not an
            :class:`~qprogram.Expression` contributes nothing.

    Returns:
        Set of capability tokens contributed by the value and its descendants.
    """
    from qprogram.variable import (  # ruff: ignore[import-outside-top-level]
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
        severity (Literal["error", "warning", "info"]): ``"error"`` for hard failures the program
            cannot execute with (missing capability, exceeded limit, empty execution domain);
            ``"warning"`` for programs that *will* run but in a degraded or surprising way callers
            should surface prominently (notably the ``forced-host`` notice attached to the highest
            block that lost ``"rt"`` from its execution domain); ``"info"`` for purely advisory
            output. The execution convention: ``execute()`` raises on errors, surfaces warnings
            without raising, and passes info through.
        code (str): Short machine-readable identifier (``"missing-capability"``,
            ``"limit-exceeded"``, ``"empty-domain"``, ``"forced-host"``, or a vendor-prefixed code).
        message (str): Human-readable explanation.
        node (Operation | Block | None): The offending AST node when one is available.
            Capability-missing diagnostics always have one; whole-program checks
            (total-measurement-count, ...) do not.
        path (tuple[int | str, ...] | None): Structural address of :attr:`node` under the validated
            program's body (see :mod:`qprogram.paths`). Stamped by ``validate()``; ``None`` when
            there is no node. Because the ``.qp`` round-trip preserves structure, the same path
            resolves against ``loads(dumps(p))`` — whose :attr:`~qprogram.QProgram.source_map` then
            maps it to a 1-based ``.qp`` line.
        capability (str | None): The token that was missing, when applicable.
        limit (tuple[str, float] | None): ``(name, observed_value)`` when a numeric limit was
            exceeded. The threshold itself lives in :attr:`CompilerCapabilities.limits`.
        domain (Domain | None): Populated on ``"forced-host"`` diagnostics with the domain the node
            ended up running in (typically ``"host"``).
    """

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    node: Operation | Block | None = None
    path: tuple[int | str, ...] | None = None
    capability: str | None = None
    limit: tuple[str, float] | None = None
    domain: Domain | None = None

    def __str__(self) -> str:
        location = ""
        if self.path is not None:
            # qprogram.paths imports QProgram, so this import stays inside the method.
            from qprogram.paths import format_path  # ruff: ignore[import-outside-top-level]

            location = f" (at {format_path(self.path)})"
        return f"[{self.severity}] {self.code}: {self.message}{location}"


@dataclass(frozen=True)
class DomainConstraint:
    """A predicate's soft outcome: this node *would* be supported, except in the listed domains.

    The classifier collects these and subtracts ``exclude`` from the node's per-domain support set.
    Compare to :class:`Diagnostic`, which is a hard outcome (the node is unsupported outright in the
    slot being validated). A predicate may yield zero or more of each type from a single call.

    Attributes:
        node (Operation | Block): The AST node the constraint applies to. Must be a
            :class:`~qprogram.blocks.Block` — the classifier reports an op-targeted constraint as
            ``bad-domain-constraint``.
        exclude (frozenset[Domain]): Domains the node cannot run in. Usually a single-element
            frozenset (``{"rt"}``).
        reason (str): Human-readable explanation surfaced in the eventual ``forced-host``
            diagnostic, or in the ``empty-domain`` error if the exclusion leaves nothing.
    """

    node: Operation | Block
    exclude: frozenset[Domain]
    reason: str


# ---------------------------------------------------------------------------
# Predicate + ValidationContext
# ---------------------------------------------------------------------------


@runtime_checkable
class Predicate(Protocol):
    """Per-node validation predicate consulted while ``validate()`` walks the program.

    Receives a :class:`ValidationContext` with cross-op data-flow facts. Returns zero or more
    :class:`Diagnostic` or :class:`DomainConstraint` objects.

    A node is judged against each domain half of each slot it routes to, so a predicate carried by
    both the ``rt`` and the ``host`` half of a profile runs once per (domain, bus) pair: twice for a
    single-bus node, and twice more for every additional bus a multi-bus operation such as
    :class:`~qprogram.operations.Sync` touches. The validator discards duplicate outputs, so a predicate should be a cheap,
    side-effect-free function of ``(node, ctx)``.

    Motivating examples:

    - Flagging a :class:`Wait` whose ``duration`` is bound by an arbitrary-valued sweep —
      qblox can't run it at all, so the predicate emits a :class:`Diagnostic`.
    - Flagging an :class:`IQDrag` whose ``sigma`` is loop-bound — qblox can't realtime-update
      ``sigma``, but the platform can still dispatch one shot per iteration host-side, so the
      predicate emits a :class:`DomainConstraint` excluding ``"rt"``.
    """

    def __call__(
        self,
        node: Operation | Block,
        ctx: ValidationContext,
    ) -> Iterable[Diagnostic | DomainConstraint]:
        """Judge one AST node against whatever fact this predicate cares about.

        Args:
            node (Operation | Block): The node currently being validated.
            ctx (ValidationContext): Program-wide data-flow facts, for questions the node alone
                cannot answer.

        Returns:
            Zero or more outputs, in any order: a :class:`Diagnostic` for a hard failure in the
            slot being validated, a :class:`DomainConstraint` for a restriction that only rules out
            some domains.
        """
        ...


SweepKind = Literal["linear", "arbitrary", "averaged"]


class ValidationContext:
    """Read-only view of program-wide data-flow facts, built once per ``validate()`` call.

    Predicates use the queries here to answer "in *this* AST, is X legal?" without re-walking the
    tree. New queries are added here so predicate authors have a single, discoverable surface;
    predicates must treat the context as immutable.

    Args:
        variable_bindings (Mapping[Variable, Block]): Each loop-bound variable mapped to the block
            that binds it.
        sweep_kinds (Mapping[Variable, SweepKind]): Each loop-bound variable mapped to its sweep
            source's ``KIND``.
        max_loop_nesting (int): Deepest repetition-level count observed in the program.
        max_parallel_arity (int): Largest number of loops composed by any
            :class:`~qprogram.blocks.Parallel` block.
        measurement_count (int): Total number of measurement operations in the program.
        measurement_fields (Mapping[str, tuple[str, ...]] | None): Requested fields per measurement
            name; ``None`` is read as an empty mapping.
        program_buses (frozenset[str], optional): Every bus referenced anywhere in the program.
    """

    def __init__(  # ruff: ignore[too-many-arguments]  # all-keyword constructor for a small data carrier
        self,
        *,
        variable_bindings: Mapping[Variable, Block],
        sweep_kinds: Mapping[Variable, SweepKind],
        max_loop_nesting: int,
        max_parallel_arity: int,
        measurement_count: int,
        measurement_fields: Mapping[str, tuple[str, ...]] | None = None,
        program_buses: frozenset[str] = frozenset(),
    ) -> None:
        self._variable_bindings = dict(variable_bindings)
        self._sweep_kinds = dict(sweep_kinds)
        self._max_loop_nesting = max_loop_nesting
        self._max_parallel_arity = max_parallel_arity
        self._measurement_count = measurement_count
        self._measurement_fields: dict[str, tuple[str, ...]] = dict(measurement_fields or {})
        self._program_buses = frozenset(program_buses)

    def sweep_kind_of(self, var: Variable) -> SweepKind | None:
        """Return how ``var`` is loop-bound.

        Args:
            var (Variable): The variable to look up.

        Returns:
            The binding :class:`~qprogram.blocks.Sweep`'s source ``KIND`` — ``"linear"`` for an
            exact ``start + step * i`` ramp, ``"arbitrary"`` otherwise — or ``None`` when the
            variable is not loop-bound (set externally, or unused). ``"averaged"`` belongs to the
            vocabulary but no built-in source declares it.
        """
        return self._sweep_kinds.get(var)

    def binding_loop_of(self, var: Variable) -> Block | None:
        """Return the loop block that binds ``var``, or ``None`` if it has no binding.

        Args:
            var (Variable): The variable to look up.

        Returns:
            The :class:`~qprogram.blocks.Sweep` that binds ``var`` — a standalone loop or one of a
            :class:`~qprogram.blocks.Parallel`'s composed headers — or ``None`` when nothing binds
            it. This is the node a :class:`DomainConstraint` about ``var`` must target.
        """
        return self._variable_bindings.get(var)

    @property
    def max_loop_nesting(self) -> int:
        """Deepest repetition-level count observed in the program.

        A block adds a level when it declares :attr:`~qprogram.blocks.Block.REPEATS`, so a
        :class:`~qprogram.blocks.Parallel` counts as one level however many loops it composes —
        its headers advance in lockstep rather than nesting.
        """
        return self._max_loop_nesting

    @property
    def max_parallel_arity(self) -> int:
        """Largest ``len(parallel.loops)`` observed across any :class:`Parallel` block."""
        return self._max_parallel_arity

    @property
    def measurement_count(self) -> int:
        """Total number of :class:`MeasurementOperation` instances in the program."""
        return self._measurement_count

    def measurement_fields(self, name: str) -> tuple[str, ...] | None:
        """Return the ``fields`` tuple of the named measurement, or ``None`` if it doesn't exist.

        Predicates use this to check that a referenced measurement requested the data they care
        about — e.g. that a ``handle.state`` reference's source measurement requested
        :attr:`~qprogram.MeasurementField.STATE` classification.

        Args:
            name (str): Measurement name to look up.

        Returns:
            The measurement's requested field names in canonical order, or ``None`` when the
            program holds no measurement with that name.
        """
        return self._measurement_fields.get(name)

    def known_measurement_names(self) -> set[str]:
        """Return the set of every measurement name in the program.

        Returns:
            Every name a measurement in the program carries, whether the author spelled it or the
            builder allocated it.
        """
        return set(self._measurement_fields)

    @property
    def program_buses(self) -> frozenset[str]:
        """Every bus name referenced anywhere in the program (``QProgram.buses`` at build time).

        Elements may be :class:`~qprogram.BusRef` instances (which subclass ``str``), so per-bus
        routing through :meth:`PlatformCapabilities.for_bus` keeps its schema awareness. Used by
        the validator to route broadcast ops (``Sync(targets=None)``) across every touched bus.
        """
        return self._program_buses


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
        name (str): Unique profile name (e.g. ``"myvendor-default-v1"``).
        version (tuple[int, int, int]): Profile version as ``(major, minor, patch)``.
        extends (str | None): Name of a parent profile, or ``None`` for a root profile.
        capabilities (frozenset[str]): Capability tokens this profile advertises. Validated against
            :data:`CAPABILITY_REGISTRY` at construction.
        limits (Mapping[str, float]): Numeric thresholds. The validator ignores keys it does not
            know, so a profile may declare a limit an older validator has no check for.
        predicates (tuple[Predicate, ...]): Predicates run against every visited node.
        vendor_versions (Mapping[str, tuple[int, int, int]]): Informational record of which vendor
            extension versions this profile was designed for; mirrors the ``.qp``
            ``require <vendor> <version>`` line.
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
        profile (str): Name of the source profile.
        version (tuple[int, int, int]): Source profile version.
        capabilities (frozenset[str]): Merged set of capability tokens.
        limits (Mapping[str, float]): Merged numeric limits.
        predicates (tuple[Predicate, ...]): Merged predicates, parent's first.
        vendor_versions (Mapping[str, tuple[int, int, int]]): Merged vendor-extension version
            expectations.
    """

    profile: str
    version: tuple[int, int, int]
    capabilities: frozenset[str]
    limits: Mapping[str, float]
    predicates: tuple[Predicate, ...]
    vendor_versions: Mapping[str, tuple[int, int, int]]

    def supports(self, token: str) -> bool:
        """Return whether ``token`` is in this descriptor's capability set.

        Args:
            token (str): The capability token to look for.

        Returns:
            ``True`` when this descriptor advertises the token.
        """
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
            profile_name (str): Name of a profile registered with :func:`register_profile`.
            limit_overrides (Mapping[str, float] | None): Per-limit replacements for the merged
                values — typically supplied by a device that knows its hardware is tighter than the
                profile defaults.
            extra_predicates (tuple[Predicate, ...], optional): Site-specific predicates run on
                top of the profile's. Useful for rack-level constraints that don't belong in the
                vendor-shipped profile.

        Returns:
            The materialized :class:`CompilerCapabilities`.

        Raises:
            KeyError: If ``profile_name``, or any profile named by an ``extends`` link, is not
                registered.
            ValueError: If the ``extends`` chain forms a cycle.
        """
        profile = resolve_profile(profile_name)
        chain = _profile_chain(profile)
        merged_caps: set[str] = set()
        merged_limits: dict[str, float] = {}
        merged_predicates: list[Predicate] = []
        merged_vendor_versions: dict[str, tuple[int, int, int]] = {}
        for p in chain:
            merged_caps |= p.capabilities
            merged_limits.update(p.limits)
            merged_predicates.extend(p.predicates)
            # Root-first like limits: a child's expectation for a vendor overrides the parent's.
            merged_vendor_versions.update(p.vendor_versions)
        if limit_overrides:
            merged_limits.update(limit_overrides)
        merged_predicates.extend(extra_predicates)
        return cls(
            profile=profile.name,
            version=profile.version,
            capabilities=frozenset(merged_caps),
            limits=dict(merged_limits),
            predicates=tuple(merged_predicates),
            vendor_versions=merged_vendor_versions,
        )


# ---------------------------------------------------------------------------
# BusCapabilities + PlatformCapabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BusCapabilities:
    """Two stacked :class:`CompilerCapabilities` for a single bus or platform slot.

    Each half describes what the slot supports in that execution domain. Either may be ``None``
    when the bus or platform lacks an engine for that domain — e.g. a flux bus driven only by a
    slow DAC has ``rt=None``; a real-time-only bus has ``host=None``.

    Attributes:
        rt (CompilerCapabilities | None): What the slot supports on the real-time sequencer.
        host (CompilerCapabilities | None): What the slot supports under host-side orchestration.
    """

    rt: CompilerCapabilities | None
    host: CompilerCapabilities | None

    def get(self, domain: Domain) -> CompilerCapabilities | None:
        """Return the :class:`CompilerCapabilities` for ``domain``, or ``None`` if unsupported.

        Args:
            domain (Domain): The execution domain to read.

        Returns:
            The half describing that domain, or ``None`` when the slot has no engine for it.
        """
        return self.rt if domain == "rt" else self.host

    def supported_domains(self) -> frozenset[Domain]:
        """Return the set of domains this slot has a non-``None`` descriptor for.

        Returns:
            A subset of ``{"rt", "host"}``; empty when the slot can run nothing at all.
        """
        out: set[Domain] = set()
        if self.rt is not None:
            out.add("rt")
        if self.host is not None:
            out.add("host")
        return frozenset(out)


@dataclass(frozen=True)
class PlatformCapabilities:
    """The capability descriptor returned by :attr:`PlatformProtocol.capabilities`.

    Capabilities split along two grains — per-bus and platform-wide — plus a fallback slot for
    buses the per-bus mapping does not cover.

    Attributes:
        bus (Mapping[BusSelector, BusCapabilities]): One slot per ``(element_kind, bus_kind)``.
            Bus-touching ops (``play``, ``wait``, ``measure``, ...) route here via :meth:`for_bus`,
            and so do the tokens that travel with them: ``waveform.*`` and ``measure.fields.*``.
        platform (BusCapabilities): The platform-wide slot. Holds block-structure tokens,
            expression tokens, and bus-less ops.
        default_bus_profile (BusCapabilities): Fallback for raw-string buses lacking schema
            metadata, and for bus-touching ops whose ``(element, kind)`` key is missing from
            :attr:`bus`.
    """

    bus: Mapping[BusSelector, BusCapabilities]
    platform: BusCapabilities
    default_bus_profile: BusCapabilities

    def for_bus(self, bus: str | BusRef) -> BusCapabilities:
        """Resolve the :class:`BusCapabilities` that applies to ``bus``.

        A :class:`BusRef` carrying schema metadata routes to ``bus[(element, kind)]`` when present,
        otherwise to :attr:`default_bus_profile`. A plain ``str`` or schema-less ``BusRef`` always
        routes to :attr:`default_bus_profile`.

        Args:
            bus (str | BusRef): The bus a node touches.

        Returns:
            The slot whose capabilities apply to that bus.
        """
        from qprogram.buses import BusRef as _BusRef  # ruff: ignore[import-outside-top-level] — break the import cycle

        if isinstance(bus, _BusRef) and bus.schema is not None:
            key: BusSelector = (bus.element, bus.kind)
            if key in self.bus:
                return self.bus[key]
        return self.default_bus_profile


ExecutionPlan = Mapping["Operation | Block", frozenset[Domain]]
"""Classifier output: each AST node mapped to the set of domains it may execute in.

A ``frozenset({"rt"})`` entry means a real-time hardware path; ``frozenset({"host"})`` means host-side
dispatch; ``frozenset({"rt", "host"})`` means the platform may pick either at compile time. Delivered
by :meth:`PlatformProtocol.plan`."""


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

PROFILE_REGISTRY: dict[str, Profile] = {}
"""All registered profiles, keyed by ``Profile.name``."""


def register_profile(profile: Profile) -> None:
    """Register a profile in :data:`PROFILE_REGISTRY` under its name.

    Idempotent for the *same* Profile object — useful for import-time side-effect modules that may
    load twice. Re-registering a different profile under an existing name raises.

    Args:
        profile (Profile): The bundle to register under its own ``name``.

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

    Args:
        name (str): The registered profile name.

    Returns:
        The registered :class:`Profile`.

    Raises:
        KeyError: If ``name`` is not registered. The message lists the currently-known names.
    """
    if name not in PROFILE_REGISTRY:
        available = ", ".join(sorted(PROFILE_REGISTRY)) or "(none registered)"
        msg = f"Unknown profile {name!r}. Available: {available}"
        raise KeyError(msg)
    return PROFILE_REGISTRY[name]


def _profile_chain(profile: Profile) -> list[Profile]:
    """Walk the ``extends`` chain root-first, detecting cycles.

    Args:
        profile (Profile): The leaf profile to walk up from.

    Returns:
        The inheritance chain, root first and ``profile`` last — the order in which limits and
        vendor versions are merged so that a child overrides its parent.

    Raises:
        ValueError: If the chain revisits a profile, meaning ``extends`` forms a cycle.
        KeyError: If a profile in the chain names an unregistered parent.
    """
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
PredicateFn = Callable[
    ["Operation | Block", "ValidationContext"],
    Iterable["Diagnostic | DomainConstraint"],
]


__all__ = [
    "CAPABILITY_REGISTRY",
    "MEASUREMENT_FIELD_TOKEN_PREFIX",
    "PROFILE_REGISTRY",
    "WAVEFORM_TOKEN",
    "BusCapabilities",
    "BusSelector",
    "CompilerCapabilities",
    "Diagnostic",
    "Domain",
    "DomainConstraint",
    "ExecutionPlan",
    "PlatformCapabilities",
    "Predicate",
    "PredicateFn",
    "Profile",
    "SweepKind",
    "ValidationContext",
    "expression_tokens",
    "known_measurement_fields",
    "measurement_field_token",
    "register_capability_tokens",
    "register_profile",
    "register_waveform_token",
    "resolve_profile",
    "validate_tokens",
    "waveform_token",
]
