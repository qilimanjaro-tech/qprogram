"""Tests for the ``qdac-default-v1`` capability profile.

These tests are the integration story: every qdac op + waveform validates against the
profile-built :class:`~qprogram.PlatformCapabilities`, every predicate fires on the case it
should, and the canonical hw→sw fallback (sweep on ``SetOffset.offset``) reports a single
``forced-software`` info diagnostic on the highest forced block.
"""

from __future__ import annotations

from collections.abc import Mapping

import qprogram_qdac  # noqa: F401 — side effect: registers the profile
from qprogram_qdac import QProgram
from qprogram.protocol import (
    BusCapabilities,
    CompilerCapabilities,
    Diagnostic,
    PlatformCapabilities,
    resolve_profile,
)
from qprogram.validation import validate
from qprogram.waveforms import Ramp, Square
from qprogram_qdac.profiles import QDAC_DEFAULT_V1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_caps(
    *,
    bus_limit_overrides: Mapping[str, float] | None = None,
    platform_limit_overrides: Mapping[str, float] | None = None,
) -> PlatformCapabilities:
    """Build a :class:`PlatformCapabilities` stacking qdac-default-v1 + qprogram-base-v1.

    Both halves of each :class:`BusCapabilities` use the same profile — the
    ``SetOffset(variable)`` DomainConstraint predicate is the thing that distinguishes hw from
    sw for these tests.
    """
    bus_cc = CompilerCapabilities.from_profile("qdac-default-v1", limit_overrides=bus_limit_overrides)
    platform_cc = CompilerCapabilities.from_profile(
        "qprogram-base-v1",
        limit_overrides=platform_limit_overrides,
    )
    bus_slot = BusCapabilities(hw=bus_cc, sw=bus_cc)
    platform_slot = BusCapabilities(hw=platform_cc, sw=platform_cc)
    return PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )


def _diagnostics(p: QProgram, caps: PlatformCapabilities) -> list[Diagnostic]:
    diagnostics, _ = validate(p, caps)
    return diagnostics


# ---------------------------------------------------------------------------
# Profile registration
# ---------------------------------------------------------------------------


def test_qdac_default_profile_is_registered():
    assert resolve_profile("qdac-default-v1") is QDAC_DEFAULT_V1


def test_qdac_default_profile_carries_every_qdac_token():
    """qdac-default-v1: every qdac vendor op (all bus-touching) + single-channel waveforms."""
    caps = CompilerCapabilities.from_profile("qdac-default-v1")
    assert caps.profile == "qdac-default-v1"
    assert caps.version == (0, 1, 0)
    for token in (
        "vendor.qdac.wait_trigger",
        "vendor.qdac.set_trigger",
        "vendor.qdac.set_offset",
        "vendor.qdac.play",
        "waveform.ramp",
        "waveform.square",
        "waveform.single",
    ):
        assert token in caps.capabilities, token
    # Block / expression / sweep tokens belong to the platform profile.
    for token in ("block.for_loop", "expr.constant", "sweep.linear"):
        assert token not in caps.capabilities


def test_qdac_default_vendor_versions_record_qdac():
    caps = CompilerCapabilities.from_profile("qdac-default-v1")
    assert "qdac" in caps.vendor_versions


# ---------------------------------------------------------------------------
# Happy path — every supported construct validates clean
# ---------------------------------------------------------------------------


def test_program_with_every_supported_construct_validates_clean():
    caps = _make_caps()
    p = QProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    p.qdac.set_trigger("flux_q0", 50, position="start", outputs={1, 2, 3})
    p.qdac.play("flux_q0", Ramp(0.0, 1.0, 1000), dwell=10)
    p.qdac.wait_trigger("flux_q0", port=2)
    assert _diagnostics(p, caps) == []


def test_play_uses_bus_waveforms():
    """Play is bus-touching; its waveform-token check hits the routed bus slot."""
    caps = _make_caps()
    p = QProgram()
    p.qdac.play("flux_q0", Square(0.5, 100))
    assert _diagnostics(p, caps) == []


# ---------------------------------------------------------------------------
# Predicate: empty trigger outputs — hard Diagnostic
# ---------------------------------------------------------------------------


