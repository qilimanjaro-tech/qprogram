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
"""Tests for the fluent sweep form — ``program.sweep(var).from_*(...)``.

The contract under test is *equivalence*: the fluent form is a spelling, not a second semantics. Every
``from_*`` must build the same :class:`~qprogram.blocks.Sweep` node the two-argument
``sweep(variable, source)`` form builds, so the AST compares equal, the ``.qp`` output is byte-identical
and everything downstream (``|`` composition, validation, execution, round-tripping) is unaffected.

The sources themselves are covered in ``test_sweeps.py``; this file only tests the builder that reaches
them — including the registry-driven fallback that gives a vendor source its ``from_*`` for free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

import qprogram as qp
from qprogram import QProgram, dumps, loads
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.errors import ValidationError
from qprogram.fragments import fragment
from qprogram.protocol import CAPABILITY_REGISTRY
from qprogram.serialization import registry
from qprogram.serialization.registry import register_sweep_source
from qprogram.sweeps import (
    Concat,
    File,
    Linspace,
    Logspace,
    Range,
    Repeat,
    Rotate,
    SweepSource,
    Values,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.buses import BusSchema

# (fluent call, equivalent source) for every built-in with a spelled-out builder method.
BUILTIN_CASES = [
    (lambda b: b.from_range(0.0, 1.0, 0.25), Range(0.0, 1.0, 0.25)),
    (lambda b: b.from_range(0.0, 1.0), Range(0.0, 1.0)),
    (lambda b: b.from_linspace(0.0, 1.0, 5), Linspace(0.0, 1.0, num=5)),
    (lambda b: b.from_logspace(1.0, 100.0, 5), Logspace(1.0, 100.0, num=5)),
    (lambda b: b.from_values([0.1, 0.4, 0.9]), Values([0.1, 0.4, 0.9])),
    (lambda b: b.from_file("phases.npy"), File("phases.npy")),
]
BUILTIN_IDS = [type(source).__name__ for _, source in BUILTIN_CASES]


# ---------------------------------------------------------------------------
# Equivalence with the explicit form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("call", "source"), BUILTIN_CASES, ids=BUILTIN_IDS)
def test_from_method_builds_the_same_sweep_as_the_explicit_form(call, source, transmon_schema: BusSchema):
    fluent = QProgram(schema=transmon_schema)
    with call(fluent.sweep(fluent.variable("v"))):
        pass

    explicit = QProgram(schema=transmon_schema)
    with explicit.sweep(explicit.variable("v"), source):
        pass

    assert fluent.body == explicit.body
    assert hash(fluent.body) == hash(explicit.body)
    assert dumps(fluent) == dumps(explicit)


@pytest.mark.parametrize(("call", "source"), BUILTIN_CASES, ids=BUILTIN_IDS)
def test_from_method_stores_the_source_instance_itself(call, source, empty_program: QProgram):
    ctx = call(empty_program.sweep(empty_program.variable("v")))
    with ctx as block:
        pass
    assert isinstance(block, Sweep)
    assert block.source == source
    assert type(block.source) is type(source)


def test_fluent_sweep_round_trips_through_qp(transmon_schema: BusSchema):
    program = QProgram(schema=transmon_schema)
    freq = program.variable("freq")
    with program.sweep(freq).from_linspace(4e9, 6e9, 101):
        program.set_frequency(transmon_schema.q[0].drive, freq)

    assert loads(dumps(program)).body == program.body


def test_from_values_accepts_a_numpy_array(empty_program: QProgram):
    points = np.linspace(0.0, 1.0, 7)
    with empty_program.sweep(empty_program.variable("v")).from_values(points) as block:
        pass
    assert np.array_equal(block.source.values(), points)


def test_source_validation_still_happens_at_the_call_site(empty_program: QProgram):
    """The builder forwards to the constructor, so the source's own errors surface unchanged."""
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(ValidationError, match="step must be non-zero"):
        builder.from_range(0.0, 1.0, 0.0)


# ---------------------------------------------------------------------------
# The builder is not a block
# ---------------------------------------------------------------------------


def test_entering_a_builder_without_picking_values_raises(empty_program: QProgram):
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(ValidationError, match="picked no values"), builder:
        pass


def test_a_builder_that_is_never_used_appends_nothing(empty_program: QProgram):
    empty_program.sweep(empty_program.variable("v"))
    empty_program.sweep(empty_program.variable("w")).from_range(0.0, 1.0)  # Not entered either.
    assert empty_program.body.elements == []


