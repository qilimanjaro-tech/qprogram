"""Tests for QdacNamespace — the typed methods that append to a QProgram body."""

from __future__ import annotations

from qprogram import Variable
from qprogram.waveforms import Ramp, Square
from qprogram_qdac import QProgram as QdacQProgram
from qprogram_qdac.namespace import QdacNamespace
from qprogram_qdac.operations import Play, SetOffset, SetTrigger, WaitTrigger


# ---------------------------------------------------------------------------
# wait_trigger
# ---------------------------------------------------------------------------


def test_wait_trigger_appends_op(qdac_program):
    qdac_program.qdac.wait_trigger("flux_q0", port=3)
    op = qdac_program.body.elements[0]
    assert isinstance(op, WaitTrigger)
    assert op.bus == "flux_q0"
    assert op.port == 3


def test_wait_trigger_returns_none(qdac_program):
    assert qdac_program.qdac.wait_trigger("flux_q0", port=1) is None


# ---------------------------------------------------------------------------
# set_trigger
# ---------------------------------------------------------------------------


def test_set_trigger_defaults(qdac_program):
    qdac_program.qdac.set_trigger("flux_q0", duration=50)
    op = qdac_program.body.elements[0]
    assert isinstance(op, SetTrigger)
    assert op.duration == 50
    assert op.position == "start"
    assert op.outputs == ()


def test_set_trigger_with_set_outputs(qdac_program):
    qdac_program.qdac.set_trigger("flux_q0", 50, outputs={3, 1, 2})
    op = qdac_program.body.elements[0]
    assert op.outputs == (1, 2, 3)


def test_set_trigger_with_list_outputs(qdac_program):
    qdac_program.qdac.set_trigger("flux_q0", 50, outputs=[1, 2])
    op = qdac_program.body.elements[0]
    assert op.outputs == (1, 2)


def test_set_trigger_with_position_end_step(qdac_program):
    qdac_program.qdac.set_trigger("flux_q0", 50, position="end_step", outputs=[1])
    op = qdac_program.body.elements[0]
    assert op.position == "end_step"


# ---------------------------------------------------------------------------
# set_offset
# ---------------------------------------------------------------------------


def test_set_offset_with_float(qdac_program):
    qdac_program.qdac.set_offset("flux_q0", 0.42)
    op = qdac_program.body.elements[0]
    assert isinstance(op, SetOffset)
    assert op.offset == 0.42


def test_set_offset_with_variable(qdac_program):
    v = Variable("flux")
    qdac_program.qdac.set_offset("flux_q0", v)
    op = qdac_program.body.elements[0]
    assert op.offset is v


def test_set_offset_accepts_arithmetic_expression(qdac_program):
    """Expression compatibility: arithmetic on Variables flows through unchanged."""
    v = qdac_program.variable("scale")
    qdac_program.qdac.set_offset("flux_q0", v * 2 + 0.1)
    op = qdac_program.body.elements[0]
    # The stored offset is a BinaryOp containing v.
    assert v in op.variables()


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


def test_play_appends_op_default_kwargs(qdac_program):
    wf = Ramp(0.0, 1.0, 1000)
    qdac_program.qdac.play("flux_q0", wf)
    op = qdac_program.body.elements[0]
    assert isinstance(op, Play)
    assert op.bus == "flux_q0"
    assert op.waveform is wf
    assert op.dwell == 1
    assert op.delay == 0
    assert op.repetitions == 1
    assert op.stepped is False


def test_play_with_all_kwargs(qdac_program):
    wf = Square(0.5, 100)
    qdac_program.qdac.play("flux_q0", wf, dwell=10, delay=5, repetitions=3, stepped=True)
    op = qdac_program.body.elements[0]
    assert op.bus == "flux_q0"
    assert op.dwell == 10
    assert op.delay == 5
    assert op.repetitions == 3
    assert op.stepped is True


def test_play_returns_none(qdac_program):
    assert qdac_program.qdac.play("flux_q0", Ramp(0.0, 1.0, 100)) is None


# ---------------------------------------------------------------------------
# Namespace identity
# ---------------------------------------------------------------------------


def test_namespace_is_subclass_of_vendor_namespace():
    from qprogram.vendor import VendorNamespace

    assert issubclass(QdacNamespace, VendorNamespace)


def test_namespace_holds_program_reference(qdac_program):
    ns = qdac_program.qdac
    assert ns._program is qdac_program  # type: ignore[attr-defined]


def test_namespace_cached_per_instance(qdac_program):
    """Same namespace instance returned on repeated access."""
    assert qdac_program.qdac is qdac_program.qdac


def test_namespace_distinct_per_program():
    p1 = QdacQProgram()
    p2 = QdacQProgram()
    assert p1.qdac is not p2.qdac


# ---------------------------------------------------------------------------
# Integration with control flow
# ---------------------------------------------------------------------------


def test_qdac_ops_inside_for_loop(qdac_program):
    v = qdac_program.variable("scale")
    with qdac_program.for_loop(v, 0.0, 1.0, 0.1):
        qdac_program.qdac.set_offset("flux_q0", v)
        qdac_program.qdac.play("flux_q0", Ramp(0.0, 1.0, 100), dwell=10)
    # The for-loop wraps two qdac ops.
    loop = qdac_program.body.elements[0]
    assert len(loop.elements) == 2
    assert isinstance(loop.elements[0], SetOffset)
    assert isinstance(loop.elements[1], Play)
