"""Tests for :func:`qprogram.validation.validate`.

Covers the two-pass validator + classifier on a :class:`PlatformCapabilities`:

- Per-node check + routing (BusRef → bus slot, raw string → default, blocks → platform).
- Missing-capability diagnostics surface when no domain supports the node.
- Limit-violation checks (loop nesting, parallel arity, measurement count) against the platform
  slot; ``min_wait_duration_ns`` against the bus slot.
- Predicates with :class:`ValidationContext` access; can emit either :class:`Diagnostic` (hard
  error) or :class:`DomainConstraint` (soft domain restriction).
- HW/SW classification: a :class:`DomainConstraint` excluding ``"hw"`` propagates up through
  enclosing blocks; the highest forced-sw block emits one ``"forced-software"`` info diagnostic.
"""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import QProgram
from qprogram.buses import BusSchema
from qprogram.operations.play import Play
from qprogram.operations.wait import Wait
from qprogram.protocol import (
    BusCapabilities,
    CompilerCapabilities,
    Diagnostic,
    DomainConstraint,
    PlatformCapabilities,
    ValidationContext,
    register_capability_tokens,
)
from qprogram.validation import validate
from qprogram.variable import Variable
from qprogram.waveforms import IQDrag, Square

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BUS_TOKENS: frozenset[str] = frozenset(
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
        "waveform.single",
        "waveform.iq",
        "waveform.alias",
        "waveform.square",
        "waveform.iq_drag",
        "measure.returns.iq",
        "measure.returns.raw",
        "measure.returns.state",
    },
)
_PLATFORM_TOKENS: frozenset[str] = frozenset(
    {
        "op.set_parameter",
        "op.get_parameter",
        "op.set_crosstalk",
        "block.block",
        "block.average",
        "block.for_loop",
        "block.loop",
        "block.parallel",
        "block.conditional",
        "sweep.linear",
        "sweep.arbitrary",
        "expr.constant",
        "expr.variable",
        "expr.binary_op",
        "expr.comparison",
        "expr.measurement_ref",
    },
)


def _cc(profile: str, tokens: frozenset[str], *, limits=None, predicates=()) -> CompilerCapabilities:
    return CompilerCapabilities(
        profile=profile,
        version=(1, 0, 0),
        capabilities=tokens,
        limits=limits or {},
        predicates=predicates,
        vendor_versions={},
    )


def _slot(  # noqa: PLR0913  # small fixture helper; named-keyword args keep the call sites readable
    profile: str,
    tokens: frozenset[str],
    *,
    limits=None,
    predicates=(),
    hw: bool = True,
    sw: bool = True,
) -> BusCapabilities:
    """A BusCapabilities slot. Both halves share the same CompilerCapabilities by default."""
    cc = _cc(profile, tokens, limits=limits, predicates=predicates)
    return BusCapabilities(hw=cc if hw else None, sw=cc if sw else None)


def _empty_caps(*, bus_tokens: frozenset[str] = frozenset()) -> PlatformCapabilities:
    """Empty-ish PlatformCapabilities — the bus slot has the given tokens, platform is bare."""
    register_capability_tokens()  # idempotent: ensures core tokens are registered
    bus_slot = _slot("test-bus", bus_tokens)
    platform_slot = _slot("test-platform", frozenset())
    return PlatformCapabilities(bus={}, platform=platform_slot, default_bus_profile=bus_slot)


def _full_caps(
    *,
    bus_limits=None,
    platform_limits=None,
    bus_predicates=(),
) -> PlatformCapabilities:
    """A liberal :class:`PlatformCapabilities` accepting every core token in both halves."""
    register_capability_tokens()  # idempotent — ensures core tokens exist
    bus_slot = _slot("test-bus-full", _BUS_TOKENS, limits=bus_limits, predicates=bus_predicates)
    platform_slot = _slot("test-platform-full", _PLATFORM_TOKENS, limits=platform_limits)
    return PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )


def _diagnostics(p: QProgram, caps: PlatformCapabilities) -> list[Diagnostic]:
    """Validate and return just the diagnostic list (discards the plan)."""
    diagnostics, _plan = validate(p, caps)
    return diagnostics


# ---------------------------------------------------------------------------
# Empty and trivial programs
# ---------------------------------------------------------------------------


def test_validate_empty_program_returns_no_diagnostics() -> None:
    p = QProgram()
    assert _diagnostics(p, _empty_caps(bus_tokens=frozenset({"op.play"}))) == []


def test_validate_with_empty_capabilities_rejects_used_ops() -> None:
    p = QProgram()
    p.play("drive_q0", "pi")
    codes = [d.code for d in _diagnostics(p, _empty_caps())]
    assert "missing-capability" in codes


# ---------------------------------------------------------------------------
# Missing-capability diagnostics
# ---------------------------------------------------------------------------


