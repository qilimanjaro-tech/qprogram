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
"""Tests for fragments — builder API, ``@fragment`` decorator, call binding, and expansion semantics."""

from __future__ import annotations

import numpy as np
import pytest

import qprogram as qp
from qprogram import Fragment, Parameter, QProgram, fragment
from qprogram.buses import BusSchema
from qprogram.errors import ValidationError
from qprogram.operations import Call, Measure, Play, Sync, Wait
from qprogram.sweeps import Range, Values
from qprogram.variable import BinaryOp, Constant, Variable
from qprogram.waveforms import FlatTop, Gaussian, IQPair, Square

# ---------------------------------------------------------------------------
# Fragment construction (explicit API)
# ---------------------------------------------------------------------------


def test_fragment_name_and_params():
    frag = Fragment("x_pulse")
    drive = frag.parameter("drive")
    amp = frag.parameter("amp")
    assert frag.name == "x_pulse"
    assert frag.params == (drive, amp)
    assert isinstance(drive, Parameter)
    assert isinstance(drive, Variable)  # params participate in expressions


def test_fragment_invalid_name_rejected():
    with pytest.raises(ValidationError, match="invalid"):
        Fragment("bad name")
    with pytest.raises(ValidationError, match="reserved"):
        Fragment("fragment")
    with pytest.raises(ValidationError, match="reserved"):
        Fragment("def")


def test_duplicate_parameter_rejected():
    frag = Fragment("f")
    frag.parameter("a")
    with pytest.raises(ValidationError, match="already declared"):
        frag.parameter("a")


def test_parameter_collides_with_local_variable():
    frag = Fragment("f")
    frag.variable("a")
    with pytest.raises(ValidationError, match="collides with a local variable"):
        frag.parameter("a")


def test_local_variable_collides_with_parameter():
    frag = Fragment("f")
    frag.parameter("a")
    with pytest.raises(ValidationError, match="collides with a parameter"):
        frag.variable("a")


def test_fragment_builder_is_full_qprogram_surface():
    frag = Fragment("full")
    bus = frag.parameter("bus")
    t = frag.variable("t")
    with frag.average(10), frag.sweep(t, Range(0, 100, 10)):
        frag.play(bus, "wf")
        frag.wait(bus, t)
        frag.sync()
    assert len(frag.body.elements) == 1  # the average block


def test_fragment_structural_equality():
    def build() -> Fragment:
        frag = Fragment("f")
        b = frag.parameter("b")
        frag.play(b, Square(0.5, 100))
        return frag

    a = build()
    b = build()
    assert a == b
    other = build()
    other.wait("x", 4)
    assert build() != other
    renamed = Fragment("g")
    renamed.parameter("b")
    renamed.play(renamed.params[0], Square(0.5, 100))
    assert build() != renamed


# ---------------------------------------------------------------------------
# @fragment decorator
# ---------------------------------------------------------------------------


def test_decorator_builds_equivalent_fragment():
    @fragment
    def x_pulse(f, drive, amp):
        f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))

    explicit = Fragment("x_pulse")
    drive = explicit.parameter("drive")
    amp = explicit.parameter("amp")
    explicit.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))

    assert isinstance(x_pulse, Fragment)
    assert x_pulse == explicit
    assert [p.id for p in x_pulse.params] == ["drive", "amp"]


def test_decorator_zero_arg_signature_rejected():
    with pytest.raises(ValidationError, match="first parameter"):

        @fragment
        def broken():  # pragma: no cover — decorator raises before the body could run
            pass


def test_decorator_varargs_rejected():
    with pytest.raises(ValidationError, match="positional parameters"):

        @fragment
        def broken(f, *args):  # pragma: no cover
            pass


def test_decorator_kwargs_rejected():
    with pytest.raises(ValidationError, match="positional parameters"):

        @fragment
        def broken(f, **kw):  # pragma: no cover
            pass


def test_decorator_default_rejected():
    with pytest.raises(ValidationError, match="default value"):

        @fragment
        def broken(f, amp=0.5):  # pragma: no cover
            pass


def test_decorator_keyword_only_rejected():
    with pytest.raises(ValidationError, match="positional parameters"):

        @fragment
        def broken(f, *, amp):  # pragma: no cover
            pass


def test_decorator_nested_call():
    @fragment
    def inner(f, bus):
        f.sync([bus])

    @fragment
    def outer(f, bus):
        f.call(inner, bus)

    assert "inner" in outer.fragments
    call = outer.body.elements[0]
    assert isinstance(call, Call)
    assert call.fragment is inner