def test_explicit_none_source_is_rejected_rather_than_returning_a_builder(empty_program: QProgram):
    """``None`` is a failed source, not an omitted one — the sentinel keeps the two apart."""
    v = empty_program.variable("v")
    with pytest.raises(ValidationError, match="must be a SweepSource"):
        empty_program.sweep(v, None)


# ---------------------------------------------------------------------------
# Composition: ``|`` and the combinator shortcuts
# ---------------------------------------------------------------------------


def test_fluent_sweeps_compose_with_the_parallel_operator(transmon_schema: BusSchema):
    fluent = QProgram(schema=transmon_schema)
    with fluent.sweep(fluent.variable("a")).from_range(0.0, 1.0, 0.1) | fluent.sweep(
        fluent.variable("b")
    ).from_linspace(0.0, 1.0, 11) as block:
        pass
    assert isinstance(block, Parallel)

    explicit = QProgram(schema=transmon_schema)
    with explicit.sweep(explicit.variable("a"), Range(0.0, 1.0, 0.1)) | explicit.sweep(
        explicit.variable("b"), Linspace(0.0, 1.0, 11)
    ):
        pass
    assert fluent.body == explicit.body


def test_the_two_forms_mix_in_one_parallel_composition(transmon_schema: BusSchema):
    program = QProgram(schema=transmon_schema)
    with program.sweep(program.variable("a")).from_range(0.0, 1.0, 0.1) | program.sweep(
        program.variable("b"), Range(0.0, 1.0, 0.1)
    ) as block:
        pass
    assert isinstance(block, Parallel)
    assert [loop.source for loop in block.loops] == [Range(0.0, 1.0, 0.1)] * 2


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        (lambda ctx: ctx.repeat(3), Repeat(Values([0.0, 1.0, 2.0]), times=3)),
        (lambda ctx: ctx.rotate(), Rotate(Values([0.0, 1.0, 2.0]), by=1)),
        (lambda ctx: ctx.rotate(by=2), Rotate(Values([0.0, 1.0, 2.0]), by=2)),
        (
            lambda ctx: ctx.rotate(by=1).repeat(3),
            Repeat(Rotate(Values([0.0, 1.0, 2.0]), by=1), times=3),
        ),
        (
            lambda ctx: ctx.repeat(3).rotate(by=1),
            Rotate(Repeat(Values([0.0, 1.0, 2.0]), times=3), by=1),
        ),
    ],
    ids=["repeat", "rotate-default", "rotate-by", "rotate-then-repeat", "repeat-then-rotate"],
)
def test_combinator_shortcuts_wrap_the_bound_source_in_call_order(shape, expected, empty_program: QProgram):
    ctx = empty_program.sweep(empty_program.variable("v")).from_values([0.0, 1.0, 2.0])
    with shape(ctx) as block:
        pass
    assert block.source == expected


def test_combinator_shortcuts_also_work_on_an_explicit_source(empty_program: QProgram):
    """``repeat`` / ``rotate`` live on the context, so both spellings reach them."""
    with empty_program.sweep(empty_program.variable("v"), Range(0.0, 1.0, 0.5)).repeat(2) as block:
        pass
    assert block.source == Repeat(Range(0.0, 1.0, 0.5), times=2)


def test_combinator_shortcuts_are_pure(empty_program: QProgram):
    """Like ``|``, they hand back a fresh context and leave the original usable."""
    plain = empty_program.sweep(empty_program.variable("v")).from_values([0.0, 1.0])
    shaped = plain.repeat(3)
    assert shaped is not plain
    with plain as first:
        pass
    with shaped as second:
        pass
    assert first.source == Values([0.0, 1.0])
    assert second.source == Repeat(Values([0.0, 1.0]), times=3)


@pytest.mark.parametrize("method", ["repeat", "rotate"])
def test_combinator_shortcuts_refuse_a_parallel_composition(method, empty_program: QProgram):
    composed = empty_program.sweep(empty_program.variable("a")).from_range(0.0, 1.0) | empty_program.sweep(
        empty_program.variable("b")
    ).from_range(0.0, 1.0)
    shortcut = getattr(composed, method)
    with pytest.raises(ValidationError, match=rf"{method}\(\) shapes one sweep's source"):
        shortcut(2)


# ---------------------------------------------------------------------------
# The registry-driven fallback
# ---------------------------------------------------------------------------


def test_combinators_are_reachable_through_the_registry_fallback(empty_program: QProgram):
    """Every registered source has a builder, combinators included (they just need a source)."""
    with empty_program.sweep(empty_program.variable("v")).from_concat([Range(0.0, 1.0, 0.5), [9.0]]) as block:
        pass
    assert block.source == Concat([Range(0.0, 1.0, 0.5), Values([9.0])])


