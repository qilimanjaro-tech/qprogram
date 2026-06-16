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
from qprogram.optimization import optimize
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


def test_missing_capability_diagnostics_deduped_across_domains() -> None:
    """A token missing in BOTH domains yields exactly one diagnostic naming both, in sorted
    token order — not one copy per domain."""
    caps = _empty_caps(bus_tokens=frozenset())
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    missing = [d for d in diagnostics if d.code == "missing-capability"]
    tokens = [d.capability for d in missing if d.capability is not None]
    assert len(tokens) == len(missing)
    assert tokens == sorted(tokens)
    assert len(tokens) == len(set(tokens))  # one diagnostic per token
    for d in missing:
        # Missing in both domains → no single-domain attribution.
        assert d.domain is None
        assert "(hw)" in d.message
        assert "(sw)" in d.message


def test_missing_capability_keeps_domain_when_one_sided() -> None:
    """A token missing in only one domain (the other half is None) is attributed to it."""
    bus_slot = BusCapabilities(hw=_cc("hw-empty", frozenset()), sw=None)
    caps = PlatformCapabilities(
        bus={},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=bus_slot,
    )
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    missing = [d for d in _diagnostics(p, caps) if d.code == "missing-capability"]
    assert missing
    assert all(d.domain == "hw" for d in missing)


def test_diagnostic_silent_when_one_domain_supports_node() -> None:
    """When sw supports the node but hw doesn't, no diagnostic — the fallback works."""
    bus_slot = BusCapabilities(
        hw=_cc("hw-empty", frozenset()),
        sw=_cc("sw-full", _BUS_TOKENS),
    )
    platform_slot = _slot("platform-full", _PLATFORM_TOKENS)
    caps = PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
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
    """DomainConstraint targeting the binding loop of an IQDrag.sigma-sweep.

    Per the spec, predicates emit DomainConstraints that target the **loop block** binding the
    variable — not the operation node itself. The Play's classification is unaffected; only the
    loop's domain is restricted.
    """
    if not isinstance(node, Play) or not isinstance(node.waveform, IQDrag):
        return
    sigma = node.waveform.sigma
    if not isinstance(sigma, Variable):
        return
    binding_loop = ctx.binding_loop_of(sigma)
    if binding_loop is None:
        return
    yield DomainConstraint(
        node=binding_loop,
        exclude=frozenset({"hw"}),
        reason="IQDrag.sigma sweep is not real-time",
    )


def test_domain_constraint_targets_block_not_op() -> None:
    """A DomainConstraint targeting the binding loop forces the loop to {sw} but leaves the
    Play's classification unchanged (spec (e2))."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == []
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    play_node = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    # The loop is forced to {sw}.
    assert plan[for_loop] == frozenset({"sw"})
    # The op's classification stays whatever the slot supports — Play itself is unaffected.
    assert "hw" in plan[play_node]


def test_forced_software_warning_fires_once_on_highest_block() -> None:
    """A DomainConstraint targeting an inner loop, with both the outer Average and the loop forced
    to sw, surfaces exactly one ``forced-software`` warning on the topmost forced block, carrying
    the constraint's reason text."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(shots=100), p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics = _diagnostics(p, caps)
    info_diags = [d for d in diagnostics if d.code == "forced-software"]
    assert len(info_diags) == 1
    # The info attaches to Average — the topmost forced-sw block (its parent is the root body).
    assert type(info_diags[0].node).__name__ == "Average"
    assert info_diags[0].severity == "warning"
    assert info_diags[0].domain == "sw"
    # The reason from the subtree's DomainConstraint surfaces in the message.
    assert "sigma" in info_diags[0].message


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


def test_op_targeted_constraint_emits_bad_domain_constraint_error() -> None:
    """A predicate that incorrectly targets an op node (not a Block) gets caught."""

    def bad_predicate(node, ctx):  # noqa: ARG001
        if isinstance(node, Play):
            yield DomainConstraint(
                node=node,  # WRONG — should be a Block.
                exclude=frozenset({"hw"}),
                reason="this predicate is broken",
            )

    caps = _full_caps(bus_predicates=(bad_predicate,))
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    bad = [d for d in diagnostics if d.code == "bad-domain-constraint"]
    # Predicates run once per slot domain (hw + sw), but equivalent constraint outputs are
    # deduplicated, so the authoring mistake is reported exactly once.
    assert len(bad) == 1


