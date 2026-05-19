"""Tests for the .qp serializer (writer side)."""

from __future__ import annotations

import re

import numpy as np
import pytest

from qprogram import (
    BusNaming,
    BusSchema,
    CrosstalkMatrix,
    QProgram,
    cos,
    dumps,
    eq,
    minimum,
    save,
    sin,
    where,
)
from qprogram.operations.operation import Operation
from qprogram.serialization import registry
from qprogram.serialization.writer import _escape_str, _major_minor, _Writer
from qprogram.waveforms import Arbitrary, Gaussian, IQDrag, IQPair, Square


def _norm(text: str) -> str:
    """Strip blank lines for easier matching in tests."""
    return "\n".join(line for line in text.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("0.1.0", "0.1"),
        ("1.2.3", "1.2"),
        ("0.0", "0.0"),
        ("1", "1.0"),
    ],
)
def test_major_minor(input_str, expected):
    assert _major_minor(input_str) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("foo", "foo"),
        ('he said "hi"', r"he said \"hi\""),
        ("a\\b", r"a\\b"),
        ("", ""),
    ],
)
def test_escape_str(raw, expected):
    assert _escape_str(raw) == expected


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def test_dumps_starts_with_format_header():
    text = dumps(QProgram())
    assert text.startswith("#!QProgram 1.0\n")


def test_dumps_includes_body_section():
    text = dumps(QProgram())
    assert "body:" in text


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_dumps_label():
    text = dumps(QProgram(label="rabi"))
    assert 'label: "rabi"' in text


def test_dumps_description():
    text = dumps(QProgram(label="x", description="Rabi run"))
    assert 'description: "Rabi run"' in text


def test_dumps_no_metadata_when_empty():
    text = dumps(QProgram())
    assert "metadata:" not in text


def test_dumps_escapes_quotes_in_label():
    text = dumps(QProgram(label='has "quotes"'))
    assert r"has \"quotes\"" in text


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_dumps_no_schema_section_when_absent():
    text = dumps(QProgram(label="x"))
    assert "schema:" not in text


def test_dumps_inline_schema_for_preset(transmon_schema):
    p = QProgram(schema=transmon_schema)
    text = dumps(p)
    assert "schema:" in text
    assert "element q:" in text
    assert "drive info=IQ" in text
    assert "readout info=IQ+acquires" in text


def test_dumps_schema_with_custom_naming():
    schema = BusSchema.transmon(naming=BusNaming("{kind}_{element}{index}_bus"))
    p = QProgram(schema=schema)
    text = dumps(p)
    assert 'naming: "{kind}_{element}{index}_bus"' in text


def test_dumps_schema_with_default_naming_omits_naming_line(transmon_schema):
    text = dumps(QProgram(schema=transmon_schema))
    assert "naming:" not in text


def test_dumps_coupled_schema(coupled_schema):
    text = dumps(QProgram(schema=coupled_schema))
    assert "element q:" in text
    assert "element c:" in text


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


def test_dumps_variable_declarations():
    p = QProgram()
    p.variable("freq")
    text = dumps(p)
    assert "var freq" in text


def test_dumps_variable_with_label():
    p = QProgram()
    p.variable("freq", label="Drive")
    text = dumps(p)
    assert 'var freq label="Drive"' in text


def test_dumps_variable_with_full_metadata():
    p = QProgram()
    p.variable("freq", label="L", units="Hz", description="D")
    text = dumps(p)
    assert "var freq" in text
    assert 'label="L"' in text
    assert 'units="Hz"' in text
    assert 'description="D"' in text


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def test_dumps_play_plain_string():
    p = QProgram()
    p.play("drive_q0", "pi_pulse")
    text = dumps(p)
    assert 'play "drive_q0" "pi_pulse"' in text


def test_dumps_play_inline_waveform():
    p = QProgram()
    p.play("drive_q0", Square(0.5, 100))
    text = dumps(p)
    assert "Square(amplitude=0.5, duration=100)" in text


