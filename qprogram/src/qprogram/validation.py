"""Capability validation + execution-domain classification for QProgram.

A single :func:`validate` entry point performs (a) per-node capability checks across the routed
(bus, domain) slots of :class:`~qprogram.PlatformCapabilities`, (b) bottom-up classification of
every AST node's execution domain, and (c) whole-program limit / Conditional checks. Returns
``(diagnostics, plan)`` — a flat diagnostic list (errors plus advisory info events) and a mapping
from every AST node to its final domain set.

The validator never raises — callers decide how to react. A typical
:meth:`PlatformProtocol.execute` calls :func:`validate` and raises
:class:`~qprogram.UnsupportedOperationError` on any ``severity="error"`` diagnostic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.operations.operation import MeasurementOperation, Operation
from qprogram.operations.wait import Wait
from qprogram.protocol import (
    BusCapabilities,
    Diagnostic,
    Domain,
    DomainConstraint,
    SweepKind,
    ValidationContext,
)
from qprogram.variable import (
    BinaryOp,
    Comparison,
    Expression,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    MeasurementRef,
    UnaryOp,
    Where,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from qprogram.protocol import ExecutionPlan, PlatformCapabilities
    from qprogram.qprogram import QProgram
    from qprogram.variable import Variable


_ALL_DOMAINS: frozenset[Domain] = frozenset({"hw", "sw"})


def validate(
    qprogram: QProgram,
    caps: PlatformCapabilities,
) -> tuple[list[Diagnostic], ExecutionPlan]:
    """Run capability validation + execution-domain classification.

    Algorithm:

    1. Pre-walk to build a :class:`ValidationContext` (variable bindings, sweep kinds, ...).
    2. Recursive post-order walk computing per-node ``available`` (domains where the node's
       :meth:`required_capabilities` fits the routed slot and no predicate-Diagnostic fires) and
       ``support`` (``available`` minus the union of DomainConstraints firing on the node), with
       block-level final domains taken as the intersection of children's domains.
    3. Whole-program limit checks (loop nesting, parallel arity, measurement count) against the
       platform slot's limits; ``min_wait_duration_ns`` checks against the bus slot's limits.
    4. Universal Conditional checks (unknown measurement, missing state classification).
    5. Emit one ``"forced-software"`` info per highest-block whose ``support`` was reduced from
       ``{hw, sw}`` to ``{sw}``.

    Args:
        qprogram: Program to validate.
        caps: Platform capability descriptor.

    Returns:
        ``(diagnostics, plan)``. The plan covers every visited AST node (excluding the root body).
    """
    ctx = _build_context(qprogram)
    diagnostics: list[Diagnostic] = []
    available: dict[Operation | Block, frozenset[Domain]] = {}
    support: dict[Operation | Block, frozenset[Domain]] = {}
    parent: dict[Operation | Block, Block | None] = {}

    for child in qprogram.body.elements:
        _classify_node(child, qprogram.body, caps, ctx, diagnostics, available, support, parent)

    diagnostics.extend(_check_limits(qprogram, ctx, caps))
    diagnostics.extend(_check_conditional_classification(qprogram, ctx))

    _emit_forced_software(diagnostics, available, support, parent)

    # The root body itself doesn't get classified, but external callers may want to know — give it
    # the intersection of top-level children.
    return diagnostics, dict(support)


# ---------------------------------------------------------------------------
# Per-node + block classification (single recursive post-order walk)
# ---------------------------------------------------------------------------


def _classify_node(  # noqa: PLR0913  # small data carrier — six mutable accumulators
    node: Operation | Block,
    parent_block: Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: dict[Operation | Block, frozenset[Domain]],
    support: dict[Operation | Block, frozenset[Domain]],
    parent: dict[Operation | Block, Block | None],
) -> tuple[frozenset[Domain], frozenset[Domain]]:
    """Recursively classify ``node`` and its descendants.

    Returns the node's ``(available, support)`` pair after combining with its children.
    Side-effects: appends diagnostics, populates ``available``, ``support``, ``parent``.
    """
    parent[node] = parent_block

    # 1. Per-node check (the node's own contribution).
    own_available, own_support, own_diags = _check_node_self(node, caps, ctx)
    diagnostics.extend(own_diags)

    # 2. Children — Conditional has arms+else_body; Parallel has loops+_elements; Block has _elements.
    children_av, children_sup = _classify_children(
        node, caps, ctx, diagnostics, available, support, parent,
    )

    final_av = own_available & children_av
    final_sup = own_support & children_sup
    available[node] = final_av
    support[node] = final_sup

    # 3. Block-level empty domain: own check passed but children disagreed.
    if isinstance(node, Block) and not final_sup and own_support:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="empty-domain",
                message=(
                    f"Block '{type(node).__name__}' has no executable domain: "
                    f"children require incompatible hw/sw support."
                ),
                node=node,
            ),
        )

    return final_av, final_sup


def _classify_children(  # noqa: PLR0913
    node: Operation | Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: dict[Operation | Block, frozenset[Domain]],
    support: dict[Operation | Block, frozenset[Domain]],
    parent: dict[Operation | Block, Block | None],
) -> tuple[frozenset[Domain], frozenset[Domain]]:
    """Intersect children's ``(available, support)`` for the structural shape of ``node``."""
    if isinstance(node, Conditional):
        kids: list[Operation | Block] = [body for _, body in node.arms]
        if node.else_body is not None:
            kids.append(node.else_body)
    elif isinstance(node, Parallel):
        kids = [*node.loops, *node.elements]
    elif isinstance(node, Block):
        kids = list(node.elements)
    else:
        return _ALL_DOMAINS, _ALL_DOMAINS

    av: frozenset[Domain] = _ALL_DOMAINS
    sup: frozenset[Domain] = _ALL_DOMAINS
    for kid in kids:
        kid_av, kid_sup = _classify_node(kid, node, caps, ctx, diagnostics, available, support, parent)
        av &= kid_av
        sup &= kid_sup
    return av, sup


