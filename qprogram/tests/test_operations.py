"""Tests for every core Operation + the introspection contract."""

from __future__ import annotations

import pytest

from qprogram import (
    CrosstalkMatrix,
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
    SetCrosstalk,
    SetFrequency,
    SetGain,
    SetOffset,
    SetParameter,
    SetPhase,
    Sync,
    Wait,
)
from qprogram.operations.operation import (
    MeasurementOperation,
    _collect_variables,
    normalize_returns,
)
from qprogram.waveforms import Gaussian, Square

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
    assert op.returns == ("iq",)


def test_measure_waveform_attrs():
    assert Measure.WAVEFORM_ATTRS == ("waveform", "weights")


def test_measure_returns_string_input():
    op = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"), returns="iq,raw")
    assert op.returns == ("iq", "raw")


def test_measure_returns_tuple_input():
    op = Measure("readout", "wf", "w", handle=MeasurementHandle("m0"), returns=("iq", "raw"))
    assert op.returns == ("iq", "raw")


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
    assert op.alias == "cluster"
    assert op.parameter == "lo_freq"
    assert op.value == 5e9
    assert op.channel_id is None


def test_set_parameter_with_channel_id():
    op = SetParameter("cluster", "lo_freq", 5e9, channel_id=3)
    assert op.channel_id == 3


def test_set_parameter_bus_attrs_empty():
    assert SetParameter.BUS_ATTRS == ()


def test_get_parameter_construction():
    v = Variable("result")
    op = GetParameter(variable=v, alias="cluster", parameter="lo_freq")
    assert op.variable is v
    assert op.alias == "cluster"
    assert op.parameter == "lo_freq"
    assert op.channel_id is None


def test_get_parameter_with_channel_id():
    v = Variable("result")
    op = GetParameter(variable=v, alias="cluster", parameter="lo_freq", channel_id=2)
    assert op.channel_id == 2


def test_get_parameter_bus_attrs_empty():
    assert GetParameter.BUS_ATTRS == ()


def test_set_crosstalk_construction():
    m = CrosstalkMatrix()
    op = SetCrosstalk(crosstalk=m)
    assert op.crosstalk is m


def test_set_crosstalk_bus_attrs_empty():
    assert SetCrosstalk.BUS_ATTRS == ()


# ---------------------------------------------------------------------------
# Introspection: variables()
# ---------------------------------------------------------------------------


def test_play_variables_from_waveform():
    v = Variable("amp")
    wf = Gaussian(amplitude=v, duration=40, num_sigmas=2.5)
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
    wf = Gaussian(amplitude=v, duration=2000, num_sigmas=2.5)
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
    op = SetParameter("alias", "param", v)
    assert op.variables() == {v}


def test_get_parameter_variables_returns_target():
    v = Variable("result")
    op = GetParameter(variable=v, alias="a", parameter="p")
    assert op.variables() == {v}


def test_sync_no_variables():
    assert Sync(["a", "b"]).variables() == set()


def test_reset_phase_no_variables():
    assert ResetPhase("bus").variables() == set()


def test_set_crosstalk_no_variables():
    assert SetCrosstalk(CrosstalkMatrix()).variables() == set()


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


def test_set_parameter_buses_empty():
    assert SetParameter("alias", "param", 1.0).buses() == set()


def test_get_parameter_buses_empty():
    assert GetParameter(Variable("v"), "alias", "param").buses() == set()


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
# normalize_returns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("iq", ("iq",)),
        ("iq,raw", ("iq", "raw")),
        ("iq, raw", ("iq", "raw")),  # whitespace tolerated
        (" iq , raw ", ("iq", "raw")),
        (["iq"], ("iq",)),
        (["iq", "raw"], ("iq", "raw")),
        (("iq",), ("iq",)),
        (("iq", "raw"), ("iq", "raw")),
    ],
)
def test_normalize_returns(input_val, expected):
    assert normalize_returns(input_val) == expected


def test_normalize_returns_drops_empty_entries():
    assert normalize_returns("iq,,raw,") == ("iq", "raw")


def test_normalize_returns_empty_string_raises():
    with pytest.raises(ValidationError, match="at least one"):
        normalize_returns("")


def test_normalize_returns_empty_list_raises():
    with pytest.raises(ValidationError, match="at least one"):
        normalize_returns([])


def test_normalize_returns_only_whitespace_raises():
    with pytest.raises(ValidationError, match="at least one"):
        normalize_returns(",  ,")


def test_normalize_returns_preserves_order():
    assert normalize_returns("c,b,a") == ("c", "b", "a")


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
    wf = Gaussian(amplitude=v, duration=40, num_sigmas=2.5)
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
    nested = [Gaussian(amplitude=v, duration=40, num_sigmas=2.5), w]
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
# Structural equality on operations (from §11)
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


def test_measure_returns_default_constant_singleton():
    """Two Measure ops with default returns share the tuple value (not necessarily identity)."""
    a = Measure("b", "w", "wt", handle=MeasurementHandle("m0"))
    b = Measure("b", "w", "wt", handle=MeasurementHandle("m1"))
    assert a.returns == b.returns == ("iq",)


def test_measurement_handle_in_kwargs():
    """MeasurementHandle as a value: confirm we can still inspect."""
    h = MeasurementHandle("q0_m0")
    assert h.name == "q0_m0"
