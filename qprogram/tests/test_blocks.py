"""Tests for Block / ForLoop / Loop / Average / Parallel."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from qprogram import ValidationError, Variable
from qprogram.blocks import Average, Block, ForLoop, Loop, Parallel
from qprogram.operations import Play, Wait
from qprogram.waveforms import Square

# ---------------------------------------------------------------------------
# Block — base container
# ---------------------------------------------------------------------------


def test_block_starts_empty():
    b = Block()
    assert b.elements == []


def test_block_append():
    b = Block()
    op = Wait("bus", 100)
    b.append(op)
    assert b.elements == [op]


def test_block_append_multiple():
    b = Block()
    ops = [Wait("bus", i) for i in (1, 2, 3)]
    for op in ops:
        b.append(op)
    assert b.elements == ops


def test_block_walk_empty():
    b = Block()
    assert list(b.walk()) == [b]


def test_block_walk_with_children():
    b = Block()
    op1 = Wait("bus", 100)
    op2 = Play("bus", Square(0.5, 100))
    b.append(op1)
    b.append(op2)
    walked = list(b.walk())
    assert walked == [b, op1, op2]


def test_block_walk_nested():
    outer = Block()
    inner = Block()
    op = Wait("bus", 100)
    inner.append(op)
    outer.append(inner)
    walked = list(outer.walk())
    assert walked == [outer, inner, op]


def test_block_variables_empty():
    b = Block()
    assert b.variables() == set()


def test_block_variables_union_over_children():
    v = Variable("x")
    b = Block()
    b.append(Wait("bus", v))
    assert b.variables() == {v}


def test_block_buses_empty():
    b = Block()
    assert b.buses() == set()


def test_block_buses_union():
    b = Block()
    b.append(Wait("bus1", 100))
    b.append(Wait("bus2", 100))
    assert b.buses() == {"bus1", "bus2"}


def test_block_waveforms_empty():
    b = Block()
    assert b.waveforms() == set()


def test_block_waveforms_union():
    b = Block()
    wf = Square(0.5, 100)
    b.append(Play("bus", wf))
    assert wf in b.waveforms()


# ---------------------------------------------------------------------------
# Block — structural equality
# ---------------------------------------------------------------------------


def test_block_structural_equality_empty():
    assert Block() == Block()


def test_block_inequality_different_children():
    a = Block()
    a.append(Wait("bus", 100))
    b = Block()
    b.append(Wait("bus", 200))
    assert a != b


def test_block_equality_with_same_children():
    a = Block()
    a.append(Wait("bus", 100))
    b = Block()
    b.append(Wait("bus", 100))
    assert a == b
    assert hash(a) == hash(b)


def test_block_deepcopy_structural_eq():
    a = Block()
    a.append(Wait("bus", 100))
    a.append(Play("bus", Square(0.5, 100)))
    assert a == copy.deepcopy(a)


def test_block_not_equal_to_subclass():
    """Strict type check: Block != Average even when both are empty."""
    assert Block() != Average(shots=1000)


# ---------------------------------------------------------------------------
# ForLoop
# ---------------------------------------------------------------------------


def test_for_loop_construction():
    v = Variable("freq")
    fl = ForLoop(v, 0.0, 1.0, 0.01)
    assert fl.variable is v
    assert fl.start == 0.0
    assert fl.stop == 1.0
    assert fl.step == 0.01


def test_for_loop_inherits_block_behavior():
    v = Variable("freq")
    fl = ForLoop(v, 0.0, 1.0, 0.01)
    fl.append(Wait("bus", 100))
    assert len(fl.elements) == 1


def test_for_loop_variables_includes_loop_var():
    v = Variable("freq")
    fl = ForLoop(v, 0.0, 1.0, 0.01)
    assert v in fl.variables()


def test_for_loop_variables_includes_children():
    v = Variable("freq")
    w = Variable("dur")
    fl = ForLoop(v, 0.0, 1.0, 0.01)
    fl.append(Wait("bus", w))
    assert fl.variables() == {v, w}


def test_for_loop_structural_equality():
    v = Variable("x")
    a = ForLoop(v, 0.0, 1.0, 0.1)
    b = ForLoop(v, 0.0, 1.0, 0.1)
    assert a == b
    assert hash(a) == hash(b)


def test_for_loop_inequality_different_params():
    v = Variable("x")
    a = ForLoop(v, 0.0, 1.0, 0.1)
    b = ForLoop(v, 0.0, 1.0, 0.2)
    assert a != b


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def test_loop_construction():
    v = Variable("amp")
    values = np.array([0.0, 0.5, 1.0])
    lp = Loop(v, values)
    assert lp.variable is v
    assert np.array_equal(lp.values, values)


def test_loop_accepts_list():
    v = Variable("amp")
    lp = Loop(v, [0.0, 0.5, 1.0])
    assert isinstance(lp.values, np.ndarray)


def test_loop_variables_includes_loop_var():
    v = Variable("amp")
    lp = Loop(v, np.array([0.0, 0.5, 1.0]))
    assert v in lp.variables()


def test_loop_structural_equality():
    v = Variable("amp")
    a = Loop(v, np.array([0.0, 0.5, 1.0]))
    b = Loop(v, np.array([0.0, 0.5, 1.0]))
    assert a == b
    assert hash(a) == hash(b)


def test_loop_inequality_different_values():
    v = Variable("amp")
    a = Loop(v, np.array([0.0, 0.5, 1.0]))
    b = Loop(v, np.array([0.0, 0.5, 2.0]))
    assert a != b


# ---------------------------------------------------------------------------
# Average
# ---------------------------------------------------------------------------


def test_average_construction():
    avg = Average(shots=1000)
    assert avg.shots == 1000


def test_average_walk():
    avg = Average(shots=1000)
    avg.append(Wait("bus", 100))
    assert len(list(avg.walk())) == 2


def test_average_structural_equality():
    a = Average(shots=1000)
    b = Average(shots=1000)
    assert a == b
    assert hash(a) == hash(b)


def test_average_inequality_different_shots():
    assert Average(shots=1000) != Average(shots=500)


# ---------------------------------------------------------------------------
# Parallel
# ---------------------------------------------------------------------------


def test_parallel_construction():
    v = Variable("x")
    w = Variable("y")
    loops = [ForLoop(v, 0.0, 1.0, 0.1), ForLoop(w, 0.0, 1.0, 0.1)]
    par = Parallel(loops=loops)
    assert par.loops == loops


def test_parallel_variables_includes_each_loop_var():
    v = Variable("x")
    w = Variable("y")
    par = Parallel([ForLoop(v, 0.0, 1.0, 0.1), ForLoop(w, 0.0, 1.0, 0.1)])
    assert par.variables() == {v, w}


def test_parallel_walk_yields_loops_and_body():
    v = Variable("x")
    w = Variable("y")
    fl = ForLoop(v, 0.0, 1.0, 0.1)
    fl2 = ForLoop(w, 0.0, 1.0, 0.1)
    par = Parallel([fl, fl2])
    body_op = Wait("bus", 100)
    par.append(body_op)
    walked = list(par.walk())
    assert par in walked
    assert fl in walked
    assert body_op in walked


def test_parallel_with_nested_body_variable():
    v = Variable("x")
    v2 = Variable("x2")
    w = Variable("y")
    par = Parallel([ForLoop(v, 0.0, 1.0, 0.1), ForLoop(v2, 0.0, 1.0, 0.1)])
    par.append(Wait("bus", w))
    assert par.variables() == {v, v2, w}


def test_parallel_structural_equality():
    v = Variable("x")
    w = Variable("y")
    a = Parallel([ForLoop(v, 0.0, 1.0, 0.1), ForLoop(w, 0.0, 1.0, 0.1)])
    b = Parallel([ForLoop(v, 0.0, 1.0, 0.1), ForLoop(w, 0.0, 1.0, 0.1)])
    assert a == b
    assert hash(a) == hash(b)


def test_parallel_rejects_single_loop():
    v = Variable("x")
    with pytest.raises(ValidationError, match="at least two loops"):
        Parallel([ForLoop(v, 0.0, 1.0, 0.1)])


def test_parallel_rejects_mismatched_iteration_counts():
    v = Variable("x")
    w = Variable("y")
    with pytest.raises(ValidationError, match="same number of iterations"):
        Parallel([ForLoop(v, 0.0, 1.0, 0.1), ForLoop(w, 0.0, 1.0, 0.5)])


def test_parallel_accepts_mixed_loop_kinds_with_equal_counts():
    v = Variable("x")
    w = Variable("y")
    par = Parallel([ForLoop(v, 0.0, 1.0, 0.5), Loop(w, np.array([1.0, 2.0, 3.0]))])
    assert [lp.num_iterations() for lp in par.loops] == [3, 3]


# ---------------------------------------------------------------------------
# Constructor validation: ForLoop / Loop / Average
# ---------------------------------------------------------------------------


def test_for_loop_rejects_zero_step():
    with pytest.raises(ValidationError, match="non-zero"):
        ForLoop(Variable("x"), 0.0, 1.0, 0)


def test_for_loop_rejects_non_finite_bounds():
    with pytest.raises(ValidationError, match="finite"):
        ForLoop(Variable("x"), 0.0, float("inf"), 1.0)
    with pytest.raises(ValidationError, match="finite"):
        ForLoop(Variable("x"), float("nan"), 1.0, 1.0)


def test_for_loop_rejects_wrong_step_direction():
    with pytest.raises(ValidationError, match="moves away from stop"):
        ForLoop(Variable("x"), 0.0, 10.0, -1.0)


def test_for_loop_rejects_non_numeric_bounds():
    with pytest.raises(ValidationError, match="int or float"):
        ForLoop(Variable("x"), "0", 10.0, 1.0)  # ty:ignore[invalid-argument-type]
    with pytest.raises(ValidationError, match="int or float"):
        ForLoop(Variable("x"), 0.0, 10.0, True)  # noqa: FBT003 — bool-as-step is the case under test


def test_for_loop_descending_sweep_accepted():
    fl = ForLoop(Variable("x"), 10.0, 0.0, -2.0)
    assert fl.num_iterations() == 6


def test_for_loop_num_iterations_handles_float_noise():
    assert ForLoop(Variable("x"), 0.0, 1.0, 0.01).num_iterations() == 101
    assert ForLoop(Variable("x"), 4e9, 6e9, 1e6).num_iterations() == 2001
    assert ForLoop(Variable("x"), 5.0, 5.0, 1.0).num_iterations() == 1


def test_loop_rejects_empty_values():
    with pytest.raises(ValidationError, match="non-empty"):
        Loop(Variable("x"), np.array([]))


def test_loop_rejects_2d_values():
    with pytest.raises(ValidationError, match="1-D"):
        Loop(Variable("x"), np.zeros((2, 2)))


def test_loop_num_iterations():
    assert Loop(Variable("x"), np.array([1.0, 2.0, 3.0])).num_iterations() == 3


def test_average_rejects_non_positive_shots():
    with pytest.raises(ValidationError, match=">= 1"):
        Average(shots=0)
    with pytest.raises(ValidationError, match=">= 1"):
        Average(shots=-5)


def test_average_rejects_non_integer_shots():
    with pytest.raises(ValidationError, match="integer"):
        Average(shots=10.5)  # ty:ignore[invalid-argument-type]
    with pytest.raises(ValidationError, match="integer"):
        Average(shots=True)


# ---------------------------------------------------------------------------
# Elements property
# ---------------------------------------------------------------------------


def test_block_elements_returns_list():
    b = Block()
    b.append(Wait("bus", 100))
    assert isinstance(b.elements, list)
    assert len(b.elements) == 1
