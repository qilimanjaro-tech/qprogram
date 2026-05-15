"""Tests for qprogram_qblox registration side-effects on import."""

from __future__ import annotations

import qprogram_qblox  # noqa: F401
from qprogram import QProgram as BaseQProgram
from qprogram.serialization.registry import (
    get_operation_spec,
    get_vendor_version,
)
from qprogram_qblox.namespace import QbloxNamespace
from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)


def test_vendor_namespace_registered():
    """Step 1: register_vendor."""
    assert "qblox" in BaseQProgram._vendor_registry  # type: ignore[attr-defined]
    assert BaseQProgram._vendor_registry["qblox"] is QbloxNamespace  # type: ignore[attr-defined]


def test_vendor_version_registered():
    """Step 2: register_vendor_version."""
    ver = get_vendor_version("qblox")
    assert ver is not None
    assert ver.startswith("0.")


def test_all_operations_registered():
    """Step 3: register_vendor_operation for each op."""
    expected = {
        "acquire": Acquire,
        "set_markers": SetMarkers,
        "set_trigger": SetTrigger,
        "wait_trigger": WaitTrigger,
        "active_reset": ActiveReset,
        "set_acquisition_threshold": SetAcquisitionThreshold,
    }
    for name, cls in expected.items():
        spec = get_operation_spec("qblox", name)
        assert spec is not None, f"qblox.{name} not registered"
        assert spec.cls is cls


def test_qprogram_qblox_version_string():
    assert isinstance(qprogram_qblox.__version__, str)
    assert qprogram_qblox.__version__.count(".") >= 1


def test_qblox_pre_combined_qprogram_exists():
    from qprogram_qblox import QProgram as QbloxQProgram
    from qprogram_qblox.mixin import QbloxMixin

    assert issubclass(QbloxQProgram, QbloxMixin)
    assert issubclass(QbloxQProgram, BaseQProgram)