def _check_node_self(
    node: Operation | Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
) -> tuple[frozenset[Domain], frozenset[Domain], list[Diagnostic]]:
    """Per-node availability + support check (no recursion into children).

    For each domain ``d`` of the routed slot, the domain is *available* iff the slot has a non-None
    :class:`CompilerCapabilities` for ``d``, the node's required tokens are a subset, and no
    predicate emits a :class:`Diagnostic` for ``d``. ``support`` then subtracts the union of
    :class:`DomainConstraint` exclude sets.

    Required-token routing splits along the ``expr.*`` namespace: expression tokens always check
    against ``caps.platform`` (they describe what Python AST node kinds the platform's compiler
    accepts, not what any particular bus's instrument can do), while every other token checks
    against the node's primary routed slot. Predicates only run from the primary slot.

    Diagnostics are emitted only when ``support`` is empty — when at least one domain works, the
    per-domain Diagnostics from predicates are suppressed (the fallback worked).
    """
    bus_slots = _route(node, caps)
    required = node.required_capabilities()
    expr_required = {t for t in required if t.startswith("expr.")}
    other_required = required - expr_required

    available: set[Domain] = set()
    per_domain_diags: dict[Domain, list[Diagnostic]] = {"hw": [], "sw": []}
    constraints: list[DomainConstraint] = []

    for d in ("hw", "sw"):
        bus_ccs = [bs.get(d) for bs in bus_slots]
        if any(cc is None for cc in bus_ccs):
            continue
        platform_cc = caps.platform.get(d)
        if expr_required and platform_cc is None:
            # Expression tokens to check, but the platform slot has no engine in this domain.
            continue
        domain_diags: list[Diagnostic] = []
        for cc in bus_ccs:
            if cc is None:  # pragma: no cover - already filtered above; pleases the type checker
                continue
            missing = sorted(other_required - cc.capabilities)
            domain_diags.extend(
                Diagnostic(
                    severity="error",
                    code="missing-capability",
                    message=(
                        f"'{type(node).__name__}' requires capability "
                        f"{token!r} which is not supported by profile "
                        f"{cc.profile!r} in domain {d!r}"
                    ),
                    node=node,
                    capability=token,
                    domain=d,
                )
                for token in missing
            )
            for predicate in cc.predicates:
                for output in predicate(node, ctx):
                    if isinstance(output, Diagnostic):
                        domain_diags.append(output)
                    else:
                        constraints.append(output)
        if expr_required and platform_cc is not None:
            missing_expr = sorted(expr_required - platform_cc.capabilities)
            domain_diags.extend(
                Diagnostic(
                    severity="error",
                    code="missing-capability",
                    message=(
                        f"'{type(node).__name__}' requires expression capability "
                        f"{token!r} which is not supported by platform profile "
                        f"{platform_cc.profile!r} in domain {d!r}"
                    ),
                    node=node,
                    capability=token,
                    domain=d,
                )
                for token in missing_expr
            )
        per_domain_diags[d] = domain_diags
        if not any(diag.severity == "error" for diag in domain_diags):
            available.add(d)

    available_set: frozenset[Domain] = frozenset(available)
    excluded: set[Domain] = set()
    for constraint in constraints:
        excluded.update(constraint.exclude)
    support: frozenset[Domain] = available_set - frozenset(excluded)

    diagnostics_out: list[Diagnostic] = []
    if not support:
        # Surface every per-domain diagnostic so the user sees why each domain failed.
        for d in ("hw", "sw"):
            diagnostics_out.extend(per_domain_diags[d])
        if not diagnostics_out:
            # No predicate or token complaints — the slot(s) had None engines in every domain.
            diagnostics_out.append(
                Diagnostic(
                    severity="error",
                    code="empty-domain",
                    message=(
                        f"'{type(node).__name__}' has no executable domain on its routed slot."
                    ),
                    node=node,
                ),
            )

    return available_set, support, diagnostics_out


