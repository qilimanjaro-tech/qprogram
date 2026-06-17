"""Entry-point auto-activation: loading a `.qp` that requires `myplatform` imports the package.

Because the rest of the suite imports `my_platform` (via conftest), the vendor is already
registered in-process and the entry-point path never runs. This test therefore spawns a *fresh*
interpreter that only imports `qprogram`, and asserts that `qprogram.loads()` discovers and imports
`my_platform` on demand through its `qprogram.vendors` entry point.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

_QP_FILE = """#!QProgram 1.0

require myplatform 0.1

metadata:
  label: "auto"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires
    flux info=single
  element switch:
    rf info=single

body:
  myplatform.set_crosstalk q[0].flux matrix=[[1.0, 0.1], [0.1, 1.0]]
  myplatform.set_rf_switch switch[0].rf 2
"""


def test_loads_auto_activates_myplatform_in_fresh_interpreter():
    script = textwrap.dedent(
        f"""
        import sys
        assert "my_platform" not in sys.modules, "my_platform must not be pre-imported"
        from qprogram import loads
        prog = loads({_QP_FILE!r})
        assert "my_platform" in sys.modules, "loads() should have auto-imported my_platform"
        ops = [type(n).__name__ for n in prog.body.walk() if type(n).__name__ != "Block"]
        assert ops == ["SetCrosstalk", "SetRFSwitch"], ops
        print("OK")
        """,
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout
