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
"""Tests for the reference software executor — shapes, values, feedback, and the execution convention."""

from __future__ import annotations

import numpy as np
import pytest

import qprogram as qp
from qprogram import (
    BusSchema,
    ExecutionWarning,
    Fragment,
    MeasurementField,
    MockMeasurementModel,
    QProgram,
    ReferencePlatform,
    UnsupportedOperationError,
    fragment,
    simulate,
)
from qprogram.errors import UnassignedVariableError
from qprogram.sweeps import Range, Values
from qprogram.waveforms import Gaussian, IQPair, Square

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ro() -> IQPair:
    return IQPair(Square(1.0, 100), Square(0.0, 100))


def _unit_response(bus: str, env: dict) -> complex:  # ruff: ignore[unused-function-argument]
    return 1.0 + 2.0j


# ---------------------------------------------------------------------------
# Result shapes (spec §8)
# ---------------------------------------------------------------------------


def test_nested_sweep_dims_coords_and_range_value_formula():
    p = QProgram()
    freq = p.variable("freq")
    gain = p.variable("gain")
    with p.sweep(freq, Range(4e9, 5e9, 0.5e9)), p.sweep(gain, Range(0.0, 1.0, 0.25)):
        p.measure("readout_q0", _ro(), "w")
    da = simulate(p).get("m0")
    assert da.dims == ("freq", "gain", "IQ")
    assert da.shape == (3, 5, 2)
    assert np.allclose(da.coords["freq"].values, [4e9, 4.5e9, 5e9])
    assert np.allclose(da.coords["gain"].values, [0.0, 0.25, 0.5, 0.75, 1.0])
    assert list(da.coords["IQ"].values) == ["I", "Q"]


def test_arbitrary_loop_coords_are_the_values():
    p = QProgram()
    amp = p.variable("amp")
    values = np.array([0.1, 0.4, 0.9])
    with p.sweep(amp, Values(values)):
        p.measure("readout_q0", _ro(), "w")
    da = simulate(p).get("m0")
    assert da.dims == ("amp", "IQ")
    assert np.array_equal(da.coords["amp"].values, values)


def test_parallel_loops_share_one_dimension_with_both_coords():
    p = QProgram()
    a = p.variable("a")
    b = p.variable("b")
    with p.sweep(a, Range(0.0, 1.0, 0.5)) | p.sweep(b, Range(10.0, 20.0, 5.0)):
        p.measure("readout_q0", _ro(), "w")
    da = simulate(p).get("m0")
    assert da.dims == ("a|b", "IQ")
    assert da.shape == (3, 2)
    assert np.allclose(da.coords["a"].values, [0.0, 0.5, 1.0])
    assert np.allclose(da.coords["b"].values, [10.0, 15.0, 20.0])


def test_no_loop_measurement_is_scalar():
    p = QProgram()
    p.measure("readout_q0", _ro(), "w", fields=("iq", "state"))
    result = simulate(p)
    assert result.get("m0").dims == ("IQ",)
    assert result.get("m0", field="state").dims == ()


def test_average_contributes_no_dimension():
    p = QProgram()
    g = p.variable("g")
    with p.average(50), p.sweep(g, Range(0.0, 1.0, 0.5)):
        p.measure("readout_q0", _ro(), "w")
    da = simulate(p).get("m0")
    assert da.dims == ("g", "IQ")
    assert da.shape == (3, 2)


def test_loop_inside_average_and_average_inside_loop():
    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0.0, 1.0, 0.5)), p.average(10):
        p.measure("readout_q0", _ro(), "w")
    assert simulate(p).get("m0").dims == ("g", "IQ")


def test_return_token_shapes():
    model = MockMeasurementModel(raw_samples=8)
    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0.0, 1.0, 0.5)):
        p.measure("readout_q0", _ro(), "w", fields=("iq", "state", "raw"))
    result = simulate(p, model=model)
    assert result.get("m0", field="iq").dims == ("g", "IQ")
    assert result.get("m0", field="state").dims == ("g",)
    raw = result.get("m0", field="raw")
    assert raw.dims == ("g", "time", "IQ")
    assert raw.shape == (3, 8, 2)
    assert np.array_equal(raw.coords["time"].values, np.arange(8))


