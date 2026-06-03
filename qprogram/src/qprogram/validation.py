"""Capability validation + execution-domain classification for QProgram.

A single :func:`validate` entry point walks the program AST, checking each operation's required
capability tokens against its routed :class:`~qprogram.BusCapabilities` slot and classifying each
block's execution domain from its op-children's consensus plus any
:class:`~qprogram.DomainConstraint` predicates emitted while walking. Returns
``(diagnostics, plan)``: diagnostics is a flat list (errors + advisory info events), and the plan
maps every AST node to the domain set it may execute in.

The classification rules implemented here match the spec:

* Operations have a domain derived from which slots (``hw``, ``sw``) of their routed
  :class:`BusCapabilities` carry the required tokens. Vendor-specific operations have a fixed
  domain by design (e.g. qdac ops are ``sw`` only); core ops depend on the platform's wiring.
* Block classification is determined by **op-children consensus only** — child blocks act as
  units and don't constrain the parent's domain. If a block contains all-HW op-children the
  block's natural domain is ``{hw, sw}`` (HW ops can run real-time or be dispatched per shot);
  if all-SW the block must be ``{sw}``; mixed op-children at the same level → ``mixed-domain``
  error (spec (d)).
* :class:`~qprogram.DomainConstraint` predicate outputs target **block** nodes (typically the
  binding loop of a swept variable) and subtract from the targeted block's domain. The
  operation's classification is unaffected (spec (e2)).
* Nesting: a hardware-only block (``support == {hw}``) can only contain hardware-capable
  block-children — an SW block-child inside an HW block-parent emits ``sw-in-hw`` error (spec
  (e1)). The reverse (HW block inside SW block) is always allowed.
* A block whose natural ``{hw, sw}`` is reduced to ``{sw}`` by a constraint surfaces a single
  ``severity="warning"`` ``"forced-software"`` diagnostic on the highest such block in its chain,
  carrying the constraint reasons collected from the forced subtree.

Every node-bearing diagnostic is stamped with a structural :attr:`Diagnostic.path` (see
:mod:`qprogram.paths`) so tooling can locate it in a serialized ``.qp`` file via
:attr:`QProgram.source_map`.

The validator never raises — callers decide how to react. A typical
:meth:`PlatformProtocol.execute` calls :func:`validate`, raises
:class:`~qprogram.UnsupportedOperationError` on any ``severity="error"`` diagnostic, and surfaces
``"warning"`` / ``"info"`` diagnostics without raising.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import MutableMapping
from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar

from qprogram.blocks.average import Average
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
_HW_ONLY: frozenset[Domain] = frozenset({"hw"})
_SW_ONLY: frozenset[Domain] = frozenset({"sw"})

_V = TypeVar("_V")


class _IdentityNodeMap(MutableMapping["Operation | Block", _V]):
    """A mapping over AST nodes keyed by **object identity**, not structural equality.

    AST nodes use structural ``__eq__`` / ``__hash__`` (two identical ``play`` ops compare equal),
    which is right for round-trip comparison but wrong for the classifier's bookkeeping: a plain
    ``dict`` would collapse repeated identical operations into a single entry, so a three-op
    program could come back with a two-entry :data:`~qprogram.ExecutionPlan`. Keying by ``id()``
    keeps one entry per node *instance* while still satisfying the public ``Mapping`` contract
    (iteration yields the node objects; ``plan[node]`` looks up by identity).

    Holds a reference to every key, so ``id()`` values can't be recycled while the map lives.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[int, tuple[Operation | Block, _V]] = {}

    def __getitem__(self, key: Operation | Block) -> _V:
        try:
            return self._entries[id(key)][1]
        except KeyError:
            raise KeyError(key) from None

    def __setitem__(self, key: Operation | Block, value: _V) -> None:
        self._entries[id(key)] = (key, value)

    def __delitem__(self, key: Operation | Block) -> None:
        try:
            del self._entries[id(key)]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self):  # noqa: ANN204 — Iterator[Operation | Block]
        return (node for node, _ in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return id(key) in self._entries

    def __repr__(self) -> str:
        items = ", ".join(f"{type(node).__name__}: {value!r}" for node, value in self._entries.values())
        return f"{type(self).__name__}({{{items}}})"


def validate(
    qprogram: QProgram,
    caps: PlatformCapabilities,
) -> tuple[list[Diagnostic], ExecutionPlan]:
    """Run capability validation + execution-domain classification.

    Algorithm:

    1. Pre-walk to build a :class:`ValidationContext` (variable bindings, sweep kinds, …).
    2. Single recursive post-order walk that, per node, computes:

       - For operations: the *available* domain set is the slots where the op's required tokens
         are present (and any predicate-emitted :class:`Diagnostic` is absent). The op's
         ``support`` equals its ``available`` — :class:`DomainConstraint` outputs are *not*
         applied to the op; they are routed to the **block** they target (typically the binding
         loop of a swept variable).
       - For blocks: ``support = own_available & natural_from_ops - exclude_from_constraints``,
         where ``natural_from_ops = {sw} | (ops_consensus & {hw})`` captures the rule that all-HW
         op-children permit either an HW block or an SW-dispatched block. Mixed op-children produce a
         ``mixed-domain`` error. Block-children act as units; they don't constrain the parent's
         domain but the parent's domain constrains them (no SW block inside an HW block).
    3. Whole-program limit checks (loop nesting, parallel arity, measurement count) against the
       platform slot's limits; ``min_wait_duration_ns`` checks against the bus slot's limits.
    4. Universal Conditional checks (unknown measurement, missing state classification).
    5. Emit one ``"forced-software"`` warning per highest-block whose ``support`` was reduced
       from ``{hw, sw}`` to ``{sw}``, with the subtree's constraint reasons in the message.
    6. Stamp each node-bearing diagnostic with its structural :attr:`Diagnostic.path`.

    Args:
        qprogram: Program to validate.
        caps: Platform capability descriptor.

    Returns:
        ``(diagnostics, plan)``. The plan covers every visited AST node (excluding the root
        body) and is **identity-keyed**: each node *instance* gets its own entry, even when two
        nodes are structurally identical (``plan[node]`` looks up by ``id``, and iterating the
        plan yields every instance).

    Note:
        Programs containing fragment :class:`~qprogram.operations.Call` nodes are **expanded
        first** (:meth:`QProgram.expand`) — capabilities are checked against the substituted
        fragment bodies, and diagnostics reference nodes of that internal expansion. Callers
        that need the identity-keyed plan for nodes they hold should expand explicitly and
        validate the expanded program.
    """
    from qprogram.operations.call import Call  # noqa: PLC0415

    if any(isinstance(node, Call) for node in qprogram.body.walk()):
        qprogram = qprogram.expand()
    ctx = _build_context(qprogram)
    diagnostics: list[Diagnostic] = []
    available: _IdentityNodeMap[frozenset[Domain]] = _IdentityNodeMap()
    support: _IdentityNodeMap[frozenset[Domain]] = _IdentityNodeMap()
    parent: _IdentityNodeMap[Block | None] = _IdentityNodeMap()
    constraints_by_block: dict[int, list[DomainConstraint]] = defaultdict(list)

    for child in qprogram.body.elements:
        _classify_node(
            child,
            qprogram.body,
            caps,
            ctx,
            diagnostics,
            available,
            support,
            parent,
            constraints_by_block,
        )

    diagnostics.extend(_check_limits(qprogram, ctx, caps))
    diagnostics.extend(_check_conditional_classification(qprogram, ctx))
    _emit_forced_software(diagnostics, available, support, parent, constraints_by_block)

    return _stamp_paths(diagnostics, qprogram), support


# ---------------------------------------------------------------------------
# Single recursive post-order walk
# ---------------------------------------------------------------------------


def _classify_node(  # noqa: PLR0913
    node: Operation | Block,
    parent_block: Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: _IdentityNodeMap[frozenset[Domain]],
    support: _IdentityNodeMap[frozenset[Domain]],
    parent: _IdentityNodeMap[Block | None],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Recursively classify ``node`` and its descendants in post-order.

    Side-effects: populates ``diagnostics``, ``available``, ``support``, ``parent``, and
    ``constraints_by_block`` (keyed by the target block's :func:`id`).
    """
    parent[node] = parent_block

    if isinstance(node, Block):
        _classify_block(
            node,
            caps,
            ctx,
            diagnostics,
            available,
            support,
            parent,
            constraints_by_block,
        )
    else:
        _classify_operation(
            node,
            caps,
            ctx,
            diagnostics,
            available,
            support,
            constraints_by_block,
        )


def _classify_operation(  # noqa: PLR0913
    op: Operation,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: _IdentityNodeMap[frozenset[Domain]],
    support: _IdentityNodeMap[frozenset[Domain]],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Classify a leaf operation.

    An op's ``support`` equals its ``available`` — :class:`DomainConstraint` outputs target
    block nodes and never directly subtract from the op's support (the op's classification is
    by spec fixed by its vendor / bus slot, not by surrounding variables).
    """
    avail, op_diags, dcs = _check_node_self(op, caps, ctx)
    diagnostics.extend(op_diags)
    available[op] = avail
    support[op] = avail
    _route_constraints(dcs, op, diagnostics, constraints_by_block)


def _classify_block(  # noqa: PLR0913
    block: Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: _IdentityNodeMap[frozenset[Domain]],
    support: _IdentityNodeMap[frozenset[Domain]],
    parent: _IdentityNodeMap[Block | None],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Classify a block from its immediate op-children plus any DomainConstraints targeting it.

    Block-children are recursed into first (post-order) and tracked separately — they don't
    enter the natural-domain computation, but the (e1) nesting check inspects them after the
    parent's domain is known.
    """
    # 1. Identify immediate op-children vs block-children (without recursing yet).
    op_children, block_children = _immediate_children(block)

    # 2. Recurse into both — post-order, so children's support and any constraints they raise are
    # in place before we classify this block.
    for child in (*block_children, *op_children):
        _classify_node(
            child,
            block,
            caps,
            ctx,
            diagnostics,
            available,
            support,
            parent,
            constraints_by_block,
        )

    # 3. Block's own check (block tokens against the platform slot).
    own_avail, own_diags, own_dcs = _check_node_self(block, caps, ctx)
    diagnostics.extend(own_diags)
    _route_constraints(own_dcs, block, diagnostics, constraints_by_block)

    # 4. Op-children consensus → block's natural domain (spec (c) + (d)).
    # Op-children whose own support is already empty are excluded from the consensus: they were
    # diagnosed at their own node (missing-capability / predicate error), and folding their empty
    # set in would manufacture a misleading extra "mixed-domain" error on the parent. The block
    # still can't execute, so its support goes empty — silently, the child diagnostic explains it.
    healthy_ops = [op for op in op_children if support[op]]
    has_defective_op = len(healthy_ops) < len(op_children)
    if healthy_ops:
        ops_consensus = _ALL_DOMAINS
        for op in healthy_ops:
            ops_consensus &= support[op]
        if not ops_consensus:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="mixed-domain",
                    message=(
                        f"Block '{type(block).__name__}' has op-children with incompatible "
                        f"domain singletons: "
                        f"{[(type(op).__name__, sorted(support[op])) for op in healthy_ops]}"
                    ),
                    node=block,
                ),
            )
            available[block] = frozenset()
            support[block] = frozenset()
            return
        # Per spec (c): all-HW ops → block IS HW; all-SW ops → block IS SW. The natural domain
        # is the ops' consensus directly. (e2) constraints can still shift HW → SW; see the
        # fallback below.
        natural_from_ops = ops_consensus
    else:
        # No (healthy) op-children — the block's domain is unconstrained by content.
        natural_from_ops = _ALL_DOMAINS
    if has_defective_op:
        # At least one op-child can run nowhere: the block as a whole cannot execute. The child's
        # own diagnostics already say why — no additional block-level diagnostic.
        available[block] = frozenset()
        support[block] = frozenset()
        return

    # 5. Combine with own slot availability.
    avail = own_avail & natural_from_ops

    # 6. Apply DomainConstraints accumulated for this block.
    excluded: set[Domain] = set()
    for dc in constraints_by_block.get(id(block), ()):
        excluded.update(dc.exclude)

    # 6b. Block-children SW propagation (spec (e1) constructive side): if any block-child has
    # support {sw}, the parent must also support sw — an SW sub-block cannot be hosted by an HW
    # parent. We treat that as an implicit ``exclude={"hw"}`` constraint on the parent. The
    # explicit (e1) check below catches the rare case where this propagation can't be honoured
    # (the parent's slot doesn't carry sw at all).
    for child in block_children:
        if support.get(child) == _SW_ONLY:
            excluded.add("hw")
            break

    sup = avail - frozenset(excluded)

    # 6c. (e2) HW→SW fallback: when a constraint (or propagated SW block-child) excludes hw
    # from an all-HW block, the block falls back to SW dispatch (one HW shot per iteration). The
    # op-children remain HW; the block's iteration mechanism becomes software. This implements
    # the spec's "variable influences block class, not op class" rule.
    if not sup and "hw" in excluded and natural_from_ops == _HW_ONLY and "sw" in own_avail:
        sup = _SW_ONLY

    available[block] = avail
    support[block] = sup

    # 7. Empty-domain error if support is empty.
    if not sup:
        if avail:
            reasons = "; ".join(f"{dc.reason}" for dc in constraints_by_block.get(id(block), ()))
            msg = f"Block '{type(block).__name__}' has no executable domain after DomainConstraints" + (
                f": {reasons}" if reasons else "."
            )
        elif not own_avail:
            msg = (
                f"Block '{type(block).__name__}' has no executable domain — the platform slot "
                f"supports none of {sorted(_ALL_DOMAINS)} for the block's required tokens."
            )
        else:
            msg = (
                f"Block '{type(block).__name__}' has no executable domain: own slot supports "
                f"{sorted(own_avail)} but op-children consensus is {sorted(natural_from_ops)}."
            )
        diagnostics.append(
            Diagnostic(severity="error", code="empty-domain", message=msg, node=block),
        )

    # 8. (e1) nesting check: HW-only parent can only contain HW-capable block-children.
    if sup == _HW_ONLY:
        for child in block_children:
            child_sup = support.get(child, _ALL_DOMAINS)
            if "hw" not in child_sup:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        code="sw-in-hw",
                        message=(
                            f"Software block '{type(child).__name__}' nested inside hardware "
                            f"block '{type(block).__name__}' — the FPGA cannot host an SW "
                            f"sub-block (only HW-in-SW is allowed by spec (e1))."
                        ),
                        node=child,
                    ),
                )


def _constraint_seen(dc: DomainConstraint, existing: list[DomainConstraint]) -> bool:
    """Whether an equivalent constraint is already recorded.

    Equivalence is *identity* on the target node plus structural equality on the restriction —
    plain dataclass equality would compare nodes structurally and could merge constraints that
    target two different (but identical-looking) loop instances.
    """
    return any(e.node is dc.node and e.exclude == dc.exclude and e.reason == dc.reason for e in existing)


def _route_constraints(
    dcs: list[DomainConstraint],
    source_node: Operation | Block,
    diagnostics: list[Diagnostic],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Accumulate each constraint into the per-target-block bucket.

    A constraint must target a :class:`Block` — predicates that emit ``DomainConstraint`` with
    ``node`` set to something else are a programming error in the predicate author's code; we
    emit a meta-diagnostic explaining that and drop the constraint on the floor. Equivalent
    constraints already in the bucket are skipped (a profile filling both halves of a slot runs
    its predicates once per domain).
    """
    for dc in dcs:
        if isinstance(dc.node, Block):
            bucket = constraints_by_block[id(dc.node)]
            if not _constraint_seen(dc, bucket):
                bucket.append(dc)
        else:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="bad-domain-constraint",
                    message=(
                        f"Predicate triggered by '{type(source_node).__name__}' emitted a "
                        f"DomainConstraint whose 'node' is not a Block — DomainConstraints must "
                        f"target the loop block they restrict. Got: "
                        f"{type(dc.node).__name__ if dc.node is not None else 'None'}"
                    ),
                    node=source_node if isinstance(source_node, Operation | Block) else None,
                ),
            )


def _immediate_children(block: Block) -> tuple[list[Operation], list[Block]]:
    """Partition ``block``'s immediate children into (op-children, block-children).

    Conditional has arm bodies + else_body (each a :class:`Block`) — those bodies are
    block-children of the Conditional, not op-children. Parallel has loop headers (Blocks) plus
    a body (`._elements`); the loop headers are block-children, and the body's elements are
    immediate children of the Parallel.
    """
    if isinstance(block, Conditional):
        bodies: list[Block] = [body for _, body in block.arms]
        if block.else_body is not None:
            bodies.append(block.else_body)
        return [], bodies
    if isinstance(block, Parallel):
        block_children: list[Block] = [*block.loops]
        op_children: list[Operation] = []
        for el in block.elements:
            if isinstance(el, Block):
                block_children.append(el)
            else:
                op_children.append(el)
        return op_children, block_children
    # Regular Block, ForLoop, Loop, Average — flat .elements.
    op_children = []
    block_children = []
    for el in block.elements:
        if isinstance(el, Block):
            block_children.append(el)
        else:
            op_children.append(el)
    return op_children, block_children


# ---------------------------------------------------------------------------
# Per-node slot check (tokens + predicates)
# ---------------------------------------------------------------------------


def _check_node_self(
    node: Operation | Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
) -> tuple[frozenset[Domain], list[Diagnostic], list[DomainConstraint]]:
    """Run the node's slot-based token check and any registered predicates.

    Returns ``(available, diagnostics, domain_constraints)``:

    - ``available``: domains where the node's required tokens fit the routed slot AND no
      predicate emitted a :class:`Diagnostic`.
    - ``diagnostics``: the per-domain failure reasons, **only** if the result ``available`` is
      empty (so per-domain noise is suppressed when at least one domain works). Deduplicated:
      a token missing in both domains produces one diagnostic naming both, and a predicate
      registered on both halves of a slot contributes its (equal) Diagnostic once.
    - ``domain_constraints``: the ``DomainConstraint`` outputs the predicates emitted,
      deduplicated by equality — the same profile filling both the hw and sw halves runs its
      predicates twice and would otherwise double every constraint (and double the reason text
      in downstream ``empty-domain`` messages).

    Required-token routing splits along the ``expr.*`` namespace: expression tokens always check
    against ``caps.platform`` (they describe what Python AST node kinds the platform's compiler
    accepts, not what any particular bus's instrument can do), while every other token checks
    against the node's primary routed slot.
    """
    bus_slots = _route(node, caps, ctx)
    required = node.required_capabilities()
    expr_required = {t for t in required if t.startswith("expr.")}
    other_required = required - expr_required

    available: set[Domain] = set()
    # token -> [(domain, profile_name, is_expr)] in domain order; merged into one diagnostic per
    # token at the end so a token missing in both domains isn't reported twice.
    missing_by_token: dict[str, list[tuple[Domain, str, bool]]] = {}
    predicate_diags: list[Diagnostic] = []
    constraints: list[DomainConstraint] = []

    for d in ("hw", "sw"):
        bus_ccs = [bs.get(d) for bs in bus_slots]
        if any(cc is None for cc in bus_ccs):
            continue
        platform_cc = caps.platform.get(d)
        if expr_required and platform_cc is None:
            continue
        domain_failed = False
        for cc in bus_ccs:
            if cc is None:  # pragma: no cover — already filtered above; pleases the type checker
                continue
            for token in sorted(other_required - cc.capabilities):
                missing_by_token.setdefault(token, []).append((d, cc.profile, False))
                domain_failed = True
            for predicate in cc.predicates:
                for output in predicate(node, ctx):
                    if isinstance(output, Diagnostic):
                        if output not in predicate_diags:
                            predicate_diags.append(output)
                        domain_failed = True
                    elif not _constraint_seen(output, constraints):
                        constraints.append(output)
        if expr_required and platform_cc is not None:
            for token in sorted(expr_required - platform_cc.capabilities):
                missing_by_token.setdefault(token, []).append((d, platform_cc.profile, True))
                domain_failed = True
        if not domain_failed:
            available.add(d)

    available_set: frozenset[Domain] = frozenset(available)

    diagnostics_out: list[Diagnostic] = []
    if not available_set:
        # Surface the failure reasons — one diagnostic per missing token (not per domain).
        for token in sorted(missing_by_token):
            sites = missing_by_token[token]
            domains = sorted({d for d, _, _ in sites})
            where = " / ".join(f"{profile!r} ({d})" for d, profile, _ in sites)
            kind = "expression capability" if sites[0][2] else "capability"
            diagnostics_out.append(
                Diagnostic(
                    severity="error",
                    code="missing-capability",
                    message=(f"'{type(node).__name__}' requires {kind} {token!r} which is not supported by {where}"),
                    node=node,
                    capability=token,
                    domain=domains[0] if len(domains) == 1 else None,
                ),
            )
        diagnostics_out.extend(predicate_diags)
        if not diagnostics_out:
            # No predicate or token complaints — the slot(s) had ``None`` engines in every domain.
            diagnostics_out.append(
                Diagnostic(
                    severity="error",
                    code="empty-domain",
                    message=(f"'{type(node).__name__}' has no executable domain on its routed slot."),
                    node=node,
                ),
            )

    return available_set, diagnostics_out, constraints


def _route(
    node: Operation | Block,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
) -> list[BusCapabilities]:
    """Return the :class:`BusCapabilities` slots that ``node`` routes to.

    - Blocks always route to ``caps.platform``.
    - Bus-less ops (``BUS_ATTRS = ()``) route to ``caps.platform``.
    - Bus-touching ops with one or more buses route to ``caps.for_bus(bus)`` per bus; the caller
      intersects across the returned list when multiple are present.
    - Broadcast ops whose bus list resolves to empty (``Sync(targets=None)``, which means "sync
      every active bus") route across **every** bus in the program — the intersection semantics
      then match the explicit-targets form. A broadcast in a program with no buses at all, or a
      non-broadcast op with an empty bus list, falls back to ``caps.default_bus_profile``.
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
        if type(node).BROADCASTS_WHEN_NO_BUS and ctx.program_buses:
            return [caps.for_bus(b) for b in sorted(ctx.program_buses)]
        return [caps.default_bus_profile]
    return [caps.for_bus(b) for b in bus_values]


# ---------------------------------------------------------------------------
# Diagnostic path stamping
# ---------------------------------------------------------------------------


def _stamp_paths(diagnostics: list[Diagnostic], qprogram: QProgram) -> list[Diagnostic]:
    """Return ``diagnostics`` with each node-bearing entry's :attr:`Diagnostic.path` filled in.

    One walk builds an identity-keyed node→path table; :class:`Diagnostic` is frozen, so entries
    are rebuilt via :func:`dataclasses.replace`. A node that is no longer reachable from the body
    (defensive — shouldn't happen) keeps ``path=None``.
    """
    if not any(d.node is not None for d in diagnostics):
        return diagnostics
    from qprogram.paths import iter_child_edges  # noqa: PLC0415 — paths imports QProgram; lazy avoids a cycle

    paths_by_id: dict[int, tuple[int | str, ...]] = {id(qprogram.body): ()}

    def collect(node: Block | Operation, prefix: tuple[int | str, ...]) -> None:
        for segment, child in iter_child_edges(node):
            child_path = (*prefix, segment)
            paths_by_id[id(child)] = child_path
            collect(child, child_path)

    collect(qprogram.body, ())
    return [
        replace(d, path=paths_by_id.get(id(d.node))) if d.node is not None and d.path is None else d
        for d in diagnostics
    ]


# ---------------------------------------------------------------------------
# Forced-software warning emission
# ---------------------------------------------------------------------------


def _emit_forced_software(
    diagnostics: list[Diagnostic],
    available: Mapping[Operation | Block, frozenset[Domain]],
    support: Mapping[Operation | Block, frozenset[Domain]],
    parent: Mapping[Operation | Block, Block | None],
    constraints_by_block: Mapping[int, list[DomainConstraint]],
) -> None:
    """Emit one ``severity="warning"`` ``"forced-software"`` per highest forced-sw block.

    A block is *forced sw* when its final ``support`` is ``{sw}`` and its ``available`` contains
    ``"hw"`` (so HW would have been viable without DomainConstraints). The *highest* block in a
    forced-sw chain is the one whose parent isn't itself forced sw — emitting only there keeps
    the diagnostic output skimmable.

    The message carries the *reasons*: the highest forced block typically lost ``"hw"`` via the
    implicit sw-block-child propagation, so the human-readable causes are the
    :class:`DomainConstraint` reasons recorded anywhere in its subtree. When no constraint is on
    record (a purely propagated force), a generic explanation is used.
    """
    for node, sup in support.items():
        if not isinstance(node, Block):
            continue
        if sup != _SW_ONLY:
            continue
        if "hw" not in available.get(node, frozenset()):
            continue
        p = parent.get(node)
        if p is not None and support.get(p) == _SW_ONLY and "hw" in available.get(p, frozenset()):
            continue
        reasons: list[str] = []
        for sub in node.walk():
            for dc in constraints_by_block.get(id(sub), ()):
                if dc.reason and dc.reason not in reasons:
                    reasons.append(dc.reason)
        detail = (
            "; ".join(reasons)
            if reasons
            else "a contained operation or software-only sub-block requires software dispatch"
        )
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="forced-software",
                message=f"Block '{type(node).__name__}' falls back to software execution: {detail}.",
                node=node,
                domain="sw",
            ),
        )


# ---------------------------------------------------------------------------
# Context construction (unchanged)
# ---------------------------------------------------------------------------


def _build_context(qprogram: QProgram) -> ValidationContext:
    """Walk the program once to gather the cross-op data-flow facts predicates need.

    ``max_loop_nesting`` counts how many *repetition levels* wrap a leaf operation — every
    construct that consumes a hardware loop register contributes one level:

    - :class:`ForLoop` / :class:`Loop` — one level each.
    - :class:`Parallel` — one level total: it composes its loops in lockstep, not nested.
    - :class:`Average` — one level: it compiles to a repetition loop just like a sweep does.
    - :class:`Conditional` arms and plain :class:`Block` groupings — zero levels: branching and
      grouping don't iterate.
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
        elif isinstance(node, Average):
            # An average repeats its body shots-times — it occupies a loop level on the
            # sequencer exactly like a sweep does.
            for child in node.elements:
                visit(child, depth + 1)
        elif isinstance(node, Conditional):
            # Branching selects a body; it doesn't iterate — no extra loop level.
            for _, body in node.arms:
                for child in body.elements:
                    visit(child, depth)
            if node.else_body is not None:
                for child in node.else_body.elements:
                    visit(child, depth)
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
        program_buses=frozenset(qprogram.buses),
    )


# ---------------------------------------------------------------------------
# Whole-program limit checks (unchanged)
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

    if "max_parallel_loops" in platform_limits and ctx.max_parallel_arity > platform_limits["max_parallel_loops"]:
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
# Universal Conditional checks (unchanged)
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
