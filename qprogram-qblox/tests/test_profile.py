"""Tests for the ``qblox-default-v1`` capability profile.

These tests are the integration story: every core op the profile claims to
support actually validates clean, and every constraint the profile declares
fires when violated. If something in the core or vendor code starts emitting
a token the profile doesn't list, the failure shows up here.
"""

from __future__ import annotations

import numpy as np

import qprogram_qblox  # noqa: F401 — side-effect: registers profile
from qprogram import QProgram, validate
from qprogram.protocol import CompilerCapabilities, resolve_profile
from qprogram.waveforms import IQDrag, IQPair, Square
from qprogram_qblox.profiles import QBLOX_DEFAULT_V1


# ---------------------------------------------------------------------------
# Profile registration
# ---------------------------------------------------------------------------


def test_qblox_default_profile_is_registered() -> None:
    assert resolve_profile("qblox-default-v1") is QBLOX_DEFAULT_V1


def test_compiler_capabilities_from_qblox_default() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    assert caps.profile == "qblox-default-v1"
    assert caps.version == (0, 1, 0)
    # Sanity-check a few representative tokens.
    for token in (
        "op.play",
        "op.measure",
        "op.wait",
        "block.for_loop",
        "block.loop",
        "block.parallel",
        "sweep.linear",
        "sweep.arbitrary",
        "vendor.qblox.acquire",
        "vendor.qblox.active_reset",
        "waveform.iq_drag",
        "measure.returns.iq",
    ):
        assert token in caps.capabilities, token
    # Limits are populated.
    assert caps.limits["max_loop_nesting"] > 0
    assert caps.limits["min_wait_duration_ns"] > 0


def test_qblox_default_vendor_versions_record_qblox() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    assert "qblox" in caps.vendor_versions


# ---------------------------------------------------------------------------
# Happy path — every supported op validates clean
# ---------------------------------------------------------------------------


def test_full_program_with_every_supported_construct_validates_clean() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = QProgram()
    freq = p.variable("freq")
    pi_wf = IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1)
    readout_wf = IQPair(I=Square(0.5, 200), Q=Square(0.0, 200))
    weights = IQPair(I=Square(1.0, 200), Q=Square(1.0, 200))
    with p.average(1000), p.for_loop(freq, 5e9, 6e9, 1e6):
        p.set_frequency("drive_q0", freq)
        p.play("drive_q0", pi_wf)
        p.sync()
        p.measure("readout_q0", readout_wf, weights)
    diagnostics = validate(p, caps)
    assert diagnostics == [], diagnostics


def test_qblox_vendor_op_validates_clean() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = qprogram_qblox.QProgram()
    weights = IQPair(I=Square(1.0, 200), Q=Square(1.0, 200))
    p.qblox.acquire("readout_q0", weights)
    assert validate(p, caps) == []


# ---------------------------------------------------------------------------
# Predicate: arbitrary-wait-sweep
# ---------------------------------------------------------------------------


def test_arbitrary_sweep_at_wait_duration_is_rejected() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = QProgram()
    d = p.variable("dur")
    with p.loop(d, np.array([100, 200, 400])):
        p.wait("drive_q0", d)
    diagnostics = validate(p, caps)
    codes = [diag.code for diag in diagnostics]
    assert "qblox.arbitrary-wait-sweep" in codes


def test_linear_sweep_at_wait_duration_is_accepted() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = QProgram()
    d = p.variable("dur")
    with p.for_loop(d, 100, 500, 100):
        p.wait("drive_q0", d)
    diagnostics = validate(p, caps)
    assert "qblox.arbitrary-wait-sweep" not in [diag.code for diag in diagnostics]


# ---------------------------------------------------------------------------
# Limits sourced from the profile
# ---------------------------------------------------------------------------


def test_min_wait_duration_limit_fires() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    # Profile default is 4 ns; a 2 ns wait should fail.
    p = QProgram()
    p.wait("drive_q0", 2)
    diagnostics = validate(p, caps)
    assert any(
        diag.code == "limit-exceeded" and diag.limit and diag.limit[0] == "min_wait_duration_ns" for diag in diagnostics
    )


def test_device_can_tighten_limits_via_overrides() -> None:
    caps = CompilerCapabilities.from_profile(
        "qblox-default-v1",
        limit_overrides={"max_loop_nesting": 1},
    )
    p = QProgram()
    v1 = p.variable("a")
    v2 = p.variable("b")
    with p.for_loop(v1, 0, 1, 0.1), p.for_loop(v2, 0, 1, 0.1):
        p.play("drive_q0", Square(0.5, 100))
    diagnostics = validate(p, caps)
    assert any(
        diag.code == "limit-exceeded" and diag.limit and diag.limit[0] == "max_loop_nesting" for diag in diagnostics
    )


# ---------------------------------------------------------------------------
# Conditional execution
# ---------------------------------------------------------------------------


def test_conditional_active_reset_validates_clean() -> None:
    """The canonical motivation: portable active-reset expressed as a conditional."""
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = QProgram()
    m = p.measure(
        "readout_q0",
        IQPair(I=Square(0.5, 200), Q=Square(0.0, 200)),
        IQPair(I=Square(1.0, 200), Q=Square(1.0, 200)),
        returns="iq,state",
    )
    with p.if_(m.state == 1):
        p.play("drive_q0", "pi_pulse")
    assert validate(p, caps) == []


def test_conditional_with_full_chain_validates_clean() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
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
    assert validate(p, caps) == []


def test_conditional_missing_state_classification_caught_by_validator() -> None:
    caps = CompilerCapabilities.from_profile("qblox-default-v1")
    p = QProgram()
    m = p.measure(
        "readout_q0",
        IQPair(I=Square(0.5, 200), Q=Square(0.0, 200)),
        IQPair(I=Square(1.0, 200), Q=Square(1.0, 200)),
    )  # default returns=("iq",) — no state
    with p.if_(m.state == 1):
        p.play("drive_q0", "pi_pulse")
    diagnostics = validate(p, caps)
    assert any(d.code == "missing-classification" for d in diagnostics)