def _route(node: Operation | Block, caps: PlatformCapabilities) -> list[BusCapabilities]:
    """Return the :class:`BusCapabilities` slots that ``node`` routes to.

    - Blocks always route to ``caps.platform``.
    - Bus-less ops (``BUS_ATTRS = ()``) route to ``caps.platform``.
    - Bus-touching ops with one or more buses route to ``caps.for_bus(bus)`` per bus; the caller
      intersects across the returned list when multiple are present.
    - Bus-touching ops whose bus list resolves to empty (e.g. ``Sync(targets=None)``, which means
      "sync every active bus") route to ``caps.default_bus_profile`` — the op needs *some* bus
      slot to validate against, and the default is the platform-wide fallback.
    """
    if isinstance(node, Block):
        return [caps.platform]
    bus_attrs = type(node).BUS_ATTRS
    if not bus_attrs:
        return [caps.platform]
    bus_values: list[str] = []
    for attr in bus_attrs:
        value = getattr(node, attr, None)
        if isinstance(value, str):
            bus_values.append(value)
        elif isinstance(value, list):
            bus_values.extend(v for v in value if isinstance(v, str))
    if not bus_values:
        return [caps.default_bus_profile]
    return [caps.for_bus(b) for b in bus_values]


# ---------------------------------------------------------------------------
# Forced-software info emission
# ---------------------------------------------------------------------------


def _emit_forced_software(
    diagnostics: list[Diagnostic],
    available: Mapping[Operation | Block, frozenset[Domain]],
    support: Mapping[Operation | Block, frozenset[Domain]],
    parent: Mapping[Operation | Block, Block | None],
) -> None:
    """Emit one ``severity="info"`` ``"forced-software"`` per highest forced-sw block.

    A block is *forced sw* when its final ``support`` is ``{sw}`` and its ``available`` contains
    ``"hw"`` (so hw would have been viable without DomainConstraints). The *highest* block in a
    forced-sw chain is the one whose parent isn't itself forced sw — emitting only there keeps
    the diagnostic output skimmable.
    """
    sw_only: frozenset[Domain] = frozenset({"sw"})
    for node, sup in support.items():
        if not isinstance(node, Block):
            continue
        if sup != sw_only:
            continue
        if "hw" not in available[node]:
            continue
        p = parent.get(node)
        if (
            p is not None
            and support.get(p) == sw_only
            and "hw" in available.get(p, frozenset())
        ):
            continue
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="forced-software",
                message=(
                    f"Block '{type(node).__name__}' falls back to software execution; "
                    f"a descendant excludes hardware-realtime."
                ),
                node=node,
                domain="sw",
            ),
        )


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