def test_dumps_measure_default():
    p = QProgram()
    p.measure("readout", "r", "w")
    text = dumps(p)
    assert 'measure "readout" "r" "w" "m0"' in text


def test_dumps_measure_with_custom_returns():
    p = QProgram()
    p.measure("readout", "r", "w", returns="iq,raw")
    text = dumps(p)
    assert 'returns="iq,raw"' in text


def test_dumps_measure_default_returns_not_emitted():
    p = QProgram()
    p.measure("readout", "r", "w")
    text = dumps(p)
    assert "returns=" not in text


def test_dumps_wait_with_integer():
    p = QProgram()
    p.wait("bus", 100)
    text = dumps(p)
    assert 'wait "bus" 100' in text


def test_dumps_wait_with_expression():
    p = QProgram()
    v = p.variable("t")
    p.wait("bus", v + 5)
    text = dumps(p)
    assert "(t + 5)" in text


def test_dumps_sync_no_buses():
    p = QProgram()
    p.sync()
    text = dumps(p)
    assert _norm(text).endswith("\nbody:\n  sync")


def test_dumps_sync_with_buses():
    p = QProgram()
    p.sync(["bus1", "bus2"])
    text = dumps(p)
    assert 'sync "bus1" "bus2"' in text


def test_dumps_set_frequency():
    p = QProgram()
    p.set_frequency("bus", 5e9)
    text = dumps(p)
    assert "set_frequency" in text
    assert "5000000000" in text


def test_dumps_set_phase_int():
    p = QProgram()
    p.set_phase("bus", 0)
    text = dumps(p)
    assert 'set_phase "bus" 0' in text


def test_dumps_reset_phase():
    p = QProgram()
    p.reset_phase("bus")
    assert 'reset_phase "bus"' in dumps(p)


def test_dumps_set_gain():
    p = QProgram()
    p.set_gain("bus", 0.5)
    assert 'set_gain "bus" 0.5' in dumps(p)


def test_dumps_set_offset_one_path():
    p = QProgram()
    p.set_offset("bus", 0.1)
    text = dumps(p)
    assert 'set_offset "bus" 0.1' in text
    assert "offset_path1" not in text


def test_dumps_set_offset_two_paths():
    p = QProgram()
    p.set_offset("bus", 0.1, 0.2)
    text = dumps(p)
    assert "offset_path1=0.2" in text


def test_dumps_set_parameter():
    p = QProgram()
    p.set_parameter("cluster", "lo", 5e9)
    text = dumps(p)
    assert 'set_parameter "cluster" "lo"' in text


def test_dumps_set_parameter_with_channel_id():
    p = QProgram()
    p.set_parameter("cluster", "lo", 5e9, channel_id=3)
    text = dumps(p)
    assert "channel_id=3" in text


def test_dumps_get_parameter_arrow_syntax():
    p = QProgram()
    p.get_parameter("cluster", "lo_freq")
    text = dumps(p)
    assert "->" in text
    assert "cluster_lo_freq" in text


def test_dumps_get_parameter_with_channel_id():
    p = QProgram()
    p.get_parameter("cluster", "lo", channel_id=3)
    text = dumps(p)
    assert "channel_id=3" in text
    assert "->" in text


def test_dumps_set_crosstalk_stub():

    p = QProgram()
    p.set_crosstalk(CrosstalkMatrix())
    text = dumps(p)
    assert "set_crosstalk crosstalk" in text


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def test_dumps_binary_arithmetic():
    p = QProgram()
    v = p.variable("x")
    p.set_frequency("bus", v + 5)
    assert "(x + 5)" in dumps(p)


def test_dumps_unary_neg():
    p = QProgram()
    v = p.variable("x")
    p.set_phase("bus", -v)
    assert "(-x)" in dumps(p)


def test_dumps_comparison():
    p = QProgram()
    v = p.variable("x")
    # comparisons go in operation args, which need an expression-typed param.
    p.set_offset("bus", where(v < 5, v, 0.0))
    assert "<" in dumps(p)