def test_empty_trigger_outputs_is_rejected():
    caps = _make_caps()
    p = QProgram()
    p.qdac.set_trigger("flux_q0", 50, outputs=())
    diagnostics = _diagnostics(p, caps)
    codes = {d.code for d in diagnostics}
    assert "qdac.empty-trigger-outputs" in codes


def test_non_empty_trigger_outputs_validates_clean():
    caps = _make_caps()
    p = QProgram()
    p.qdac.set_trigger("flux_q0", 50, outputs={1})
    assert _diagnostics(p, caps) == []


# ---------------------------------------------------------------------------
# Predicate: SetOffset variable sweep → DomainConstraint(exclude={"hw"})
# ---------------------------------------------------------------------------


def test_set_offset_constant_stays_hw():
    """SetOffset with a literal float doesn't trip the constraint."""
    caps = _make_caps()
    p = QProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    diagnostics, plan = validate(p, caps)
    set_off = next(n for n in p.body.walk() if type(n).__name__ == "SetOffset")
    assert "hw" in plan[set_off]
    assert not any(d.code == "forced-software" for d in diagnostics)


def test_set_offset_unbound_variable_stays_hw():
    """A free Variable (not loop-bound) is treated as a constant at upload time."""
    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    p.qdac.set_offset("flux_q0", v)
    set_off = next(n for n in p.body.walk() if type(n).__name__ == "SetOffset")
    _, plan = validate(p, caps)
    assert "hw" in plan[set_off]


def test_set_offset_swept_by_for_loop_forces_software():
    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        p.qdac.set_offset("flux_q0", v)
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], errors
    info = [d for d in diagnostics if d.code == "forced-software"]
    assert len(info) == 1, info
    # Attached to the topmost forced block — the ForLoop here (its parent is the root body).
    assert type(info[0].node).__name__ == "ForLoop"
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_set_offset_swept_by_arbitrary_loop_forces_software():
    """Same constraint, ``loop`` arbitrary sweep instead of ``for_loop`` linear sweep."""
    import numpy as np

    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    with p.loop(v, np.array([0.0, 0.25, 0.5, 0.75])):
        p.qdac.set_offset("flux_q0", v)
    _, plan = validate(p, caps)
    loop = next(n for n in p.body.walk() if type(n).__name__ == "Loop")
    assert plan[loop] == frozenset({"sw"})


def test_swept_waveform_var_in_play_also_forces_software():
    """Any qdac op referencing a loop-bound variable forces sw — even when the variable is
    embedded inside the Play's waveform parameters, not on the op's direct attributes.
    """
    caps = _make_caps()
    p = QProgram()
    amp = p.variable("amp")
    with p.for_loop(amp, 0.0, 1.0, 0.1):
        p.qdac.play("flux_q0", Square(amp, 100), dwell=10)
    diagnostics, plan = validate(p, caps)
    info = [d for d in diagnostics if d.code == "forced-software"]
    assert len(info) == 1
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_unbound_variable_in_qdac_op_stays_hw():
    """A free Variable (not loop-bound) is treated as a constant at upload time."""
    caps = _make_caps()
    p = QProgram()
    amp = p.variable("amp")
    # No surrounding loop binds `amp`, so the predicate doesn't fire.
    p.qdac.play("flux_q0", Square(amp, 100), dwell=10)
    diagnostics, plan = validate(p, caps)
    play_node = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    assert "hw" in plan[play_node]
    assert not any(d.code == "forced-software" for d in diagnostics)


def test_qdac_op_without_swept_var_stays_hw():
    """A qdac op with only literal arguments inside a loop is unaffected (the *loop* might still
    need to be hw-runnable, but the qdac op itself doesn't restrict it)."""
    caps = _make_caps()
    p = QProgram()
    v = p.variable("dummy")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        # qdac op references no swept variable — it's a constant ramp inside a loop.
        p.qdac.play("flux_q0", Square(0.5, 100), dwell=10)
    diagnostics, plan = validate(p, caps)
    assert not any(d.code == "forced-software" for d in diagnostics)
    play_node = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    assert "hw" in plan[play_node]
