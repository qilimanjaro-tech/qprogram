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
"""Capability validation + execution-domain classification for QProgram.

A single [`validate`][qprogram.validate] entry point walks the program AST, checking each operation's required
capability tokens against its routed [`BusCapabilities`][qprogram.BusCapabilities] slot and classifying each
block's execution domain from its op-children's consensus plus any
[`DomainConstraint`][qprogram.DomainConstraint] predicates emitted while walking. Returns
``(diagnostics, plan)``: diagnostics is a flat list (errors + advisory info events), and the plan
maps every AST node to the domain set it may execute in.

The classification rules implemented here match the spec:

* Operations have a domain derived from which slots (``rt``, ``host``) of their routed
  [`BusCapabilities`][qprogram.BusCapabilities] carry the required tokens. Vendor-specific operations have a fixed
  domain by design (e.g. qdac ops are ``host`` only); core ops depend on the platform's wiring.
* Block classification is determined by **op-children consensus only** — child blocks do not enter
  the consensus. A block whose op-children are all real-time has natural domain ``{rt}``; all-host
  gives ``{host}``; mixed op-children at the same level → ``mixed-domain`` error (spec (d)). An
  all-real-time block still reaches host-side execution, but through the (e2) fallback below (a
  constraint or a contained host-side block strips ``"rt"``), not by widening the consensus. An
  [`Average`][qprogram.blocks.Average] is the one exception: it accumulates
  **measurement results**, so only its averaging-relevant op-children
  (`Operation.AFFECTS_AVERAGING`) enter the consensus — the rest still validate and run but
  don't pull the average into host-side execution (see `_domain_relevant_ops`).
* [`DomainConstraint`][qprogram.DomainConstraint] predicate outputs target **block** nodes (typically the
  binding loop of a swept variable) and subtract from the targeted block's domain. The
  operation's classification is unaffected (spec (e2)).
* Nesting: a real-time-only block (``support == {rt}``) can only contain real-time-capable
  block-children — a host-side block-child inside a real-time block-parent emits ``host-in-rt`` error (spec
  (e1)). The reverse (real-time block inside host-side block) is always allowed.
* A block that could have run real-time — its pre-constraint domain contains ``"rt"`` — but ends
  at ``{host}`` surfaces a single ``severity="warning"`` ``"forced-host"`` diagnostic on the
  highest such block in its chain. The reason is attributed to the block's *immediate* cause — its
  own constraints, or (for a block forced merely by containing a host-side sub-block) the named
  sub-block.
* An [`Average`][qprogram.blocks.Average] that is host-side-only solely because it encloses a host-side
  sweep — while its measurements all support real-time hardware — gets a ``severity="info"``
  ``"reorderable-averaging"`` hint pointing at [`qprogram.optimize`][], which rewrites it to run
  the averaging in real-time hardware.

Every node-bearing diagnostic is stamped with a structural `Diagnostic.path` (see
`qprogram.paths`) so tooling can locate it in a serialized ``.qp`` file via
[`QProgram.source_map`][qprogram.QProgram.source_map].

The validator never raises — callers decide how to react. A typical
[`PlatformProtocol.execute`][qprogram.PlatformProtocol.execute] calls [`validate`][qprogram.validate], raises
[`UnsupportedOperationError`][qprogram.UnsupportedOperationError] on any ``severity="error"`` diagnostic, and surfaces
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
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.operations.operation import MeasurementField, MeasurementOperation, Operation
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


_ALL_DOMAINS: frozenset[Domain] = frozenset({"rt", "host"})
_RT_ONLY: frozenset[Domain] = frozenset({"rt"})
_HOST_ONLY: frozenset[Domain] = frozenset({"host"})

_V = TypeVar("_V")


class _IdentityNodeMap(MutableMapping["Operation | Block", _V]):
    """A mapping over AST nodes keyed by **object identity**, not structural equality.

    AST nodes use structural ``__eq__`` / ``__hash__`` (two identical ``play`` ops compare equal),
    which is right for round-trip comparison but wrong for the classifier's bookkeeping: a plain
    ``dict`` would collapse repeated identical operations into a single entry, so a three-op
    program could come back with a two-entry [`ExecutionPlan`][qprogram.ExecutionPlan]. Keying by ``id()``
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

    def __iter__(self):  # ruff: ignore[missing-return-type-special-method] — Iterator[Operation | Block]
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

    1. Pre-walk to build a [`ValidationContext`][qprogram.ValidationContext] (variable bindings, sweep kinds, …).
    2. Single recursive post-order walk that, per node, computes:

       - For operations: the *available* domain set is the slots where the op's required tokens
         are present (and any predicate-emitted [`Diagnostic`][qprogram.Diagnostic] is absent). The op's
         ``support`` equals its ``available`` — [`DomainConstraint`][qprogram.DomainConstraint] outputs are *not*
         applied to the op; they are routed to the **block** they target (typically the binding
         loop of a swept variable).
       - For blocks: ``support = own_available & natural_from_ops - exclude_from_constraints``,
         where ``natural_from_ops`` is the op-children consensus directly (an all-real-time block is
         shifted to host-side by the (e2) fallback, not by widening the consensus). For an ``Average``
         only the averaging-relevant op-children enter the consensus. Mixed op-children produce a
         ``mixed-domain`` error. Block-children act as units; they don't constrain the parent's
         domain but the parent's domain constrains them (no host-side block inside a real-time block).
    3. Whole-program limit checks (loop nesting, parallel arity, measurement count) against the
       platform slot's limits; ``min_wait_duration_ns`` checks against the bus slot's limits.
    4. Universal Conditional checks (unknown measurement, missing state classification).
    5. Emit one ``"forced-host"`` warning per highest-block whose ``support`` was reduced
       from ``{rt, host}`` to ``{host}``, with the subtree's constraint reasons in the message.
    6. Stamp each node-bearing diagnostic with its structural `Diagnostic.path`.

    Args:
        qprogram (QProgram): Program to validate.
        caps (PlatformCapabilities): Platform capability descriptor to check the program against.

    Returns:
        ``(diagnostics, plan)``. The plan covers every visited AST node (excluding the root
        body) and is **identity-keyed**: each node *instance* gets its own entry, even when two
        nodes are structurally identical (``plan[node]`` looks up by ``id``, and iterating the
        plan yields every instance).

    Note:
        Programs containing fragment [`Call`][qprogram.operations.Call] nodes are **expanded
        first** ([`QProgram.expand`][qprogram.QProgram.expand]) — capabilities are checked against the substituted
        fragment bodies, and diagnostics reference nodes of that internal expansion. Callers
        that need the identity-keyed plan for nodes they hold should expand explicitly and
        validate the expanded program.
    """
    from qprogram.operations.call import Call  # ruff: ignore[import-outside-top-level]

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
    _emit_forced_host(diagnostics, available, support, parent, constraints_by_block)
    _emit_averaging_hints(diagnostics, support)

    return _stamp_paths(diagnostics, qprogram), support


# ---------------------------------------------------------------------------
# Single recursive post-order walk
# ---------------------------------------------------------------------------


def _classify_node(  # ruff: ignore[too-many-arguments]
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
    ``constraints_by_block`` (keyed by the target block's `id`).

    Args:
        node (Operation | Block): The node to classify.
        parent_block (Block): The block ``node`` is an immediate child of.
        caps (PlatformCapabilities): Platform capability descriptor to check against.
        ctx (ValidationContext): Program-wide data-flow facts, passed on to predicates.
        diagnostics (list[Diagnostic]): Accumulator every diagnostic is appended to.
        available (_IdentityNodeMap[frozenset[Domain]]): Per-node domains before any constraint is
            applied — for an operation the domains its routed slot allows, for a block that same
            slot allowance intersected with the op-children consensus.
        support (_IdentityNodeMap[frozenset[Domain]]): Per-node executable domains — the execution
            plan under construction.
        parent (_IdentityNodeMap[Block | None]): Per-node parent block.
        constraints_by_block (dict[int, list[DomainConstraint]]): Constraints bucketed by the
            `id` of the block they target.
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


def _classify_operation(  # ruff: ignore[too-many-arguments]
    op: Operation,
    caps: PlatformCapabilities,
    ctx: ValidationContext,
    diagnostics: list[Diagnostic],
    available: _IdentityNodeMap[frozenset[Domain]],
    support: _IdentityNodeMap[frozenset[Domain]],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Classify a leaf operation.

    An op's ``support`` equals its ``available`` — [`DomainConstraint`][qprogram.DomainConstraint] outputs target
    block nodes and never directly subtract from the op's support (the op's classification is
    by spec fixed by its vendor / bus slot, not by surrounding variables).

    Args:
        op (Operation): The operation to classify.
        caps (PlatformCapabilities): Platform capability descriptor to check against.
        ctx (ValidationContext): Program-wide data-flow facts, passed on to predicates.
        diagnostics (list[Diagnostic]): Accumulator every diagnostic is appended to.
        available (_IdentityNodeMap[frozenset[Domain]]): Per-node domains the routed slot allows.
        support (_IdentityNodeMap[frozenset[Domain]]): Per-node executable domains.
        constraints_by_block (dict[int, list[DomainConstraint]]): Constraints bucketed by the
            `id` of the block they target.
    """
    avail, op_diags, dcs = _check_node_self(op, caps, ctx)
    diagnostics.extend(op_diags)
    available[op] = avail
    support[op] = avail
    _route_constraints(dcs, op, diagnostics, constraints_by_block)