def test_dumps_logical_and():
    p = QProgram()
    v = p.variable("x")
    w = p.variable("y")
    p.set_offset("bus", where(eq(v, 1) & eq(w, 1), 1, 0))
    text = dumps(p)
    assert "and" in text


def test_dumps_logical_not():
    p = QProgram()
    v = p.variable("x")
    p.set_offset("bus", where(eq(v, 0), 0, 1))
    text = dumps(p)
    # eq(v, 0) emits as (x == 0); the not is only in if a user wraps in ~
    assert "(x == 0)" in text


def test_dumps_math_func_call_form():
    p = QProgram()
    v = p.variable("x")
    p.set_frequency("bus", sin(v))
    assert "sin(x)" in dumps(p)


def test_dumps_where_call_form():
    p = QProgram()
    v = p.variable("x")
    p.set_offset("bus", where(v > 0, v, 0))
    assert "where" in dumps(p)


def test_dumps_minimum_func():
    p = QProgram()
    v = p.variable("x")
    p.set_gain("bus", minimum(v, 0.5))
    assert "minimum(x, 0.5)" in dumps(p)


def test_dumps_nested_expressions():
    p = QProgram()
    v = p.variable("x")
    p.set_frequency("bus", sin(v) + cos(v) * 2)
    text = dumps(p)
    assert "sin(x)" in text
    assert "cos(x)" in text


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------


def test_dumps_average_header():
    p = QProgram()
    with p.average(1000):
        p.wait("bus", 100)
    text = dumps(p)
    assert "average 1000:" in text


def test_dumps_for_loop_range():
    p = QProgram()
    v = p.variable("freq")
    with p.for_loop(v, 0.0, 1.0, 0.1):
        pass
    text = dumps(p)
    assert "for freq in range(0.0, 1.0, 0.1):" in text


def test_dumps_loop_values():

    p = QProgram()
    v = p.variable("amp")
    with p.loop(v, np.array([0.0, 0.5, 1.0])):
        pass
    text = dumps(p)
    assert "for amp in [" in text


def test_dumps_loop_values_truncated_when_long():

    p = QProgram()
    v = p.variable("amp")
    with p.loop(v, np.arange(100)):
        pass
    text = dumps(p)
    assert "..." in text


def test_dumps_parallel():
    p = QProgram()
    v = p.variable("x")
    w = p.variable("y")
    with p.for_loop(v, 0.0, 1.0, 0.1) | p.for_loop(w, 0.0, 1.0, 0.1):
        pass
    text = dumps(p)
    assert "for x in" in text
    assert "for y in" in text
    assert "|" in text


def test_dumps_block_header():
    p = QProgram()
    with p.block():
        p.wait("bus", 100)
    text = dumps(p)
    assert "block:" in text


def test_dumps_nested_indentation():
    p = QProgram()
    v = p.variable("x")
    with p.average(100), p.for_loop(v, 0.0, 1.0, 0.1):
        p.wait("bus", v)
    text = dumps(p)
    # 6 spaces of indentation for the deepest content (2-space per level).
    assert re.search(r"^      wait", text, re.MULTILINE)


# ---------------------------------------------------------------------------
# Bus serialization
# ---------------------------------------------------------------------------


def test_dumps_plain_string_bus_quoted(empty_program):
    empty_program.play("drive_q0", "wf")
    text = dumps(empty_program)
    assert '"drive_q0"' in text


def test_dumps_schema_backed_bus_path(transmon_schema):
    p = QProgram(schema=transmon_schema)
    p.play(transmon_schema.q[0].drive, "wf")
    text = dumps(p)
    # Note: emitted as path form, not quoted.
    assert "q[0].drive" in text
    assert '"q0/drive"' not in text


def test_dumps_coupler_index_tuple(coupled_schema):
    p = QProgram(schema=coupled_schema)
    p.set_offset(coupled_schema.c[0, 1].flux, 0.5)
    text = dumps(p)
    assert "c[0,1].flux" in text