def test_missing_op_token_emits_diagnostic_with_node_and_token() -> None:
    caps = _empty_caps(bus_tokens=frozenset({"op.sync"}))
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    p.sync()
    diagnostics = _diagnostics(p, caps)
    missing = [d for d in diagnostics if d.code == "missing-capability"]
    missing_tokens = {d.capability for d in missing}
    # op.play, waveform.single, waveform.square all missing on the Play node.
    assert "op.play" in missing_tokens
    assert "waveform.single" in missing_tokens
    assert "waveform.square" in missing_tokens
    for d in missing:
        assert d.node is not None
        assert type(d.node).__name__ == "Play"


def test_missing_capability_diagnostics_are_deterministic() -> None:
    """Token order matters for human-readable diff output; the validator sorts within each node."""
    caps = _empty_caps(bus_tokens=frozenset())
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    # When BOTH hw and sw fail with the same tokens, we get two copies (one per domain).
    # Within each domain, the order is sorted.
    diagnostics = _diagnostics(p, caps)
    hw_tokens = [
        d.capability
        for d in diagnostics
        if d.code == "missing-capability" and d.domain == "hw" and d.capability is not None
    ]
    sw_tokens = [
        d.capability
        for d in diagnostics
        if d.code == "missing-capability" and d.domain == "sw" and d.capability is not None
    ]
    assert hw_tokens == sorted(hw_tokens)
    assert sw_tokens == sorted(sw_tokens)


def test_diagnostic_silent_when_one_domain_supports_node() -> None:
    """When sw supports the node but hw doesn't, no diagnostic — the fallback works."""
    bus_slot = BusCapabilities(
        hw=_cc("hw-empty", frozenset()),
        sw=_cc("sw-full", _BUS_TOKENS),
    )
    platform_slot = _slot("platform-full", _PLATFORM_TOKENS)
    caps = PlatformCapabilities(
        bus={}, platform=platform_slot, default_bus_profile=bus_slot,
    )
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == []


# ---------------------------------------------------------------------------
# Routing: per-bus vs platform vs default
# ---------------------------------------------------------------------------


def test_bus_touching_op_routes_to_per_element_slot_when_present() -> None:
    """A Play on a (q, drive) BusRef routes to caps.bus[('q','drive')], not default."""
    drive_slot = _slot("drive-only", _BUS_TOKENS)
    other_slot = _slot("default-no-play", _BUS_TOKENS - {"op.play"})
    platform_slot = _slot("platform-full", _PLATFORM_TOKENS)
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive_slot},
        platform=platform_slot,
        default_bus_profile=other_slot,
    )
    schema = BusSchema.transmon()
    p = QProgram(schema=schema)
    p.play(schema.q[0].drive, IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1))
    diagnostics = _diagnostics(p, caps)
    # The drive slot supports op.play, so no diagnostic. If we'd routed to default, op.play would be missing.
    assert not any(d.code == "missing-capability" and d.capability == "op.play" for d in diagnostics)


def test_block_routes_to_platform_slot() -> None:
    """A for_loop's block.for_loop token is checked against the platform slot, not bus slots."""
    bus_slot = _slot("bus-without-blocks", _BUS_TOKENS)  # no block.* tokens
    platform_slot = _slot("platform-with-for", frozenset({"block.for_loop", "sweep.linear", "expr.constant"}))
    caps = PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )
    p = QProgram()
    v = p.variable("a")
    with p.for_loop(v, 0, 1, 0.5):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    # block.for_loop must NOT be missing — it's on the platform slot.
    assert not any(d.code == "missing-capability" and d.capability == "block.for_loop" for d in diagnostics)


# ---------------------------------------------------------------------------
# Limit checks
# ---------------------------------------------------------------------------