def test_primary_data_is_iq_when_requested_else_first_token():
    p = QProgram()
    p.measure("readout_q0", _ro(), "w", fields=("state", "iq"))
    result = simulate(p)
    assert result.get("m0").dims == ("IQ",)  # iq is primary even when listed second
    p2 = QProgram()
    p2.measure("readout_q0", _ro(), "w", fields=("state",))
    assert simulate(p2).measurements[0].data.dims == ()  # no iq requested -> first token


def test_get_defaults_to_iq_and_never_substitutes_another_field():
    p = QProgram()
    p.measure("readout_q0", _ro(), "w", fields=("state",))
    result = simulate(p)
    # ``data`` is the state array (the only field requested), but ``get`` asks for iq by default
    # and says so rather than handing back the state array under the wrong name.
    with pytest.raises(KeyError, match=r"no field 'iq'.*available: state"):
        result.get("m0")
    assert result.get("m0", field=MeasurementField.STATE).dims == ()


def test_get_field_miss_names_available_fields():
    p = QProgram()
    p.measure("readout_q0", _ro(), "w", fields=("iq",))
    result = simulate(p)
    with pytest.raises(KeyError, match=r"no field 'raw'.*available: iq"):
        result.get("m0", field="raw")


def test_multiple_measurements_in_declaration_order():
    p = QProgram()
    p.measure("readout_q0", _ro(), "w")
    p.measure("readout_q1", _ro(), "w")
    result = simulate(p)
    assert [m.name for m in result.measurements] == ["m0", "m1"]
    assert result.get(0, bus="readout_q1").dims == ("IQ",)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def test_rabi_response_values_exact_with_zero_noise():
    model = MockMeasurementModel(response=lambda bus, env: np.sin(np.pi * env["g"] / 2) ** 2 + 0j)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    g = p.variable("g")
    with p.average(100), p.sweep(g, Range(0.0, 1.0, 0.25)):
        p.play("drive_q0", Gaussian(amplitude=g, duration=40, sigma=8))
        p.measure("readout_q0", _ro(), "w")
    da = simulate(p, model=model).get("m0")
    expected = np.sin(np.pi * np.array([0.0, 0.25, 0.5, 0.75, 1.0]) / 2) ** 2
    assert np.allclose(da.sel(IQ="I").values, expected)
    assert np.allclose(da.sel(IQ="Q").values, 0.0)


def test_state_is_population_under_averaging():
    model = MockMeasurementModel(p_excited=lambda bus, env: 0.5, seed=7)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    with p.average(2000):
        p.measure("readout_q0", _ro(), "w", fields=("state",))
    population = float(simulate(p, model=model).get("m0", field="state"))
    assert 0.4 < population < 0.6  # ~Binomial(2000, 0.5) / 2000


def test_state_is_binary_without_averaging():
    model = MockMeasurementModel(p_excited=lambda bus, env: 1.0)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    p.measure("readout_q0", _ro(), "w", fields=("state",))
    assert float(simulate(p, model=model).get("m0", field="state")) == 1.0


def test_seeded_noise_is_deterministic():
    def build() -> QProgram:
        p = QProgram()
        g = p.variable("g")
        with p.average(10), p.sweep(g, Range(0.0, 1.0, 0.5)):
            p.measure("readout_q0", _ro(), "w")
        return p

    a = simulate(build(), model=MockMeasurementModel(noise=0.1, seed=42)).get("m0")
    b = simulate(build(), model=MockMeasurementModel(noise=0.1, seed=42)).get("m0")
    assert np.array_equal(a.values, b.values)
    c = simulate(build(), model=MockMeasurementModel(noise=0.1, seed=43)).get("m0")
    assert not np.array_equal(a.values, c.values)


