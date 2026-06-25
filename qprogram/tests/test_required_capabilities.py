"""Tests for per-node :meth:`required_capabilities` declarations.

Each Operation/Block subclass returns the capability tokens *it* needs in
isolation, instance-aware. The validator walks the AST and unions per-node
sets; correctness here is what makes the validator correct downstream.
"""

from __future__ import annotations

import numpy as np

from qprogram import MeasurementHandle, QProgram
from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
from qprogram.operations.set_frequency import SetFrequency
from qprogram.operations.set_gain import SetGain
from qprogram.operations.set_offset import SetOffset
from qprogram.operations.set_parameter import SetParameter
from qprogram.operations.set_phase import SetPhase
from qprogram.operations.sync import Sync
from qprogram.operations.wait import Wait
from qprogram.variable import Variable
from qprogram.waveforms import IQDrag, IQPair, Square

# ---------------------------------------------------------------------------
# Play — waveform-sensitive
# ---------------------------------------------------------------------------


def test_play_with_string_alias_includes_alias_token() -> None:
    caps = Play(bus="drive_q0", waveform="pi_pulse").required_capabilities()
    assert caps == {"op.play", "waveform.alias"}


def test_play_with_single_channel_waveform() -> None:
    caps = Play(bus="drive_q0", waveform=Square(amplitude=0.5, duration=100)).required_capabilities()
    assert caps == {"op.play", "waveform.single", "waveform.square"}


def test_play_with_iq_waveform() -> None:
    wf = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
    caps = Play(bus="drive_q0", waveform=wf).required_capabilities()
    assert caps == {"op.play", "waveform.iq", "waveform.iq_drag"}


def test_play_with_iq_pair_includes_iq_pair_token() -> None:
    wf = IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))
    caps = Play(bus="drive_q0", waveform=wf).required_capabilities()
    assert caps == {"op.play", "waveform.iq", "waveform.iq_pair"}


# ---------------------------------------------------------------------------
# Measure — gathers tokens from waveform + weights + returns
# ---------------------------------------------------------------------------


def test_measure_with_concrete_waveform_and_weights() -> None:
    wf = IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))
    weights = IQPair(I=Square(1.0, 100), Q=Square(1.0, 100))
    m = Measure(bus="readout_q0", waveform=wf, weights=weights, handle=MeasurementHandle("q0_m0"))
    caps = m.required_capabilities()
    assert "op.measure" in caps
    assert "waveform.iq" in caps
    assert "waveform.iq_pair" in caps
    assert "measure.returns.iq" in caps


def test_measure_with_string_aliases_includes_alias_token_once() -> None:
    m = Measure(bus="readout_q0", waveform="readout", weights="weights", handle=MeasurementHandle("q0_m0"))
    caps = m.required_capabilities()
    assert "waveform.alias" in caps  # both aliases collapse into one token


def test_measure_with_raw_return_token() -> None:
    wf = IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))
    m = Measure(bus="readout_q0", waveform=wf, weights=wf, handle=MeasurementHandle("q0_m0"), returns="iq,raw")
    caps = m.required_capabilities()
    assert "measure.returns.iq" in caps
    assert "measure.returns.raw" in caps


# ---------------------------------------------------------------------------
# Wait — expression-sensitive for ``duration``
# ---------------------------------------------------------------------------


def test_wait_with_constant_duration() -> None:
    assert Wait(bus="drive_q0", duration=100).required_capabilities() == {"op.wait"}


def test_wait_with_variable_duration_adds_expr_variable() -> None:
    v = Variable("d")
    caps = Wait(bus="drive_q0", duration=v).required_capabilities()
    assert caps == {"op.wait", "expr.variable"}


def test_wait_with_expression_duration_adds_expr_tokens() -> None:
    v = Variable("d")
    caps = Wait(bus="drive_q0", duration=v * 2 + 10).required_capabilities()
    assert caps == {"op.wait", "expr.binary_op", "expr.variable", "expr.constant"}


# ---------------------------------------------------------------------------
# Other simple ops
# ---------------------------------------------------------------------------


