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
"""Tests for :func:`qprogram.validation.validate`.

Covers the two-pass validator + classifier on a :class:`PlatformCapabilities`:

- Per-node check + routing (BusRef → bus slot, raw string → default, blocks → platform).
- Missing-capability diagnostics surface when no domain supports the node.
- Limit-violation checks (loop nesting, parallel arity, measurement count) against the platform
  slot; ``min_wait_duration_ns`` against the bus slot.
- Predicates with :class:`ValidationContext` access; can emit either :class:`Diagnostic` (hard
  error) or :class:`DomainConstraint` (soft domain restriction).
- real-time/host-side classification: a :class:`DomainConstraint` excluding ``"rt"`` propagates up through
  enclosing blocks; the highest forced-host block emits one ``"forced-host"`` warning diagnostic.
"""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import QProgram
from qprogram.blocks import Block, Sweep
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
from qprogram.sweeps import Range, Values
from qprogram.validation import _build_context, validate
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
        # set_parameter / get_parameter route to a bus slot (BUS_ATTRS == ("bus",)),
        # so their tokens belong here and not on the platform slot.
        "op.set_parameter",
        "op.get_parameter",
        "waveform.single",
        "waveform.iq",
        "waveform.alias",
        "waveform.square",
        "waveform.iq_drag",
        "measure.fields.iq",
        "measure.fields.raw",
        "measure.fields.state",
    },
)
_PLATFORM_TOKENS: frozenset[str] = frozenset(
    {
        "block.block",
        "block.average",
        "block.sweep",
        "block.parallel",
        "block.conditional",
        "sweep.linear",
        "sweep.arbitrary",
        "sweep.range",
        "sweep.values",
        "sweep.linspace",
        "sweep.logspace",
        "sweep.file",
        "sweep.repeat",
        "sweep.rotate",
        "sweep.concat",
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


def _slot(  # ruff: ignore[too-many-arguments]  # small fixture helper; named-keyword args keep the call sites readable
    profile: str,
    tokens: frozenset[str],
    *,
    limits=None,
    predicates=(),
    rt: bool = True,
    host: bool = True,
) -> BusCapabilities:
    """A BusCapabilities slot. Both halves share the same CompilerCapabilities by default."""
    cc = _cc(profile, tokens, limits=limits, predicates=predicates)
    return BusCapabilities(rt=cc if rt else None, host=cc if host else None)


def _empty_caps(*, bus_tokens: frozenset[str] = frozenset()) -> PlatformCapabilities:
    """Empty-ish :class:`PlatformCapabilities` — the bus slot has the given tokens, platform is bare."""
    register_capability_tokens()
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
    register_capability_tokens()
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
    """A token missing in BOTH domains yields exactly one diagnostic, not one copy per domain.

    That single diagnostic names both domains, and the missing tokens come out in sorted order.
    """
    caps = _empty_caps(bus_tokens=frozenset())
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    missing = [d for d in diagnostics if d.code == "missing-capability"]
    tokens = [d.capability for d in missing if d.capability is not None]
    assert len(tokens) == len(missing)
    assert tokens == sorted(tokens)
    assert len(tokens) == len(set(tokens))  # One diagnostic per token.
    for d in missing:
        # Missing in both domains → no single-domain attribution.
        assert d.domain is None
        assert "(rt)" in d.message
        assert "(host)" in d.message


def test_missing_capability_keeps_domain_when_one_sided() -> None:
    """A token missing in only one domain (the other half is None) is attributed to it."""
    bus_slot = BusCapabilities(rt=_cc("rt-empty", frozenset()), host=None)
    caps = PlatformCapabilities(
        bus={},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=bus_slot,
    )
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    missing = [d for d in _diagnostics(p, caps) if d.code == "missing-capability"]
    assert missing
    assert all(d.domain == "rt" for d in missing)


def test_diagnostic_silent_when_one_domain_supports_node() -> None:
    """When host supports the node but rt doesn't, no diagnostic — the fallback works."""
    bus_slot = BusCapabilities(
        rt=_cc("rt-empty", frozenset()),
        host=_cc("host-full", _BUS_TOKENS),
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
    # The drive slot supports op.play, so no diagnostic; routing to the default slot would report it missing.
    assert not any(d.code == "missing-capability" and d.capability == "op.play" for d in diagnostics)


def test_block_routes_to_platform_slot() -> None:
    """A sweep's block.sweep token is checked against the platform slot, not bus slots."""
    bus_slot = _slot("bus-without-blocks", _BUS_TOKENS)  # No block.* tokens.
    platform_slot = _slot(
        "platform-with-for", frozenset({"block.sweep", "sweep.linear", "sweep.range", "expr.constant"})
    )
    caps = PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )
    p = QProgram()
    v = p.variable("a")
    with p.sweep(v, Range(0, 1, 0.5)):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    # block.sweep must NOT be missing — it's on the platform slot.
    assert not any(d.code == "missing-capability" and d.capability == "block.sweep" for d in diagnostics)


# ---------------------------------------------------------------------------
# Limit checks
# ---------------------------------------------------------------------------


def test_max_loop_nesting_violation_emits_limit_exceeded() -> None:
    caps = _full_caps(platform_limits={"max_loop_nesting": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.sweep(v1, Range(0, 1, 0.1)), p.sweep(v2, Range(0, 1, 0.1)), p.sweep(v3, Range(0, 1, 0.1)):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "limit-exceeded" and d.limit and d.limit[0] == "max_loop_nesting" for d in diagnostics)


def test_max_parallel_loops_violation() -> None:
    caps = _full_caps(platform_limits={"max_parallel_loops": 2})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    v3 = p.variable("c")
    with p.sweep(v1, Range(0, 1, 0.1)) | p.sweep(v2, Range(0, 1, 0.1)) | p.sweep(v3, Range(0, 1, 0.1)):
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
    with p.sweep(v, Values(np.array([100, 200]))):
        p.wait("drive_q0", v)
    validate(p, caps)
    # Predicate ran twice — once for each non-None domain on the bus slot.
    assert seen_kinds == ["arbitrary", "arbitrary"]


def test_predicate_can_emit_diagnostics_per_node() -> None:
    """A predicate emitting a Diagnostic surfaces when the domain has no fallback."""

    def always_complain(node, ctx):  # ruff: ignore[unused-function-argument]
        if isinstance(node, Wait):
            yield Diagnostic(severity="error", code="test.complain", message="boo", node=node)

    # Put the predicate on a slot with host=None so the diagnostic isn't suppressed by host fallback.
    bus_slot = BusCapabilities(
        rt=_cc("rt", _BUS_TOKENS, predicates=(always_complain,)),
        host=None,
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
    """A hard predicate ``Diagnostic`` surfaces when no domain saves the node.

    Both halves of the slot run the predicate and both emit the diagnostic, so support is empty and
    the diagnostic reaches the caller.
    """
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d_var = p.variable("d")
    with p.sweep(d_var, Values(np.array([100, 200, 400]))):
        p.wait("drive_q0", d_var)
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_linear_sweep_at_wait_duration_is_accepted() -> None:
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    d_var = p.variable("d")
    with p.sweep(d_var, Range(100, 500, 100)):
        p.wait("drive_q0", d_var)
    diagnostics = _diagnostics(p, caps)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in diagnostics)


def test_constant_wait_duration_is_accepted() -> None:
    caps = _full_caps(bus_predicates=(_arbitrary_wait_predicate,))
    p = QProgram()
    p.wait("drive_q0", 100)
    assert not any(d.code == "test.arbitrary-wait-sweep" for d in _diagnostics(p, caps))


# ---------------------------------------------------------------------------
# real-time / host-side classification
# ---------------------------------------------------------------------------


def _drag_sigma_excludes_rt(node, ctx):
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
        exclude=frozenset({"rt"}),
        reason="IQDrag.sigma sweep is not real-time",
    )


def test_domain_constraint_targets_block_not_op() -> None:
    """A ``DomainConstraint`` on the binding loop forces the loop to ``{host}``, not the op.

    Rule (e2): the loop drops to host-side dispatch while the ``Play`` keeps whatever its slot
    supports.
    """
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_rt,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.sweep(sigma, Range(1, 10, 1)):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == []
    sweep_block = next(n for n in p.body.walk() if type(n).__name__ == "Sweep")
    play_node = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    # The loop is forced to {host}.
    assert plan[sweep_block] == frozenset({"host"})
    # The op's classification stays whatever the slot supports — Play itself is unaffected.
    assert "rt" in plan[play_node]


def test_forced_host_warning_fires_once_on_highest_block() -> None:
    """Exactly one ``forced-host`` warning surfaces, on the topmost forced block.

    A ``DomainConstraint`` on the inner loop forces both that loop and the enclosing ``Average``
    host-side; the single warning attaches to the ``Average`` and carries the constraint's reason
    text.
    """
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_rt,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(shots=100), p.sweep(sigma, Range(1, 10, 1)):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics = _diagnostics(p, caps)
    info_diags = [d for d in diagnostics if d.code == "forced-host"]
    assert len(info_diags) == 1
    # The diagnostic attaches to Average — the topmost forced-host block (its parent is the root body).
    assert type(info_diags[0].node).__name__ == "Average"
    assert info_diags[0].severity == "warning"
    assert info_diags[0].domain == "host"
    # The reason from the subtree's DomainConstraint surfaces in the message.
    assert "sigma" in info_diags[0].message


def test_amplitude_sweep_stays_rt_when_only_sigma_is_constrained() -> None:
    """A Sweep sweeping IQDrag.amplitude (not sigma) is unaffected by the sigma constraint."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_rt,))
    p = QProgram()
    amp = p.variable("amp")
    with p.sweep(amp, Range(0, 1, 0.1)):
        p.play("drive_q0", IQDrag(amplitude=amp, duration=40, sigma=8, beta=0.1))
    diagnostics, plan = validate(p, caps)
    info_diags = [d for d in diagnostics if d.code == "forced-host"]
    assert info_diags == []
    sweep_block = next(n for n in p.body.walk() if type(n).__name__ == "Sweep")
    assert "rt" in plan[sweep_block]


def test_op_targeted_constraint_emits_bad_domain_constraint_error() -> None:
    """A predicate that incorrectly targets an op node (not a Block) gets caught."""

    def bad_predicate(node, ctx):  # ruff: ignore[unused-function-argument]
        if isinstance(node, Play):
            yield DomainConstraint(
                node=node,  # WRONG — should be a Block.
                exclude=frozenset({"rt"}),
                reason="this predicate is broken",
            )

    caps = _full_caps(bus_predicates=(bad_predicate,))
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    diagnostics = _diagnostics(p, caps)
    bad = [d for d in diagnostics if d.code == "bad-domain-constraint"]
    # Predicates run once per slot domain (rt + host), but equivalent constraint outputs are
    # deduplicated, so the authoring mistake is reported exactly once.
    assert len(bad) == 1


def test_mixed_domain_error_when_op_children_disagree() -> None:
    """Two op-children with disjoint singleton supports trip the (d) mixed-domain check."""
    rt_bus = BusCapabilities(rt=_cc("rt-only", _BUS_TOKENS), host=None)
    host_bus = BusCapabilities(rt=None, host=_cc("host-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): rt_bus, ("q", "flux"): host_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=rt_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    v = p.variable("v")
    with p.sweep(v, Range(0, 1, 0.1)):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        p.play(schema.q[0].flux, Square(0.5, 100))  # Different bus → different singleton.
    diagnostics, _ = validate(p, caps)
    mixed = [d for d in diagnostics if d.code == "mixed-domain"]
    assert len(mixed) == 1


def test_host_block_child_auto_propagates_rt_exclusion() -> None:
    """A host-side block-child drops its parent host-side when the platform slot has a host half.

    Nothing is reported: the (e1) ``host-in-rt`` error fires only when that propagation cannot be
    honored.
    """
    rt_bus = BusCapabilities(rt=_cc("rt-only", _BUS_TOKENS), host=None)
    host_bus = BusCapabilities(rt=None, host=_cc("host-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): rt_bus, ("q", "flux"): host_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=rt_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    a = p.variable("a")
    b = p.variable("b")
    with p.sweep(a, Range(0, 1, 0.1)):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))  # Real-time op.
        with p.sweep(b, Range(0, 1, 0.1)):
            p.play(schema.q[0].flux, Square(0.5, 100))  # Host-side op nested inside.
    diagnostics, plan = validate(p, caps)
    nesting_errs = [d for d in diagnostics if d.code == "host-in-rt"]
    assert nesting_errs == []
    outer = next(n for n in p.body.walk() if type(n).__name__ == "Sweep")
    assert plan[outer] == frozenset({"host"})


def test_host_in_rt_nesting_error_fires_when_platform_lacks_host() -> None:
    """The explicit ``host-in-rt`` error fires when the platform slot has no host half.

    Without a host half, the (e1) auto-propagation has nowhere to fall back to.
    """
    rt_bus = BusCapabilities(rt=_cc("rt-only", _BUS_TOKENS), host=None)
    host_bus = BusCapabilities(rt=None, host=_cc("host-only", _BUS_TOKENS))
    # Platform slot has rt only — no host fallback available for blocks.
    platform_slot = BusCapabilities(rt=_cc("platform-rt", _PLATFORM_TOKENS), host=None)
    caps = PlatformCapabilities(
        bus={("q", "drive"): rt_bus, ("q", "flux"): host_bus},
        platform=platform_slot,
        default_bus_profile=rt_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    a = p.variable("a")
    b = p.variable("b")
    with p.sweep(a, Range(0, 1, 0.1)):
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        with p.sweep(b, Range(0, 1, 0.1)):
            p.play(schema.q[0].flux, Square(0.5, 100))
    diagnostics, _ = validate(p, caps)
    nesting_errs = [d for d in diagnostics if d.code == "host-in-rt"]
    assert len(nesting_errs) == 1


@pytest.mark.parametrize(
    ("rt_present", "host_present", "expected"),
    [
        (True, True, frozenset({"rt", "host"})),
        (True, False, frozenset({"rt"})),
        (False, True, frozenset({"host"})),
    ],
)
def test_node_domain_set_reflects_slot_availability(
    *,
    rt_present: bool,
    host_present: bool,
    expected: frozenset[str],
) -> None:
    """A node's plan domain set is determined by which halves of its slot are non-None."""
    bus_slot = BusCapabilities(
        rt=_cc("rt", _BUS_TOKENS) if rt_present else None,
        host=_cc("host", _BUS_TOKENS) if host_present else None,
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
    """Structurally identical ops get one plan entry each.

    A plan keyed by structural equality would collapse them and hand the compiler a plan with nodes
    missing.
    """
    caps = _full_caps()
    p = QProgram()
    p.play("drive_q0", "pi")
    p.wait("drive_q0", 100)
    p.play("drive_q0", "pi")  # Structurally identical to the first.
    _, plan = validate(p, caps)
    assert len(plan) == 3
    first, _, third = p.body.elements
    assert first == third  # Structural equality holds...
    assert plan[first] == plan[third]  # ...and both instances are present, looked up by identity.


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
    from qprogram.operations.play import Play as _Play  # ruff: ignore[import-outside-top-level]

    caps = _full_caps()
    p = QProgram()
    p.play("drive_q0", "pi")
    _, plan = validate(p, caps)
    stranger = _Play(bus="drive_q0", waveform="pi")
    assert stranger == p.body.elements[0]
    assert stranger not in plan
    with pytest.raises(KeyError):
        plan[stranger]


def test_forced_host_counted_per_block_instance() -> None:
    """Two identical loops, each independently forced to host, each surface their own info."""
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_rt,))
    p = QProgram()
    s1 = p.variable("s1")
    s2 = p.variable("s2")
    with p.sweep(s1, Range(1, 10, 1)):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=s1, beta=0.1))
    with p.sweep(s2, Range(1, 10, 1)):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=s2, beta=0.1))
    diagnostics, plan = validate(p, caps)
    infos = [d for d in diagnostics if d.code == "forced-host"]
    assert len(infos) == 2
    loops = [n for n in plan if type(n).__name__ == "Sweep"]
    assert [plan[lp] for lp in loops] == [frozenset({"host"}), frozenset({"host"})]


