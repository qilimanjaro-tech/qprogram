"""Tests for MyPlatformNamespace — the typed ``program.myplatform.*`` surface."""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import QProgram as BaseQProgram
from qprogram.buses import BusSchema
from qprogram.errors import ValidationError

from my_platform.namespace import MyPlatformNamespace
from my_platform.operations import SetCrosstalk, SetRFSwitch
from my_platform.schema import RFSwitchSchema


def test_dynamic_namespace_on_base_qprogram():
    """`.myplatform` works on a *base* QProgram via the dynamic registry (no mixin needed)."""
    prog = BaseQProgram()
    assert isinstance(prog.myplatform, MyPlatformNamespace)


def test_set_crosstalk_appends_op(schema: BusSchema):
    prog = BaseQProgram(schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])
    (op,) = list(prog.body.walk())[1:]  # body itself is first
    assert isinstance(op, SetCrosstalk)
    assert op.bus == "q0/flux"
    assert np.array_equal(op.matrix, [[1.0, 0.1], [0.1, 1.0]])


def test_set_rf_switch_appends_op(schema: BusSchema):
    prog = BaseQProgram(schema=schema)
    prog.myplatform.set_rf_switch(schema.switch[1].rf, 3)
    (op,) = list(prog.body.walk())[1:]
    assert isinstance(op, SetRFSwitch)
    assert op.bus == "switch1/rf"
    assert op.channel == 3


def test_namespace_validates_bus_schema():
    """A bus ref from a different schema must be rejected when appended."""
    schema_a = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    schema_b = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    prog = BaseQProgram(schema=schema_a)
    with pytest.raises(ValidationError):
        prog.myplatform.set_crosstalk(schema_b.q[0].flux, [[1.0]])
