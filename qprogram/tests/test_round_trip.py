"""Integration tests: end-to-end .qp round-trip across feature combinations."""

from __future__ import annotations

import numpy as np

from qprogram import (
    QProgram,
    cos,
    dumps,
    eq,
    loads,
    minimum,
    sin,
    sqrt,
    where,
)
from qprogram.waveforms import Arbitrary, Gaussian, IQDrag, IQPair, Square


def _assert_byte_stable(program: QProgram) -> None:
    """Assert that dumps→loads→dumps yields identical text."""
    text = dumps(program)
    reloaded = loads(text)
    text2 = dumps(reloaded)
    assert text == text2, f"\n--- FIRST ---\n{text}\n--- SECOND ---\n{text2}"


# ---------------------------------------------------------------------------
# Round-trip stability across feature surfaces
# ---------------------------------------------------------------------------


def test_round_trip_empty_program():
    _assert_byte_stable(QProgram())


def test_round_trip_with_metadata():
    _assert_byte_stable(QProgram(label="foo", description="bar"))


def test_round_trip_with_variables():
    p = QProgram()
    p.variable("freq", label="L", units="Hz")
    p.variable("gain")
    _assert_byte_stable(p)


def test_round_trip_with_inline_schema(transmon_schema):
    _assert_byte_stable(QProgram(schema=transmon_schema))


def test_round_trip_with_custom_naming(custom_naming_schema):
    _assert_byte_stable(QProgram(schema=custom_naming_schema))


def test_round_trip_with_dynamic_schema(dynamic_schema):
    _assert_byte_stable(QProgram(schema=dynamic_schema))


def test_round_trip_with_coupled_schema(coupled_schema):
    p = QProgram(schema=coupled_schema)
    p.set_offset(coupled_schema.c[0, 1].flux, 0.5)
    _assert_byte_stable(p)


def test_round_trip_with_fluxonium_schema(fluxonium_schema):
    p = QProgram(schema=fluxonium_schema)
    p.set_offset(fluxonium_schema.q[0].flux_x, 0.1)
    p.set_offset(fluxonium_schema.q[0].flux_z, 0.2)
    _assert_byte_stable(p)


def test_round_trip_with_plain_string_buses():
    p = QProgram()
    p.play("drive_q0", "pi_pulse")
    p.measure("readout_q0", "wf", "weights")
    _assert_byte_stable(p)


def test_round_trip_mixed_schema_and_plain_buses(transmon_schema):
    p = QProgram(schema=transmon_schema)
    p.play(transmon_schema.q[0].drive, "wf")
    p.play("raw_bus", "wf")
    _assert_byte_stable(p)


def test_round_trip_all_core_operations(transmon_schema):

    p = QProgram(schema=transmon_schema)
    p.set_frequency(transmon_schema.q[0].drive, 5e9)
    p.set_phase(transmon_schema.q[0].drive, 1.5708)
    p.reset_phase(transmon_schema.q[0].drive)
    p.set_gain(transmon_schema.q[0].drive, 0.5)
    p.set_offset("flux", 0.1, 0.2)
    p.play(transmon_schema.q[0].drive, "wf")
    p.measure(transmon_schema.q[0].readout, "wf", "w")
    p.wait(transmon_schema.q[0].drive, 100)
    p.sync()
    p.set_parameter("alias", "param", 5e9, channel_id=3)
    p.get_parameter("alias", "param")
    _assert_byte_stable(p)


def test_round_trip_with_inline_waveforms():
    p = QProgram()
    p.play("drive", Gaussian(amplitude=0.5, duration=40, sigma=8))
    p.play("drive", IQDrag(0.5, 40, 8, 0.1))
    p.measure(
        "readout",
        IQPair(Square(1.0, 100), Square(0.0, 100)),
        IQPair(Square(1.0, 100), Square(1.0, 100)),
    )
    _assert_byte_stable(p)


def test_round_trip_with_variable_in_waveform():
    p = QProgram()
    v = p.variable("amp")
    p.play("drive", Gaussian(amplitude=v, duration=40, sigma=8))
    _assert_byte_stable(p)


def test_round_trip_with_expressions():
    p = QProgram()
    v = p.variable("freq")
    w = p.variable("gain")
    p.set_frequency("drive", v + 1e6)
    p.set_phase("drive", v - w)
    p.set_gain("drive", w * 2)
    p.set_offset("flux", -v)
    _assert_byte_stable(p)