# ---------------------------------------------------------------------------
# Diagnostic noise suppression
# ---------------------------------------------------------------------------


def test_no_spurious_mixed_domain_when_op_child_already_failed() -> None:
    """An op-child with empty support must not also trip a parent ``mixed-domain`` error.

    The child's own diagnostic already explains why the parent's support is empty.
    """
    caps = _empty_caps(bus_tokens=_BUS_TOKENS - {"op.play", "waveform.alias"})
    # Platform slot needs the block tokens for the average itself.
    caps = PlatformCapabilities(
        bus={},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=_slot("bus-no-play", _BUS_TOKENS - {"op.play", "waveform.alias"}),
    )
    p = QProgram()
    with p.average(100):
        p.play("drive_q0", "pi")  # Fails everywhere: op.play is missing.
        p.wait("drive_q0", 100)  # Supported in both domains.
    diagnostics, plan = validate(p, caps)
    codes = [d.code for d in diagnostics]
    assert "missing-capability" in codes
    assert "mixed-domain" not in codes
    avg = next(n for n in plan if type(n).__name__ == "Average")
    assert plan[avg] == frozenset()


def test_genuine_mixed_domain_still_fires() -> None:
    """Disjoint singleton supports among healthy op-children still produce mixed-domain."""
    rt_bus = BusCapabilities(rt=_cc("rt-only", _BUS_TOKENS), host=None)
    host_bus = BusCapabilities(rt=None, host=_cc("host-only", _BUS_TOKENS))
    caps = PlatformCapabilities(
        bus={("q", "drive"): rt_bus, ("q", "flux"): host_bus},
        platform=_slot("platform-full", _PLATFORM_TOKENS),
        default_bus_profile=rt_bus,
    )
    schema = BusSchema.flux_tunable_transmon()
    p = QProgram(schema=schema)
    with p.block():
        p.play(schema.q[0].drive, IQDrag(0.5, 40, 8, 0.1))
        p.play(schema.q[0].flux, Square(0.5, 100))
    diagnostics, _ = validate(p, caps)
    assert any(d.code == "mixed-domain" for d in diagnostics)