# ---------------------------------------------------------------------------
# QProgram.call — binding and registration
# ---------------------------------------------------------------------------


def _two_param_fragment() -> Fragment:
    frag = Fragment("two")
    a = frag.parameter("a")
    b = frag.parameter("b")
    frag.set_frequency("bus", a)
    frag.set_gain("bus", b)
    return frag


def test_call_appends_call_node_with_bound_arguments():
    frag = _two_param_fragment()
    p = QProgram()
    p.call(frag, 1.0, b=2.0)
    call = p.body.elements[0]
    assert isinstance(call, Call)
    assert call.fragment is frag
    assert call.arguments == {"a": 1.0, "b": 2.0}
    assert p.fragments == {"two": frag}


def test_call_rejects_non_fragment():
    p = QProgram()
    with pytest.raises(ValidationError, match="expects a Fragment"):
        p.call("not a fragment")  # type: ignore[arg-type]


def test_call_too_many_positionals():
    p = QProgram()
    with pytest.raises(ValidationError, match="takes 2 argument"):
        p.call(_two_param_fragment(), 1, 2, 3)


def test_call_unknown_keyword():
    p = QProgram()
    with pytest.raises(ValidationError, match="no parameter 'c'"):
        p.call(_two_param_fragment(), 1, 2, c=3)


def test_call_duplicate_binding():
    p = QProgram()
    with pytest.raises(ValidationError, match="multiple values for parameter 'a'"):
        p.call(_two_param_fragment(), 1, a=2, b=3)


def test_call_missing_argument():
    p = QProgram()
    with pytest.raises(ValidationError, match=r"missing argument.*b"):
        p.call(_two_param_fragment(), 1)


def test_call_unsupported_argument_type():
    p = QProgram()
    with pytest.raises(ValidationError, match="unsupported argument type"):
        p.call(_two_param_fragment(), {"not": "allowed"}, 2)
    with pytest.raises(ValidationError, match="unsupported argument type"):
        p.call(_two_param_fragment(), True, 2)  # ruff: ignore[boolean-positional-value-in-call] — bool is exactly the rejected type


def test_call_self_rejected():
    frag = Fragment("selfish")
    with pytest.raises(ValidationError, match="cannot call itself"):
        frag.call(frag)


def test_call_registers_dependencies_first():
    inner = Fragment("inner")
    inner.sync()
    outer = Fragment("outer")
    outer.call(inner)
    p = QProgram()
    p.call(outer)
    assert list(p.fragments) == ["inner", "outer"]


def test_call_name_clash_rejected():
    first = Fragment("dup")
    first.sync()
    second = Fragment("dup")
    second.wait("b", 4)
    p = QProgram()
    p.call(first)
    with pytest.raises(ValidationError, match="different fragment named 'dup'"):
        p.call(second)


def test_call_cycle_detected_at_registration():
    a = Fragment("a")
    b = Fragment("b")
    a.call(b)  # b has no calls yet
    b.call(a)  # creates the cycle a -> b -> a (allowed here; detected on host use)
    p = QProgram()
    with pytest.raises(ValidationError, match="fragment call cycle"):
        p.call(a)


def test_call_schema_mismatch_rejected():
    schema1 = BusSchema.transmon()
    schema2 = BusSchema.transmon()
    frag = Fragment("ro")
    bus = frag.parameter("bus")
    frag.play(schema1.q[0].drive, IQPair(Square(1.0, 10), Square(0.0, 10)))
    frag.wait(bus, 4)
    p = QProgram(schema=schema2)
    with pytest.raises(ValidationError, match="different BusSchema"):
        p.call(frag, "raw")


def test_call_adopts_fragment_schema():
    schema = BusSchema.transmon()
    frag = Fragment("ro")
    frag.play(schema.q[0].drive, IQPair(Square(1.0, 10), Square(0.0, 10)))
    p = QProgram()
    p.call(frag)
    assert p.schema is schema


def test_vendor_namespace_works_on_fragment(dummy_vendor):  # ruff: ignore[unused-function-argument]
    frag = Fragment("uses_vendor")
    bus = frag.parameter("bus")
    frag.dummy.set_markers(bus, "0001")
    p = QProgram()
    p.call(frag, "drive_q0")
    expanded = p.expand()
    ops = [n for n in expanded.body.walk() if type(n).__name__ == "DummySetMarkers"]
    assert len(ops) == 1
    assert ops[0].bus == "drive_q0"


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