def test_round_trip_with_comparisons_and_logical():
    p = QProgram()
    v = p.variable("freq")
    w = p.variable("gain")
    # set_offset is one of the easiest ops to receive an arbitrary expression.
    p.set_offset("flux", where(v < 5e9, v, 0.0))
    p.set_offset("flux", where(eq(v, 5e9), w, 0.0))
    p.set_offset("flux", where(v > 0, w, w * 2))
    _assert_byte_stable(p)


def test_round_trip_with_math_functions():
    p = QProgram()
    v = p.variable("x")
    p.set_frequency("drive", sin(v))
    p.set_phase("drive", cos(v))
    p.set_gain("drive", sqrt(v))
    p.set_offset("flux", minimum(v, 0.5))
    p.wait("drive", abs(v - 5))
    _assert_byte_stable(p)


def test_round_trip_with_average():
    p = QProgram()
    with p.average(1000):
        p.play("drive", "wf")
    _assert_byte_stable(p)


def test_round_trip_with_for_loop():
    p = QProgram()
    v = p.variable("freq")
    with p.for_loop(v, 4e9, 6e9, 1e6):
        p.set_frequency("drive", v)
    _assert_byte_stable(p)


def test_round_trip_with_loop_values():
    p = QProgram()
    v = p.variable("amp")
    with p.loop(v, np.array([0.1, 0.3, 0.5])):
        p.set_gain("drive", v)
    _assert_byte_stable(p)


def test_round_trip_with_block():
    p = QProgram()
    with p.block():
        p.wait("bus", 100)
    _assert_byte_stable(p)


def test_round_trip_with_parallel_loops():
    p = QProgram()
    v = p.variable("freq")
    w = p.variable("gain")
    with p.for_loop(v, 4e9, 6e9, 1e6) | p.for_loop(w, 0.0, 2.0, 0.001):
        p.set_frequency("drive", v)
        p.set_gain("drive", w)
    _assert_byte_stable(p)


def test_round_trip_with_deeply_nested_blocks():
    p = QProgram()
    v = p.variable("x")
    with p.average(100), p.for_loop(v, 0.0, 1.0, 0.1), p.block():
        p.wait("bus", v)
    _assert_byte_stable(p)


def test_round_trip_with_measurement_handles(transmon_schema):
    p = QProgram(schema=transmon_schema)
    p.measure(transmon_schema.q[0].readout, "r", "w")
    p.measure(transmon_schema.q[0].readout, "r", "w")
    p.measure(transmon_schema.q[0].readout, "r", "w", name="custom")
    _assert_byte_stable(p)


def test_round_trip_with_returns_field(transmon_schema):
    p = QProgram(schema=transmon_schema)
    p.measure(transmon_schema.q[0].readout, "r", "w")
    p.measure(transmon_schema.q[0].readout, "r", "w", returns="iq,raw")
    _assert_byte_stable(p)


def test_round_trip_full_features(transmon_schema):
    """The big one: every feature surface in a single program."""
    p = QProgram(
        label="big",
        description="Cross-feature integration",
        schema=transmon_schema,
    )
    gain = p.variable("gain", label="Drive amp", units="V")
    freq = p.variable("freq")
    t = p.variable("t", units="ns")

    with (
        p.average(shots=1000),
        p.for_loop(gain, 0.0, 0.3, 0.1) | p.loop(t, np.array([10, 20, 30, 40])),
        p.for_loop(freq, 4e9, 6e9, 1e6),
    ):
        p.set_gain(transmon_schema.q[0].drive, minimum(gain, 0.5))
        p.set_frequency(transmon_schema.q[0].drive, freq + sin(freq) * 1e6)
        p.set_phase(transmon_schema.q[0].drive, where(gain > 0.5, gain, 0.0))
        p.set_offset("flux", abs(gain - 0.5))
        p.play(
            transmon_schema.q[0].drive,
            IQDrag(amplitude=gain, duration=40, sigma=8, beta=0.1),
        )
        p.sync([transmon_schema.q[0].drive, transmon_schema.q[0].readout])
        p.wait(transmon_schema.q[0].drive, t)
        p.measure(transmon_schema.q[0].readout, "r", "w", returns=("iq", "raw"))

    _assert_byte_stable(p)


def test_round_trip_loaded_equals_original(transmon_schema):
    """The §11 structural-equality guarantee across .qp round-trip."""
    p = QProgram(schema=transmon_schema)
    v = p.variable("x")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        p.play(transmon_schema.q[0].drive, "wf")
    text = dumps(p)
    reloaded = loads(text)
    assert reloaded.body == p.body
    assert hash(reloaded.body) == hash(p.body)