def test_predicate_diagnostic_deduped_when_profile_fills_both_halves() -> None:
    """An identical ``Diagnostic`` from both halves of one profile is reported once.

    A profile filling the rt and the host half of a slot runs its predicates twice.
    """

    def complain_on_wait(node, ctx):  # ruff: ignore[unused-function-argument]
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
    """``sync()`` with no targets intersects across every bus the program touches.

    A bus slot lacking ``op.sync`` makes the broadcast fail, exactly like the explicit form does.
    """
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
    p.sync()  # Broadcast — touches q0/drive, whose slot lacks op.sync.
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
# Sweep-nesting accounting
# ---------------------------------------------------------------------------


def test_conditional_arms_do_not_count_toward_loop_nesting() -> None:
    from qprogram.validation import _build_context  # ruff: ignore[import-outside-top-level]

    p = QProgram()
    h = p.measure("readout_q0", "r", "w", fields=("iq", "state"))
    with p.if_(h.state == 1):
        p.play("drive_q0", Square(0.5, 100))
    ctx = _build_context(p)
    assert ctx.max_loop_nesting == 0


def test_average_counts_as_one_loop_level() -> None:
    from qprogram.validation import _build_context  # ruff: ignore[import-outside-top-level]

    p = QProgram()
    v = p.variable("x")
    with p.average(100), p.sweep(v, Range(0, 10, 1)):
        p.play("drive_q0", Square(0.5, 100))
    ctx = _build_context(p)
    assert ctx.max_loop_nesting == 2


