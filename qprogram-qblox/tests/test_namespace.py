"""Tests for QbloxNamespace — the typed methods that append to a QProgram body."""

from __future__ import annotations

from qprogram import MeasurementHandle, Variable
from qprogram.waveforms import IQDrag, Square
from qprogram_qblox import QProgram as QbloxQProgram
from qprogram_qblox.namespace import QbloxNamespace
from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


def test_acquire_returns_handle_and_appends_op(qblox_program):
    handle = qblox_program.qblox.acquire("readout", "weights")
    assert isinstance(handle, MeasurementHandle)
    assert len(qblox_program.body.elements) == 1
    assert isinstance(qblox_program.body.elements[0], Acquire)


def test_acquire_uses_per_qubit_naming(qblox_program, transmon_schema):
    h0 = qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights")
    h1 = qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights")
    assert h0.name == "q0_m0"
    assert h1.name == "q0_m1"


def test_acquire_with_explicit_name(qblox_program):
    h = qblox_program.qblox.acquire("readout", "weights", name="custom")
    assert h.name == "custom"


def test_acquire_with_returns_csv(qblox_program):
    qblox_program.qblox.acquire("readout", "weights", returns="iq,raw")
    op = qblox_program.body.elements[0]
    assert op.returns == ("iq", "raw")


def test_acquire_with_returns_iterable(qblox_program):
    qblox_program.qblox.acquire("readout", "weights", returns=("raw",))
    op = qblox_program.body.elements[0]
    assert op.returns == ("raw",)


def test_acquire_shares_counter_with_core_measure(qblox_program, transmon_schema):
    """measure + acquire on the same qubit share the m0, m1, ... counter."""
    h0 = qblox_program.measure(transmon_schema.q[0].readout, "wf", "weights")
    h1 = qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights")
    assert h0.name == "q0_m0"
    assert h1.name == "q0_m1"


# ---------------------------------------------------------------------------
# set_markers
# ---------------------------------------------------------------------------


def test_set_markers_appends_op(qblox_program):
    qblox_program.qblox.set_markers("drive", "0001")
    op = qblox_program.body.elements[0]
    assert isinstance(op, SetMarkers)
    assert op.bus == "drive"
    assert op.mask == "0001"


def test_set_markers_returns_none(qblox_program):
    assert qblox_program.qblox.set_markers("drive", "0001") is None


# ---------------------------------------------------------------------------
# set_trigger
# ---------------------------------------------------------------------------


def test_set_trigger_defaults(qblox_program):
    qblox_program.qblox.set_trigger("drive", duration=100)
    op = qblox_program.body.elements[0]
    assert isinstance(op, SetTrigger)
    assert op.duration == 100
    assert op.outputs is None
    assert op.position == "start"


def test_set_trigger_with_all_kwargs(qblox_program):
    qblox_program.qblox.set_trigger("drive", duration=50, outputs=[1, 2], position="end")
    op = qblox_program.body.elements[0]
    assert op.outputs == [1, 2]
    assert op.position == "end"


# ---------------------------------------------------------------------------
# wait_trigger
# ---------------------------------------------------------------------------


def test_wait_trigger_appends_op(qblox_program):
    qblox_program.qblox.wait_trigger("drive", duration=1000, port=2)
    op = qblox_program.body.elements[0]
    assert isinstance(op, WaitTrigger)
    assert op.duration == 1000
    assert op.port == 2


def test_wait_trigger_default_port(qblox_program):
    qblox_program.qblox.wait_trigger("drive", duration=500)
    op = qblox_program.body.elements[0]
    assert op.port is None


# ---------------------------------------------------------------------------
# active_reset
# ---------------------------------------------------------------------------


def test_active_reset_appends_op(qblox_program):
    qblox_program.qblox.active_reset(
        bus="readout",
        waveform="rwf",
        weights="w",
        control_bus="drive",
        reset_pulse="pi",
    )
    op = qblox_program.body.elements[0]
    assert isinstance(op, ActiveReset)
    assert op.trigger_address == 1


def test_active_reset_with_inline_waveforms(qblox_program):
    qblox_program.qblox.active_reset(
        bus="readout",
        waveform=Square(1.0, 100),
        weights=Square(1.0, 100),
        control_bus="drive",
        reset_pulse=IQDrag(0.5, 40, 2.5, 0.1),
        trigger_address=3,
    )
    op = qblox_program.body.elements[0]
    assert op.trigger_address == 3


# ---------------------------------------------------------------------------
# set_acquisition_threshold
# ---------------------------------------------------------------------------


def test_set_acquisition_threshold_with_float(qblox_program):
    qblox_program.qblox.set_acquisition_threshold("readout", 0.42)
    op = qblox_program.body.elements[0]
    assert isinstance(op, SetAcquisitionThreshold)
    assert op.value == 0.42


def test_set_acquisition_threshold_with_expression(qblox_program):
    v = Variable("threshold")
    qblox_program.qblox.set_acquisition_threshold("readout", v)
    op = qblox_program.body.elements[0]
    assert op.value is v


# ---------------------------------------------------------------------------
# Bus validation in namespace
# ---------------------------------------------------------------------------


def test_acquire_accepts_schema_readout_bus(qblox_program, transmon_schema):
    """A schema readout bus has acquires=True and is accepted."""
    h = qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights")
    assert isinstance(h, MeasurementHandle)


def test_acquire_accepts_plain_string_bus(qblox_program):
    """Plain string buses opt out of validation."""
    h = qblox_program.qblox.acquire("any_bus", "weights")
    assert isinstance(h, MeasurementHandle)


# ---------------------------------------------------------------------------
# Namespace identity
# ---------------------------------------------------------------------------


def test_namespace_is_subclass_of_vendor_namespace():
    from qprogram.vendor import VendorNamespace

    assert issubclass(QbloxNamespace, VendorNamespace)


def test_namespace_holds_program_reference(qblox_program):
    ns = qblox_program.qblox
    assert ns._program is qblox_program  # type: ignore[attr-defined]


def test_namespace_cached_per_instance(qblox_program):
    """Same namespace instance returned on repeated access."""
    assert qblox_program.qblox is qblox_program.qblox


def test_namespace_distinct_per_program():
    p1 = QbloxQProgram()
    p2 = QbloxQProgram()
    assert p1.qblox is not p2.qblox
