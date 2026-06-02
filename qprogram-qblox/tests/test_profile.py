"""Tests for the ``qblox-default-v1`` capability profile.

These tests are the integration story: every core op the profile claims to support actually
validates clean against a :class:`PlatformCapabilities` that stacks ``qblox-default-v1`` (bus
side) with ``qprogram-base-v1`` (platform side), and every constraint the profile declares fires
when violated. If something in the core or vendor code starts emitting a token neither profile
lists, the failure shows up here.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

import qprogram_qblox  # noqa: F401 — side-effect: registers profile
from qprogram import QProgram
from qprogram.protocol import (
    BusCapabilities,
    CompilerCapabilities,
    Diagnostic,
    PlatformCapabilities,
    resolve_profile,
)
from qprogram.validation import validate
from qprogram.waveforms import IQDrag, IQPair, Square
from qprogram_qblox.profiles import QBLOX_DEFAULT_V1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_caps(
    *,
    bus_limit_overrides: Mapping[str, float] | None = None,
    platform_limit_overrides: Mapping[str, float] | None = None,
) -> PlatformCapabilities:
    """Build a :class:`PlatformCapabilities` stacking the qblox bus profile + qprogram base.

    Both halves of each :class:`BusCapabilities` use the same profile — the IQDrag-sigma
    DomainConstraint predicate is the only thing distinguishing hw from sw for these tests.
    """
    bus_cc = CompilerCapabilities.from_profile("qblox-default-v1", limit_overrides=bus_limit_overrides)
    platform_cc = CompilerCapabilities.from_profile(
        "qprogram-base-v1",
        limit_overrides=platform_limit_overrides,
    )
    # Spec-compliant wiring: qblox is HW by design, so its profile fills the hw slot only.
    # The platform-level slot has the qprogram-base profile on both halves so blocks can land
    # on either hw or sw depending on what their op-children require.
    bus_slot = BusCapabilities(hw=bus_cc, sw=None)
    platform_slot = BusCapabilities(hw=platform_cc, sw=platform_cc)
    return PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )


def _diagnostics(p: QProgram, caps: PlatformCapabilities) -> list[Diagnostic]:
    """Validate and return just the diagnostic list (drops the plan)."""
    diagnostics, _ = validate(p, caps)
    return diagnostics


# ---------------------------------------------------------------------------
# Profile registration
# ---------------------------------------------------------------------------


def test_qblox_default_profile_is_registered() -> None:
    assert resolve_profile("qblox-default-v1") is QBLOX_DEFAULT_V1


def test_qblox_default_profile_carries_bus_side_tokens() -> None:
    """qblox-default-v1 is bus-side now: op.play / waveform.* / measure.returns.* / vendor.qblox.*.
    No block.*, no expr.*, no sweep.* — those moved to qprogram-base-v1."""
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    assert caps.profile == "qblox-default-v1"
    assert caps.version == (0, 1, 0)
    for token in (
        "op.play",
        "op.measure",
        "op.wait",
        "vendor.qblox.acquire",
        "vendor.qblox.active_reset",
        "waveform.iq_drag",
        "measure.returns.iq",
        "measure.returns.state",
    ):
        assert token in caps.capabilities, token
    # These moved to qprogram-base-v1 and must NOT be in the qblox profile.
    for token in ("block.for_loop", "expr.constant", "sweep.linear"):
        assert token not in caps.capabilities, f"{token} should have moved to qprogram-base-v1"
    # Limits: only min_wait_duration_ns lives on the bus side.
    assert caps.limits["min_wait_duration_ns"] > 0
    assert "max_loop_nesting" not in caps.limits


def test_qprogram_base_profile_carries_platform_side_tokens() -> None:
    """qprogram-base-v1 holds every non-bus capability the DSL has — blocks, sweeps, expressions,
    bus-less ops. ``measure.returns.*`` is bus-level (it travels with Measure to the bus)."""
    caps = CompilerCapabilities.from_profile("qprogram-base-v1")
    for token in (
        "block.for_loop",
        "block.loop",
        "block.parallel",
        "block.conditional",
        "sweep.linear",
        "sweep.arbitrary",
        "expr.constant",
        "expr.measurement_ref",
        "op.set_parameter",
        "op.get_parameter",
        "op.set_crosstalk",
    ):
        assert token in caps.capabilities, token
    # measure.returns.* belongs on the bus profile.
    for token in ("measure.returns.iq", "measure.returns.state"):
        assert token not in caps.capabilities, f"{token} should be on the bus profile"


def test_qblox_default_vendor_versions_record_qblox() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    assert "qblox" in caps.vendor_versions


# ---------------------------------------------------------------------------
# Happy path — every supported op validates clean
# ---------------------------------------------------------------------------


def test_full_program_with_every_supported_construct_validates_clean() -> None:
    caps = _make_caps()
    p = QProgram()
    freq = p.variable("freq")
    pi_wf = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
    readout_wf = IQPair(I=Square(0.5, 200), Q=Square(0.0, 200))
    weights = IQPair(I=Square(1.0, 200), Q=Square(1.0, 200))
    with p.average(1000), p.for_loop(freq, 5e9, 6e9, 1e6):
        p.set_frequency("drive_q0", freq)
        p.play("drive_q0", pi_wf)
        p.sync()
        p.measure("readout_q0", readout_wf, weights)
    diagnostics = _diagnostics(p, caps)
    assert diagnostics == [], diagnostics


def test_qblox_vendor_op_validates_clean() -> None:
    caps = _make_caps()
    p = qprogram_qblox.QProgram()
    weights = IQPair(I=Square(1.0, 200), Q=Square(1.0, 200))
    p.qblox.acquire("readout_q0", weights)
    assert _diagnostics(p, caps) == []


# ---------------------------------------------------------------------------
# Predicate: arbitrary-wait-sweep (hard Diagnostic)
# ---------------------------------------------------------------------------


def test_arbitrary_sweep_at_wait_duration_is_rejected() -> None:
    caps = _make_caps()
    p = QProgram()
    d = p.variable("dur")
    with p.loop(d, np.array([100, 200, 400])):
        p.wait("drive_q0", d)
    diagnostics = _diagnostics(p, caps)
    codes = [diag.code for diag in diagnostics]
    assert "qblox.arbitrary-wait-sweep" in codes


def test_linear_sweep_at_wait_duration_is_accepted() -> None:
    caps = _make_caps()
    p = QProgram()
    d = p.variable("dur")
    with p.for_loop(d, 100, 500, 100):
        p.wait("drive_q0", d)
    diagnostics = _diagnostics(p, caps)
    assert "qblox.arbitrary-wait-sweep" not in [diag.code for diag in diagnostics]


# ---------------------------------------------------------------------------
# Predicate: IQDrag-sigma sweep → DomainConstraint(exclude=hw), forced-software info
# ---------------------------------------------------------------------------


def test_iq_drag_sigma_sweep_forces_software() -> None:
    """The DomainConstraint excludes hw; the enclosing for-loop must classify as ``{sw}`` only,
    and a single ``forced-software`` info diagnostic should surface on the topmost forced block.
    """
    caps = _make_caps()
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(100), p.for_loop(sigma, 1.0, 10.0, 1.0):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], errors
    info = [d for d in diagnostics if d.code == "forced-software"]
    assert len(info) == 1, info
    # The topmost forced block is Average (its parent is the root body).
    assert type(info[0].node).__name__ == "Average"
    # ForLoop ends up sw-only in the plan.
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_iq_drag_amplitude_sweep_stays_hw() -> None:
    """Sweeping IQDrag.amplitude (not sigma) doesn't trip the constraint; hw remains in plan."""
    caps = _make_caps()
    p = QProgram()
    amp = p.variable("amp")
    with p.for_loop(amp, 0.0, 1.0, 0.1):
        p.play("drive_q0", IQDrag(amplitude=amp, duration=40, sigma=8, beta=0.1))
    diagnostics, plan = validate(p, caps)
    info = [d for d in diagnostics if d.code == "forced-software"]
    assert info == []
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert "hw" in plan[for_loop]