# ---------------------------------------------------------------------------
# Average concerns only its measurement op-children (AFFECTS_AVERAGING)
# ---------------------------------------------------------------------------


def _heterogeneous_caps() -> PlatformCapabilities:
    """MyPlatform-shaped caps: drive/readout real-time+host-side, flux host-side-only."""
    register_capability_tokens()
    drive = _slot("drive", _BUS_TOKENS)
    readout = _slot("readout", _BUS_TOKENS)
    flux = _slot("flux", _BUS_TOKENS, rt=False)  # Slow DAC: no real-time engine.
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
    from qprogram.operations.measure import Measure  # ruff: ignore[import-outside-top-level]
    from qprogram.operations.play import Play  # ruff: ignore[import-outside-top-level]
    from qprogram.operations.set_offset import SetOffset  # ruff: ignore[import-outside-top-level]

    assert Measure.AFFECTS_AVERAGING is True
    assert Play.AFFECTS_AVERAGING is False
    assert SetOffset.AFFECTS_AVERAGING is False


def test_average_domain_ignores_non_measurement_op_children() -> None:
    """A host-side-only, non-measurement op in an average does not pull the average host-side.

    Only the averaging-relevant op-children — the measurements — gate the average's domain.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(100):
        p.set_offset(q0.flux, 0.1)  # Host-side-only (flux has no rt), NOT a measurement.
        p.measure(q0.readout, "wf", "w")  # Real-time-capable measurement.
    diagnostics, plan = validate(p, caps)
    assert [d for d in diagnostics if d.severity == "error"] == []
    assert plan[_node(p, "Average")] == frozenset({"rt", "host"})  # Gated by the measurement only.
    assert plan[_node(p, "SetOffset")] == frozenset({"host"})  # The op itself is still host-side.


def test_average_enclosing_host_sweep_forced_with_structural_reason_and_hint() -> None:
    """Example 1, average outside the sweep: the average is forced host-side and draws a hint.

    The reason is structural — the average *contains* a host-side loop — so a
    ``reorderable-averaging`` hint accompanies the ``forced-host`` warning.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.sweep(bias, Range(-0.5, 0.5, 0.1)):
        p.set_offset(q0.flux, bias)  # Host-side-only.
        p.measure(q0.readout, "wf", "w")
    diagnostics, plan = validate(p, caps)
    avg, floop = _node(p, "Average"), _node(p, "Sweep")
    assert plan[avg] == frozenset({"host"})
    assert plan[floop] == frozenset({"host"})

    forced = [d for d in diagnostics if d.code == "forced-host"]
    assert len(forced) == 1
    assert forced[0].node is avg
    assert "contains host-side-only sub-block 'Sweep'" in forced[0].message

    hints = [d for d in diagnostics if d.code == "reorderable-averaging"]
    assert len(hints) == 1
    assert hints[0].node is avg
    assert hints[0].severity == "info"