@pytest.fixture
def vendor_source() -> Iterator[type[SweepSource]]:
    """A registered out-of-core source, as a vendor extension would add it."""

    class IQTable(SweepSource):
        KIND = "arbitrary"
        TOKEN = "sweep.iqtable"

        def __init__(self, size: int) -> None:
            self.size = size

        def length(self) -> int:
            return self.size

        def values(self) -> np.ndarray:
            return np.arange(self.size, dtype=float)

    register_sweep_source(IQTable)
    try:
        yield IQTable
    finally:
        registry._sweep_source_registry.pop("IQTable", None)
        CAPABILITY_REGISTRY.discard("sweep.iqtable")


def test_a_registered_vendor_source_gets_its_builder_with_no_core_change(vendor_source, empty_program: QProgram):
    with empty_program.sweep(empty_program.variable("v")).from_iq_table(4) as block:
        pass
    assert isinstance(block.source, vendor_source)
    assert block.source.length() == 4


def test_the_fallback_matches_a_class_name_ignoring_case_and_underscores(vendor_source, empty_program: QProgram):
    """``IQTable`` is reachable as ``from_iq_table`` — an acronym doesn't need a special case."""
    builder = empty_program.sweep(empty_program.variable("v"))
    assert isinstance(builder.from_iqtable(2)._block.source, vendor_source)
    assert isinstance(builder.from_iq_table(2)._block.source, vendor_source)


@pytest.mark.usefixtures("vendor_source")
def test_a_vendor_built_sweep_round_trips(transmon_schema: BusSchema):
    program = QProgram(schema=transmon_schema)
    with program.sweep(program.variable("v")).from_iq_table(3):
        pass
    assert loads(dumps(program)).body == program.body


def test_unknown_from_attribute_raises_with_a_did_you_mean(empty_program: QProgram):
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(AttributeError, match="Did you mean from_range") as excinfo:
        _ = builder.from_rnge
    assert "no sweep source is registered for 'from_rnge'" in str(excinfo.value)
    assert "from_linspace" in str(excinfo.value)  # The full available list is included.


def test_a_non_from_attribute_raises_a_plain_attribute_error(empty_program: QProgram):
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(AttributeError, match="has no attribute 'sweep_range'"):
        _ = builder.sweep_range


@pytest.mark.parametrize("dunder", ["__deepcopy__", "__wrapped__"])
def test_dunder_lookups_are_not_intercepted(dunder, empty_program: QProgram):
    """A ``from_*``-shaped guard that swallowed dunders would break copy/pickle/inspect."""
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(AttributeError):
        getattr(builder, dunder)


# ---------------------------------------------------------------------------
# Downstream: fragments and execution
# ---------------------------------------------------------------------------


def test_the_fluent_form_works_inside_a_fragment(transmon_schema: BusSchema):
    """The whole builder surface works in a fragment body, the fluent sweep included."""

    @fragment
    def scan(frag, bus):
        v = frag.variable("v")
        with frag.sweep(v).from_range(0.0, 1.0, 0.5):
            frag.set_gain(bus, v)

    program = QProgram(schema=transmon_schema)
    program.call(scan, transmon_schema.q[0].drive)

    assert loads(dumps(program)).body == program.body
    sweeps = [node for node in program.expand().body.walk() if isinstance(node, Sweep)]
    assert [node.source for node in sweeps] == [Range(0.0, 1.0, 0.5)]


def test_a_fluent_program_executes_like_its_explicit_twin(transmon_schema: BusSchema):
    def build(fluent: bool) -> QProgram:
        program = QProgram(schema=transmon_schema)
        amp = program.variable("amp")
        loop = program.sweep(amp).from_linspace(0.0, 1.0, 5) if fluent else program.sweep(amp, Linspace(0.0, 1.0, 5))
        with loop:
            program.set_gain(transmon_schema.q[0].drive, amp)
            program.measure(transmon_schema.q[0].readout, "readout_pulse", "weights", name="m")
        return program

    fluent_result = qp.simulate(build(fluent=True), model=qp.MockMeasurementModel(seed=7))
    explicit_result = qp.simulate(build(fluent=False), model=qp.MockMeasurementModel(seed=7))
    assert np.array_equal(fluent_result.get("m").values, explicit_result.get("m").values)


@pytest.mark.parametrize("method", ["repeat", "rotate"])
def test_a_shaping_shortcut_on_a_valueless_builder_says_to_pick_values_first(method, empty_program: QProgram):
    builder = empty_program.sweep(empty_program.variable("v"))
    with pytest.raises(AttributeError, match=rf"{method}\(\) shapes a sweep that already has values"):
        getattr(builder, method)
