"""Serialization tests for qdac vendor operations in .qp text format."""

from __future__ import annotations

import pytest

from qprogram import dumps, loads
from qprogram.waveforms import Ramp, Square
from qprogram_qdac import QProgram as QdacQProgram
from qprogram_qdac.operations import Play, SetOffset, SetTrigger, WaitTrigger


# ---------------------------------------------------------------------------
# Vendor-require header
# ---------------------------------------------------------------------------


def test_dumps_includes_require_qdac():
    p = QdacQProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    text = dumps(p)
    assert "require qdac" in text


def test_dumps_no_require_when_no_qdac_ops():
    p = QdacQProgram()
    p.wait("flux_q0", 100)
    text = dumps(p)
    assert "require qdac" not in text


# ---------------------------------------------------------------------------
# Per-operation round-trips
# ---------------------------------------------------------------------------


def test_wait_trigger_round_trip():
    p = QdacQProgram()
    p.qdac.wait_trigger("flux_q0", port=3)
    text = dumps(p)
    assert "qdac.wait_trigger" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, WaitTrigger)
    assert op.bus == "flux_q0"
    assert op.port == 3


def test_set_trigger_minimal_round_trip():
    """No outputs, default position — outputs/position kwargs are omitted on emit."""
    p = QdacQProgram()
    p.qdac.set_trigger("flux_q0", duration=50)
    text = dumps(p)
    assert "qdac.set_trigger" in text
    assert "outputs" not in text   # empty defaults to ()
    assert "position" not in text  # default "start" suppressed
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetTrigger)
    assert op.outputs == ()


def test_set_trigger_with_outputs_round_trip():
    p = QdacQProgram()
    p.qdac.set_trigger("flux_q0", 50, outputs={3, 1, 2})
    text = dumps(p)
    assert "outputs=[1,2,3]" in text  # sorted, no spaces (tokenizer-friendly)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert op.outputs == (1, 2, 3)


@pytest.mark.parametrize("position", ["step", "end", "end_step"])
def test_set_trigger_non_default_position_round_trip(position):
    p = QdacQProgram()
    p.qdac.set_trigger("flux_q0", 50, position=position, outputs=[1])
    text = dumps(p)
    assert f'position="{position}"' in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert op.position == position


def test_set_offset_float_round_trip():
    p = QdacQProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    text = dumps(p)
    assert "qdac.set_offset" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, SetOffset)
    assert op.offset == 0.42


def test_set_offset_with_variable_round_trip():
    p = QdacQProgram()
    v = p.variable("flux")
    p.qdac.set_offset("flux_q0", v)
    text = dumps(p)
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert op.offset.id == v.id  # type: ignore[union-attr]


def test_play_round_trip():
    p = QdacQProgram()
    p.qdac.play("flux_q0", Ramp(0.0, 1.0, 1000), dwell=10, delay=5, repetitions=2, stepped=True)
    text = dumps(p)
    assert "qdac.play" in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, Play)
    assert op.bus == "flux_q0"
    assert op.dwell == 10
    assert op.delay == 5
    assert op.repetitions == 2
    assert op.stepped is True


def test_play_default_args_round_trip():
    """Default-valued args should be suppressed in the emit; the parse reconstructs the defaults."""
    p = QdacQProgram()
    p.qdac.play("flux_q0", Square(0.5, 100))
    text = dumps(p)
    # The signature-driven serialiser drops kwargs matching their default.
    assert "dwell" not in text
    assert "delay" not in text
    assert "repetitions" not in text
    assert "stepped" not in text
    reloaded = loads(text)
    op = reloaded.body.elements[0]
    assert isinstance(op, Play)
    assert op.bus == "flux_q0"
    assert op.dwell == 1
    assert op.delay == 0
    assert op.repetitions == 1
    assert op.stepped is False


# ---------------------------------------------------------------------------
# Combined with core ops
# ---------------------------------------------------------------------------


def test_round_trip_qdac_and_core_ops(flux_tunable_schema):
    p = QdacQProgram(schema=flux_tunable_schema)
    p.qdac.set_offset(flux_tunable_schema.q[0].flux, 0.5)
    p.wait(flux_tunable_schema.q[0].flux, 100)
    p.qdac.play(flux_tunable_schema.q[0].flux, Ramp(0.0, 1.0, 1000), dwell=10)
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text


# ---------------------------------------------------------------------------
# Vendor-compatibility check
# ---------------------------------------------------------------------------


def test_loads_with_matching_qdac_require_ok():
    p = QdacQProgram()
    p.qdac.set_offset("flux_q0", 0.42)
    text = dumps(p)
    loads(text)  # round-trip ok when versions match


def test_loads_with_future_minor_rejected():
    """A .qp file requiring a higher minor of qdac should be rejected."""
    text = '#!QProgram 1.0\nrequire qdac 99.0\nbody:\n  qdac.set_offset "flux" 0.5\n'
    with pytest.raises(Exception, match=r"(?i)qdac"):
        loads(text)


def test_loads_with_wrong_major_rejected():
    text = '#!QProgram 1.0\nrequire qdac 999.0\nbody:\n  qdac.set_offset "flux" 0.5\n'
    with pytest.raises(Exception):
        loads(text)


# ---------------------------------------------------------------------------
# Byte-stability across a feature-rich program
# ---------------------------------------------------------------------------


def test_full_features_round_trip(flux_tunable_schema):
    p = QdacQProgram(label="big-qdac", schema=flux_tunable_schema)
    v = p.variable("scale")
    with p.average(100):
        with p.for_loop(v, 0.0, 1.0, 0.1):
            p.qdac.set_offset(flux_tunable_schema.q[0].flux, v)
            p.qdac.set_trigger(
                flux_tunable_schema.q[0].flux, duration=20, position="step", outputs={1, 2},
            )
            p.qdac.play(flux_tunable_schema.q[0].flux, Ramp(0.0, 1.0, 500), dwell=10, stepped=True)
            p.qdac.wait_trigger(flux_tunable_schema.q[0].flux, port=1)

    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text
    assert reloaded.body == p.body