def test_average_inside_host_sweep_runs_in_rt() -> None:
    """Example 2, sweep outside and average inside: the average runs in real-time.

    It holds only the measurement sequence, so neither a ``forced-host`` warning nor a reorder hint
    is emitted.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.sweep(bias, Range(-0.5, 0.5, 0.1)):
        p.set_offset(q0.flux, bias)
        with p.average(100):
            p.measure(q0.readout, "wf", "w")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"rt", "host"})
    assert [d for d in diagnostics if d.code in {"forced-host", "reorderable-averaging"}] == []


def test_optimize_averaging_rewrites_to_rt() -> None:
    """``optimize`` turns example 1 into example 2: sweep outer, real-time average inner."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.sweep(bias, Range(-0.5, 0.5, 0.1)):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w")

    optimized = optimize(p, caps)

    # After the rewrite the sweep is the outer block, with the average nested inside it in real-time.
    assert type(optimized.body.elements[0]).__name__ == "Sweep"
    diagnostics, plan = validate(optimized, caps)
    assert plan[_node(optimized, "Average")] == frozenset({"rt", "host"})
    assert [d for d in diagnostics if d.code in {"forced-host", "reorderable-averaging"}] == []
    # The original program is untouched.
    assert type(p.body.elements[0]).__name__ == "Average"


