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
    # Spec-compliant wiring: qdac has no FPGA, so its profile fills the sw slot only.
    # The platform-level slot has both halves so blocks can be hw or sw depending on contents.
    bus_slot = BusCapabilities(hw=None, sw=bus_cc)
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
# Op classification — qdac ops are SW by design (spec (b))
# ---------------------------------------------------------------------------


def test_qdac_set_offset_is_sw_only():
    """qdac is wired to the sw slot only, so every qdac op classifies as ``{sw}`` regardless of
    arguments — that's the design-time classification from spec (b)."""
    caps = _make_caps()
    p = QProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    _, plan = validate(p, caps)
    set_off = next(n for n in p.body.walk() if type(n).__name__ == "SetOffset")
    assert plan[set_off] == frozenset({"sw"})


def test_qdac_play_is_sw_only():
    """Same for qdac.play — bus-touching but the bus is sw-only."""
    caps = _make_caps()
    p = QProgram()
    p.qdac.play("flux_q0", Square(0.5, 100), dwell=10)
    _, plan = validate(p, caps)
    play = next(n for n in p.body.walk() if type(n).__name__ == "Play")
    assert plan[play] == frozenset({"sw"})


# ---------------------------------------------------------------------------
# Loop classification — op-children consensus + predicate constraints
# ---------------------------------------------------------------------------


def test_loop_with_qdac_op_classifies_as_sw():
    """A loop whose only op-child is a qdac op (SW) classifies as SW from op-children consensus
    alone — no constraint needed."""
    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        p.qdac.set_offset("flux_q0", v)
    _, plan = validate(p, caps)
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_loop_with_unswept_qdac_op_also_classifies_as_sw():
    """No swept variable on the qdac op, but the op is still SW by design — so the enclosing
    loop classifies as SW from op-children consensus."""
    caps = _make_caps()
    p = QProgram()
    v = p.variable("dummy")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        p.qdac.play("flux_q0", Square(0.5, 100), dwell=10)
    diagnostics, plan = validate(p, caps)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == []
    for_loop = next(n for n in p.body.walk() if type(n).__name__ == "ForLoop")
    assert plan[for_loop] == frozenset({"sw"})


def test_arbitrary_loop_with_qdac_op_classifies_as_sw():
    """The ``loop`` (arbitrary-array) variant works identically."""
    import numpy as np

    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    with p.loop(v, np.array([0.0, 0.25, 0.5, 0.75])):
        p.qdac.set_offset("flux_q0", v)
    _, plan = validate(p, caps)
    loop = next(n for n in p.body.walk() if type(n).__name__ == "Loop")
    assert plan[loop] == frozenset({"sw"})


# ---------------------------------------------------------------------------
# Predicate diagnostic — surface a clearer ``forced-software`` reason
# ---------------------------------------------------------------------------


def test_no_forced_software_info_when_consensus_alone_picks_sw():
    """With strict (sw-only) wiring of qdac, the loop is naturally SW from op-children consensus
    alone — no constraint is applied, and no ``forced-software`` info diagnostic surfaces.

    The predicate-driven ``forced-software`` info is reserved for cases where the block's
    available domain set was ``{hw}`` (or ``{hw, sw}``) and constraints reduced it to ``{sw}``.
    With sw-only ops, the available is already ``{sw}`` — there's no constraint to surface.
    """
    caps = _make_caps()
    p = QProgram()
    v = p.variable("flux")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        p.qdac.set_offset("flux_q0", v)
    diagnostics, _ = validate(p, caps)
    assert not any(d.code == "forced-software" for d in diagnostics)
