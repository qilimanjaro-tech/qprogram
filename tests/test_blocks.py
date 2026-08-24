# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for Block / Sweep / Average / Parallel."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from qprogram import ValidationError, Variable
from qprogram.blocks import Average, Block, Conditional, Parallel, Sweep
from qprogram.operations import Play, Wait
from qprogram.sweeps import Linspace, Logspace, Range, Values
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
# Sweep — the one loop block
# ---------------------------------------------------------------------------


def test_sweep_construction():
    v = Variable("freq")
    src = Range(4e9, 6e9, 1e6)
    sw = Sweep(v, src)
    assert sw.variable is v
    assert sw.source is src
    assert sw.elements == []


def test_sweep_num_iterations_delegates_to_the_source():
    """The block knows nothing about how the values are produced — it asks."""
    assert Sweep(Variable("x"), Range(0.0, 1.0, 0.25)).num_iterations() == 5
    assert Sweep(Variable("x"), Values([1.0, 2.0, 3.0])).num_iterations() == 3
    assert Sweep(Variable("x"), Linspace(0.0, 1.0, num=7)).num_iterations() == 7


def test_sweep_variables_includes_the_loop_variable():
    v = Variable("freq")
    sw = Sweep(v, Range(0, 10, 1))
    sw.append(Play(bus="drive", waveform=Square(1.0, 100)))
    assert v in sw.variables()


def test_sweep_required_capabilities_combines_block_and_source_tokens():
    caps = Sweep(Variable("x"), Range(0, 10, 1)).required_capabilities()
    assert caps == {"block.sweep", "sweep.linear", "sweep.range"}
    caps = Sweep(Variable("x"), Logspace(1.0, 100.0, num=5)).required_capabilities()
    assert caps == {"block.sweep", "sweep.arbitrary", "sweep.logspace"}


def test_sweep_repeats():
    assert Sweep.REPEATS is True


def test_sweep_accepts_a_bare_sequence_as_values_shorthand():
    sw = Sweep(Variable("x"), [0.1, 0.2, 0.3])
    assert isinstance(sw.source, Values)
    assert sw.num_iterations() == 3


def test_sweep_rejects_a_callable_source():
    """The AST holds descriptions of values, never producers of them."""
    with pytest.raises(ValidationError, match="not a callable"):
        Sweep(Variable("x"), lambda i: i)  # ty:ignore[invalid-argument-type]


def test_sweep_rejects_a_non_source_non_sequence():
    with pytest.raises(ValidationError, match="SweepSource or a 1-D sequence"):
        Sweep(Variable("x"), object())  # ty:ignore[invalid-argument-type]


def test_sweep_equality_is_structural_over_variable_source_and_children():
    a = Sweep(Variable("x"), Range(0, 10, 1))
    b = Sweep(Variable("x"), Range(0, 10, 1))
    assert a == b
    assert hash(a) == hash(b)
    assert a != Sweep(Variable("x"), Range(0, 10, 2))
    assert a != Sweep(Variable("y"), Range(0, 10, 1))
    c = Sweep(Variable("x"), Range(0, 10, 1))
    c.append(Play(bus="drive", waveform=Square(1.0, 100)))
    assert a != c


def test_sweep_walk_yields_self_then_children():
    sw = Sweep(Variable("x"), Range(0, 2, 1))
    op = Play(bus="drive", waveform=Square(1.0, 100))
    sw.append(op)
    assert list(sw.walk()) == [sw, op]


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
    loops = [Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(w, Range(0.0, 1.0, 0.1))]
    par = Parallel(loops=loops)
    assert par.loops == loops


def test_parallel_variables_includes_each_loop_var():
    v = Variable("x")
    w = Variable("y")
    par = Parallel([Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(w, Range(0.0, 1.0, 0.1))])
    assert par.variables() == {v, w}


def test_parallel_walk_yields_loops_and_body():
    v = Variable("x")
    w = Variable("y")
    fl = Sweep(v, Range(0.0, 1.0, 0.1))
    fl2 = Sweep(w, Range(0.0, 1.0, 0.1))
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
    par = Parallel([Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(v2, Range(0.0, 1.0, 0.1))])
    par.append(Wait("bus", w))
    assert par.variables() == {v, v2, w}


def test_parallel_structural_equality():
    v = Variable("x")
    w = Variable("y")
    a = Parallel([Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(w, Range(0.0, 1.0, 0.1))])
    b = Parallel([Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(w, Range(0.0, 1.0, 0.1))])
    assert a == b
    assert hash(a) == hash(b)


def test_parallel_rejects_single_loop():
    v = Variable("x")
    with pytest.raises(ValidationError, match="at least two loops"):
        Parallel([Sweep(v, Range(0.0, 1.0, 0.1))])


def test_parallel_rejects_mismatched_iteration_counts():
    v = Variable("x")
    w = Variable("y")
    with pytest.raises(ValidationError, match="same number of iterations"):
        Parallel([Sweep(v, Range(0.0, 1.0, 0.1)), Sweep(w, Range(0.0, 1.0, 0.5))])


def test_parallel_accepts_mixed_loop_kinds_with_equal_counts():
    v = Variable("x")
    w = Variable("y")
    par = Parallel([Sweep(v, Range(0.0, 1.0, 0.5)), Sweep(w, Values(np.array([1.0, 2.0, 3.0])))])
    assert [lp.num_iterations() for lp in par.loops] == [3, 3]


# ---------------------------------------------------------------------------
# Constructor validation: Average
# (sweep-source validation — bounds, step direction, empty values — lives in test_sweeps.py,
#  since it is the source's contract rather than the block's)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Block.REPEATS — the marker the validator's depth counter reads
# ---------------------------------------------------------------------------


def test_plain_block_does_not_repeat():
    assert Block.REPEATS is False
    assert Block().REPEATS is False


def test_conditional_does_not_repeat():
    """Branching selects a body; it doesn't iterate."""
    assert Conditional.REPEATS is False


@pytest.mark.parametrize("cls", [Sweep, Average, Parallel])
def test_core_loop_blocks_declare_that_they_repeat(cls):
    assert cls.REPEATS is True


def test_repeats_is_a_class_attribute_so_subclasses_can_opt_in():
    """A vendor block sets it on its own subclass — that is the whole extension point."""

    class _VendorLoop(Block):
        REPEATS = True

    class _VendorGrouping(Block):
        pass

    assert _VendorLoop().REPEATS is True
    assert _VendorGrouping().REPEATS is False