# ---------------------------------------------------------------------------
# Limits sourced from the profile
# ---------------------------------------------------------------------------


def test_min_wait_duration_limit_fires() -> None:
    caps = _make_caps()
    # qblox-default-v1 sets min_wait_duration_ns=4; a 2 ns wait should fail.
    p = QProgram()
    p.wait("drive_q0", 2)
    diagnostics = _diagnostics(p, caps)
    assert any(
        diag.code == "limit-exceeded" and diag.limit and diag.limit[0] == "min_wait_duration_ns"
        for diag in diagnostics
    )


def test_device_can_tighten_limits_via_platform_overrides() -> None:
    """``max_loop_nesting`` lives at the platform slot now; overrides go there."""
    caps = _make_caps(platform_limit_overrides={"max_loop_nesting": 1})
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    with p.for_loop(v1, 0, 1, 0.1), p.for_loop(v2, 0, 1, 0.1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1))
    diagnostics = _diagnostics(p, caps)
    assert any(
        diag.code == "limit-exceeded" and diag.limit and diag.limit[0] == "max_loop_nesting"
        for diag in diagnostics
    )


# ---------------------------------------------------------------------------
# Conditional execution
# ---------------------------------------------------------------------------


def test_conditional_active_reset_validates_clean() -> None:
    """The canonical motivation: portable active-reset expressed as a conditional."""
    caps = _make_caps()
    p = QProgram()
    m = p.measure(
        "readout_q0",
        IQPair(I=Square(0.5, 200), Q=Square(0.0, 200)),
        IQPair(I=Square(1.0, 200), Q=Square(1.0, 200)),
        returns="iq,state",
    )
    with p.if_(m.state == 1):
        p.play("drive_q0", "pi_pulse")
    assert _diagnostics(p, caps) == []


def test_conditional_with_full_chain_validates_clean() -> None:
    caps = _make_caps()
    p = QProgram()
    m = p.measure(
        "readout_q0",
        IQPair(I=Square(0.5, 200), Q=Square(0.0, 200)),
        IQPair(I=Square(1.0, 200), Q=Square(1.0, 200)),
        returns="iq,state",
    )
    with p.if_(m.state == 0):
        p.play("drive_q0", "id_pulse")
    with p.elif_(m.state == 1):
        p.play("drive_q0", "pi_pulse")
    with p.else_():
        p.sync()
    assert _diagnostics(p, caps) == []


def test_conditional_missing_state_classification_caught_by_validator() -> None:
    caps = _make_caps()
    p = QProgram()
    m = p.measure(
        "readout_q0",
        IQPair(I=Square(0.5, 200), Q=Square(0.0, 200)),
        IQPair(I=Square(1.0, 200), Q=Square(1.0, 200)),
    )  # default returns=("iq",) — no state
    with p.if_(m.state == 1):
        p.play("drive_q0", "pi_pulse")
    diagnostics = _diagnostics(p, caps)
    assert any(d.code == "missing-classification" for d in diagnostics)
