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
"""Tests for the QProgram top-level container."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from qprogram import (
    InvalidVariableIdError,
    MeasurementHandle,
    QProgram,
    ValidationError,
    Variable,
    WaveformLibrary,
    dumps,
    loads,
)
from qprogram.blocks import Average, Block, Parallel, Sweep
from qprogram.buses import BusNaming, BusRef, BusSchema
from qprogram.operations import (
    Measure,
    Play,
    ResetPhase,
    SetFrequency,
    SetGain,
    SetOffset,
    SetParameter,
    SetPhase,
    Sync,
    Wait,
)
from qprogram.qprogram import (
    _AverageContext,
    _BlockContext,
    _LoopContext,
    _measurement_name_prefix,
    _sanitize_id,
    _walk_measurement_ops,
)
from qprogram.sweeps import Range, Values
from qprogram.waveforms import Gaussian, IQDrag, IQPair, Square

# ---------------------------------------------------------------------------
# Construction & properties
# ---------------------------------------------------------------------------


def test_construction_defaults():
    p = QProgram()
    assert p.label == ""
    assert p.description is None
    assert p.schema is None
    assert p.variables == []
    assert isinstance(p.body, Block)


def test_construction_with_label_and_description():
    p = QProgram(label="foo", description="bar")
    assert p.label == "foo"
    assert p.description == "bar"


def test_construction_with_schema(transmon_schema):
    p = QProgram(schema=transmon_schema)
    assert p.schema is transmon_schema


def test_variables_property_is_a_copy():
    p = QProgram()
    p.variable("x")
    vs = p.variables
    vs.clear()
    assert len(p.variables) == 1


def test_buses_starts_empty(empty_program):
    assert empty_program.buses == set()


def test_buses_reflects_appended_operations(empty_program):
    empty_program.play("bus1", "wf")
    empty_program.measure("bus2", "wf", "weights")
    assert empty_program.buses == {"bus1", "bus2"}


# ---------------------------------------------------------------------------
# Variable declaration
# ---------------------------------------------------------------------------


def test_variable_creates_and_registers():
    p = QProgram()
    v = p.variable("freq")
    assert isinstance(v, Variable)
    assert v.id == "freq"
    assert v in p.variables


def test_variable_with_metadata():
    p = QProgram()
    v = p.variable("freq", label="Drive", units="Hz", description="...")
    assert v.label == "Drive"
    assert v.units == "Hz"


def test_variable_duplicate_id_raises():
    p = QProgram()
    p.variable("freq")
    with pytest.raises(ValidationError, match="already declared"):
        p.variable("freq")


def test_variable_invalid_id_raises():
    p = QProgram()
    with pytest.raises(InvalidVariableIdError):
        p.variable("123bad")


def test_variable_reserved_id_raises():
    p = QProgram()
    with pytest.raises(InvalidVariableIdError):
        p.variable("if")


# ---------------------------------------------------------------------------
# Core operations append to active block
# ---------------------------------------------------------------------------


def test_play_appends(empty_program):
    wf = Square(0.5, 100)
    empty_program.play("bus", wf)
    assert isinstance(empty_program.body.elements[0], Play)


def test_play_with_string_alias(empty_program):
    empty_program.play("bus", "pi_pulse")
    assert empty_program.body.elements[0].waveform == "pi_pulse"


def test_measure_returns_handle(empty_program):
    handle = empty_program.measure("readout", "wf", "weights")
    assert isinstance(handle, MeasurementHandle)
    assert handle.name == "m0"


def test_measure_with_explicit_name(empty_program):
    handle = empty_program.measure("readout", "wf", "w", name="rabi_pt")
    assert handle.name == "rabi_pt"


def test_measure_default_fields(empty_program):
    empty_program.measure("readout", "wf", "w")
    op = empty_program.body.elements[0]
    assert op.fields == ("iq",)


def test_measure_with_custom_fields(empty_program):
    empty_program.measure("readout", "wf", "w", fields=("iq", "raw"))
    op = empty_program.body.elements[0]
    assert op.fields == ("iq", "raw")


def test_wait_appends(empty_program):
    empty_program.wait("bus", 100)
    assert isinstance(empty_program.body.elements[0], Wait)


def test_wait_with_expression():
    p = QProgram()
    v = p.variable("t")
    p.wait("bus", v + 5)
    assert v in p.body.variables()


def test_sync_no_args(empty_program):
    empty_program.sync()
    op = empty_program.body.elements[0]
    assert isinstance(op, Sync)
    assert op.targets is None


def test_sync_with_buses(empty_program):
    empty_program.sync(["a", "b"])
    op = empty_program.body.elements[0]
    assert op.targets == ["a", "b"]


def test_sync_empty_list_rejected(empty_program):
    with pytest.raises(ValidationError, match="ambiguous"):
        empty_program.sync([])


def test_set_frequency_appends(empty_program):
    empty_program.set_frequency("bus", 5e9)
    assert isinstance(empty_program.body.elements[0], SetFrequency)


def test_set_phase_appends(empty_program):
    empty_program.set_phase("bus", 1.5708)
    assert isinstance(empty_program.body.elements[0], SetPhase)


def test_reset_phase_appends(empty_program):
    empty_program.reset_phase("bus")
    assert isinstance(empty_program.body.elements[0], ResetPhase)


def test_set_gain_appends(empty_program):
    empty_program.set_gain("bus", 0.5)
    assert isinstance(empty_program.body.elements[0], SetGain)


def test_set_offset_one_path(empty_program):
    empty_program.set_offset("bus", 0.1)
    op = empty_program.body.elements[0]
    assert isinstance(op, SetOffset)
    assert op.offset_path1 is None


def test_set_offset_two_paths(empty_program):
    empty_program.set_offset("bus", 0.1, 0.2)
    op = empty_program.body.elements[0]
    assert op.offset_path1 == 0.2


def test_set_parameter_appends(empty_program):
    empty_program.set_parameter("cluster", "lo", 5e9)
    assert isinstance(empty_program.body.elements[0], SetParameter)


def test_get_parameter_returns_variable(empty_program):
    v = empty_program.get_parameter("cluster", "lo_freq")
    assert isinstance(v, Variable)
    assert v.id == "cluster_lo_freq"
    assert v.label == "cluster.lo_freq"


def test_get_parameter_auto_disambiguates(empty_program):
    a = empty_program.get_parameter("cluster", "lo_freq")
    b = empty_program.get_parameter("cluster", "lo_freq")
    assert a.id == "cluster_lo_freq"
    assert b.id == "cluster_lo_freq_2"


# ---------------------------------------------------------------------------
# Bus validation (schema-backed)
# ---------------------------------------------------------------------------


def test_play_iq_waveform_on_iq_bus_ok(schema_program, iq_pulse):
    schema_program.play(schema_program.schema.q[0].drive, iq_pulse)


def test_play_iq_waveform_on_single_bus_raises(flux_tunable_schema):
    p = QProgram(schema=flux_tunable_schema)
    with pytest.raises(ValidationError, match="IQ channel"):
        p.play(flux_tunable_schema.q[0].drive, Square(0.5, 100))


def test_play_single_waveform_on_single_bus_ok(flux_tunable_schema):
    p = QProgram(schema=flux_tunable_schema)
    p.play(flux_tunable_schema.q[0].flux, Square(0.5, 100))


def test_play_iq_waveform_on_flux_raises(flux_tunable_schema):
    p = QProgram(schema=flux_tunable_schema)
    with pytest.raises(ValidationError, match="single channel"):
        p.play(flux_tunable_schema.q[0].flux, IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1))


def test_play_with_string_waveform_skips_channel_validation(schema_program):
    # String aliases bypass validation — they could be anything.
    schema_program.play(schema_program.schema.q[0].drive, "any_alias")


def test_measure_on_non_acquire_bus_raises(schema_program):
    with pytest.raises(ValidationError, match=r"acquisition|acquires"):
        schema_program.measure(schema_program.schema.q[0].drive, "wf", "w")


def test_measure_on_readout_ok(schema_program, iq_pair_pulse):
    schema_program.measure(schema_program.schema.q[0].readout, iq_pair_pulse, iq_pair_pulse)


def test_bus_from_different_schema_raises():
    schema_a = BusSchema.transmon()
    schema_b = BusSchema.transmon()
    p = QProgram(schema=schema_a)
    with pytest.raises(ValidationError, match="different BusSchema"):
        p.play(schema_b.q[0].drive, "wf")


def test_bus_from_program_without_schema_adopts_it():
    schema = BusSchema.transmon()
    p = QProgram()
    p.play(schema.q[0].drive, "wf")
    assert p.schema is schema


def test_plain_string_bus_validation_skipped(empty_program):
    # No schema means no validation — anything goes.
    empty_program.play("anything", "wf")
    empty_program.measure("anything", "wf", "w")


# ---------------------------------------------------------------------------
# Control flow context managers
# ---------------------------------------------------------------------------


def test_sweep_context_with_a_range(empty_program):
    v = empty_program.variable("x")
    with empty_program.sweep(v, Range(0.0, 1.0, 0.1)) as ctx:
        assert isinstance(ctx, Sweep)
        empty_program.wait("bus", 100)
    block = empty_program.body.elements[0]
    assert isinstance(block, Sweep)
    assert isinstance(block.source, Range)
    assert len(block.elements) == 1


def test_sweep_context_with_values(empty_program):
    v = empty_program.variable("x")
    with empty_program.sweep(v, Values(np.array([0.0, 0.5, 1.0]))) as ctx:
        assert isinstance(ctx, Sweep)
        empty_program.wait("bus", 100)
    assert isinstance(empty_program.body.elements[0].source, Values)


def test_average_context(empty_program):
    with empty_program.average(1000) as ctx:
        assert isinstance(ctx, Average)
        empty_program.wait("bus", 100)
    assert isinstance(empty_program.body.elements[0], Average)


def test_block_context(empty_program):
    with empty_program.block() as ctx:
        assert isinstance(ctx, Block)
        empty_program.wait("bus", 100)
    # ``type(...) is`` rather than ``isinstance``: Sweep, Average, Parallel and Conditional
    # are all Block subclasses, so ``isinstance`` would pass for any of them.
    assert type(empty_program.body.elements[0]) is Block


def test_parallel_via_or_operator(empty_program):
    v = empty_program.variable("x")
    w = empty_program.variable("y")
    with empty_program.sweep(v, Range(0.0, 1.0, 0.1)) | empty_program.sweep(w, Range(0.0, 1.0, 0.1)) as ctx:
        assert isinstance(ctx, Parallel)
        empty_program.wait("bus", 100)
    assert isinstance(empty_program.body.elements[0], Parallel)


def test_three_loops_parallel(empty_program):
    v = empty_program.variable("x")
    w = empty_program.variable("y")
    z = empty_program.variable("z")
    parallel_ctx = (
        empty_program.sweep(v, Range(0.0, 1.0, 0.1))
        | empty_program.sweep(w, Range(0.0, 1.0, 0.1))
        | empty_program.sweep(z, Range(0.0, 1.0, 0.1))
    )
    with parallel_ctx as ctx:
        assert isinstance(ctx, Parallel)
        assert len(ctx.loops) == 3


def test_nested_blocks(empty_program):
    v = empty_program.variable("x")
    with empty_program.average(1000), empty_program.sweep(v, Range(0.0, 1.0, 0.1)):
        empty_program.wait("bus", 100)
    body = empty_program.body
    avg = body.elements[0]
    assert isinstance(avg, Average)
    fl = avg.elements[0]
    assert isinstance(fl, Sweep)
    assert len(fl.elements) == 1


def test_block_stack_pops_after_context(empty_program):
    v = empty_program.variable("x")
    with empty_program.sweep(v, Range(0.0, 1.0, 0.1)):
        pass
    # After exiting, subsequent appends go to the body.
    empty_program.wait("bus", 100)
    assert isinstance(empty_program.body.elements[-1], Wait)


def test_loop_context_class_directly_callable():
    """Internals: _LoopContext can compose with another via __or__."""
    p = QProgram()
    v = p.variable("x")
    w = p.variable("y")
    ctx1 = p.sweep(v, Range(0.0, 1.0, 0.1))
    ctx2 = p.sweep(w, Range(0.0, 1.0, 0.1))
    combined = ctx1 | ctx2
    assert isinstance(combined, _LoopContext)
    assert len(combined._parallel_blocks) == 2


def test_average_context_class():
    p = QProgram()
    ctx = p.average(1000)
    assert isinstance(ctx, _AverageContext)


def test_block_context_class():
    p = QProgram()
    ctx = p.block()
    assert isinstance(ctx, _BlockContext)


# ---------------------------------------------------------------------------
# Measurement handles
# ---------------------------------------------------------------------------


def test_measurement_naming_per_bus(schema_program):
    schema = schema_program.schema
    m_q0_first = schema_program.measure(schema.q[0].readout, "r", "w")
    m_q1_first = schema_program.measure(schema.q[1].readout, "r", "w")
    m_q0_second = schema_program.measure(schema.q[0].readout, "r", "w")
    assert m_q0_first.name == "q0/readout/m0"
    assert m_q1_first.name == "q1/readout/m0"
    assert m_q0_second.name == "q0/readout/m1"


def test_measurement_raw_string_bus_fallback(empty_program):
    handle = empty_program.measure("raw_bus", "wf", "w")
    assert handle.name == "m0"


def test_measurement_handles_returns_handles(schema_program):
    schema_program.measure(schema_program.schema.q[0].readout, "r", "w")
    schema_program.measure(schema_program.schema.q[1].readout, "r", "w")
    handles = schema_program.measurement_handles()
    assert len(handles) == 2
    assert all(isinstance(h, MeasurementHandle) for h in handles)


def test_measurement_handles_in_declaration_order(schema_program):
    schema = schema_program.schema
    schema_program.measure(schema.q[1].readout, "r", "w")
    schema_program.measure(schema.q[0].readout, "r", "w")
    schema_program.measure(schema.q[1].readout, "r", "w")
    handles = schema_program.measurement_handles()
    assert [h.name for h in handles] == ["q1/readout/m0", "q0/readout/m0", "q1/readout/m1"]


def test_measurement_name_collision_raises(schema_program):
    schema_program.measure(schema_program.schema.q[0].readout, "r", "w", name="rabi")
    with pytest.raises(ValidationError, match="already used"):
        schema_program.measure(schema_program.schema.q[0].readout, "r", "w", name="rabi")


def test_measurement_name_empty_raises(schema_program):
    with pytest.raises(ValidationError, match="non-empty"):
        schema_program.measure(schema_program.schema.q[0].readout, "r", "w", name="")


def test_allocate_measurement_name_finds_first_free(schema_program):
    schema = schema_program.schema
    # With m0 and m2 taken, the next auto-allocation fills the gap at m1.
    schema_program.measure(schema.q[0].readout, "r", "w", name="q0/readout/m0")
    schema_program.measure(schema.q[0].readout, "r", "w", name="q0/readout/m2")
    next_handle = schema_program.measure(schema.q[0].readout, "r", "w")
    assert next_handle.name == "q0/readout/m1"


# ---------------------------------------------------------------------------
# rebind — structural bus re-resolution
# ---------------------------------------------------------------------------


def test_rebind_reindexes_qubit(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    p.measure(q[0].readout, "ro", "w")

    ported = p.rebind(elements={("q", 0): ("q", 1)})
    assert ported.buses == {"q1/drive", "q1/readout"}
    assert p.buses == {"q0/drive", "q0/readout"}  # original untouched


def test_rebind_result_stays_busref_and_serializes_as_path(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    ported = p.rebind(elements={("q", 0): ("q", 1)})

    play_op = ported.body.elements[0]
    assert isinstance(play_op.bus, BusRef)  # a BusRef, not a plain str
    assert play_op.bus.element == "q"
    assert play_op.bus.idx == 1
    assert play_op.bus.channel == "IQ"

    text = dumps(ported)
    assert "q[1].drive" in text  # path form, not a quoted string
    assert loads(text).body == ported.body  # round-trips body-equal


def test_rebind_absent_kind_raises(transmon_schema):
    # A transmon coupler ``c`` has only ``flux``, so re-elementing a drive op onto it cannot resolve.
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    with pytest.raises(AttributeError):
        p.rebind(schema=BusSchema.transmon_coupled(), elements={("q", 0): ("c", 0)})


def test_rebind_naming_reconciles_cross_platform(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")

    ported = p.rebind(naming=BusNaming("{kind}_{element}{index}_bus"))
    play_op = ported.body.elements[0]
    assert play_op.bus == "drive_q0_bus"
    assert isinstance(play_op.bus, BusRef)
    assert loads(dumps(ported)).body == ported.body


def test_rebind_handles_sync_list(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.sync([q[0].readout, q[1].readout])
    ported = p.rebind(elements={("q", 0): ("q", 2)})
    sync_op = ported.body.elements[0]
    assert isinstance(sync_op, Sync)
    assert sync_op.targets == ["q2/readout", "q1/readout"]


def test_rebind_rederives_auto_measurement_name(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    handle = p.measure(q[0].readout, "ro", "w")
    assert handle.name == "q0/readout/m0"

    ported = p.rebind(elements={("q", 0): ("q", 1)})
    assert ported.measurement_handles()[0].name == "q1/readout/m0"


def test_rebind_preserves_user_measurement_name(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.measure(q[0].readout, "ro", "w", name="my_meas")
    ported = p.rebind(elements={("q", 0): ("q", 1)})
    assert ported.measurement_handles()[0].name == "my_meas"


def test_rebind_raw_string_unported_raises():
    p = QProgram()
    p.play("aux_line", "wf")
    with pytest.raises(ValidationError, match="aux_line"):
        p.rebind(elements={("q", 0): ("q", 1)})


def test_rebind_raw_string_via_strings_map():
    p = QProgram()
    p.play("a", "wf")
    p.sync(["a", "b", "c"])
    ported = p.rebind(strings={"a": "X", "c": "Z"}, allow_unported_strings=True)
    sync_op = ported.body.elements[1]
    assert isinstance(sync_op, Sync)
    assert sync_op.targets == ["X", "b", "Z"]
    assert ported.body.elements[0].bus == "X"


def test_rebind_raw_string_allow_unported_leaves_in_place():
    p = QProgram()
    p.play("aux_line", "wf")
    ported = p.rebind(allow_unported_strings=True)
    assert ported.body.elements[0].bus == "aux_line"


def test_rebind_strings_self_map_marks_intentional():
    p = QProgram()
    p.play("aux_line", "wf")
    ported = p.rebind(strings={"aux_line": "aux_line"})  # explicit no-raise marker
    assert ported.body.elements[0].bus == "aux_line"


def test_rebind_original_unchanged(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    p.rebind(elements={("q", 0): ("q", 1)})
    assert p.buses == {"q0/drive"}


# ---------------------------------------------------------------------------
# with_waveforms — per-bus waveform resolution via WaveformLibrary
# ---------------------------------------------------------------------------


def test_with_waveforms_per_bus_resolution(transmon_schema):
    """The same name resolves to different pulses on q0 vs q1 (the portability win)."""
    q = transmon_schema.q
    library = WaveformLibrary()
    library.set("pi", IQDrag(0.5, 40, 8, 0.1), element="q", idx=0, kind="drive")
    library.set("pi", IQDrag(0.9, 40, 8, 0.1), element="q", idx=1, kind="drive")

    p0 = QProgram(schema=transmon_schema)
    p0.play(q[0].drive, "pi")
    p1 = QProgram(schema=transmon_schema)
    p1.play(q[1].drive, "pi")

    assert p0.with_waveforms(library).body.elements[0].waveform.amplitude == 0.5
    assert p1.with_waveforms(library).body.elements[0].waveform.amplitude == 0.9


def test_with_waveforms_family_tier(transmon_schema):
    q = transmon_schema.q
    library = WaveformLibrary()
    ro = IQPair(Square(1.0, 100), Square(0.0, 100))
    library.set("readout", ro, element="q", kind="readout")  # any q[*].readout

    p = QProgram(schema=transmon_schema)
    p.measure(q[3].readout, "readout", IQPair(Square(1.0, 100), Square(1.0, 100)))
    resolved = p.with_waveforms(library)
    assert resolved.body.elements[0].waveform is ro


def test_with_waveforms_exact_shadows_global(transmon_schema):
    q = transmon_schema.q
    library = WaveformLibrary()
    library.set("pi", IQDrag(0.1, 40, 8, 0.1))  # global
    library.set("pi", IQDrag(0.7, 40, 8, 0.1), element="q", idx=0, kind="drive")  # exact

    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    assert p.with_waveforms(library).body.elements[0].waveform.amplitude == 0.7


def test_with_waveforms_bare_dict_global_tier():
    """A plain dict is the global tier — it resolves on every bus."""
    p = QProgram()
    p.play("bus", "pi_pulse")
    p.measure("bus_r", "readout", "weights")
    pi = Gaussian(0.5, 40, 8)
    readout = IQPair(Square(1.0, 100), Square(0.0, 100))
    weights = IQPair(Square(1.0, 100), Square(1.0, 100))

    resolved = p.with_waveforms({"pi_pulse": pi, "readout": readout, "weights": weights})
    assert resolved.body.elements[0].waveform is pi
    assert resolved.body.elements[1].waveform is readout
    assert resolved.body.elements[1].weights is weights


def test_with_waveforms_preserves_unmatched_names():
    p = QProgram()
    p.play("bus", "pi_pulse")
    resolved = p.with_waveforms({"unrelated": Gaussian(0.5, 40, 8)})
    assert resolved.body.elements[0].waveform == "pi_pulse"


def test_with_waveforms_channel_revalidation(flux_tunable_schema):
    """A single-channel waveform resolved onto an IQ bus is caught at resolution time."""
    q = flux_tunable_schema.q
    p = QProgram(schema=flux_tunable_schema)
    p.play(q[0].drive, "foo")  # drive is IQ
    library = WaveformLibrary()
    library.set("foo", Square(0.5, 100))  # single-channel
    with pytest.raises(ValidationError):
        p.with_waveforms(library)


def test_with_waveforms_nested_inside_block():
    p = QProgram()
    v = p.variable("x")
    with p.average(100), p.sweep(v, Range(0.0, 1.0, 0.1)):
        p.play("bus", "pi_pulse")
    pi = Gaussian(0.5, 40, 8)
    resolved = p.with_waveforms({"pi_pulse": pi})
    deepest_op = resolved.body.elements[0].elements[0].elements[0]
    assert isinstance(deepest_op, Play)
    assert deepest_op.waveform is pi


def test_waveform_library_invalid_combination_raises():
    library = WaveformLibrary()
    with pytest.raises(ValidationError, match="exact entry"):
        library.set("pi", Gaussian(0.5, 40, 8), element="q", idx=0)  # missing kind


def test_waveform_library_apply_equals_with_waveforms(transmon_schema):
    q = transmon_schema.q
    library = WaveformLibrary()
    pi = IQDrag(0.5, 40, 8, 0.1)
    library.set("pi", pi, element="q", idx=0, kind="drive")
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    assert library.apply(p).body == p.with_waveforms(library).body


# ---------------------------------------------------------------------------
# Vendor namespace lookup (vendor.py is tested separately; only the dispatch)
# ---------------------------------------------------------------------------


def test_unknown_vendor_attribute_raises_attribute_error(empty_program):
    with pytest.raises(AttributeError, match="No vendor namespace"):
        empty_program.unknown_vendor  # ruff: ignore[useless-expression]


def test_underscore_attribute_passes_through_attribute_error():
    p = QProgram()
    with pytest.raises(AttributeError):
        p._nonexistent  # ruff: ignore[useless-expression]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_walk_measurement_ops_empty():
    p = QProgram()
    assert _walk_measurement_ops(p.body) == []


def test_walk_measurement_ops_finds_nested():
    p = QProgram()
    with p.average(100):
        p.measure("bus", "wf", "w")
    found = _walk_measurement_ops(p.body)
    assert len(found) == 1
    assert isinstance(found[0], Measure)


def test_measurement_name_prefix_for_schema_backed_bus():
    schema = BusSchema.transmon()
    ref = schema.q[0].readout
    assert _measurement_name_prefix(ref) == "q0/readout/m"


def test_measurement_name_prefix_for_tuple_index():
    schema = BusSchema.transmon_coupled()
    ref = schema.c[(0, 1)].flux
    assert _measurement_name_prefix(ref) == "c0_1/flux/m"


def test_measurement_name_prefix_for_plain_string():
    assert _measurement_name_prefix("raw_bus") == "m"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc", "abc"),
        ("abc.def", "abc_def"),
        ("123", "_123"),
        ("", "var"),
        ("a-b", "a_b"),
        ("abc def", "abc_def"),
    ],
)
def test_sanitize_id(raw, expected):
    assert _sanitize_id(raw) == expected


# ---------------------------------------------------------------------------
# Edge cases & integration
# ---------------------------------------------------------------------------


def test_deepcopy_preserves_structure():
    p = QProgram()
    v = p.variable("x")
    with p.sweep(v, Range(0.0, 1.0, 0.1)):
        p.wait("bus", v)
    copied = copy.deepcopy(p)
    assert copied.body == p.body


def test_buses_for_set_offset_two_paths(empty_program):
    """set_offset with both paths still only references one bus."""
    empty_program.set_offset("bus", 0.1, 0.2)
    assert empty_program.buses == {"bus"}


def test_get_parameter_added_variable_in_program_list():
    p = QProgram()
    v = p.get_parameter("cluster", "lo")
    assert v in p.variables


def test_loop_variable_appears_in_body_variables():
    p = QProgram()
    v = p.variable("x")
    with p.sweep(v, Range(0.0, 1.0, 0.1)):
        p.wait("bus", 100)
    assert v in p.body.variables()