def _classify_block(  # ruff: ignore[too-many-arguments]
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

    Args:
        block (Block): The block to classify.
        caps (PlatformCapabilities): Platform capability descriptor to check against.
        ctx (ValidationContext): Program-wide data-flow facts, passed on to predicates.
        diagnostics (list[Diagnostic]): Accumulator every diagnostic is appended to.
        available (_IdentityNodeMap[frozenset[Domain]]): Per-node domains before any constraint is
            applied. ``block``'s own entry is written here as the platform slot's allowance
            intersected with the op-children consensus.
        support (_IdentityNodeMap[frozenset[Domain]]): Per-node executable domains.
        parent (_IdentityNodeMap[Block | None]): Per-node parent block.
        constraints_by_block (dict[int, list[DomainConstraint]]): Constraints bucketed by the
            `id` of the block they target.
    """
    # 1. Identify immediate op-children vs block-children (without recursing yet).
    op_children, block_children = _immediate_children(block)

    # 2. Recurse into both — post-order, so children's support and any constraints they raise are
    # in place before this block is classified.
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
    # Two distinct subsets of op-children matter here:
    #  - `has_defective_op` considers EVERY op-child: if any op can run nowhere, the whole block
    #    can't execute (regardless of which ops gate its domain).
    #  - the domain *consensus* is taken only over the op-children that gate this block's domain
    #    (`_domain_relevant_ops`): for an Average that is only its averaging-relevant (measurement)
    #    op-children — the rest are repeated in the body but don't decide whether the averaging is
    #    a real-time hardware feature; for every other block it is all op-children.
    # Op-children whose own support is already empty are excluded from the consensus: they were
    # diagnosed at their own node (missing-capability / predicate error), and folding their empty
    # set in would manufacture a misleading extra "mixed-domain" error on the parent. The block
    # still can't execute, so its support goes empty — silently, the child diagnostic explains it.
    has_defective_op = any(not support[op] for op in op_children)
    # `_domain_relevant_ops` relaxes an Average's consensus to its measurement op-children. That
    # relaxation must never *widen* the domain: an Average that has op-children but NO measurement
    # has nothing to relax onto, so it falls back to all op-children — a host-side-only op there
    # still pulls the average to host-side, exactly as for any other block. (A block with no op-children
    # at all stays unconstrained by content.)
    relevant_ops = _domain_relevant_ops(block, op_children) or op_children
    consensus_ops = [op for op in relevant_ops if support[op]]
    if consensus_ops:
        ops_consensus = _ALL_DOMAINS
        for op in consensus_ops:
            ops_consensus &= support[op]
        if not ops_consensus:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="mixed-domain",
                    message=(
                        f"Block '{type(block).__name__}' has op-children with incompatible "
                        f"domain singletons: "
                        f"{[(type(op).__name__, sorted(support[op])) for op in consensus_ops]}"
                    ),
                    node=block,
                ),
            )
            available[block] = frozenset()
            support[block] = frozenset()
            return
        # Per spec (c): all-real-time ops → block IS real-time; all-host ops → block IS host-side. The
        # natural domain is the ops' consensus directly. (e2) constraints can still shift rt → host; see the
        # fallback below.
        natural_from_ops = ops_consensus
    else:
        # No (relevant, healthy) op-children — the block's domain is unconstrained by its ops.
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

    # 6b. Block-children host-side propagation (spec (e1) constructive side): if any block-child has
    # support {host}, the parent must also support host — a host-side sub-block cannot be hosted by a
    # real-time parent. That acts as an implicit ``exclude={"rt"}`` constraint on the parent. The
    # explicit (e1) check below catches the rare case where this propagation can't be honored
    # (the parent's slot doesn't carry host at all).
    for child in block_children:
        if support.get(child) == _HOST_ONLY:
            excluded.add("rt")
            break

    sup = avail - frozenset(excluded)

    # 6c. (e2) rt→host fallback: when a constraint (or propagated host-side block-child) excludes rt
    # from an all-real-time block, the block falls back to host-side dispatch (one real-time shot per
    # iteration). The op-children remain real-time; the block's iteration mechanism becomes host-side.
    # This implements the spec's "variable influences block class, not op class" rule.
    if not sup and "rt" in excluded and natural_from_ops == _RT_ONLY and "host" in own_avail:
        sup = _HOST_ONLY

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

    # 8. (e1) nesting check: real-time-only parent can only contain real-time-capable block-children.
    if sup == _RT_ONLY:
        for child in block_children:
            child_sup = support.get(child, _ALL_DOMAINS)
            if "rt" not in child_sup:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        code="host-in-rt",
                        message=(
                            f"Host-side block '{type(child).__name__}' nested inside real-time "
                            f"block '{type(block).__name__}' — the FPGA cannot host a host-side "
                            f"sub-block (only rt-in-host is allowed by spec (e1))."
                        ),
                        node=child,
                    ),
                )


def _constraint_seen(dc: DomainConstraint, existing: list[DomainConstraint]) -> bool:
    """Return whether an equivalent constraint is already recorded.

    Equivalence is *identity* on the target node plus structural equality on the restriction —
    plain dataclass equality would compare nodes structurally and could merge constraints that
    target two different (but identical-looking) loop instances.

    Args:
        dc (DomainConstraint): The constraint about to be recorded.
        existing (list[DomainConstraint]): The constraints already recorded in the same bucket.

    Returns:
        ``True`` when ``existing`` already holds an equivalent constraint, so ``dc`` would be a
        duplicate.
    """
    return any(e.node is dc.node and e.exclude == dc.exclude and e.reason == dc.reason for e in existing)


def _route_constraints(
    dcs: list[DomainConstraint],
    source_node: Operation | Block,
    diagnostics: list[Diagnostic],
    constraints_by_block: dict[int, list[DomainConstraint]],
) -> None:
    """Accumulate each constraint into the per-target-block bucket.

    A constraint must target a [`Block`][qprogram.blocks.Block]. A ``DomainConstraint`` whose ``node`` is anything
    else is a mistake in the predicate, so it is dropped and reported as a
    ``bad-domain-constraint`` error that states the rule. Equivalent constraints already in the
    bucket are skipped (a profile filling both halves of a slot runs its predicates once per
    domain).

    Args:
        dcs (list[DomainConstraint]): Constraints emitted while checking ``source_node``.
        source_node (Operation | Block): The node whose predicates produced ``dcs``; named in the
            ``bad-domain-constraint`` message.
        diagnostics (list[Diagnostic]): Accumulator every diagnostic is appended to.
        constraints_by_block (dict[int, list[DomainConstraint]]): Buckets keyed by the `id`
            of the target block.
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


def _domain_relevant_ops(block: Block, op_children: list[Operation]) -> list[Operation]:
    """Return the op-children that gate ``block``'s natural execution domain (spec (c)).

    For most blocks this is *every* op-child — a parameter sweep is real-time only if its whole
    body is. An [`Average`][qprogram.blocks.Average] is special: it repeats its body and accumulates
    **measurement results**, so only the averaging-relevant op-children
    (`Operation.AFFECTS_AVERAGING` — measurements/acquisitions) decide whether the averaging
    can be a real-time hardware feature. The other ops still validate and execute inside the body;
    they do not pull the Average into host-side execution.

    Args:
        block (Block): The block whose domain is being decided.
        op_children (list[Operation]): The block's immediate op-children, in document order.

    Returns:
        The subset of ``op_children`` whose support intersects into the block's natural domain.
        Possibly empty for an Average whose body holds no measurement, which the caller reads as
        "fall back to every op-child".
    """
    if isinstance(block, Average):
        return [op for op in op_children if op.AFFECTS_AVERAGING]
    return op_children


def reorderable_average_split(
    average: Average,
    support: Mapping[Operation | Block, frozenset[Domain]],
) -> tuple[list[Operation], list[Operation]] | None:
    """Return the ``(hoist, keep)`` split for a reorderable ``average``, or ``None`` when it isn't one.

    The single supported shape: the Average's **sole** child is one flat sweep loop
    (a ``Sweep``) whose body is a *leading contiguous run* of host-side-only ops (to hoist
    out, ahead of the loop) followed by real-time-capable ops including at least one averaging-relevant
    op (to keep inside a real-time ``average``). The leading-run requirement matters: a host-side-only
    op that sits *after* a kept op can't be hoisted without reordering it past that op, which could
    change results — such an Average is not reorderable.

    Shared by the validator's ``reorderable-averaging`` hint (`_emit_averaging_hints`) and the
    rewrite ([`qprogram.optimize`][]) so the two never disagree.

    Args:
        average (Average): The block to test.
        support (Mapping[Operation | Block, frozenset[Domain]]): Per-node executable domains, as
            returned by [`validate`][qprogram.validate].

    Returns:
        A ``(hoist, keep)`` pair — the leading host-side-only ops to lift out ahead of the loop, and
        the real-time ops to keep inside the average — or ``None`` when the block does not match the
        pattern.
    """
    children = average.elements
    if len(children) != 1 or not isinstance(children[0], Sweep):
        return None
    body = children[0].elements
    if any(isinstance(el, Block) for el in body):
        # only the flat-body case is handled
        return None
    prefix = 0
    while prefix < len(body) and support.get(body[prefix]) == _HOST_ONLY:
        prefix += 1
    hoist = [el for el in body[:prefix] if isinstance(el, Operation)]
    keep = [el for el in body[prefix:] if isinstance(el, Operation)]
    if not hoist or not keep:
        return None
    # Everything kept must be real-time-capable (this also rejects any host-side-only op that landed
    # after the leading run) and the average must still have something to average.
    if not all("rt" in (support.get(op) or frozenset()) for op in keep):
        return None
    if not any(op.AFFECTS_AVERAGING for op in keep):
        return None
    return hoist, keep


def _immediate_children(block: Block) -> tuple[list[Operation], list[Block]]:
    """Partition ``block``'s immediate children into (op-children, block-children).

    Conditional has arm bodies plus an else body (each a [`Block`][qprogram.blocks.Block]) — those bodies are
    block-children of the Conditional, not op-children. Parallel has loop headers (Blocks) plus
    a body (``._elements``); the loop headers are block-children, and the body's elements are
    immediate children of the Parallel.

    Args:
        block (Block): The block whose children to partition.

    Returns:
        An ``(op_children, block_children)`` pair, each in document order.
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
    # Regular Block, Sweep, Average — flat .elements.
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

    Required-token routing splits along the ``expr.*`` namespace: expression tokens always check
    against ``caps.platform`` (they describe what Python AST node kinds the platform's compiler
    accepts, not what any particular bus's instrument can do), while every other token checks
    against the node's primary routed slot.

    Args:
        node (Operation | Block): The node to check.
        caps (PlatformCapabilities): Platform capability descriptor to resolve slots from.
        ctx (ValidationContext): Program-wide data-flow facts, passed on to predicates.

    Returns:
        An ``(available, diagnostics, domain_constraints)`` triple:

        - ``available``: domains where the node's required tokens fit the routed slot and no
          predicate emitted a [`Diagnostic`][qprogram.Diagnostic].
        - ``diagnostics``: the per-domain failure reasons, **only** if ``available`` came out
          empty (so per-domain noise is suppressed when at least one domain works). Deduplicated:
          a token missing in both domains produces one diagnostic naming both, and a predicate
          registered on both halves of a slot contributes its (equal) diagnostic once.
        - ``domain_constraints``: the [`DomainConstraint`][qprogram.DomainConstraint] outputs the predicates emitted,
          deduplicated by equality — the same profile filling both the rt and host halves runs its
          predicates twice and would otherwise double every constraint (and double the reason text
          in downstream ``empty-domain`` messages).
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

    for d in ("rt", "host"):
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
    """Return the [`BusCapabilities`][qprogram.BusCapabilities] slots that ``node`` routes to.

    - Blocks always route to ``caps.platform``.
    - Bus-less ops (``BUS_ATTRS = ()``) route to ``caps.platform``.
    - Bus-touching ops with one or more buses route to ``caps.for_bus(bus)`` per bus; the caller
      intersects across the returned list when multiple are present.
    - Broadcast ops whose bus list resolves to empty (``Sync(targets=None)``, which means "sync
      every active bus") route across **every** bus in the program — the intersection semantics
      then match the explicit-targets form. A broadcast in a program with no buses at all, or a
      non-broadcast op with an empty bus list, falls back to ``caps.default_bus_profile``.

    Args:
        node (Operation | Block): The node being routed.
        caps (PlatformCapabilities): Platform capability descriptor to resolve slots from.
        ctx (ValidationContext): Program-wide facts; supplies ``program_buses`` for broadcast ops.

    Returns:
        One slot per bus the node touches, or a single-element list for a block, a bus-less op, or
        a fallback to the default bus profile. Never empty.
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
    """Return ``diagnostics`` with each node-bearing entry's `Diagnostic.path` filled in.

    One walk builds an identity-keyed node→path table; [`Diagnostic`][qprogram.Diagnostic] is frozen, so entries
    are rebuilt via `dataclasses.replace`. A node unreachable from the body (defensive —
    shouldn't happen) keeps ``path=None``.

    Args:
        diagnostics (list[Diagnostic]): The diagnostics to stamp.
        qprogram (QProgram): The validated program whose body the paths are resolved against.

    Returns:
        A list in the same order, with `Diagnostic.path` filled in on every entry whose node
        the walk reached. The input list is handed back unchanged when no entry has a node.
    """
    if not any(d.node is not None for d in diagnostics):
        return diagnostics
    # qprogram.paths imports QProgram, so this import stays inside the function.
    from qprogram.paths import iter_child_edges  # ruff: ignore[import-outside-top-level]

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
# Forced-host warning emission
# ---------------------------------------------------------------------------


def _emit_forced_host(
    diagnostics: list[Diagnostic],
    available: Mapping[Operation | Block, frozenset[Domain]],
    support: Mapping[Operation | Block, frozenset[Domain]],
    parent: Mapping[Operation | Block, Block | None],
    constraints_by_block: Mapping[int, list[DomainConstraint]],
) -> None:
    """Emit one ``severity="warning"`` ``"forced-host"`` per highest forced-host block.

    A block is *forced host-side* when its final ``support`` is ``{host}`` and its ``available``
    contains ``"rt"`` (so real-time would have been viable without DomainConstraints). The *highest*
    block in a forced-host chain is the one whose parent isn't itself forced host-side — emitting only
    there keeps the diagnostic output skimmable.

    The message attributes the force to the block's **immediate** cause, not the whole subtree:
    a block forced by DomainConstraints targeting *it* reports those reasons; a block
    forced by *containing* a host-side-only sub-block (the implicit host-block-child propagation)
    reports that structurally — naming the sub-block(s) and, for context, the sub-block's own
    constraint reasons. This keeps each level's diagnostic about *that* level (e.g. an ``average``
    says "contains a host-side loop", while the loop itself says why it is host-side).

    Args:
        diagnostics (list[Diagnostic]): Accumulator the warnings are appended to.
        available (Mapping[Operation | Block, frozenset[Domain]]): Per-node domains before
            constraints — for a block, its routed slot's allowance intersected with the op-children
            consensus, which is what tells a forced block apart from a natively host-side one.
        support (Mapping[Operation | Block, frozenset[Domain]]): Per-node executable domains.
        parent (Mapping[Operation | Block, Block | None]): Per-node parent block, read to find the
            highest block of each forced-host chain.
        constraints_by_block (Mapping[int, list[DomainConstraint]]): Constraints bucketed by the
            `id` of the block they target, read for the reason text.
    """
    for node, sup in support.items():
        if not isinstance(node, Block):
            continue
        if sup != _HOST_ONLY:
            continue
        if "rt" not in available.get(node, frozenset()):
            continue
        p = parent.get(node)
        if p is not None and support.get(p) == _HOST_ONLY and "rt" in available.get(p, frozenset()):
            continue
        # Reason attribution: this block's own constraints take precedence; otherwise it was forced
        # by a host-side-only sub-block (structural propagation), which the message names.
        own_reasons = [dc.reason for dc in constraints_by_block.get(id(node), ()) if dc.reason]
        host_child_blocks = [
            child
            for child in support
            if isinstance(child, Block) and parent.get(child) is node and support.get(child) == _HOST_ONLY
        ]
        if own_reasons:
            detail = "; ".join(dict.fromkeys(own_reasons))
        elif host_child_blocks:
            names = ", ".join(f"'{type(c).__name__}'" for c in host_child_blocks)
            # Gather the real causes from the forced sub-tree, not only one level down — the
            # constraint may sit deeper (e.g. average → block → sweep), with unconstrained
            # blocks in between.
            nested = [
                dc.reason
                for c in host_child_blocks
                for sub in c.walk()
                for dc in constraints_by_block.get(id(sub), ())
                if dc.reason
            ]
            detail = f"contains host-side-only sub-block {names}"
            if nested:
                detail += " (" + "; ".join(dict.fromkeys(nested)) + ")"
        else:
            detail = "a contained operation requires host-side dispatch"
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="forced-host",
                message=f"Block '{type(node).__name__}' falls back to host-side execution: {detail}.",
                node=node,
                domain="host",
            ),
        )