def test_expand_value_substitution_raw_and_expression():
    frag = Fragment("f")
    bus = frag.parameter("bus")
    t = frag.parameter("t")
    frag.wait(bus, t)  # bare param in value position -> raw binding
    frag.wait(bus, t + 4)  # param inside expression -> Constant-wrapped

    p = QProgram()
    p.call(frag, "drive", 100)
    body = p.expand().body.elements[0]
    wait_raw, wait_expr = body.elements
    assert isinstance(wait_raw, Wait)
    assert wait_raw.bus == "drive"
    assert wait_raw.duration == 100  # raw int, exactly as the builder would store
    assert isinstance(wait_expr.duration, BinaryOp)
    assert wait_expr.duration == Constant(100) + Constant(4)


def test_expand_host_variable_sweeps_through_fragment():
    frag = Fragment("f")
    amp = frag.parameter("amp")
    frag.play("drive", Gaussian(amplitude=amp, duration=40, sigma=8))

    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0, 1, 0.1)):
        p.call(frag, amp=g)
    expanded = p.expand()
    play = next(n for n in expanded.body.walk() if isinstance(n, Play))
    # The bound host Variable is substituted into the waveform attribute *by identity*,
    # so the runtime's per-iteration set_value() is visible to the expanded op.
    assert play.waveform.amplitude is expanded.variables[0]


def test_expand_waveform_binding():
    frag = Fragment("f")
    wf = frag.parameter("wf")
    frag.play("drive", wf)
    p = QProgram()
    p.call(frag, Gaussian(amplitude=0.5, duration=40, sigma=8))
    play = next(n for n in p.expand().body.walk() if isinstance(n, Play))
    assert play.waveform == Gaussian(amplitude=0.5, duration=40, sigma=8)


def test_expand_bus_binding_in_sync_targets():
    frag = Fragment("f")
    a = frag.parameter("a")
    b = frag.parameter("b")
    frag.sync([a, b])
    p = QProgram()
    p.call(frag, "bus0", "bus1")
    sync = next(n for n in p.expand().body.walk() if isinstance(n, Sync))
    assert sync.targets == ["bus0", "bus1"]


def test_expand_local_variable_hygiene_across_calls():
    frag = Fragment("scan")
    n = frag.variable("n")
    with frag.sweep(n, Range(0, 10, 1)):
        frag.wait("aux", n)

    p = QProgram()
    p.call(frag)
    p.call(frag)
    expanded = p.expand()
    ids = [v.id for v in expanded.variables]
    assert ids == ["scan_n", "scan_n_2"]
    loops = [b for b in expanded.body.walk() if type(b).__name__ == "Sweep"]
    assert [lp.variable.id for lp in loops] == ["scan_n", "scan_n_2"]
    # each loop's body waits on its own renamed variable
    for lp in loops:
        assert lp.elements[0].duration is lp.variable


def test_expand_local_variable_collision_with_host():
    frag = Fragment("scan")
    n = frag.variable("n")
    with frag.sweep(n, Values(np.asarray([1.0, 2.0]))):
        frag.wait("aux", n)
    p = QProgram()
    p.variable("scan_n")  # occupy the natural hygiene name
    p.call(frag)
    ids = [v.id for v in p.expand().variables]
    assert ids == ["scan_n", "scan_n_2"]


def test_expand_measurement_names_uniquified_with_consistent_refs():
    frag = Fragment("ro")
    bus = frag.parameter("bus")
    handle = frag.measure(bus, "wf", "w", fields=("iq", "state"))
    with frag.if_(handle.state == 0):
        frag.play(bus, "reset_pulse")

    p = QProgram()
    p.measure("readout_q9", "wf", "w")  # host takes the bare "m0" name first
    p.call(frag, "readout_q0")
    p.call(frag, "readout_q1")
    expanded = p.expand()
    measures = [n for n in expanded.body.walk() if isinstance(n, Measure)]
    assert [m.name for m in measures] == ["m0", "m0_2", "m0_3"]
    # MeasurementRefs inside each expanded conditional follow the renamed handle
    conds = [n for n in expanded.body.walk() if type(n).__name__ == "Conditional"]
    ref_names = [cond.arms[0][0].left.handle.name for cond in conds]
    assert ref_names == ["m0_2", "m0_3"]


