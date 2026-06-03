"""Serialization tests for qblox vendor operations in .qp text format."""

from __future__ import annotations

import pytest

from qprogram import dumps, loads
from qprogram.waveforms import IQDrag, IQPair, Square
from qprogram_qblox import QProgram as QbloxQProgram
from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)


# ---------------------------------------------------------------------------
# Vendor-require header
# ---------------------------------------------------------------------------


def test_dumps_includes_require_qblox():
    p = QbloxQProgram()
    p.qblox.set_markers("drive", "0001")
    text = dumps(p)
    assert "require qblox" in text


def test_dumps_no_require_when_no_qblox_ops():
    p = QbloxQProgram()
    p.wait("bus", 100)
    text = dumps(p)
    assert "require qblox" not in text


# ---------------------------------------------------------------------------
# Per-operation round-trips
# ---------------------------------------------------------------------------


def test_acquire_round_trip(qblox_program, transmon_schema):
    qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights")
    text = dumps(qblox_program)
    assert "qblox.acquire" in text
    reloaded = loads(text)
    assert dumps(reloaded) == text


def test_acquire_with_returns_round_trip(qblox_program, transmon_schema):
    qblox_program.qblox.acquire(transmon_schema.q[0].readout, "weights", returns="iq,raw")
    text = dumps(qblox_program)
    assert 'returns="iq,raw"' in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, Acquire)
    assert op.returns == ("iq", "raw")


def test_set_markers_round_trip():
    p = QbloxQProgram()
    p.qblox.set_markers("drive", "0001")
    text = dumps(p)
    assert "qblox.set_markers" in text
    assert '"0001"' in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetMarkers)
    assert op.mask == "0001"


def test_set_trigger_round_trip():
    p = QbloxQProgram()
    p.qblox.set_trigger("drive", duration=100, outputs=3, position="end")
    text = dumps(p)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetTrigger)
    assert op.duration == 100
    assert op.outputs == 3
    assert op.position == "end"


def test_set_trigger_round_trip_minimal():
    p = QbloxQProgram()
    p.qblox.set_trigger("drive", duration=50)
    text = dumps(p)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert op.duration == 50


def test_wait_trigger_round_trip():
    p = QbloxQProgram()
    p.qblox.wait_trigger("drive", duration=1000, port=2)
    text = dumps(p)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, WaitTrigger)
    assert op.duration == 1000
    assert op.port == 2


def test_active_reset_round_trip():
    p = QbloxQProgram()
    p.qblox.active_reset(
        bus="readout",
        waveform="rwf",
        weights="w",
        control_bus="drive",
        reset_pulse="pi",
        trigger_address=3,
    )
    text = dumps(p)
    assert "qblox.active_reset" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, ActiveReset)
    assert op.trigger_address == 3


def test_set_acquisition_threshold_round_trip():
    p = QbloxQProgram()
    p.qblox.set_acquisition_threshold("readout", 0.42)
    text = dumps(p)
    assert "qblox.set_acquisition_threshold" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetAcquisitionThreshold)
    assert op.value == 0.42


def test_set_acquisition_threshold_expression_round_trip():
    p = QbloxQProgram()
    v = p.variable("threshold")
    p.qblox.set_acquisition_threshold("readout", v)
    text = dumps(p)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert op.value.id == v.id  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Inline waveforms inside vendor ops
# ---------------------------------------------------------------------------


def test_acquire_with_inline_iq_waveform_round_trip():
    p = QbloxQProgram()
    p.qblox.acquire("readout", IQPair(Square(1.0, 100), Square(1.0, 100)), name="m0")
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text


def test_active_reset_with_inline_waveforms_round_trip():
    p = QbloxQProgram()
    p.qblox.active_reset(
        bus="r",
        waveform=IQDrag(0.5, 40, 2.5, 0.1),
        weights=IQPair(Square(1.0, 100), Square(1.0, 100)),
        control_bus="d",
        reset_pulse=IQDrag(0.5, 40, 2.5, 0.1),
    )
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text