def test_max_loop_nesting_violation_emits_limit_exceeded() -> None:
    caps = _full_caps(platform_limits={"max_loop_nesting": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.for_loop(v1, 0, 1, 0.1), p.for_loop(v2, 0, 1, 0.1), p.for_loop(v3, 0, 1, 0.1):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "max_loop_nesting" for d in diagnostics)


def test_max_parallel_loops_violation() -> None:
    caps = _full_caps(platform_limits={"max_parallel_loops": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.for_loop(v1, 0, 1, 0.1) | p.for_loop(v2, 0, 1, 0.1) | p.for_loop(v3, 0, 1, 0.1):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "max_parallel_loops" for d in diagnostics)


def test_min_wait_duration_violation_for_constant_duration() -> None:
    caps = _full_caps(bus_limits={"min_wait_duration_ns": 10})
    p = QProgram()
    p.wait("drive_q0", 4)
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "min_wait_duration_ns" for d in diagnostics)


def test_unknown_limit_keys_are_silently_ignored() -> None:
    """Profiles may declare future limits the validator doesn't know about."""
    caps = _full_caps(platform_limits={"a_limit_the_validator_does_not_check": 0.0})
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    assert _diagnostics(p, caps) == []


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

    caps = _full_caps(bus_predicates=(my_predicate,))
    p = QProgram()
    v = p.variable("dur")
    with p.loop(v, np.array([100, 200])):
        p.wait("drive_q0", v)
    validate(p, caps)
    # Predicate ran twice — once for each non-None domain on the bus slot.
    assert seen_kinds == ["arbitrary", "arbitrary"]


def test_predicate_can_emit_diagnostics_per_node() -> None:
    """A predicate emitting a Diagnostic surfaces when the domain has no fallback."""

    def always_complain(node, ctx):  # noqa: ARG001
        if isinstance(node, Wait):
            yield Diagnostic(severity="error", code="test.complain", message="boo", node=node)

    # Put the predicate on a slot with sw=None so the diagnostic isn't suppressed by sw fallback.
    bus_slot = BusCapabilities(
        hw=_cc("hw", _BUS_TOKENS, predicates=(always_complain,)),
        sw=None,
    )
    platform_slot = _slot("platform-full", _PLATFORM_TOKENS)
    caps = PlatformCapabilities(bus={}, platform=platform_slot, default_bus_profile=bus_slot)
    p = QProgram()
    p.wait("drive_q0", 100)
    p.wait("drive_q0", 200)
    diagnostics = _diagnostics(p, caps)
    test_diags = [d for d in diagnostics if d.code == "test.complain"]
    assert len(test_diags) == 2


# ---------------------------------------------------------------------------
# Diagnostic-vs-DomainConstraint data-flow motivating case
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
    """A hard predicate Diagnostic fires when no domain saves it (here both hw and sw run the
    predicate and both emit Diagnostic, so support is empty and the diagnostic surfaces)."""
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d_var = p.variable("d")
    with p.loop(d_var, np.array([100, 200, 400])):
        p.wait("drive_q0", d_var)
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_linear_sweep_at_wait_duration_is_accepted() -> None:
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d_var = p.variable("d")
    with p.for_loop(d_var, 100, 500, 100):
        p.wait("drive_q0", d_var)
    diagnostics = _diagnostics(p, caps)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_constant_wait_duration_is_accepted() -> None:
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    p.wait("drive_q0", 100)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in _diagnostics(p, caps))


# ---------------------------------------------------------------------------
# HW / SW classification
# ---------------------------------------------------------------------------


def _drag_sigma_excludes_hw(node, ctx):
    """DomainConstraint version of the IQDrag-sigma case."""
    if not isinstance(node, Play) or not isinstance(node.waveform, IQDrag):
        return
    sigma = node.waveform.sigma
    if not isinstance(sigma, Variable):
        return
    if ctx.sweep_kind_of(sigma) is None:
        return
    yield DomainConstraint(
        node=node,
        exclude=frozenset({"hw"}),
        reason="IQDrag.sigma sweep is not real-time",
    )


def test_domain_constraint_silently_narrows_support() -> None:
    """A DomainConstraint alone (no Diagnostic) silences error output and sets the plan to {sw}."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == []
    # Find the for_loop node in the AST.
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_forced_software_info_diagnostic_fires_once_on_highest_block() -> None:
    """A DomainConstraint excluding hw causes one info diagnostic on the highest forced-sw block."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(shots=100), p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics = _diagnostics(p, caps)
    info_diags = [d for d in diagnostics if d.code == "forced-software"]
    assert len(info_diags) == 1
    # The info should attach to Average, the topmost forced-sw block (its parent is the root body).
    assert type(info_diags[0].node).__name__ == "Average"
    assert info_diags[0].severity == "info"
    assert info_diags[0].domain == "sw"


def test_amplitude_sweep_stays_hw_when_only_sigma_is_constrained() -> None:
    """A ForLoop sweeping IQDrag.amplitude (not sigma) is unaffected by the sigma constraint."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    amp = p.variable("amp")
    with p.for_loop(amp, 0, 1, 0.1):
        p.play("drive_q0", IQDrag(amplitude=amp, duration=40, sigma=8, beta=0.1))
    diagnostics, plan = validate(p, caps)
    info_diags = [d for d in diagnostics if d.code == "forced-software"]
    assert info_diags == []
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert "hw" in plan[for_loop]


@pytest.mark.parametrize(
    ("hw_present", "sw_present", "expected"),
    [
        (True, True, frozenset({"hw", "sw"})),
        (True, False, frozenset({"hw"})),
        (False, True, frozenset({"sw"})),
    ],
)
def test_node_domain_set_reflects_slot_availability(
    *, hw_present: bool, sw_present: bool, expected: frozenset[str],
) -> None:
    """A node's plan domain set is determined by which halves of its slot are non-None."""
    bus_slot = BusCapabilities(
        hw=_cc("hw", _BUS_TOKENS) if hw_present else None,
        sw=_cc("sw", _BUS_TOKENS) if sw_present else None,
    )
    platform_slot = _slot("platform-full", _PLATFORM_TOKENS)
    caps = PlatformCapabilities(bus={}, platform=platform_slot, default_bus_profile=bus_slot)
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    _, plan = validate(p, caps)
    play_node = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    assert plan[play_node] == expected