def test_sync_token() -> None:
    assert Sync().required_capabilities() == {"op.sync"}


def test_reset_phase_token() -> None:
    assert ResetPhase(bus="drive_q0").required_capabilities() == {"op.reset_phase"}


def test_set_frequency_picks_up_expr_tokens() -> None:
    v = Variable("f")
    assert SetFrequency(bus="drive_q0", frequency=v).required_capabilities() == {
        "op.set_frequency",
        "expr.variable",
    }


def test_set_phase_picks_up_expr_tokens() -> None:
    assert SetPhase(bus="drive_q0", phase=0.5).required_capabilities() == {"op.set_phase"}


def test_set_gain_picks_up_expr_tokens() -> None:
    v = Variable("g")
    assert SetGain(bus="drive_q0", gain=v).required_capabilities() == {
        "op.set_gain",
        "expr.variable",
    }


def test_set_offset_picks_up_both_paths() -> None:
    v = Variable("o")
    caps = SetOffset(bus="drive_q0", offset_path0=v, offset_path1=0.0).required_capabilities()
    assert caps == {"op.set_offset", "expr.variable"}


def test_set_parameter_picks_up_value_expr() -> None:
    v = Variable("p")
    assert SetParameter(alias="dev", parameter="x", value=v).required_capabilities() == {
        "op.set_parameter",
        "expr.variable",
    }


def test_get_parameter_token() -> None:
    v = Variable("x")
    assert GetParameter(variable=v, alias="dev", parameter="y").required_capabilities() == {
        "op.get_parameter",
    }


# ---------------------------------------------------------------------------
# Block subclasses
# ---------------------------------------------------------------------------


def test_base_block_token() -> None:
    assert Block().required_capabilities() == {"block.block"}


def test_average_token() -> None:
    assert Average(shots=10).required_capabilities() == {"block.average"}


def test_for_loop_emits_linear_sweep() -> None:
    v = Variable("x")
    assert ForLoop(variable=v, start=0, stop=1, step=0.1).required_capabilities() == {
        "block.for_loop",
        "sweep.linear",
    }


def test_loop_emits_arbitrary_sweep() -> None:
    v = Variable("x")
    assert Loop(variable=v, values=np.array([0.0, 0.5, 1.0])).required_capabilities() == {
        "block.loop",
        "sweep.arbitrary",
    }


def test_parallel_emits_block_parallel_only() -> None:
    """Parallel's own token is just ``block.parallel``; the composed loops
    contribute their own caps when the walker visits them separately."""
    v1 = Variable("a")
    v2 = Variable("b")
    p = Parallel(
        loops=[
            ForLoop(variable=v1, start=0, stop=1, step=1),
            Loop(variable=v2, values=np.array([0.0, 1.0])),
        ],
    )
    assert p.required_capabilities() == {"block.parallel"}


# ---------------------------------------------------------------------------
# Whole-program walk: union of per-node sets covers everything used
# ---------------------------------------------------------------------------


def test_walk_then_union_covers_realistic_program() -> None:
    p = QProgram()
    freq = p.variable("freq")
    with p.average(1000), p.for_loop(freq, 5e9, 6e9, 1e6):
        p.set_frequency("drive_q0", freq)
        p.play("drive_q0", Square(0.5, 100))
        p.measure(
            "readout_q0",
            IQPair(I=Square(0.5, 100), Q=Square(0.0, 100)),
            IQPair(I=Square(1.0, 100), Q=Square(1.0, 100)),
        )
    all_caps: set[str] = set()
    for node in p.body.walk():
        if node is p.body:
            continue
        all_caps |= node.required_capabilities()
    expected_subset = {
        "block.average",
        "block.for_loop",
        "sweep.linear",
        "op.set_frequency",
        "op.play",
        "op.measure",
        "expr.variable",
        "waveform.single",
        "waveform.square",
        "waveform.iq",
        "waveform.iq_pair",
        "measure.returns.iq",
    }
    assert expected_subset.issubset(all_caps), all_caps