# ---------------------------------------------------------------------------
# Combined with core ops
# ---------------------------------------------------------------------------


def test_round_trip_qblox_and_core_ops(transmon_schema):
    p = QbloxQProgram(schema=transmon_schema)
    p.play(transmon_schema.q[0].drive, "wf")
    p.qblox.set_markers(transmon_schema.q[0].drive, "0001")
    p.qblox.acquire(transmon_schema.q[0].readout, "weights")
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text


# ---------------------------------------------------------------------------
# Vendor-compatibility check
# ---------------------------------------------------------------------------


def test_loads_with_matching_qblox_require_ok():
    p = QbloxQProgram()
    p.qblox.set_markers("drive", "0001")
    text = dumps(p)
    # roundtrip should succeed when versions match
    loads(text)


def test_loads_with_future_minor_rejected():
    """A .qp file requiring a higher minor of qblox should be rejected."""
    text = '#!QProgram 1.0\nrequire qblox 99.0\nbody:\n  qblox.set_markers "drive" "0001"\n'
    with pytest.raises(Exception, match=r"(?i)qblox"):
        loads(text)


def test_loads_with_wrong_major_rejected():
    text = '#!QProgram 1.0\nrequire qblox 999.0\nbody:\n  qblox.set_markers "drive" "0001"\n'
    with pytest.raises(Exception):
        loads(text)


# ---------------------------------------------------------------------------
# Byte-stability across a feature-rich program
# ---------------------------------------------------------------------------


def test_full_features_round_trip(transmon_schema):
    p = QbloxQProgram(label="big-qblox", schema=transmon_schema)
    v = p.variable("freq")
    with p.average(100):
        with p.for_loop(v, 4e9, 6e9, 1e6):
            p.set_frequency(transmon_schema.q[0].drive, v)
            p.qblox.set_markers(transmon_schema.q[0].drive, "0001")
            p.qblox.set_trigger(transmon_schema.q[0].drive, duration=10)
            p.play(transmon_schema.q[0].drive, IQDrag(0.5, 40, 2.5, 0.1))
            p.qblox.wait_trigger(transmon_schema.q[0].drive, duration=100, port=1)
            p.qblox.acquire(transmon_schema.q[0].readout, "weights", returns="iq,raw")
    p.qblox.set_acquisition_threshold(transmon_schema.q[0].readout, 0.5)

    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text
    assert reloaded.body == p.body


# ---------------------------------------------------------------------------
# Regression coverage for the round-trip integrity fixes
# ---------------------------------------------------------------------------


def test_set_trigger_with_list_outputs_round_trips():
    """`outputs=[1, 2]` must survive the tokenizer (bracket nesting) and reload as the list.

    Regression: the tokenizer used to split the literal at the space, reloading `outputs`
    as the string `'[1,'`.
    """
    p = QbloxQProgram()
    p.qblox.set_trigger("drive", duration=100, outputs=[1, 2])
    text = dumps(p)
    assert "outputs=[1, 2]" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetTrigger)
    assert op.outputs == [1, 2]
    assert reloaded.body == p.body


def test_vendor_op_inside_conditional_emits_require_and_survives():
    """A qblox op used only inside an if_ arm keeps its require line and round-trips.

    Regression: the vendor collector used to miss Conditional arm bodies, emitting no
    `require qblox` line; on an environment without qblox the op then vanished silently.
    """
    p = QbloxQProgram()
    h = p.measure("readout", "r", "w", returns="iq,state")
    with p.if_(h.state == 1):
        p.qblox.set_markers("drive", "0001")
    text = dumps(p)
    assert "require qblox" in text
    reloaded = loads(text)
    markers = [n for n in reloaded.body.walk() if isinstance(n, SetMarkers)]
    assert len(markers) == 1


def test_acquire_emits_name_kwarg():
    """Measurement names travel as `name="..."`, matching core measure's wire form."""
    p = QbloxQProgram()
    p.qblox.acquire("readout", "weights")
    text = dumps(p)
    assert 'qblox.acquire "readout" "weights" name="m0"' in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, Acquire)
    assert op.name == "m0"