def test_model_env_includes_parameters_and_loop_variables():
    seen: list[dict] = []

    def response(bus: str, env: dict) -> complex:  # ruff: ignore[unused-function-argument]
        seen.append(dict(env))
        return 0j

    p = QProgram()
    p.set_parameter("cluster", "lo", 5e9)
    g = p.variable("g")
    with p.sweep(g, Range(0.0, 1.0, 1.0)):
        p.measure("readout_q0", _ro(), "w")
    simulate(p, model=MockMeasurementModel(response=response))
    assert seen[0]["cluster.lo"] == 5e9
    assert [env["g"] for env in seen] == [0.0, 1.0]


# ---------------------------------------------------------------------------
# Feedback and conditionals
# ---------------------------------------------------------------------------


def test_measurement_state_drives_conditional():
    model = MockMeasurementModel(p_excited=lambda bus, env: 1.0)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    m = p.measure("readout_q0", _ro(), "w", fields=("iq", "state"))
    with p.if_(m.state == 1):
        p.measure("readout_q1", _ro(), "w")  # m1: runs (always excited)
    with p.if_(m.state == 0):
        p.measure("readout_q2", _ro(), "w")  # m2: never runs
    result = simulate(p, model=model)
    assert not np.isnan(result.get("m1").values).any()
    assert np.isnan(result.get("m2").values).all()


def test_conditional_measurement_nan_at_unexecuted_points():
    # Excited only when g >= 0.5 -> the reset-arm measurement is NaN for the first two points.
    model = MockMeasurementModel(p_excited=lambda bus, env: float(env["g"] >= 0.5))  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0.0, 1.0, 0.25)):
        m = p.measure("readout_q0", _ro(), "w", fields=("iq", "state"))
        with p.if_(m.state == 1):
            p.measure("readout_q1", _ro(), "w")
    arm = simulate(p, model=model).get("m1").sel(IQ="I").values
    assert np.isnan(arm[:2]).all()
    assert not np.isnan(arm[2:]).any()


def test_else_arm_executes_when_no_condition_holds():
    model = MockMeasurementModel(p_excited=lambda bus, env: 0.0)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    m = p.measure("readout_q0", _ro(), "w", fields=("iq", "state"))
    with p.if_(m.state == 1):
        p.measure("readout_q1", _ro(), "w")
    with p.else_():
        p.measure("readout_q2", _ro(), "w")
    result = simulate(p, model=model)
    assert np.isnan(result.get("m1").values).all()
    assert not np.isnan(result.get("m2").values).any()


def test_condition_before_measurement_raises_unassigned():
    p = QProgram()
    handle = qp.MeasurementHandle("never_measured")
    cond = qp.Comparison("==", qp.MeasurementRef(handle, "state"), qp.Constant(0))
    p._validate_conditional_condition(cond, where="if_")
    with p.if_(cond):
        p.sync()
    p.measure("readout_q0", _ro(), "w", fields=("iq", "state"), name="never_measured_late")
    with pytest.raises(UnsupportedOperationError, match="unknown-measurement"):
        simulate(p)


# ---------------------------------------------------------------------------
# Variables, expressions, parameters
# ---------------------------------------------------------------------------


def test_op_expressions_are_evaluated_each_iteration():
    p = QProgram()
    t = p.variable("t")
    with p.sweep(t, Range(0.0, 10.0, 5.0)):
        p.wait("drive_q0", t + 4)
        p.measure("readout_q0", _ro(), "w")
    simulate(p)  # no raise: t is bound by the loop


def test_unassigned_variable_in_op_raises():
    p = QProgram()
    t = p.variable("t")
    p.wait("drive_q0", t + 4)  # t never bound by any loop
    with pytest.raises(UnassignedVariableError):
        simulate(p)


def test_set_get_parameter_round_trip_through_store():
    model = MockMeasurementModel(response=lambda bus, env: env.get("cluster.lo", -1.0) + 0j)  # ruff: ignore[unused-lambda-argument]
    p = QProgram()
    p.set_parameter("cluster", "lo", 7.5)
    read = p.get_parameter("cluster", "lo")
    p.wait("drive_q0", read + 1)  # the read-back value is usable in expressions
    p.measure("readout_q0", _ro(), "w")
    assert float(simulate(p, model=model).get("m0").sel(IQ="I")) == 7.5