def test_expand_nested_fragments():
    @fragment
    def echo(f, drive, t):
        f.wait(drive, t)
        f.play(drive, "pi")
        f.wait(drive, t)

    @fragment
    def cz(f, flux, drive, t):
        f.play(flux, FlatTop(amplitude=0.3, duration=200, smooth_duration=5))
        f.call(echo, drive, (t * 2))

    p = QProgram()
    p.call(cz, "flux_c01", "drive_q1", 50)
    expanded = p.expand()
    waits = [n for n in expanded.body.walk() if isinstance(n, Wait)]
    assert len(waits) == 2
    # outer binding t=50 substituted into the nested call's argument expression
    assert waits[0].duration == Constant(50) * Constant(2)
    assert not any(isinstance(n, Call) for n in expanded.body.walk())


def test_expand_cycle_detected():
    # Build the cycle *after* the host registered ``a``, so registration-time detection can't see
    # it — expansion must still catch it.
    a = Fragment("a")
    p = QProgram()
    p.call(a)  # a registered while its body is still empty
    b = Fragment("b")
    b.call(a)
    a.call(b)  # at registration b's walk sees an *empty* a body; the cycle closes only now
    with pytest.raises(ValidationError, match="fragment call cycle"):
        p.expand()


def test_expand_kind_mismatch_waveform_in_expression():
    frag = Fragment("f")
    x = frag.parameter("x")
    frag.set_frequency("bus", x * 2)
    p = QProgram()
    p.call(frag, Gaussian(amplitude=0.5, duration=40, sigma=8))
    with pytest.raises(ValidationError, match="used in an expression"):
        p.expand()


def test_expand_kind_mismatch_waveform_in_bus_position():
    frag = Fragment("f")
    bus = frag.parameter("bus")
    frag.wait(bus, 4)
    p = QProgram()
    p.call(frag, Gaussian(amplitude=0.5, duration=40, sigma=8))
    with pytest.raises(ValidationError, match="must be a bus"):
        p.expand()


def test_expand_kind_mismatch_number_in_waveform_position():
    frag = Fragment("f")
    wf = frag.parameter("wf")
    frag.play("drive", wf)
    p = QProgram()
    p.call(frag, 0.5)
    with pytest.raises(ValidationError, match="must be a waveform"):
        p.expand()


def test_expand_channel_validation_on_bound_busref():
    schema = BusSchema.transmon()
    frag = Fragment("f")
    bus = frag.parameter("bus")
    frag.play(bus, Square(1.0, 100))  # single-channel waveform
    p = QProgram(schema=schema)
    p.call(frag, schema.q[0].drive)  # IQ bus
    with pytest.raises(ValidationError, match="IQ channel"):
        p.expand()


def test_expand_acquires_validation_on_bound_busref():
    schema = BusSchema.transmon()
    frag = Fragment("f")
    bus = frag.parameter("bus")
    frag.measure(bus, "wf", "w")
    p = QProgram(schema=schema)
    p.call(frag, schema.q[0].drive)  # no ADC
    with pytest.raises(ValidationError, match="does not support acquisition"):
        p.expand()


def test_expand_is_deterministic_and_pure():
    frag = Fragment("f")
    amp = frag.parameter("amp")
    n = frag.variable("n")
    with frag.sweep(n, Range(0, 4, 1)):
        frag.set_gain("bus", amp + n)
    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Values(np.asarray([0.1, 0.2]))):
        p.call(frag, g)
        p.call(frag, (g * 3))

    before = qp.dumps(p)
    e1 = p.expand()
    e2 = p.expand()
    assert e1.body == e2.body
    assert e1.variables == e2.variables
    assert e1.fragments == {}
    assert qp.dumps(p) == before  # original untouched
    assert any(isinstance(node, Call) for node in p.body.walk())


def test_expand_without_calls_is_deep_copy():
    p = QProgram(label="plain")
    v = p.variable("v")
    with p.sweep(v, Range(0, 10, 1)):
        p.wait("bus", v)
    e = p.expand()
    assert e.body == p.body
    assert e.body is not p.body
    assert e.variables == p.variables


# ---------------------------------------------------------------------------
# Validation auto-expands
# ---------------------------------------------------------------------------


def test_validate_auto_expands_calls():
    from test_validation import _full_caps  # ruff: ignore[import-outside-top-level] — shared fixture helper

    frag = Fragment("f")
    bus = frag.parameter("bus")
    amp = frag.parameter("amp")
    frag.set_gain(bus, amp)
    p = QProgram()
    g = p.variable("g")
    with p.sweep(g, Range(0, 1, 0.1)):
        p.call(frag, "drive_q0", g)

    diagnostics, plan = qp.validate(p, _full_caps())
    assert [d for d in diagnostics if d.severity == "error"] == []
    assert len(list(plan)) > 0  # plan covers the expanded nodes
