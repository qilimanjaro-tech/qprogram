"""Tests for the MyPlatform vendor operations (SetCrosstalk, SetRFSwitch)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from qprogram import QProgram
from qprogram.errors import ValidationError

from my_platform.operations import SetCrosstalk, SetRFSwitch


def test_set_crosstalk_coerces_matrix_to_ndarray():
    op = SetCrosstalk(bus="q0/flux", matrix=[[1.0, 0.1], [0.1, 1.0]])
    assert isinstance(op.matrix, np.ndarray)
    assert op.matrix.dtype == float
    assert op.matrix.shape == (2, 2)


def test_set_crosstalk_accepts_ndarray():
    arr = np.eye(3)
    op = SetCrosstalk(bus="q0/flux", matrix=arr)
    assert np.array_equal(op.matrix, arr)


def test_set_crosstalk_rejects_1d_matrix():
    with pytest.raises(ValidationError):
        SetCrosstalk(bus="q0/flux", matrix=[1.0, 0.1, 0.1, 1.0])


def test_set_crosstalk_rejects_3d_matrix():
    with pytest.raises(ValidationError):
        SetCrosstalk(bus="q0/flux", matrix=np.zeros((2, 2, 2)))


def test_set_crosstalk_required_capabilities():
    op = SetCrosstalk(bus="q0/flux", matrix=[[1.0]])
    assert op.required_capabilities() == {"vendor.myplatform.set_crosstalk"}


def test_set_crosstalk_structural_equality_and_hash():
    a = SetCrosstalk(bus="q0/flux", matrix=[[1.0, 0.1], [0.1, 1.0]])
    b = SetCrosstalk(bus="q0/flux", matrix=np.array([[1.0, 0.1], [0.1, 1.0]]))
    assert a == b
    assert hash(a) == hash(b)
    c = SetCrosstalk(bus="q0/flux", matrix=[[1.0, 0.2], [0.2, 1.0]])
    assert a != c


def test_set_crosstalk_deepcopy_equal():
    a = SetCrosstalk(bus="q0/flux", matrix=[[1.0, 0.1], [0.1, 1.0]])
    assert copy.deepcopy(a) == a


def test_set_crosstalk_bus_attrs_default():
    assert SetCrosstalk.BUS_ATTRS == ("bus",)
    op = SetCrosstalk(bus="q0/flux", matrix=[[1.0]])
    assert op.buses() == {"q0/flux"}


def test_set_rf_switch_literal_channel():
    op = SetRFSwitch(bus="switch0/rf", channel=2)
    assert op.channel == 2
    assert op.required_capabilities() == {"vendor.myplatform.set_rf_switch"}
    assert op.variables() == set()


def test_set_rf_switch_swept_channel_contributes_expr_token():
    prog = QProgram(label="x")
    ch = prog.variable("ch")
    op = SetRFSwitch(bus="switch0/rf", channel=ch)
    caps = op.required_capabilities()
    assert "vendor.myplatform.set_rf_switch" in caps
    assert "expr.variable" in caps
    assert op.variables() == {ch}


def test_set_rf_switch_bus_attrs_default():
    op = SetRFSwitch(bus="switch0/rf", channel=1)
    assert op.buses() == {"switch0/rf"}
