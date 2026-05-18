"""Tests for :func:`qprogram.validation.validate`.

Covers:

- Empty programs validate cleanly against any non-empty capability set.
- Missing-capability diagnostics fire and carry the op + token reference.
- Limit-violation checks (loop nesting, parallel arity, measurement count,
  min wait duration).
- Predicates run with a populated :class:`ValidationContext` and see the
  variable→loop bindings the validator's pre-pass collects.
- The motivating data-flow case (arbitrary-sweep at ``Wait.duration``
  rejected, linear-sweep accepted).
"""

from __future__ import annotations

import numpy as np

from qprogram import QProgram, validate
from qprogram.operations.wait import Wait
from qprogram.protocol import (
    CompilerCapabilities,
    Diagnostic,
    ValidationContext,
    register_capability_tokens,
)
from qprogram.variable import Variable
from qprogram.waveforms import Square

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_caps(*, capabilities: frozenset[str] = frozenset()) -> CompilerCapabilities:
    """Build a bare-bones :class:`CompilerCapabilities` for tests."""
    return CompilerCapabilities(
        profile="test",
        version=(1, 0, 0),
        capabilities=capabilities,
        limits={},
        predicates=(),
        vendor_versions={},
    )


def _full_caps(*, limits: dict[str, float] | None = None, predicates: tuple = ()) -> CompilerCapabilities:
    """A liberal :class:`CompilerCapabilities` that accepts every core token."""
    register_capability_tokens()  # idempotent — ensures core tokens exist
    return CompilerCapabilities(
        profile="test-full",
        version=(1, 0, 0),
        capabilities=frozenset(
            {
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
                "block.block",
                "block.average",
                "block.for_loop",
                "block.loop",
                "block.parallel",
                "sweep.linear",
                "sweep.arbitrary",
                "waveform.single",
                "waveform.iq",
                "waveform.alias",
                "waveform.square",
                "expr.constant",
                "expr.variable",
                "expr.binary_op",
                "measure.returns.iq",
            },
        ),
        limits=limits or {},
        predicates=predicates,
        vendor_versions={},
    )


# ---------------------------------------------------------------------------
# Empty and trivial programs
# ---------------------------------------------------------------------------


def test_validate_empty_program_returns_no_diagnostics() -> None:
    p = QProgram()
    assert validate(p, _empty_caps(capabilities=frozenset({"op.play"}))) == []


def test_validate_with_empty_capabilities_rejects_used_ops() -> None:
    p = QProgram()
    p.play("drive_q0", "pi")
    diagnostics = validate(p, _empty_caps())
    codes = [d.code for d in diagnostics]
    assert "missing-capability" in codes


# ---------------------------------------------------------------------------
# Missing-capability diagnostics
# ---------------------------------------------------------------------------


def test_missing_op_token_emits_diagnostic_with_node_and_token() -> None:
    caps = _empty_caps(capabilities=frozenset({"op.sync"}))
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    p.sync()
    diagnostics = validate(p, caps)
    missing = [d for d in diagnostics if d.code == "missing-capability"]
    # Three tokens missing: op.play, waveform.single, waveform.square
    missing_tokens = {d.capability for d in missing}
    assert "op.play" in missing_tokens
    assert "waveform.single" in missing_tokens
    assert "waveform.square" in missing_tokens
    # Every diagnostic points at the Play node — Sync is fully supported.
    for d in missing:
        assert d.node is not None
        assert type(d.node).__name__ == "Play"


def test_missing_capability_diagnostics_are_deterministic() -> None:
    """Token order matters for human-readable diff output; the validator
    sorts within each node so the order is stable."""
    caps = _empty_caps(capabilities=frozenset())
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = validate(p, caps)
    tokens = [d.capability for d in diagnostics if d.code == "missing-capability"]
    assert tokens == sorted(tokens)


# ---------------------------------------------------------------------------
# Limit checks
# ---------------------------------------------------------------------------


def test_max_loop_nesting_violation_emits_limit_exceeded() -> None:
    caps = _full_caps(limits={"max_loop_nesting": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.for_loop(v1, 0, 1, 0.1), p.for_loop(v2, 0, 1, 0.1), p.for_loop(v3, 0, 1, 0.1):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = validate(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "max_loop_nesting" for d in diagnostics)


def test_max_parallel_loops_violation() -> None:
    caps = _full_caps(limits={"max_parallel_loops": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.for_loop(v1, 0, 1, 0.1) | p.for_loop(v2, 0, 1, 0.1) | p.for_loop(v3, 0, 1, 0.1):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = validate(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "max_parallel_loops" for d in diagnostics)


def test_min_wait_duration_violation_for_constant_duration() -> None:
    caps = _full_caps(limits={"min_wait_duration_ns": 10})
    p = QProgram()
    p.wait("drive_q0", 4)
    diagnostics = validate(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "min_wait_duration_ns" for d in diagnostics)


def test_unknown_limit_keys_are_silently_ignored() -> None:
    """Profiles may declare future limits the validator doesn't know about."""
    caps = _full_caps(limits={"a_limit_the_validator_does_not_check": 0.0})
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    assert validate(p, caps) == []


# ---------------------------------------------------------------------------
# Predicates + ValidationContext
# ---------------------------------------------------------------------------


def test_predicate_sees_variable_sweep_kind_through_context() -> None:
    """Predicates query ``ctx.sweep_kind_of(var)`` to reason about data flow."""
    seen_kinds: list[str | None] = []

    def my_predicate(node, ctx: ValidationContext):
        if isinstance(node, Wait) and isinstance(node.duration, Variable):
            seen_kinds.append(ctx.sweep_kind_of(node.duration))
        return ()

    caps = _full_caps(predicates=(my_predicate,))
    p = QProgram()
    v = p.variable("dur")
    with p.loop(v, np.array([100, 200])):
        p.wait("drive_q0", v)
    validate(p, caps)
    assert seen_kinds == ["arbitrary"]


def test_predicate_can_emit_diagnostics_per_node() -> None:
    def always_complain(node, ctx):  # noqa: ARG001
        if isinstance(node, Wait):
            yield Diagnostic(severity="error", code="test.complain", message="boo", node=node)

    caps = _full_caps(predicates=(always_complain,))
    p = QProgram()
    p.wait("drive_q0", 100)
    p.wait("drive_q0", 200)
    diagnostics = validate(p, caps)
    test_diags = [d for d in diagnostics if d.code == "test.complain"]
    assert len(test_diags) == 2


# ---------------------------------------------------------------------------
# Data-flow motivating case
# ---------------------------------------------------------------------------


def _arbitrary_wait_predicate(node, ctx):
    if not isinstance(node, Wait) or not isinstance(node.duration, Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(
            severity="error",
            code="test.arbitrary-wait-sweep",
            message="not supported",
            node=node,
        )


def test_arbitrary_sweep_at_wait_duration_is_rejected() -> None:
    caps = _full_caps(predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d = p.variable("d")
    with p.loop(d, np.array([100, 200, 400])):
        p.wait("drive_q0", d)
    diagnostics = validate(p, caps)
    assert any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_linear_sweep_at_wait_duration_is_accepted() -> None:
    caps = _full_caps(predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d = p.variable("d")
    with p.for_loop(d, 100, 500, 100):
        p.wait("drive_q0", d)
    diagnostics = validate(p, caps)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_constant_wait_duration_is_accepted() -> None:
    caps = _full_caps(predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    p.wait("drive_q0", 100)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in validate(p, caps))
