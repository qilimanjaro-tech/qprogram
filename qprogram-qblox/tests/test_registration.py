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


def test_qblox_declares_vendor_entry_point():
    """Step 4 (discovery): the package exposes a `qprogram.vendors` entry point named `qblox`
    pointing at the self-registering module, so `qprogram.loads(...)` can auto-activate it."""
    import importlib.metadata as md  # noqa: PLC0415

    eps = {ep.name: ep.value for ep in md.entry_points(group="qprogram.vendors")}
    assert eps.get("qblox") == "qprogram_qblox"


def test_qblox_entry_point_loads_and_registers():
    """Loading the entry point imports the self-registering module and registers the version."""
    import importlib.metadata as md  # noqa: PLC0415

    (ep,) = [e for e in md.entry_points(group="qprogram.vendors") if e.name == "qblox"]
    mod = ep.load()
    assert mod.__name__ == "qprogram_qblox"
    assert get_vendor_version("qblox") is not None


def test_qblox_autoactivates_in_fresh_process(tmp_path):
    """End-to-end self-containment: a process that imports ONLY `qprogram` loads a file whose
    `require qblox` line auto-imports the installed extension via its entry point — no explicit
    `import qprogram_qblox` anywhere."""
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    qp_file = tmp_path / "prog.qp"
    qp_file.write_text('#!QProgram 1.0\n\nrequire qblox 0.1\n\nbody:\n  qblox.set_markers "d" "0001"\n')
    script = tmp_path / "run.py"
    script.write_text(
        "import sys, qprogram as qp\n"
        "assert 'qprogram_qblox' not in sys.modules, 'qblox was pre-imported'\n"
        f"p = qp.load({str(qp_file)!r})\n"
        "assert 'qprogram_qblox' in sys.modules, 'loads() did not auto-activate qblox'\n"
        "from qprogram_qblox.operations import SetMarkers\n"
        "assert isinstance(p.body.elements[0], SetMarkers)\n"
        "print('AUTO_OK')\n",
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "AUTO_OK" in result.stdout
