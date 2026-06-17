"""Tests that importing my_platform registers the vendor, version, ops, tokens, and profiles."""

from __future__ import annotations

import my_platform  # noqa: F401 — registration side effects under test
from qprogram.protocol import CAPABILITY_REGISTRY, PROFILE_REGISTRY, resolve_profile
from qprogram.serialization.registry import (
    get_operation_spec,
    get_vendor_version,
)

from my_platform.operations import SetCrosstalk, SetRFSwitch, set_crosstalk_serialize


def test_vendor_version_registered():
    assert get_vendor_version("myplatform") == my_platform.__version__


def test_operations_registered():
    xtalk = get_operation_spec("myplatform", "set_crosstalk")
    rf = get_operation_spec("myplatform", "set_rf_switch")
    assert xtalk is not None and xtalk.cls is SetCrosstalk
    assert rf is not None and rf.cls is SetRFSwitch


def test_set_crosstalk_uses_custom_serializer():
    spec = get_operation_spec("myplatform", "set_crosstalk")
    assert spec is not None
    assert spec.serialize is set_crosstalk_serialize
    # set_rf_switch uses the default signature-driven serializer (no override).
    rf = get_operation_spec("myplatform", "set_rf_switch")
    assert rf is not None
    assert rf.serialize is None


def test_capability_tokens_registered():
    assert "vendor.myplatform.set_crosstalk" in CAPABILITY_REGISTRY
    assert "vendor.myplatform.set_rf_switch" in CAPABILITY_REGISTRY


def test_profiles_registered():
    for name in ("myplatform-readout-v1", "myplatform-flux-v1", "myplatform-rfswitch-v1"):
        assert name in PROFILE_REGISTRY


def test_flux_profile_publishes_set_crosstalk():
    from qprogram.protocol import CompilerCapabilities

    flux = CompilerCapabilities.from_profile("myplatform-flux-v1")
    assert flux.supports("vendor.myplatform.set_crosstalk")
    # …and it still carries the inherited qdac ops.
    assert flux.supports("vendor.qdac.set_offset")


def test_rfswitch_profile_publishes_set_rf_switch_plus_timing():
    profile = resolve_profile("myplatform-rfswitch-v1")
    # Its own vendor op plus the core timing ops needed to align the switch with the pulse program.
    assert profile.capabilities == frozenset({"vendor.myplatform.set_rf_switch", "op.sync", "op.wait"})
    assert profile.extends is None
