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
"""Tests for every core Operation + the introspection contract."""

from __future__ import annotations

import pytest

from qprogram import (
    MeasurementHandle,
    ValidationError,
    Variable,
)
from qprogram.operations import (
    GetParameter,
    Measure,
    Operation,
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
from qprogram.operations.operation import (
    MeasurementField,
    MeasurementOperation,
    _collect_variables,
    normalize_fields,
)
from qprogram.protocol import CAPABILITY_REGISTRY, register_capability_tokens
from qprogram.waveforms import Gaussian, IQPair, Square

# ---------------------------------------------------------------------------
# Operation base — class-level conventions
# ---------------------------------------------------------------------------


def test_operation_default_bus_attrs():
    assert Operation.BUS_ATTRS == ("bus",)


def test_operation_default_waveform_attrs():
    assert Operation.WAVEFORM_ATTRS == ()


# ---------------------------------------------------------------------------
# Each operation — basic construction
# ---------------------------------------------------------------------------


def test_play_construction():
    wf = Square(0.5, 100)
    op = Play("bus", wf)
    assert op.bus == "bus"
    assert op.waveform is wf


def test_play_waveform_attrs():
    assert Play.WAVEFORM_ATTRS == ("waveform",)


def test_measure_construction():
    op = Measure("readout", "wf", "weights", handle=MeasurementHandle("m0"))
    assert op.bus == "readout"
    assert op.waveform == "wf"
    assert op.weights == "weights"
    assert op.name == "m0"
    assert op.fields == (MeasurementField.IQ,)


def test_measure_waveform_attrs():
    assert Measure.WAVEFORM_ATTRS == ("waveform", "weights")


def test_measure_fields_enum_input():
    op = Measure(
        "readout",
        "wf",
        "w",
        handle=MeasurementHandle("m0"),
        fields=(MeasurementField.IQ, MeasurementField.RAW),
    )
    assert op.fields == ("iq", "raw")


def test_measure_fields_string_input():
    """Registered field-name strings are accepted — that's how vendor fields work."""
    op = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"), fields=["iq", "raw"])
    assert op.fields == (MeasurementField.IQ, MeasurementField.RAW)


def test_measure_fields_canonical_order_is_argument_order_independent():
    a = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"), fields=("iq", "state"))
    b = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"), fields=("state", "iq"))
    assert a.fields == b.fields == ("state", "iq")
    assert a == b
    assert hash(a) == hash(b)


def test_measure_fields_rejects_bare_string():
    handle = MeasurementHandle("m0")
    with pytest.raises(ValidationError, match=r'fields=\("iq", "raw"\)'):
        Measure("readout", "wf", "w", handle=handle, fields="iq,raw")


def test_measure_fields_rejects_unknown_field():
    handle = MeasurementHandle("m0")
    with pytest.raises(ValidationError, match="unknown measurement field"):
        Measure("readout", "wf", "w", handle=handle, fields=("iqq",))


def test_measure_is_measurement_operation():
    op = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"))
    assert isinstance(op, MeasurementOperation)


def test_wait_construction():
    op = Wait("bus", 100)
    assert op.bus == "bus"
    assert op.duration == 100


def test_wait_with_expression_duration():
    v = Variable("dur")
    op = Wait("bus", v)
    assert op.duration is v


def test_sync_construction_no_args():
    op = Sync()
    assert op.targets is None


def test_sync_construction_with_targets():
    op = Sync(targets=["a", "b"])
    assert op.targets == ["a", "b"]


def test_sync_bus_attrs_is_targets():
    assert Sync.BUS_ATTRS == ("targets",)


def test_set_frequency_construction():
    op = SetFrequency("bus", 5e9)
    assert op.bus == "bus"
    assert op.frequency == 5e9


def test_set_phase_construction():
    op = SetPhase("bus", 1.5708)
    assert op.bus == "bus"
    assert op.phase == 1.5708


def test_reset_phase_construction():
    op = ResetPhase("bus")
    assert op.bus == "bus"


def test_set_gain_construction():
    op = SetGain("bus", 0.5)
    assert op.bus == "bus"
    assert op.gain == 0.5


def test_set_offset_one_path():
    op = SetOffset("bus", 0.1)
    assert op.offset_path0 == 0.1
    assert op.offset_path1 is None


def test_set_offset_two_paths():
    op = SetOffset("bus", 0.1, 0.2)
    assert op.offset_path0 == 0.1
    assert op.offset_path1 == 0.2


def test_set_parameter_construction():
    op = SetParameter("cluster", "lo_freq", 5e9)
    assert op.bus == "cluster"
    assert op.parameter == "lo_freq"
    assert op.value == 5e9


def test_set_parameter_bus_attrs():
    assert SetParameter.BUS_ATTRS == ("bus",)


def test_get_parameter_construction():
    v = Variable("result")
    op = GetParameter(variable=v, bus="cluster", parameter="lo_freq")
    assert op.variable is v
    assert op.bus == "cluster"
    assert op.parameter == "lo_freq"


def test_get_parameter_bus_attrs():
    assert GetParameter.BUS_ATTRS == ("bus",)


# ---------------------------------------------------------------------------
# Introspection: variables()
# ---------------------------------------------------------------------------


def test_play_variables_from_waveform():
    v = Variable("amp")
    wf = Gaussian(amplitude=v, duration=40, sigma=8)
    op = Play("bus", wf)
    assert op.variables() == {v}


def test_wait_variables_from_duration():
    v = Variable("dur")
    op = Wait("bus", v)
    assert op.variables() == {v}


def test_wait_no_variables_with_literal():
    assert Wait("bus", 100).variables() == set()


def test_measure_variables_from_waveform_and_weights():
    v = Variable("amp")
    wf = IQPair(I=Gaussian(amplitude=v, duration=2000, sigma=400), Q=Square(0.0, 2000))
    op = Measure("bus", wf, "w", handle=MeasurementHandle("m0"))
    assert v in op.variables()


def test_set_frequency_variables():
    v = Variable("freq")
    assert SetFrequency("bus", v).variables() == {v}


def test_set_phase_variables():
    v = Variable("phase")
    assert SetPhase("bus", v).variables() == {v}


def test_set_gain_variables():
    v = Variable("gain")
    assert SetGain("bus", v).variables() == {v}


def test_set_offset_variables_both_paths():
    v = Variable("o0")
    w = Variable("o1")
    op = SetOffset("bus", v, w)
    assert op.variables() == {v, w}


def test_set_parameter_variables():
    v = Variable("value")
    op = SetParameter("bus", "param", v)
    assert op.variables() == {v}


def test_get_parameter_variables_returns_target():
    v = Variable("result")
    op = GetParameter(variable=v, bus="a", parameter="p")
    assert op.variables() == {v}


def test_sync_no_variables():
    assert Sync(["a", "b"]).variables() == set()


def test_reset_phase_no_variables():
    assert ResetPhase("bus").variables() == set()


# ---------------------------------------------------------------------------
# Introspection: buses()
# ---------------------------------------------------------------------------


def test_play_buses():
    assert Play("q0/drive", Square(0.5, 100)).buses() == {"q0/drive"}


def test_wait_buses():
    assert Wait("q0/drive", 100).buses() == {"q0/drive"}


def test_sync_buses_from_targets_list():
    assert Sync(["a", "b", "c"]).buses() == {"a", "b", "c"}


def test_sync_buses_empty_when_targets_none():
    assert Sync().buses() == set()


def test_set_parameter_buses():
    assert SetParameter("bus", "param", 1.0).buses() == {"bus"}


def test_get_parameter_buses():
    assert GetParameter(Variable("v"), "bus", "param").buses() == {"bus"}


# ---------------------------------------------------------------------------
# Introspection: waveforms()
# ---------------------------------------------------------------------------


def test_play_waveforms():
    wf = Square(0.5, 100)
    assert Play("bus", wf).waveforms() == {wf}


def test_measure_waveforms():
    op = Measure("bus", "wf_alias", "weights_alias", handle=MeasurementHandle("m0"))
    assert op.waveforms() == {"wf_alias", "weights_alias"}


def test_wait_no_waveforms():
    assert Wait("bus", 100).waveforms() == set()


# ---------------------------------------------------------------------------
# Introspection: walk()
# ---------------------------------------------------------------------------


def test_operation_walk_yields_self_only():
    op = Wait("bus", 100)
    assert list(op.walk()) == [op]


# ---------------------------------------------------------------------------
# MeasurementField / normalize_fields
# ---------------------------------------------------------------------------


def test_measurement_field_members_are_strings():
    assert MeasurementField.IQ == "iq"
    assert str(MeasurementField.STATE) == "state"
    assert f"{MeasurementField.RAW}" == "raw"
    assert hash(MeasurementField.IQ) == hash("iq")


def test_measurement_field_declaration_order_is_canonical_order():
    assert [str(f) for f in MeasurementField] == ["state", "iq", "raw"]


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (["iq"], ("iq",)),
        (("iq",), ("iq",)),
        ({"iq"}, ("iq",)),
        (iter(["iq", "raw"]), ("iq", "raw")),
        (["iq", "raw"], ("iq", "raw")),
        (["raw", "iq"], ("iq", "raw")),  # sorted into canonical order
        (["raw", "iq", "state"], ("state", "iq", "raw")),
        ([MeasurementField.RAW, MeasurementField.STATE], ("state", "raw")),
        ([MeasurementField.IQ, "iq"], ("iq",)),  # deduplicated across spellings
    ],
)
def test_normalize_fields(input_val, expected):
    assert normalize_fields(input_val) == expected


