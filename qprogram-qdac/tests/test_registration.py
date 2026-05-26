"""Tests for qprogram_qdac registration side-effects on import."""

from __future__ import annotations

import qprogram_qdac  # noqa: F401
from qprogram import QProgram as BaseQProgram
from qprogram.protocol import CAPABILITY_REGISTRY, PROFILE_REGISTRY
from qprogram.serialization.registry import get_operation_spec, get_vendor_version
from qprogram_qdac.namespace import QdacNamespace
from qprogram_qdac.operations import Play, SetOffset, SetTrigger, WaitTrigger


def test_vendor_namespace_registered():
    """Step 1: register_vendor."""
    assert "qdac" in BaseQProgram._vendor_registry  # type: ignore[attr-defined]
    assert BaseQProgram._vendor_registry["qdac"] is QdacNamespace  # type: ignore[attr-defined]


def test_vendor_version_registered():
    """Step 2: register_vendor_version."""
    ver = get_vendor_version("qdac")
    assert ver is not None
    assert ver.startswith("0.")


def test_all_operations_registered():
    """Step 3: register_vendor_operation for each op."""
    expected = {
        "wait_trigger": WaitTrigger,
        "set_trigger": SetTrigger,
        "set_offset": SetOffset,
        "play": Play,
    }
    for name, cls in expected.items():
        spec = get_operation_spec("qdac", name)
        assert spec is not None, f"qdac.{name} not registered"
        assert spec.cls is cls


def test_vendor_capability_tokens_registered():
    """Step 4: capability tokens are added to the registry before profile construction."""
    for tok in (
        "vendor.qdac.wait_trigger",
        "vendor.qdac.set_trigger",
        "vendor.qdac.set_offset",
        "vendor.qdac.play",
    ):
        assert tok in CAPABILITY_REGISTRY, f"{tok} not registered"


def test_profile_registered():
    """Step 5: the qdac profile ends up in PROFILE_REGISTRY."""
    assert "qdac-default-v1" in PROFILE_REGISTRY


def test_qprogram_qdac_version_string():
    assert isinstance(qprogram_qdac.__version__, str)
    assert qprogram_qdac.__version__.count(".") >= 1


def test_qdac_pre_combined_qprogram_exists():
    from qprogram_qdac import QProgram as QdacQProgram
    from qprogram_qdac.mixin import QdacMixin

    assert issubclass(QdacQProgram, QdacMixin)
    assert issubclass(QdacQProgram, BaseQProgram)