def _emit_averaging_hints(
    diagnostics: list[Diagnostic],
    support: Mapping[Operation | Block, frozenset[Domain]],
) -> None:
    """Emit one ``severity="info"`` ``"reorderable-averaging"`` hint per optimizable Average.

    Fires only for Averages that [`qprogram.optimize`][] could *actually* rewrite —
    the precondition is the shared `reorderable_average_split` predicate, so the hint never
    advertises a no-op. Such an Average is host-side-only only because it encloses a host-side sweep
    whose real-time-capable measurement sequence could run in a real-time inner ``average`` if the
    sweep were lifted out (and the host-side-only setup hoisted alongside). The validator can't prove
    that reorder preserves the author's intent (interleaved vs grouped shots differ on a drifting
    device), so it only *suggests* the change.

    Args:
        diagnostics (list[Diagnostic]): Accumulator the hints are appended to.
        support (Mapping[Operation | Block, frozenset[Domain]]): Per-node executable domains, which
            decide both which Averages are host-side-only and how the body splits.
    """
    for node in support:
        if not isinstance(node, Average):
            continue
        if reorderable_average_split(node, support) is None:
            continue
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="reorderable-averaging",
                message=(
                    f"Block '{type(node).__name__}' runs host-side only because it encloses a "
                    f"host-side sweep; its measurement sequence supports real-time hardware. Moving the "
                    f"sweep outside the average (hoisting the host-side-only setup with it) would let the "
                    f"averaging run in real-time hardware — see qprogram.optimize()."
                ),
                node=node,
            ),
        )


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