def test_optimize_averaging_is_noop_when_nothing_is_host() -> None:
    """An all-real-time average has no host-side-only setup to hoist — the program is unchanged."""
    caps = _full_caps()
    p = QProgram()
    amp = p.variable("amp")
    with p.average(100), p.sweep(amp, Range(0, 1, 0.1)):
        p.play("drive_q0", IQDrag(amplitude=amp, duration=40, sigma=8, beta=0.1))
        p.measure("readout_q0", "wf", "w")
    assert optimize(p, caps).body == p.body


def test_average_without_measurement_keeps_host_only_op_in_host() -> None:
    """An ``Average`` with a host-side-only op and NO measurement stays ``{host}``.

    The measurement-only relaxation must never *widen* a measurement-less average to real-time.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(50):
        p.set_offset(q0.flux, 0.1)  # Host-side-only (flux has no rt), not a measurement.
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"host"})
    assert plan[_node(p, "SetOffset")] == frozenset({"host"})
    assert [d for d in diagnostics if d.severity == "error"] == []


def test_average_consensus_spans_multiple_measurement_children() -> None:
    """Two real-time-capable measurements keep the ``Average`` at ``{rt, host}``.

    The consensus intersects over every averaging-relevant op-child, not just the first one.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    with p.average(100):
        p.measure(q0.readout, "wf", "w", name="m0")
        p.measure(q0.readout, "wf", "w", name="m1")
    _, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"rt", "host"})


def test_forced_host_reason_surfaces_cause_through_intervening_block() -> None:
    """An unconstrained block between the forced ``Average`` and the constrained loop keeps the reason.

    The ``Average`` names the intervening sub-block AND carries the deep constraint reason.
    """
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_rt,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(100), p.block(), p.sweep(sigma, Range(1, 10, 1)):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    forced = [d for d in _diagnostics(p, caps) if d.code == "forced-host"]
    assert len(forced) == 1
    assert type(forced[0].node).__name__ == "Average"
    assert "contains host-side-only sub-block 'Block'" in forced[0].message
    assert "IQDrag.sigma sweep is not real-time" in forced[0].message  # Deep cause preserved.