def test_mixed_domain_error_when_op_children_disagree() -> None:
    """Two op-children with disjoint singleton supports trip the (d) mixed-domain check."""
    hw_bus = BusCapabilities(hw=_cc("hw-only", _BUS_TOKENS), sw=None)
    sw_bus = BusCapabilities(hw=None, sw=_cc("sw-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): hw_bus, ("q", "flux"): sw_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=hw_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    v = p.variable("v")
    with p.for_loop(v, 0, 1, 0.1):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        p.play(schema.q[0].flux, Square(0.5, 100))  # different bus → different singleton
    diagnostics, _ = validate(p, caps)
    mixed = [d for d in diagnostics if d.code == "mixed-domain"]
    assert len(mixed) == 1


def test_sw_block_child_auto_propagates_hw_exclusion() -> None:
    """An SW block-child inside an outer with platform.sw available makes the outer drop to sw
    (no error). The (e1) error fires only when the propagation can't be honoured — see the next
    test."""
    hw_bus = BusCapabilities(hw=_cc("hw-only", _BUS_TOKENS), sw=None)
    sw_bus = BusCapabilities(hw=None, sw=_cc("sw-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): hw_bus, ("q", "flux"): sw_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=hw_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    a = p.variable("a")
    b = p.variable("b")
    with p.for_loop(a, 0, 1, 0.1):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))  # HW op
        with p.for_loop(b, 0, 1, 0.1):
            p.play(schema.q[0].flux, Square(0.5, 100))  # SW op nested inside
    diagnostics, plan = validate(p, caps)
    nesting_errs = [d for d in diagnostics if d.code == "sw-in-hw"]
    assert nesting_errs == []
    outer = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[outer] == frozenset({"sw"})


def test_sw_in_hw_nesting_error_fires_when_platform_lacks_sw() -> None:
    """When the platform slot has no sw half, the (e1) auto-propagation can't fall back to sw —
    that's when the explicit ``sw-in-hw`` error fires."""
    hw_bus = BusCapabilities(hw=_cc("hw-only", _BUS_TOKENS), sw=None)
    sw_bus = BusCapabilities(hw=None, sw=_cc("sw-only", _BUS_TOKENS))
    # Platform slot has hw only — no sw fallback available for blocks.
    platform_slot = BusCapabilities(hw=_cc("platform-hw", _PLATFORM_TOKENS), sw=None)
    caps = PlatformCapabilities(
        bus={("q", "drive"): hw_bus, ("q", "flux"): sw_bus},
        platform=platform_slot,
        default_bus_profile=hw_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    a = p.variable("a")
    b = p.variable("b")
    with p.for_loop(a, 0, 1, 0.1):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        with p.for_loop(b, 0, 1, 0.1):
            p.play(schema.q[0].flux, Square(0.5, 100))
    diagnostics, _ = validate(p, caps)
    nesting_errs = [d for d in diagnostics if d.code == "sw-in-hw"]
    assert len(nesting_errs) == 1


@pytest.mark.parametrize(
    ("hw_present", "sw_present", "expected"),
    [
        (True, True, frozenset({"hw", "sw"})),
        (True, False, frozenset({"hw"})),
        (False, True, frozenset({"sw"})),
    ],
)
def test_node_domain_set_reflects_slot_availability(
    *,
    hw_present: bool,
    sw_present: bool,
    expected: frozenset[str],
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


# ---------------------------------------------------------------------------
# ExecutionPlan identity keying
# ---------------------------------------------------------------------------


def test_plan_keeps_one_entry_per_node_instance_for_identical_ops() -> None:
    """Structurally identical ops are distinct plan entries — a dict keyed by structural
    equality would collapse them and hand the compiler a plan missing nodes."""
    caps = _full_caps()
    p = QProgram()
    p.play("drive_q0", "pi")
    p.wait("drive_q0", 100)
    p.play("drive_q0", "pi")  # structurally identical to the first
    _, plan = validate(p, caps)
    assert len(plan) == 3
    first, _, third = p.body.elements
    assert first == third  # structural equality holds...
    assert plan[first] == plan[third]  # ...and both instances are present, looked up by identity


def test_plan_keeps_identical_blocks_distinct() -> None:
    caps = _full_caps()
    p = QProgram()
    with p.average(100):
        p.play("drive_q0", "pi")
    with p.average(100):
        p.play("drive_q0", "pi")
    _, plan = validate(p, caps)
    blocks = [n for n in plan if type(n).__name__ == "Average"]
    assert len(blocks) == 2
    assert blocks[0] is not blocks[1]


def test_plan_lookup_by_identity_not_structure() -> None:
    """A structurally equal node that is NOT in the program is not in the plan."""
    from qprogram.operations.play import Play as _Play  # noqa: PLC0415

    caps = _full_caps()
    p = QProgram()
    p.play("drive_q0", "pi")
    _, plan = validate(p, caps)
    stranger = _Play(bus="drive_q0", waveform="pi")
    assert stranger == p.body.elements[0]
    assert stranger not in plan
    with pytest.raises(KeyError):
        plan[stranger]


def test_forced_software_counted_per_block_instance() -> None:
    """Two identical loops, each independently forced to sw, each surface their own info."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    s1 = p.variable("s1")
    s2 = p.variable("s2")
    with p.for_loop(s1, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=s1, beta=0.1))
    with p.for_loop(s2, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=s2, beta=0.1))
    diagnostics, plan = validate(p, caps)
    infos = [d for d in diagnostics if d.code == "forced-software"]
    assert len(infos) == 2
    loops = [n for n in plan if type(n).__name__ == "ForLoop"]
    assert [plan[lp] for lp in loops] == [frozenset({"sw"}), frozenset({"sw"})]


# ---------------------------------------------------------------------------
# Diagnostic noise suppression
# ---------------------------------------------------------------------------


def test_no_spurious_mixed_domain_when_op_child_already_failed() -> None:
    """An op-child with empty support (already diagnosed) must not also trip a parent
    ``mixed-domain`` error — the parent's emptiness is explained by the child diagnostic."""
    caps = _empty_caps(bus_tokens=_BUS_TOKENS - {"op.play", "waveform.alias"})
    # Platform slot needs the block tokens for the average itself.
    caps = PlatformCapabilities(
        bus={},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=_slot("bus-no-play", _BUS_TOKENS - {"op.play", "waveform.alias"}),
    )
    p = QProgram()
    with p.average(100):
        p.play("drive_q0", "pi")  # fails everywhere: op.play missing
        p.wait("drive_q0", 100)  # fine
    diagnostics, plan = validate(p, caps)
    codes = [d.code for d in diagnostics]
    assert "missing-capability" in codes
    assert "mixed-domain" not in codes
    avg = next(n for n in plan if type(n).__name__ == "Average")
    assert plan[avg] == frozenset()


def test_genuine_mixed_domain_still_fires() -> None:
    """Disjoint singleton supports among healthy op-children still produce mixed-domain."""
    hw_bus = BusCapabilities(hw=_cc("hw-only", _BUS_TOKENS), sw=None)
    sw_bus = BusCapabilities(hw=None, sw=_cc("sw-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): hw_bus, ("q", "flux"): sw_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=hw_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    with p.block():
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        p.play(schema.q[0].flux, Square(0.5, 100))
    diagnostics, _ = validate(p, caps)
    assert any(d.code == "mixed-domain" for d in diagnostics)


def test_predicate_diagnostic_deduped_when_profile_fills_both_halves() -> None:
    """The same profile in hw and sw runs its predicate twice; an identical Diagnostic output
    is reported once."""

    def complain_on_wait(node, ctx):  # noqa: ARG001
        if isinstance(node, Wait):
            yield Diagnostic(severity="error", code="test.dup", message="no waits", node=node)

    caps = _full_caps(bus_predicates=(complain_on_wait,))
    p = QProgram()
    p.wait("drive_q0", 100)
    diagnostics = _diagnostics(p, caps)
    assert len([d for d in diagnostics if d.code == "test.dup"]) == 1


# ---------------------------------------------------------------------------
# Sync(None) broadcast routing
# ---------------------------------------------------------------------------


def test_sync_all_intersects_every_program_bus() -> None:
    """``sync()`` with no targets must intersect across every bus the program touches —
    a bus slot lacking op.sync makes the broadcast fail, exactly like the explicit form."""
    no_sync_bus = _slot("drive-no-sync", _BUS_TOKENS - {"op.sync"})
    full_bus = _slot("default-full", _BUS_TOKENS)
    caps = PlatformCapabilities(
        bus={("q", "drive"): no_sync_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=full_bus,
    )
    schema = BusSchema.transmon()
    p = QProgram(schema=schema)
    p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
    p.sync()  # broadcast — touches q0/drive, whose slot lacks op.sync
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "missing-capability" and d.capability == "op.sync" for d in diagnostics)


def test_sync_all_passes_when_every_bus_supports_it() -> None:
    caps = _full_caps()
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    p.sync()
    assert [d for d in _diagnostics(p, caps) if d.severity == "error"] == []


def test_sync_all_in_program_with_no_buses_routes_to_default() -> None:
    """A bare sync() in a bus-less program can't broadcast — falls back to the default slot."""
    caps = _full_caps()
    p = QProgram()
    p.sync()
    assert [d for d in _diagnostics(p, caps) if d.severity == "error"] == []


# ---------------------------------------------------------------------------
# Loop-nesting accounting
# ---------------------------------------------------------------------------


def test_conditional_arms_do_not_count_toward_loop_nesting() -> None:
    from qprogram.validation import _build_context  # noqa: PLC0415

    p = QProgram()
    h = p.measure("readout_q0", "r", "w", returns="iq,state")
    with p.if_(h.state == 1):
        p.play("drive_q0", Square(0.5, 100))
    ctx = _build_context(p)
    assert ctx.max_loop_nesting == 0


def test_average_counts_as_one_loop_level() -> None:
    from qprogram.validation import _build_context  # noqa: PLC0415

    p = QProgram()
    v = p.variable("x")
    with p.average(100), p.for_loop(v, 0, 10, 1):
        p.play("drive_q0", Square(0.5, 100))
    ctx = _build_context(p)
    assert ctx.max_loop_nesting == 2


# ---------------------------------------------------------------------------
# Average concerns only its measurement op-children (AFFECTS_AVERAGING)
# ---------------------------------------------------------------------------


def _heterogeneous_caps() -> PlatformCapabilities:
    """MyPlatform-shaped caps: drive/readout hardware+software, flux software-only."""
    register_capability_tokens()
    drive = _slot("drive", _BUS_TOKENS)
    readout = _slot("readout", _BUS_TOKENS)
    flux = _slot("flux", _BUS_TOKENS, hw=False)  # slow DAC: no hardware engine
    platform = _slot("platform", _PLATFORM_TOKENS)
    return PlatformCapabilities(
        bus={("q", "drive"): drive, ("q", "readout"): readout, ("q", "flux"): flux},
        platform=platform,
        default_bus_profile=drive,
    )


def _node(p: QProgram, type_name: str):
    return next(n for n in p.body.walk() if type(n).__name__ == type_name)


def test_affects_averaging_marker_defaults() -> None:
    """Measurements opt in to averaging-relevance; ordinary ops don't."""
    from qprogram.operations.measure import Measure  # noqa: PLC0415
    from qprogram.operations.play import Play  # noqa: PLC0415
    from qprogram.operations.set_offset import SetOffset  # noqa: PLC0415

    assert Measure.AFFECTS_AVERAGING is True
    assert Play.AFFECTS_AVERAGING is False
    assert SetOffset.AFFECTS_AVERAGING is False


def test_average_domain_ignores_non_measurement_op_children() -> None:
    """A software-only, non-measurement op directly in an average does not pull it to software —
    only the measurement (averaging-relevant) op-children gate the average's domain."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(100):
        p.set_offset(q0.flux, 0.1)  # software-only (flux has no hw), NOT a measurement
        p.measure(q0.readout, "wf", "w")  # hardware-capable measurement
    diagnostics, plan = validate(p, caps)
    assert [d for d in diagnostics if d.severity == "error"] == []
    assert plan[_node(p, "Average")] == frozenset({"hw", "sw"})  # gated by the measurement only
    assert plan[_node(p, "SetOffset")] == frozenset({"sw"})  # the op itself is still software


def test_average_enclosing_software_sweep_forced_with_structural_reason_and_hint() -> None:
    """Example 1: average outside the sweep. The average is forced software because it *contains*
    a software loop (structural reason), and draws a reorderable-averaging hint."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.for_loop(bias, -0.5, 0.5, 0.1):
        p.set_offset(q0.flux, bias)  # software-only
        p.measure(q0.readout, "wf", "w")
    diagnostics, plan = validate(p, caps)
    avg, floop = _node(p, "Average"), _node(p, "ForLoop")
    assert plan[avg] == frozenset({"sw"})
    assert plan[floop] == frozenset({"sw"})

    forced = [d for d in diagnostics if d.code == "forced-software"]
    assert len(forced) == 1
    assert forced[0].node is avg
    assert "contains software-only sub-block 'ForLoop'" in forced[0].message

    hints = [d for d in diagnostics if d.code == "reorderable-averaging"]
    assert len(hints) == 1
    assert hints[0].node is avg
    assert hints[0].severity == "info"


def test_average_inside_software_sweep_runs_in_hardware() -> None:
    """Example 2: sweep outside, average inside. The average holds only the measurement sequence,
    so it runs in hardware; no forced-software warning and no reorder hint."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.for_loop(bias, -0.5, 0.5, 0.1):
        p.set_offset(q0.flux, bias)
        with p.average(100):
            p.measure(q0.readout, "wf", "w")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"hw", "sw"})
    assert [d for d in diagnostics if d.code in ("forced-software", "reorderable-averaging")] == []


def test_optimize_averaging_rewrites_to_hardware() -> None:
    """``optimize`` turns example 1 into example 2: sweep outer, hardware average inner."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.for_loop(bias, -0.5, 0.5, 0.1):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w")

    optimized = optimize(p, caps)

    # The sweep is now the outer block; the average is nested inside and runs in hardware.
    assert type(optimized.body.elements[0]).__name__ == "ForLoop"
    diagnostics, plan = validate(optimized, caps)
    assert plan[_node(optimized, "Average")] == frozenset({"hw", "sw"})
    assert [d for d in diagnostics if d.code in ("forced-software", "reorderable-averaging")] == []
    # The original program is untouched.
    assert type(p.body.elements[0]).__name__ == "Average"


def test_optimize_averaging_is_noop_when_nothing_is_software() -> None:
    """An all-hardware average has no software-only setup to hoist — the program is unchanged."""
    caps = _full_caps()
    p = QProgram()
    amp = p.variable("amp")
    with p.average(100), p.for_loop(amp, 0, 1, 0.1):
        p.play("drive_q0", IQDrag(amplitude=amp, duration=40, sigma=8, beta=0.1))
        p.measure("readout_q0", "wf", "w")
    assert optimize(p, caps).body == p.body


def test_average_without_measurement_keeps_software_only_op_in_software() -> None:
    """An Average with a software-only op and NO measurement must stay {sw}: the measurement-only
    relaxation must never *widen* a measurement-less average to hardware."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(50):
        p.set_offset(q0.flux, 0.1)  # software-only (flux has no hw), not a measurement
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"sw"})
    assert plan[_node(p, "SetOffset")] == frozenset({"sw"})
    assert [d for d in diagnostics if d.severity == "error"] == []


def test_average_consensus_spans_multiple_measurement_children() -> None:
    """Two hardware-capable measurements keep the Average {hw,sw} — the consensus intersects over
    every averaging-relevant op-child, not just the first."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(100):
        p.measure(q0.readout, "wf", "w", name="m0")
        p.measure(q0.readout, "wf", "w", name="m1")
    _, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"hw", "sw"})


def test_forced_software_reason_surfaces_cause_through_intervening_block() -> None:
    """An unconstrained block between the forced Average and the constrained loop must not swallow
    the real reason — the Average names the sub-block AND carries the deep constraint reason."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(100), p.block(), p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    forced = [d for d in _diagnostics(p, caps) if d.code == "forced-software"]
    assert len(forced) == 1
    assert type(forced[0].node).__name__ == "Average"
    assert "contains software-only sub-block 'Block'" in forced[0].message
    assert "IQDrag.sigma sweep is not real-time" in forced[0].message  # deep cause preserved


def test_reorderable_hint_not_emitted_for_block_wrapped_sweep() -> None:
    """The hint fires only for the shape ``optimize`` can rewrite. A forced-sw Average whose sweep
    is wrapped in a plain block is not reorderable — no hint, and optimize is a no-op."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.block(), p.for_loop(bias, -0.5, 0.5, 0.1):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"sw"})  # still forced sw
    assert [d for d in diagnostics if d.code == "reorderable-averaging"] == []
    assert optimize(p, caps).body == p.body  # shape doesn't match -> no rewrite


def test_reorderable_hint_suppressed_when_measurement_is_software_only() -> None:
    """If the averaged measurement itself can't run in hardware, the reorder wouldn't help: no hint."""
    register_capability_tokens()
    drive = _slot("drive", _BUS_TOKENS)
    readout = _slot("readout", _BUS_TOKENS, hw=False)  # measurement is software-only here
    flux = _slot("flux", _BUS_TOKENS, hw=False)
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive, ("q", "readout"): readout, ("q", "flux"): flux},
        platform=_slot("platform", _PLATFORM_TOKENS),
        default_bus_profile=drive,
    )
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.for_loop(bias, -0.5, 0.5, 0.1):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Measure")] == frozenset({"sw"})
    assert [d for d in diagnostics if d.code == "reorderable-averaging"] == []


def test_optimize_averaging_rewrites_arbitrary_sweep_loop() -> None:
    """The rewrite handles an arbitrary-sweep ``Loop``, not just a linear ``ForLoop``."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.loop(bias, np.array([-0.5, 0.0, 0.5])):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    optimized = optimize(p, caps)
    outer = optimized.body.elements[0]
    assert type(outer).__name__ == "Loop"
    assert np.array_equal(outer.values, [-0.5, 0.0, 0.5])  # sweep values preserved
    _, plan = validate(optimized, caps)
    assert plan[_node(optimized, "Average")] == frozenset({"hw", "sw"})