def _build_context(qprogram: QProgram) -> ValidationContext:
    """Walk the program once to gather the cross-op data-flow facts predicates need.

    Why max_loop_nesting counts Parallel as one level: depth measures how many sweeps wrap a leaf
    operation, not how many sweep variables exist — a parallel composing two for-loops still
    contributes one nesting level.
    """
    variable_bindings: dict[Variable, Block] = {}
    sweep_kinds: dict[Variable, SweepKind] = {}
    measurement_count = 0
    measurement_returns: dict[str, tuple[str, ...]] = {}
    max_parallel_arity = 0
    max_depth = 0

    def visit(node: Block | Operation, depth: int) -> None:
        nonlocal measurement_count, max_parallel_arity, max_depth
        max_depth = max(max_depth, depth)
        if isinstance(node, ForLoop):
            variable_bindings[node.variable] = node
            sweep_kinds[node.variable] = "linear"
            for child in node.elements:
                visit(child, depth + 1)
        elif isinstance(node, Loop):
            variable_bindings[node.variable] = node
            sweep_kinds[node.variable] = "arbitrary"
            for child in node.elements:
                visit(child, depth + 1)
        elif isinstance(node, Parallel):
            max_parallel_arity = max(max_parallel_arity, len(node.loops))
            for header in node.loops:
                variable_bindings[header.variable] = header
                sweep_kinds[header.variable] = "linear" if isinstance(header, ForLoop) else "arbitrary"
            for child in node.elements:
                visit(child, depth + 1)
        elif isinstance(node, Conditional):
            for _, body in node.arms:
                for child in body.elements:
                    visit(child, depth + 1)
            if node.else_body is not None:
                for child in node.else_body.elements:
                    visit(child, depth + 1)
        elif isinstance(node, Block):
            for child in node.elements:
                visit(child, depth)
        elif isinstance(node, MeasurementOperation):
            measurement_count += 1
            measurement_returns[node.name] = node.returns

    for child in qprogram.body.elements:
        visit(child, 0)

    return ValidationContext(
        variable_bindings=variable_bindings,
        sweep_kinds=sweep_kinds,
        max_loop_nesting=max_depth,
        max_parallel_arity=max_parallel_arity,
        measurement_count=measurement_count,
        measurement_returns=measurement_returns,
    )


# ---------------------------------------------------------------------------
# Whole-program limit checks
# ---------------------------------------------------------------------------