def test_platform_initial_parameters_visible_to_get_parameter():
    platform = ReferencePlatform(parameters={"cluster.lo": 3.0})
    p = QProgram()
    read = p.get_parameter("cluster", "lo")
    p.set_frequency("drive_q0", read)
    p.measure("readout_q0", _ro(), "w")
    platform.execute(p)
    assert platform.parameters["cluster.lo"] == 3.0
    assert platform.get_global_parameters() == ["cluster.lo"]
    assert platform.get_parameters("cluster") == ["lo"]


# ---------------------------------------------------------------------------
# The execution convention
# ---------------------------------------------------------------------------


def test_execute_raises_on_error_diagnostics():
    # A Conditional referencing a measurement that doesn't exist is an error diagnostic; the
    # platform follows the convention and raises before interpreting anything.
    p = QProgram()
    handle = qp.MeasurementHandle("ghost")
    cond = qp.Comparison("==", qp.MeasurementRef(handle, "state"), qp.Constant(0))
    with p.if_(cond):
        p.sync()
    with pytest.raises(UnsupportedOperationError, match="unknown-measurement"):
        simulate(p)


def test_forced_host_surfaces_as_execution_warning():
    p = QProgram()
    v = p.variable("v")
    with p.average(10), p.sweep(v, Range(0.0, 1.0, 0.5)):
        p.set_frequency("drive_q0", v)
        p.set_parameter("cluster", "lo", v)
        p.measure("readout_q0", _ro(), "w")
    with pytest.warns(ExecutionWarning, match="forced-host.*'lo' is swept"):
        result = simulate(p)
    assert result.get("m0").dims == ("v", "IQ")


def test_explain_against_reference_platform():
    p = QProgram(label="ref")
    p.play("drive_q0", "pi")
    out = ReferencePlatform().explain(p)
    assert out.splitlines()[0].startswith("plan for 'ref'")
    assert "[rt|host]" in out


# ---------------------------------------------------------------------------
# Fragments, vendor ops, round-trip
# ---------------------------------------------------------------------------


def test_fragments_expand_before_execution():
    @fragment
    def readout(f, ro):
        f.sync()
        f.measure(ro, IQPair(Square(1.0, 100), Square(0.0, 100)), "w")

    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0.0, 1.0, 0.5)):
        p.call(readout, "readout_q0")
    da = simulate(p).get("m0")
    assert da.dims == ("g", "IQ")


def test_fragment_repeated_call_measurements_recorded_separately():
    frag = Fragment("ro")
    bus = frag.parameter("bus")
    frag.measure(bus, IQPair(Square(1.0, 100), Square(0.0, 100)), "w")
    p = QProgram()
    p.call(frag, "readout_q0")
    p.call(frag, "readout_q1")
    result = simulate(p)
    assert [m.name for m in result.measurements] == ["m0", "m0_2"]
    assert [m.bus for m in result.measurements] == ["readout_q0", "readout_q1"]


def test_dummy_vendor_measurement_op_executes_generically(dummy_vendor):  # ruff: ignore[unused-function-argument]

    p = QProgram()
    handle = p.dummy.acquire("readout_q0", "weights")
    assert handle is not None
    result = simulate(p)
    assert len(result.measurements) == 1
    assert result.measurements[0].bus == "readout_q0"


def test_loaded_program_executes_identically():
    def model() -> MockMeasurementModel:
        return MockMeasurementModel(
            response=lambda bus, env: env["g"] + 0j,  # ruff: ignore[unused-lambda-argument]
            noise=0.05,
            seed=11,
        )

    p = QProgram()
    g = p.variable("g")
    with p.average(20), p.sweep(g, Range(0.0, 1.0, 0.5)):
        p.measure("readout_q0", _ro(), "w")
    reloaded = qp.loads(qp.dumps(p))
    a = simulate(p, model=model()).get("m0")
    b = simulate(reloaded, model=model()).get("m0")
    assert np.array_equal(a.values, b.values)
    assert a.dims == b.dims