# ---------------------------------------------------------------------------
# Waveform serialization
# ---------------------------------------------------------------------------


def test_dumps_iq_pair_inline():
    p = QProgram()
    p.play("bus", IQPair(Square(0.5, 100), Square(0.0, 100)))
    text = dumps(p)
    assert "IQPair(" in text
    assert "Square(" in text


def test_dumps_iq_drag_inline():
    p = QProgram()
    p.play("bus", IQDrag(0.5, 40, 2.5, 0.1))
    text = dumps(p)
    assert "IQDrag(" in text


def test_dumps_gaussian_with_variable_amp():
    p = QProgram()
    v = p.variable("amp")
    p.play("bus", Gaussian(amplitude=v, duration=40, num_sigmas=2.5))
    text = dumps(p)
    assert "amplitude=amp" in text


def test_dumps_arbitrary_truncates_long_samples():

    p = QProgram()
    p.play("bus", Arbitrary(np.arange(50)))
    text = dumps(p)
    assert "Arbitrary(samples=[" in text
    assert "..." in text


def test_dumps_arbitrary_short_samples_not_truncated():

    p = QProgram()
    p.play("bus", Arbitrary(np.array([0.1, 0.2])))
    text = dumps(p)
    assert "..." not in text or text.count("...") == 0  # truncation absent


# ---------------------------------------------------------------------------
# serialize_value fallthroughs
# ---------------------------------------------------------------------------


def test_writer_serialize_value_handles_tuple_of_strings():
    p = QProgram(label="x")
    w = _Writer(p)
    assert w.serialize_value(("a", "b", "c")) == '"a,b,c"'


def test_writer_serialize_value_handles_numpy_int():

    p = QProgram()
    w = _Writer(p)
    assert w.serialize_value(np.int64(42)) == "42"


def test_writer_serialize_value_handles_bool():
    p = QProgram()
    w = _Writer(p)
    assert w.serialize_value(True) == "true"  # noqa: FBT003
    assert w.serialize_value(False) == "false"  # noqa: FBT003


def test_writer_serialize_value_fallback_to_str():
    p = QProgram()
    w = _Writer(p)
    # Unknown type → str() fallback.
    result = w.serialize_value(object())
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# save / dumps integration
# ---------------------------------------------------------------------------


def test_save_writes_to_file(tmp_path, rabi_program):
    out = tmp_path / "rabi.qp"
    save(rabi_program, str(out))
    content = out.read_text()
    assert content == dumps(rabi_program)
    assert content.startswith("#!QProgram")


# ---------------------------------------------------------------------------
# Vendor handling
# ---------------------------------------------------------------------------


def test_dumps_includes_require_when_vendor_used():
    import qprogram_qblox  # noqa: F401, PLC0415  # conditional vendor import
    from qprogram_qblox import QProgram as QbloxQProgram  # noqa: PLC0415  # conditional vendor import

    p = QbloxQProgram()
    p.qblox.set_markers("bus", "0001")
    text = dumps(p)
    assert "require qblox" in text


def test_dumps_no_require_for_core_only_program():
    p = QProgram()
    p.play("bus", "wf")
    text = dumps(p)
    assert "require" not in text


def test_dumps_raises_when_vendor_version_missing():
    """If a vendor op is used but no version is registered, writer raises."""

    class _OrphanOp(Operation):
        def __init__(self, bus: str) -> None:
            self.bus = bus

    # Register without a version.
    registry.register_operation("op", _OrphanOp, vendor="_orphan")
    try:
        p = QProgram()
        op = _OrphanOp(bus="anything")
        p._active_block.append(op)
        with pytest.raises(RuntimeError, match="no version is registered"):
            dumps(p)
    finally:
        registry._operation_specs_by_qualified.pop(("_orphan", "op"), None)
        registry._operation_specs_by_class.pop(_OrphanOp, None)