def _check_limits(
    qprogram: QProgram,
    ctx: ValidationContext,
    caps: PlatformCapabilities,
) -> list[Diagnostic]:
    """Check whole-program limits.

    ``max_loop_nesting``, ``max_parallel_loops``, ``max_measurements`` live at the platform slot
    (whichever of hw/sw is present; hw wins when both are set since hw is typically the more
    constrained engine). ``min_wait_duration_ns`` lives at the bus slot — each :class:`Wait` is
    checked against its routed bus's limits.
    """
    diagnostics: list[Diagnostic] = []
    platform_limits = _pick_limits(caps.platform)

    if "max_loop_nesting" in platform_limits and ctx.max_loop_nesting > platform_limits["max_loop_nesting"]:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program nests loops {ctx.max_loop_nesting} deep; "
                    f"limit max_loop_nesting={platform_limits['max_loop_nesting']:g}"
                ),
                limit=("max_loop_nesting", float(ctx.max_loop_nesting)),
            ),
        )

    if (
        "max_parallel_loops" in platform_limits
        and ctx.max_parallel_arity > platform_limits["max_parallel_loops"]
    ):
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program has a Parallel block with {ctx.max_parallel_arity} concurrent loops; "
                    f"limit max_parallel_loops={platform_limits['max_parallel_loops']:g}"
                ),
                limit=("max_parallel_loops", float(ctx.max_parallel_arity)),
            ),
        )

    if "max_measurements" in platform_limits and ctx.measurement_count > platform_limits["max_measurements"]:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program contains {ctx.measurement_count} measurements; "
                    f"limit max_measurements={platform_limits['max_measurements']:g}"
                ),
                limit=("max_measurements", float(ctx.measurement_count)),
            ),
        )

    for node in qprogram.body.walk():
        if isinstance(node, Wait) and isinstance(node.duration, int):
            bus_limits = _pick_limits(caps.for_bus(node.bus))
            if "min_wait_duration_ns" in bus_limits and node.duration < bus_limits["min_wait_duration_ns"]:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        code="limit-exceeded",
                        message=(
                            f"Wait duration {node.duration} ns is shorter than "
                            f"min_wait_duration_ns={bus_limits['min_wait_duration_ns']:g}"
                        ),
                        node=node,
                        limit=("min_wait_duration_ns", float(node.duration)),
                    ),
                )

    return diagnostics


def _pick_limits(slot: BusCapabilities) -> Mapping[str, float]:
    """Pick limits from ``slot`` — prefer ``hw`` when present, fall back to ``sw``, else empty."""
    if slot.hw is not None:
        return slot.hw.limits
    if slot.sw is not None:
        return slot.sw.limits
    return {}


# ---------------------------------------------------------------------------
# Universal Conditional checks
# ---------------------------------------------------------------------------


def _iter_measurement_refs(expr: Expression) -> Iterator[MeasurementRef]:
    """Yield every :class:`MeasurementRef` reachable from ``expr`` via recursive descent."""
    if isinstance(expr, MeasurementRef):
        yield expr
        return
    if isinstance(expr, (BinaryOp, Comparison, LogicalBinaryOp)):
        yield from _iter_measurement_refs(expr.left)
        yield from _iter_measurement_refs(expr.right)
        return
    if isinstance(expr, (UnaryOp, LogicalNot)):
        yield from _iter_measurement_refs(expr.operand)
        return
    if isinstance(expr, MathFunc):
        for op in expr.operands:
            yield from _iter_measurement_refs(op)
        return
    if isinstance(expr, Where):
        yield from _iter_measurement_refs(expr.condition)
        yield from _iter_measurement_refs(expr.then)
        yield from _iter_measurement_refs(expr.else_)


def _check_conditional_classification(
    qprogram: QProgram,
    ctx: ValidationContext,
) -> list[Diagnostic]:
    """Validate :class:`Conditional` arm conditions — profile-independent.

    Emits two diagnostic codes:

    - ``unknown-measurement`` — the condition references a handle whose name doesn't match any
      measurement in the program (usually a raw ``MeasurementRef`` built outside ``measure(...)``).
    - ``missing-classification`` — the condition references ``handle.state`` but the source
      measurement's ``returns`` doesn't include ``"state"``.
    """
    diagnostics: list[Diagnostic] = []
    known = ctx.known_measurement_names()
    for node in qprogram.body.walk():
        if not isinstance(node, Conditional):
            continue
        for cond, _ in node.arms:
            for ref in _iter_measurement_refs(cond):
                name = ref.handle.name
                if name not in known:
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            code="unknown-measurement",
                            message=(
                                f"Conditional references measurement {name!r}, "
                                f"but no measurement with that name exists in "
                                f"the program"
                            ),
                            node=node,
                        ),
                    )
                    continue
                returns = ctx.measurement_returns(name) or ()
                if ref.field == "state" and "state" not in returns:
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            code="missing-classification",
                            message=(
                                f"Conditional references {name}.state, but "
                                f"the measurement does not request state "
                                f"classification (add 'state' to returns=)"
                            ),
                            node=node,
                        ),
                    )
    return diagnostics


__all__ = ["validate"]