# ---------------------------------------------------------------------------
# Platform waveform-library resolution
# ---------------------------------------------------------------------------


def _alias_program(schema: BusSchema) -> QProgram:
    q = schema.q
    p = QProgram(schema=schema)
    p.play(q[0].drive, "pi")
    p.measure(q[0].readout, "ro", "w")
    return p


def test_alias_only_program_runs_without_a_library():
    schema = BusSchema.transmon()
    # No library: aliases are left as-is and the reference model no-ops them.
    simulate(_alias_program(schema), schema=schema)


# ---------------------------------------------------------------------------
# Measurement model contract
# ---------------------------------------------------------------------------


class _NoRawModel:
    """A model with no ADC to simulate, leaving `MeasurementSample.raw` at its default."""

    def sample(self, bus: str, env: dict) -> qp.MeasurementSample:  # ruff: ignore[unused-method-argument]
        return qp.MeasurementSample(i=1.0, q=2.0, state=0)


class _BroadcastableRawModel:
    """A model returning a single (I, Q) pair where a full trace was required."""

    raw_samples = 4

    def sample(self, bus: str, env: dict) -> qp.MeasurementSample:  # ruff: ignore[unused-method-argument]
        return qp.MeasurementSample(i=1.0, q=2.0, state=0, raw=np.array([1.0, 2.0]))


class _ListRawModel:
    """A model whose trace is a nested list — array-like, so it must still be accepted."""

    raw_samples = 4

    def sample(self, bus: str, env: dict) -> qp.MeasurementSample:  # ruff: ignore[unused-method-argument]
        return qp.MeasurementSample(i=1.0, q=2.0, state=0, raw=[[1.0, 2.0]] * 4)


def _measured(model, fields):
    p = QProgram()
    handle = p.measure("readout_q0", _ro(), "w", fields=fields)
    return qp.simulate(p, model=model), handle


def test_measurement_sample_raw_defaults_to_an_empty_trace():
    """A model that simulates no ADC should not have to invent a filler array."""
    sample = qp.MeasurementSample(i=1.0, q=2.0, state=1)
    assert sample.raw.shape == (0, 2)
    # The field stays last and positional construction is unaffected.
    assert qp.MeasurementSample(1.0, 2.0, 1).raw.shape == (0, 2)


def test_model_omitting_raw_runs_when_raw_is_not_requested():
    result, handle = _measured(_NoRawModel(), (MeasurementField.IQ,))
    assert list(result.get(handle).values) == [1.0, 2.0]


def test_requesting_raw_from_a_model_that_omits_it_names_the_mismatch():
    with pytest.raises(ValueError, match=r"returned a trace of shape \(0, 2\); expected \(16, 2\)"):
        _measured(_NoRawModel(), (MeasurementField.RAW,))


def test_raw_trace_numpy_would_broadcast_is_rejected():
    """A (2,) trace used to broadcast across every time sample and return a wrong result in silence."""
    with pytest.raises(ValueError, match=r"_BroadcastableRawModel\.sample returned a trace of shape"):
        _measured(_BroadcastableRawModel(), (MeasurementField.RAW,))


def test_raw_accepts_a_trace_that_is_not_an_ndarray():
    """The shape check goes through `np.shape`, so a nested list is still a valid trace."""
    result, handle = _measured(_ListRawModel(), (MeasurementField.RAW,))
    assert result.get(handle, field=MeasurementField.RAW).shape == (4, 2)


def test_mock_measurement_model_trace_matches_its_raw_samples():
    """The built-in model must never trip the check it is measured against."""
    result, handle = _measured(MockMeasurementModel(response=_unit_response, raw_samples=8), (MeasurementField.RAW,))
    assert result.get(handle, field=MeasurementField.RAW).shape == (8, 2)
