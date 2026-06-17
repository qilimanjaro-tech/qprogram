"""Round-trip serialization tests for the MyPlatform vendor operations."""

from __future__ import annotations

import numpy as np

from qprogram import QProgram, dumps, loads
from qprogram.buses import BusSchema

from my_platform.operations import SetCrosstalk, SetRFSwitch


def _round_trip(prog: QProgram) -> QProgram:
    return loads(dumps(prog))


def test_set_crosstalk_round_trip(schema: BusSchema):
    prog = QProgram(label="xtalk", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.05, 1.0]])
    reloaded = _round_trip(prog)
    assert reloaded.body == prog.body
    (op,) = [n for n in reloaded.body.walk() if isinstance(n, SetCrosstalk)]
    assert isinstance(op.matrix, np.ndarray)
    assert op.matrix.shape == (2, 2)


def test_set_crosstalk_wire_form(schema: BusSchema):
    prog = QProgram(label="xtalk", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])
    text = dumps(prog)
    assert "myplatform.set_crosstalk q[0].flux matrix=[[1.0, 0.1], [0.1, 1.0]]" in text
    assert "require myplatform 0.1" in text


def test_set_rf_switch_literal_round_trip(schema: BusSchema):
    prog = QProgram(label="sw", schema=schema)
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 2)
    reloaded = _round_trip(prog)
    assert reloaded.body == prog.body
    text = dumps(prog)
    assert "myplatform.set_rf_switch switch[0].rf 2" in text


def test_set_rf_switch_swept_round_trip(schema: BusSchema):
    prog = QProgram(label="sw_sweep", schema=schema)
    ch = prog.variable("ch")
    with prog.for_loop(ch, 0, 4, 1):
        prog.myplatform.set_rf_switch(schema.switch[0].rf, ch)
    reloaded = _round_trip(prog)
    assert reloaded.body == prog.body
    # The swept variable survives as a reference, not a literal.
    (op,) = [n for n in reloaded.body.walk() if isinstance(n, SetRFSwitch)]
    assert op.variables() == {next(iter(op.variables()))}


def test_raw_string_bus_round_trip():
    """Vendor ops work on raw-string buses too (no schema)."""
    prog = QProgram(label="raw")
    prog.myplatform.set_rf_switch("switch0/rf", 1)
    prog.myplatform.set_crosstalk("q0/flux", [[1.0]])
    reloaded = _round_trip(prog)
    assert reloaded.body == prog.body


def test_both_ops_in_one_program_emit_single_require(schema: BusSchema):
    prog = QProgram(label="both", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0]])
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 0)
    text = dumps(prog)
    assert text.count("require myplatform") == 1