def _build_context(qprogram: QProgram) -> ValidationContext:
    """Walk the program once to gather the cross-op data-flow facts predicates need.

    ``max_loop_nesting`` counts how many *repetition levels* wrap a leaf operation — every
    construct that consumes a hardware loop register contributes one level. A block declares whether
    it is such a construct through `Block.REPEATS`, which this walk reads rather than testing
    concrete classes, so a vendor- or platform-contributed repeating block counts correctly without a
    core change:

    - [`Sweep`][qprogram.blocks.Sweep] — one level, whatever its source.
    - [`Parallel`][qprogram.blocks.Parallel] — one level total: it composes its loops in lockstep, not nested (its loop
      headers live on ``.loops``, not among its children).
    - [`Average`][qprogram.blocks.Average] — one level: it compiles to a repetition loop exactly as a sweep does.
    - [`Conditional`][qprogram.blocks.Conditional] arms and plain [`Block`][qprogram.blocks.Block] groupings — zero
      levels: branching and grouping don't iterate.

    Args:
        qprogram (QProgram): The program to walk.

    Returns:
        The context the predicates and the whole-program limit checks read.
    """
    variable_bindings: dict[Variable, Block] = {}
    sweep_kinds: dict[Variable, SweepKind] = {}
    measurement_count = 0
    measurement_fields: dict[str, tuple[str, ...]] = {}
    max_parallel_arity = 0
    max_depth = 0

    def visit(node: Block | Operation, depth: int) -> None:
        nonlocal measurement_count, max_parallel_arity, max_depth
        max_depth = max(max_depth, depth)
        # One source of truth for "does this block add a repetition level": the block says so.
        # Operations have no children, so the depth is irrelevant for them.
        child_depth = depth + 1 if isinstance(node, Block) and node.REPEATS else depth
        if isinstance(node, Sweep):
            variable_bindings[node.variable] = node
            sweep_kinds[node.variable] = node.source.KIND
            for child in node.elements:
                visit(child, child_depth)
        elif isinstance(node, Parallel):
            max_parallel_arity = max(max_parallel_arity, len(node.loops))
            for header in node.loops:
                variable_bindings[header.variable] = header
                sweep_kinds[header.variable] = header.source.KIND
            for child in node.elements:
                visit(child, child_depth)
        elif isinstance(node, Conditional):
            # Branching selects a body; it doesn't iterate — no extra loop level. The arm bodies
            # hang off `.arms` / `.else_body`, so they need their own traversal.
            for _, body in node.arms:
                for child in body.elements:
                    visit(child, child_depth)
            if node.else_body is not None:
                for child in node.else_body.elements:
                    visit(child, child_depth)
        elif isinstance(node, Block):
            # Every other block — the plain grouping, `average`, and any vendor-contributed block.
            # Whether it adds a level is already decided by `child_depth`.
            for child in node.elements:
                visit(child, child_depth)
        elif isinstance(node, MeasurementOperation):
            measurement_count += 1
            measurement_fields[node.name] = node.fields

    for child in qprogram.body.elements:
        visit(child, 0)

    return ValidationContext(
        variable_bindings=variable_bindings,
        sweep_kinds=sweep_kinds,
        max_loop_nesting=max_depth,
        max_parallel_arity=max_parallel_arity,
        measurement_count=measurement_count,
        measurement_fields=measurement_fields,
        program_buses=frozenset(qprogram.buses),
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

    ``max_loop_nesting``, ``max_parallel_loops``, ``max_measurements`` live at the platform slot (whichever of rt/host
    is present; rt wins when both are set since rt is typically the more constrained engine). ``min_wait_duration_ns``
    lives at the bus slot — a [`Wait`][qprogram.operations.Wait] is checked against its routed bus's limits only when
    its ``duration`` is a plain integer; a duration given as an [`Expression`][qprogram.Expression] has no static value
    to compare and is left unchecked.

    Args:
        qprogram (QProgram): The program whose ``wait`` operations are checked per bus.
        ctx (ValidationContext): Supplies the observed loop nesting, parallel arity, and
            measurement count.
        caps (PlatformCapabilities): Platform capability descriptor carrying the limits.

    Returns:
        One ``limit-exceeded`` diagnostic per breached limit; empty when every declared limit holds.
        A limit the platform does not declare is not checked.
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
    """Pick limits from ``slot`` — prefer ``rt`` when present, fall back to ``host``, else empty.

    Args:
        slot (BusCapabilities): The bus or platform slot to read.

    Returns:
        The chosen half's limits, or an empty mapping when the slot has neither half.
    """
    if slot.rt is not None:
        return slot.rt.limits
    if slot.host is not None:
        return slot.host.limits
    return {}


# ---------------------------------------------------------------------------
# Universal Conditional checks
# ---------------------------------------------------------------------------


def _iter_measurement_refs(expr: Expression) -> Iterator[MeasurementRef]:
    """Yield every [`MeasurementRef`][qprogram.MeasurementRef] reachable from ``expr`` via recursive descent.

    Args:
        expr (Expression): The expression tree to search.

    Yields:
        Each [`MeasurementRef`][qprogram.MeasurementRef] leaf, in left-to-right order.
    """
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
    """Validate [`Conditional`][qprogram.blocks.Conditional] arm conditions — profile-independent.

    Emits two diagnostic codes:

    - ``unknown-measurement`` — the condition references a handle whose name doesn't match any
      measurement in the program (usually a raw ``MeasurementRef`` built outside ``measure(...)``).
    - ``missing-classification`` — the condition references ``handle.state`` but the source
      measurement's ``fields`` doesn't include `STATE`.

    Args:
        qprogram (QProgram): The program whose conditionals are checked.
        ctx (ValidationContext): Supplies the program's measurement names and their fields.

    Returns:
        One diagnostic per offending measurement reference; empty when every arm condition refers
        to data the program actually produces.
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
                fields = ctx.measurement_fields(name) or ()
                if ref.field == MeasurementField.STATE and MeasurementField.STATE not in fields:
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            code="missing-classification",
                            message=(
                                f"Conditional references {name}.state, but "
                                f"the measurement does not request state "
                                f"classification (add MeasurementField.STATE "
                                f"to fields=)"
                            ),
                            node=node,
                        ),
                    )
    return diagnostics


__all__ = ["validate"]