def test_round_trip_with_rebind_raw_strings():
    p = QProgram()
    p.play("a", "wf")
    p.sync(["a", "b"])
    remapped = p.rebind(strings={"a": "x", "b": "b"})
    _assert_byte_stable(remapped)


def test_round_trip_with_rebind_schema_path(transmon_schema):
    q = transmon_schema.q
    p = QProgram(schema=transmon_schema)
    p.play(q[0].drive, "pi")
    p.measure(q[0].readout, "ro", "w")
    ported = p.rebind(elements={("q", 0): ("q", 1)})
    assert "q[1].drive" in dumps(ported)
    _assert_byte_stable(ported)


def test_round_trip_with_with_waveforms():
    p = QProgram()
    p.play("bus", "pi_pulse")
    resolved = p.with_waveforms({"pi_pulse": Gaussian(0.5, 40, 8)})
    _assert_byte_stable(resolved)


def test_round_trip_with_vendor(transmon_schema, dummy_vendor):  # noqa: ARG001
    from _dummy_vendor import DummyQProgram  # noqa: PLC0415

    p = DummyQProgram(schema=transmon_schema)
    p.dummy.set_markers(transmon_schema.q[0].drive, "0001")
    p.dummy.set_trigger(transmon_schema.q[0].drive, duration=100, position="end")
    p.dummy.wait_trigger(transmon_schema.q[0].drive, duration=1000, port=1)
    p.dummy.acquire(transmon_schema.q[0].readout, "w", returns="iq,raw")
    _assert_byte_stable(p)


# ---------------------------------------------------------------------------
# Regression coverage: the P0 round-trip integrity fixes
# ---------------------------------------------------------------------------


def test_round_trip_long_loop_values_lossless():
    """Sweeps longer than 50 points reload value-for-value (no truncation)."""
    p = QProgram()
    v = p.variable("amp")
    values = np.linspace(0.0, 1.0, 137)
    with p.loop(v, values):
        p.set_gain("drive", v)
    reloaded = loads(dumps(p))
    lp = reloaded.body.elements[0]
    assert np.array_equal(lp.values, values)
    assert reloaded.body == p.body


def test_round_trip_long_arbitrary_samples_lossless():
    """Arbitrary waveforms beyond 20 samples reload sample-for-sample."""
    p = QProgram()
    samples = np.sin(np.linspace(0, np.pi, 64))
    p.play("drive", Square(0.5, 4))  # neighbour op to keep the body realistic
    p.play("drive", Arbitrary(samples))
    reloaded = loads(dumps(p))
    wf = reloaded.body.elements[1].waveform
    assert np.array_equal(wf.samples, samples)


def test_round_trip_vendor_op_inside_conditional(transmon_schema, dummy_vendor):  # noqa: ARG001
    """Vendor ops used only inside conditional arms keep their require line and survive."""
    from _dummy_vendor import DummyQProgram, DummySetMarkers  # noqa: PLC0415

    p = DummyQProgram(schema=transmon_schema)
    h = p.measure(transmon_schema.q[0].readout, "r", "w", returns="iq,state")
    with p.if_(h.state == 1):
        p.dummy.set_markers(transmon_schema.q[0].drive, "0001")
    text = dumps(p)
    assert "require dummy" in text
    reloaded = loads(text)
    markers = [n for n in reloaded.body.walk() if isinstance(n, DummySetMarkers)]
    assert len(markers) == 1


def test_round_trip_metadata_with_quotes_and_backslashes():
    p = QProgram(label='say "hi"', description="a\\b # not a comment")
    reloaded = loads(dumps(p))
    assert reloaded.label == p.label
    assert reloaded.description == p.description


def test_round_trip_measure_name_kwarg_form(transmon_schema):
    """The wire format carries names as ``name=`` and reloads to equal handles."""
    p = QProgram(schema=transmon_schema)
    p.measure(transmon_schema.q[0].readout, "r", "w")
    text = dumps(p)
    assert 'name="q0/readout/m0"' in text
    reloaded = loads(text)
    assert reloaded.body == p.body


def test_round_trip_conditional_full_chain(transmon_schema):
    p = QProgram(schema=transmon_schema)
    h = p.measure(transmon_schema.q[0].readout, "r", "w", returns="iq,state")
    with p.if_(h.state == 1):
        p.play(transmon_schema.q[0].drive, "pi")
    with p.elif_(h.state == 0):
        p.play(transmon_schema.q[0].drive, "id")
    with p.else_():
        p.sync()
    _assert_byte_stable(p)
    assert loads(dumps(p)).body == p.body
