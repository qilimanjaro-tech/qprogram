"""Capability validation for QProgram.

A single :func:`validate` entry point walks the program AST once, builds a
:class:`~qprogram.ValidationContext` of cross-op data-flow facts, and emits a
:class:`~qprogram.Diagnostic` list covering missing capabilities, limit violations, and
predicate failures.

The validator never raises — callers decide what to do with a non-empty diagnostic list. A typical
:meth:`PlatformProtocol.execute` calls :func:`validate` and raises
:class:`~qprogram.UnsupportedOperationError` when the list isn't empty.
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
from qprogram.protocol import Diagnostic, SweepKind, ValidationContext
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

    from qprogram.protocol import CompilerCapabilities
    from qprogram.qprogram import QProgram
    from qprogram.variable import Variable


def validate(qprogram: QProgram, caps: CompilerCapabilities) -> list[Diagnostic]:
    """Run capability validation against ``caps`` and return the diagnostic list.

    One pre-pass builds the :class:`ValidationContext` (variable bindings, sweep kinds, max nesting,
    parallel arity, measurement count); the main pass is a single pre-order walk via
    :meth:`Block.walk` checking required capabilities and running predicates. Whole-program limits
    and Conditional classification are checked after the walk.

    Args:
        qprogram: Program to validate.
        caps: Capability descriptor (usually built from a profile via
            :meth:`CompilerCapabilities.from_profile`).

    Returns:
        Possibly-empty list of diagnostics in declaration order.
    """
    ctx = _build_context(qprogram)
    diagnostics: list[Diagnostic] = []

    for node in qprogram.body.walk():
        # 1. Missing-capability check (per-node, instance-aware).
        # We skip the root body Block — it has no semantic value of its own.
        if node is qprogram.body:
            continue
        required = node.required_capabilities()
        diagnostics.extend(
            Diagnostic(
                severity="error",
                code="missing-capability",
                message=(
                    f"'{type(node).__name__}' requires capability "
                    f"{token!r} which is not supported by profile "
                    f"{caps.profile!r}"
                ),
                node=node,
                capability=token,
            )
            for token in sorted(required - caps.capabilities)
        )
        # 2. Predicates — each sees the same context, may emit any number
        # of diagnostics for this node.
        for predicate in caps.predicates:
            diagnostics.extend(predicate(node, ctx))

    # 3. Whole-program limit checks. Done after the walk because the
    # numbers are aggregates over the AST.
    diagnostics.extend(_check_limits(qprogram, ctx, caps.limits))

    # 4. Universal Conditional checks (apply regardless of profile).
    diagnostics.extend(_check_conditional_classification(qprogram, ctx))

    return diagnostics


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
            # Parallel adds one nesting level for its body, regardless of
            # how many headers it composes.
            for child in node.elements:
                visit(child, depth + 1)
        elif isinstance(node, Conditional):
            # Conditional opens a new depth level for each arm body.
            for _, body in node.arms:
                for child in body.elements:
                    visit(child, depth + 1)
            if node.else_body is not None:
                for child in node.else_body.elements:
                    visit(child, depth + 1)
        elif isinstance(node, Block):
            # Generic Block / Average — does not bind a variable, does
            # not add a nesting level for our purposes.
            for child in node.elements:
                visit(child, depth)
        elif isinstance(node, MeasurementOperation):
            measurement_count += 1
            measurement_returns[node.name] = node.returns

    # Start at depth 0 for the root body; the root itself is a Block.
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
    limits: Mapping[str, float],
) -> list[Diagnostic]:
    """Check each known limit and emit one :class:`Diagnostic` per violation.

    Why unknown keys are silently ignored: vendors may declare forward-looking limits that the
    in-tree validator doesn't yet enforce — this keeps profile authors and the validator decoupled.
    """
    diagnostics: list[Diagnostic] = []

    if "max_loop_nesting" in limits and ctx.max_loop_nesting > limits["max_loop_nesting"]:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program nests loops {ctx.max_loop_nesting} deep; "
                    f"limit max_loop_nesting={limits['max_loop_nesting']:g}"
                ),
                limit=("max_loop_nesting", float(ctx.max_loop_nesting)),
            ),
        )

    if "max_parallel_loops" in limits and ctx.max_parallel_arity > limits["max_parallel_loops"]:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program has a Parallel block with {ctx.max_parallel_arity} concurrent loops; "
                    f"limit max_parallel_loops={limits['max_parallel_loops']:g}"
                ),
                limit=("max_parallel_loops", float(ctx.max_parallel_arity)),
            ),
        )

    if "max_measurements" in limits and ctx.measurement_count > limits["max_measurements"]:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(
                    f"Program contains {ctx.measurement_count} measurements; "
                    f"limit max_measurements={limits['max_measurements']:g}"
                ),
                limit=("max_measurements", float(ctx.measurement_count)),
            ),
        )

    if "min_wait_duration_ns" in limits:
        min_dur = limits["min_wait_duration_ns"]
        diagnostics.extend(
            Diagnostic(
                severity="error",
                code="limit-exceeded",
                message=(f"Wait duration {node.duration} ns is shorter than min_wait_duration_ns={min_dur:g}"),
                node=node,
                limit=("min_wait_duration_ns", float(node.duration)),
            )
            for node in qprogram.body.walk()
            if isinstance(node, Wait) and isinstance(node.duration, int) and node.duration < min_dur
        )

    return diagnostics


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
        for cond, _body in node.arms:
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