def test_normalize_fields_returns_plain_strings():
    """Storage is uniform ``str`` however the caller spelled it — enum members flatten."""
    result = normalize_fields([MeasurementField.IQ])
    assert [type(f) for f in result] == [str]


def test_normalize_fields_bare_string_raises_with_tuple_suggestion():
    with pytest.raises(ValidationError, match=r'Pass a tuple: fields=\("iq", "state"\)'):
        normalize_fields("iq,state")


def test_normalize_fields_single_bare_string_raises():
    with pytest.raises(ValidationError, match=r'fields=\("iq",\)'):
        normalize_fields("iq")


def test_normalize_fields_empty_iterable_raises():
    with pytest.raises(ValidationError, match="at least one"):
        normalize_fields([])


def test_normalize_fields_non_iterable_raises():
    with pytest.raises(ValidationError, match="must be an iterable"):
        normalize_fields(42)


def test_normalize_fields_non_string_entry_raises():
    with pytest.raises(ValidationError, match="MeasurementField values or field-name strings"):
        normalize_fields([1])


def test_normalize_fields_empty_entry_raises():
    with pytest.raises(ValidationError, match="non-empty"):
        normalize_fields([""])


def test_normalize_fields_unknown_field_raises():
    with pytest.raises(ValidationError, match="unknown measurement field"):
        normalize_fields(["nope"])


