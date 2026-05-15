"""Tests for the qblox Operation classes (data nodes in the AST)."""

from __future__ import annotations

import pytest

from qprogram import Variable
from qprogram.waveforms import IQDrag, IQPair, Square
from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)


# ---------------------------------------------------------------------------
# Acquire
# ---------------------------------------------------------------------------


def test_acquire_construct_defaults():
    op = Acquire("readout", "weights", name="q0_m0")
    assert op.bus == "readout"
    assert op.weights == "weights"
    assert op.name == "q0_m0"
    assert op.returns == ("iq",)


def test_acquire_returns_normalized_from_iterable():
    op = Acquire("readout", "weights", name="m0", returns=["iq", "raw"])
    assert op.returns == ("iq", "raw")


def test_acquire_returns_normalized_from_csv_string():
    op = Acquire("readout", "weights", name="m0", returns="iq,raw")
    assert op.returns == ("iq", "raw")


def test_acquire_introspection():
    op = Acquire("bus", "weights", name="m0")
    assert list(op.buses()) == ["bus"]
    assert list(op.waveforms()) == ["weights"]
    assert list(op.variables()) == []


def test_acquire_with_inline_waveform():
    wf = IQPair(Square(1.0, 100), Square(1.0, 100))
    op = Acquire("bus", wf, name="m0")
    assert op.weights is wf
    assert list(op.waveforms()) == [wf]


def test_acquire_structural_equality():
    a = Acquire("bus", "w", name="m0", returns=("iq",))
    b = Acquire("bus", "w", name="m0", returns=("iq",))
    assert a == b
    assert hash(a) == hash(b)


def test_acquire_distinct_when_different():
    a = Acquire("bus", "w", name="m0")
    b = Acquire("bus", "w", name="m1")
    assert a != b


# ---------------------------------------------------------------------------
# SetMarkers
# ---------------------------------------------------------------------------


def test_set_markers_construct():
    op = SetMarkers("drive", "0001")
    assert op.bus == "drive"
    assert op.mask == "0001"


@pytest.mark.parametrize("mask", ["0000", "1111", "1010", "0001"])
def test_set_markers_various_masks(mask):
    op = SetMarkers("bus", mask)
    assert op.mask == mask


def test_set_markers_introspection():
    op = SetMarkers("bus", "0001")
    assert list(op.buses()) == ["bus"]
    assert list(op.waveforms()) == []
    assert list(op.variables()) == []


def test_set_markers_equality():
    assert SetMarkers("bus", "0001") == SetMarkers("bus", "0001")
    assert SetMarkers("bus", "0001") != SetMarkers("bus", "0010")


# ---------------------------------------------------------------------------
# SetTrigger
# ---------------------------------------------------------------------------


def test_set_trigger_defaults():
    op = SetTrigger("bus", duration=100)
    assert op.bus == "bus"
    assert op.duration == 100
    assert op.outputs is None
    assert op.position == "start"


def test_set_trigger_with_outputs_list():
    op = SetTrigger("bus", duration=50, outputs=[1, 2, 3])
    assert op.outputs == [1, 2, 3]


def test_set_trigger_with_outputs_int():
    op = SetTrigger("bus", duration=50, outputs=2)
    assert op.outputs == 2


def test_set_trigger_with_position_end():
    op = SetTrigger("bus", duration=50, position="end")
    assert op.position == "end"


def test_set_trigger_equality():
    a = SetTrigger("bus", 100, outputs=[1, 2], position="end")
    b = SetTrigger("bus", 100, outputs=[1, 2], position="end")
    assert a == b


# ---------------------------------------------------------------------------
# WaitTrigger
# ---------------------------------------------------------------------------


def test_wait_trigger_defaults():
    op = WaitTrigger("bus", duration=1000)
    assert op.bus == "bus"
    assert op.duration == 1000
    assert op.port is None


def test_wait_trigger_with_port():
    op = WaitTrigger("bus", duration=500, port=3)
    assert op.port == 3


# ---------------------------------------------------------------------------
# ActiveReset
# ---------------------------------------------------------------------------


def test_active_reset_construct():
    op = ActiveReset(
        bus="readout",
        waveform="rwf",
        weights="weights",
        control_bus="drive",
        reset_pulse="pi_pulse",
        trigger_address=2,
    )
    assert op.bus == "readout"
    assert op.waveform == "rwf"
    assert op.weights == "weights"
    assert op.control_bus == "drive"
    assert op.reset_pulse == "pi_pulse"
    assert op.trigger_address == 2


def test_active_reset_default_trigger_address():
    op = ActiveReset("r", "wf", "w", "d", "pi")
    assert op.trigger_address == 1


def test_active_reset_two_buses_three_waveforms_introspection():
    op = ActiveReset("readout", "rwf", "weights", "drive", "pi_pulse")
    assert set(op.buses()) == {"readout", "drive"}
    assert set(op.waveforms()) == {"rwf", "weights", "pi_pulse"}


def test_active_reset_with_inline_waveforms():
    rwf = IQDrag(0.5, 40, 2.5, 0.1)
    weights = IQPair(Square(1.0, 100), Square(1.0, 100))
    pi = IQDrag(0.3, 20, 2.0, 0.05)  # distinct shape so it isn't deduped with rwf
    op = ActiveReset("r", rwf, weights, "d", pi)
    seen = list(op.waveforms())
    assert rwf in seen and weights in seen and pi in seen
    assert len(seen) == 3


def test_active_reset_equality():
    a = ActiveReset("r", "wf", "w", "d", "pi")
    b = ActiveReset("r", "wf", "w", "d", "pi")
    assert a == b
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# SetAcquisitionThreshold
# ---------------------------------------------------------------------------


def test_set_acquisition_threshold_float():
    op = SetAcquisitionThreshold("readout", value=0.42)
    assert op.bus == "readout"
    assert op.value == 0.42


def test_set_acquisition_threshold_with_expression():
    v = Variable("threshold")
    op = SetAcquisitionThreshold("readout", value=v)
    assert op.value is v
    assert list(op.variables()) == [v]


def test_set_acquisition_threshold_no_waveforms():
    op = SetAcquisitionThreshold("readout", 0.5)
    assert list(op.buses()) == ["readout"]
    assert list(op.waveforms()) == []