def test_reorderable_hint_not_emitted_for_block_wrapped_sweep() -> None:
    """The hint fires only for the shape ``optimize`` can rewrite.

    A forced-host ``Average`` whose sweep is wrapped in a plain block is not reorderable, so no hint
    is emitted and ``optimize`` is a no-op.
    """
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.block(), p.sweep(bias, Range(-0.5, 0.5, 0.1)):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Average")] == frozenset({"host"})  # Still forced host-side.
    assert [d for d in diagnostics if d.code == "reorderable-averaging"] == []
    assert optimize(p, caps).body == p.body  # The shape doesn't match, so nothing is rewritten.


def test_reorderable_hint_suppressed_when_measurement_is_host_only() -> None:
    """If the averaged measurement itself can't run in real-time, the reorder wouldn't help: no hint."""
    register_capability_tokens()
    drive = _slot("drive", _BUS_TOKENS)
    readout = _slot("readout", _BUS_TOKENS, rt=False)  # Measurement is host-side-only here.
    flux = _slot("flux", _BUS_TOKENS, rt=False)
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive, ("q", "readout"): readout, ("q", "flux"): flux},
        platform=_slot("platform", _PLATFORM_TOKENS),
        default_bus_profile=drive,
    )
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.sweep(bias, Range(-0.5, 0.5, 0.1)):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    diagnostics, plan = validate(p, caps)
    assert plan[_node(p, "Measure")] == frozenset({"host"})
    assert [d for d in diagnostics if d.code == "reorderable-averaging"] == []


def test_optimize_averaging_rewrites_arbitrary_sweep_loop() -> None:
    """The rewrite handles an arbitrary-sweep ``Sweep``, not just a linear ``Sweep``."""
    caps = _heterogeneous_caps()
    schema = BusSchema.flux_tunable_transmon()
    q0 = schema.q[0]
    p = QProgram(schema=schema)
    bias = p.variable("bias")
    with p.average(100), p.sweep(bias, Values(np.array([-0.5, 0.0, 0.5]))):
        p.set_offset(q0.flux, bias)
        p.measure(q0.readout, "wf", "w", name="m")
    optimized = optimize(p, caps)
    outer = optimized.body.elements[0]
    assert type(outer).__name__ == "Sweep"
    assert np.array_equal(outer.source.values(), [-0.5, 0.0, 0.5])  # Sweep values preserved.
    _, plan = validate(optimized, caps)
    assert plan[_node(optimized, "Average")] == frozenset({"rt", "host"})


# ---------------------------------------------------------------------------
# max_loop_nesting is driven by Block.REPEATS, not by concrete classes
# ---------------------------------------------------------------------------


def test_repeating_vendor_block_counts_toward_loop_nesting() -> None:
    """A non-core block that declares REPEATS occupies a loop level."""

    class _VendorLoop(Block):
        REPEATS = True

    p = QProgram()
    var = p.variable("f")
    outer = _VendorLoop()
    p.body.append(outer)
    inner = Sweep(variable=var, source=Range(start=0.0, stop=1.0, step=0.1))
    outer.append(inner)
    inner.append(Play(bus="drive_q0", waveform="pi"))
    assert _build_context(p).max_loop_nesting == 2


def test_non_repeating_vendor_block_does_not_count() -> None:
    class _VendorGrouping(Block):
        pass

    p = QProgram()
    var = p.variable("f")
    outer = _VendorGrouping()
    p.body.append(outer)
    inner = Sweep(variable=var, source=Range(start=0.0, stop=1.0, step=0.1))
    outer.append(inner)
    inner.append(Play(bus="drive_q0", waveform="pi"))
    assert _build_context(p).max_loop_nesting == 1


def test_conditional_arms_still_add_no_loop_level() -> None:
    """Conditional traverses its arms at the same depth, so an arm adds no loop level."""
    p = QProgram()
    var = p.variable("f")
    with p.sweep(var, Range(0.0, 1.0, 0.1)):
        m = p.measure("readout_q0", "ro", "w", fields=("iq", "state"))
        with p.if_(m.state == 1):
            p.play("drive_q0", "pi")
    assert _build_context(p).max_loop_nesting == 1