def test_normalize_fields_unknown_field_suggests_close_match():
    with pytest.raises(ValidationError, match="Did you mean 'state'"):
        normalize_fields(["stat"])


def test_normalize_fields_error_lists_known_fields():
    with pytest.raises(ValidationError, match=r"Known fields:.*'iq'"):
        normalize_fields(["zzz"])


def test_normalize_fields_accepts_vendor_registered_field():
    """A field is legal as soon as its ``measure.fields.*`` token is registered."""
    register_capability_tokens("measure.fields.counts")
    try:
        assert normalize_fields(["counts", "iq"]) == ("iq", "counts")
    finally:
        CAPABILITY_REGISTRY.discard("measure.fields.counts")


def test_normalize_fields_sorts_vendor_fields_after_core_alphabetically():
    register_capability_tokens("measure.fields.zeta", "measure.fields.alpha")
    try:
        assert normalize_fields(["zeta", "raw", "alpha", "state"]) == ("state", "raw", "alpha", "zeta")
    finally:
        CAPABILITY_REGISTRY.discard("measure.fields.zeta")
        CAPABILITY_REGISTRY.discard("measure.fields.alpha")


# ---------------------------------------------------------------------------
# _collect_variables helper
# ---------------------------------------------------------------------------


def test_collect_variables_from_variable():
    v = Variable("x")
    assert _collect_variables(v) == {v}


def test_collect_variables_from_expression():
    v = Variable("x")
    expr = v + 5
    assert _collect_variables(expr) == {v}


def test_collect_variables_from_waveform():
    v = Variable("amp")
    wf = Gaussian(amplitude=v, duration=40, sigma=8)
    assert _collect_variables(wf) == {v}


def test_collect_variables_from_list():
    v = Variable("a")
    w = Variable("b")
    assert _collect_variables([v, w]) == {v, w}


def test_collect_variables_from_tuple():
    v = Variable("a")
    w = Variable("b")
    assert _collect_variables((v, w)) == {v, w}


def test_collect_variables_from_unrelated_type():
    assert _collect_variables("not a variable") == set()
    assert _collect_variables(42) == set()
    assert _collect_variables(None) == set()


def test_collect_variables_from_nested_structures():
    v = Variable("a")
    w = Variable("b")
    nested = [Gaussian(amplitude=v, duration=40, sigma=8), w]
    assert _collect_variables(nested) == {v, w}


# ---------------------------------------------------------------------------
# MeasurementOperation marker
# ---------------------------------------------------------------------------


def test_measurement_operation_marker():
    op = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"))
    assert isinstance(op, MeasurementOperation)


def test_non_measurement_operation_not_marker():
    assert not isinstance(Wait("bus", 100), MeasurementOperation)


# ---------------------------------------------------------------------------
# Structural equality on operations
# ---------------------------------------------------------------------------


def test_play_structural_equality():
    a = Play("bus", Square(0.5, 100))
    b = Play("bus", Square(0.5, 100))
    assert a == b
    assert hash(a) == hash(b)


def test_measure_structural_equality():
    a = Measure("b", "w", "wt", handle=MeasurementHandle("m0"))
    b = Measure("b", "w", "wt", handle=MeasurementHandle("m0"))
    assert a == b


def test_play_inequality_different_bus():
    assert Play("b1", Square(0.5, 100)) != Play("b2", Square(0.5, 100))


def test_play_inequality_different_waveform():
    assert Play("b", Square(0.5, 100)) != Play("b", Square(0.6, 100))


def test_different_operation_classes_never_equal():
    assert Play("b", Square(0.5, 100)) != Wait("b", 100)


def test_unequal_to_non_operation():
    assert Play("b", Square(0.5, 100)) != "not an op"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_measure_fields_default_constant_singleton():
    """Two Measure ops with default fields share the tuple value (not necessarily identity)."""
    a = Measure("b", "w", "wt", handle=MeasurementHandle("m0"))
    b = Measure("b", "w", "wt", handle=MeasurementHandle("m1"))
    assert a.fields == b.fields == ("iq",)


def test_measurement_handle_in_kwargs():
    """A MeasurementHandle carries its name as a plain, inspectable value."""
    h = MeasurementHandle("q0_m0")
    assert h.name == "q0_m0"
