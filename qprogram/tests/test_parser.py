"""Tests for the .qp parser."""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import (
    Comparison,
    Constant,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    ParseError,
    Variable,
    Where,
    dumps,
    load,
    loads,
)
from qprogram.buses import BusRef
from qprogram.serialization.parser import (
    _find_comment,
    _parse_arg,
    _parse_major_minor,
    _parse_number,
    _Parser,
    _split_args,
    _to_expression,
    _tokenize,
    _unescape_str,
)
from qprogram.variable import BinaryOp, UnaryOp
from qprogram.waveforms import Square

# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("hello", -1),
        ("hello # comment", 6),
        ("# leading", 0),
        ("#!header", -1),  # shebang is not a comment
        ('text with "#" inside', -1),  # # in string doesn't count
    ],
)
def test_find_comment(line, expected):
    assert _find_comment(line) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"hello", "hello"),
        (r"a\"b", 'a"b'),
        (r"a\\b", "a\\b"),
        (r"", ""),
    ],
)
def test_unescape_str(raw, expected):
    assert _unescape_str(raw) == expected


def test_unescape_str_trailing_backslash():
    # Lone trailing backslash with no follow-up should survive.
    assert _unescape_str("a\\") == "a\\"


@pytest.mark.parametrize(
    ("ver", "expected"),
    [("1.0", (1, 0)), ("1.2.3", (1, 2)), ("0.10", (0, 10))],
)
def test_parse_major_minor(ver, expected):
    assert _parse_major_minor(ver) == expected


def test_parse_major_minor_too_short_raises():
    with pytest.raises(ValueError, match=r"at least major\.minor"):
        _parse_major_minor("1")


def test_parse_major_minor_non_integer_raises():
    with pytest.raises(ValueError, match="non-integer"):
        _parse_major_minor("a.b")


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("1", 1),
        ("1.5", 1.5),
        ("5e9", 5e9),
        ("-3", -3),
        ("100.0", 100.0),
    ],
)
def test_parse_number(s, expected):
    result = _parse_number(s)
    assert result == expected
    # Integer-looking strings should give int.
    if "." not in s and "e" not in s.lower():
        assert isinstance(result, int)


def test_tokenize_simple():
    assert _tokenize("a b c") == ["a", "b", "c"]


def test_tokenize_quoted_strings():
    assert _tokenize('a "b c" d') == ["a", '"b c"', "d"]


def test_tokenize_paren_groups():
    assert _tokenize("a (b c) d") == ["a", "(b c)", "d"]


def test_tokenize_nested_parens():
    assert _tokenize("Gauss(amp=0.5, dur=40)") == ["Gauss(amp=0.5, dur=40)"]


def test_tokenize_empty():
    assert _tokenize("") == []


def test_tokenize_leading_trailing_spaces():
    assert _tokenize("  a  b  ") == ["a", "b"]


def test_split_args_basic():
    assert _split_args("1, 2, 3") == ["1", " 2", " 3"]


def test_split_args_nested_parens():
    parts = _split_args("a, (b, c), d")
    assert len(parts) == 3


def test_split_args_with_quoted_string():
    parts = _split_args('"hello, world", x')
    assert len(parts) == 2


def test_split_args_brackets():
    parts = _split_args("a, [1, 2], b")
    assert len(parts) == 3


def test_split_args_empty():
    assert _split_args("") == []


def test_parse_arg_quoted_string():
    assert _parse_arg('"hello"') == "hello"


def test_parse_arg_true_false():
    assert _parse_arg("true") is True
    assert _parse_arg("false") is False


def test_parse_arg_list_literal():
    result = _parse_arg("[1, 2, 3]")
    assert isinstance(result, np.ndarray)
    assert np.array_equal(result, np.array([1, 2, 3]))


def test_parse_arg_number():
    assert _parse_arg("42") == 42
    assert _parse_arg("3.14") == 3.14


def test_parse_arg_unrecognized_identifier_falls_through():
    assert _parse_arg("nonsense") == "nonsense"


def test_parse_arg_with_variable_table():

    v = Variable("x")
    assert _parse_arg("x", {"x": v}) is v


def test_parse_arg_waveform_call():
    result = _parse_arg("Square(amplitude=0.5, duration=100)")
    assert isinstance(result, Square)


def test_to_expression_passes_through():

    v = Variable("x")
    assert _to_expression(v) is v


def test_to_expression_wraps_int():
    result = _to_expression(5)
    assert isinstance(result, Constant)
    assert result.value == 5


def test_to_expression_raises_on_unknown_type():
    # Non-numeric, non-expression — surface a clean ParseError instead of
    # deferring the failure to the AST constructor.
    with pytest.raises(ParseError, match="expression operand"):
        _to_expression("anything")


# ---------------------------------------------------------------------------
# Header / version
# ---------------------------------------------------------------------------


def test_loads_missing_header_raises():
    with pytest.raises(ParseError, match="Missing #!QProgram"):
        loads("body:\n")


def test_loads_unsupported_major_version_raises():
    with pytest.raises(ParseError, match="Unsupported format version"):
        loads("#!QProgram 99.0\n\nbody:\n")


def test_loads_minor_within_major_works():
    # Same major (1) is accepted regardless of minor.
    text = "#!QProgram 1.99\n\nbody:\n"
    loads(text)


def test_loads_empty_program():
    p = loads("#!QProgram 1.0\n\nbody:\n")
    assert p.label == ""
    assert p.variables == []


# ---------------------------------------------------------------------------
# Require declarations
# ---------------------------------------------------------------------------


def test_loads_with_vendor_require(dummy_vendor):  # noqa: ARG001
    text = "#!QProgram 1.0\n\nrequire dummy 0.0\n\nbody:\n"
    p = loads(text)
    assert p is not None


def test_loads_unknown_vendor_require_raises():
    text = "#!QProgram 1.0\n\nrequire nonexistent_vendor 1.0\n\nbody:\n"
    with pytest.raises(ParseError, match="no matching extension"):
        loads(text)


def test_loads_require_malformed_raises(dummy_vendor):  # noqa: ARG001
    text = "#!QProgram 1.0\n\nrequire dummy\n\nbody:\n"
    with pytest.raises(ParseError, match="must specify a version"):
        loads(text)


def test_loads_require_major_mismatch_raises(dummy_vendor):  # noqa: ARG001
    text = "#!QProgram 1.0\n\nrequire dummy 99.0\n\nbody:\n"
    with pytest.raises(ParseError, match="major versions must match"):
        loads(text)


def test_loads_require_minor_too_old_raises(dummy_vendor):  # noqa: ARG001
    text = "#!QProgram 1.0\n\nrequire dummy 0.99\n\nbody:\n"
    with pytest.raises(ParseError, match="minor version too old"):
        loads(text)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_loads_metadata_label():
    text = '#!QProgram 1.0\n\nmetadata:\n  label: "rabi"\n\nbody:\n'
    p = loads(text)
    assert p.label == "rabi"


def test_loads_metadata_description():
    text = '#!QProgram 1.0\n\nmetadata:\n  label: "x"\n  description: "desc"\n\nbody:\n'
    p = loads(text)
    assert p.description == "desc"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_loads_inline_schema():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ\n    readout info=IQ+acquires\n\nbody:\n"
    p = loads(text)
    assert p.schema is not None
    assert "q" in p.schema.elements


def test_loads_inline_schema_with_naming():
    text = (
        '#!QProgram 1.0\n\nschema:\n  naming: "{kind}_{element}{index}_bus"\n  element q:\n    drive info=IQ\n\nbody:\n'
    )
    p = loads(text)
    assert p.schema.naming.pattern == "{kind}_{element}{index}_bus"


def test_loads_rejects_old_preset_keyword_form():
    text = "#!QProgram 1.0\n\nschema: transmon\n\nbody:\n"
    with pytest.raises(ParseError, match="invalid schema declaration"):
        loads(text)


def test_loads_rejects_duplicate_schema():
    text = (
        "#!QProgram 1.0\n\n"
        "schema:\n"
        "  element q:\n"
        "    drive info=IQ\n"
        "schema:\n"
        "  element r:\n"
        "    drive info=IQ\n"
        "\n"
        "body:\n"
    )
    with pytest.raises(ParseError, match="duplicate schema"):
        loads(text)


def test_loads_rejects_empty_schema():
    text = "#!QProgram 1.0\n\nschema:\nbody:\n"
    with pytest.raises(ParseError, match="no element declarations"):
        loads(text)


def test_loads_rejects_invalid_naming_unquoted():
    text = "#!QProgram 1.0\n\nschema:\n  naming: foo\n  element q:\n    drive info=IQ\nbody:\n"
    with pytest.raises(ParseError, match="quoted string"):
        loads(text)


def test_loads_rejects_unexpected_schema_line():
    text = "#!QProgram 1.0\n\nschema:\n  garbage\nbody:\n"
    with pytest.raises(ParseError, match="unexpected line in schema"):
        loads(text)


def test_loads_rejects_bus_info_empty():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=\nbody:\n"
    with pytest.raises(ParseError):
        loads(text)


def test_loads_rejects_bus_info_unknown_token():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=banana\nbody:\n"
    with pytest.raises(ParseError, match="unknown token"):
        loads(text)


def test_loads_rejects_bus_info_multiple_channels():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ+single\nbody:\n"
    with pytest.raises(ParseError, match="multiple channel tokens"):
        loads(text)


def test_loads_rejects_bus_info_duplicate_flag():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ+acquires+acquires\nbody:\n"
    with pytest.raises(ParseError, match="duplicate flag"):
        loads(text)


def test_loads_rejects_bus_info_no_channel():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=acquires\nbody:\n"
    with pytest.raises(ParseError, match="must specify a channel"):
        loads(text)


def test_loads_rejects_duplicate_bus_kind():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ\n    drive info=single\nbody:\n"
    with pytest.raises(ParseError, match="duplicate bus"):
        loads(text)


def test_loads_rejects_invalid_bus_line():
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    not a bus line\nbody:\n"
    with pytest.raises(ParseError, match="invalid bus declaration"):
        loads(text)


# ---------------------------------------------------------------------------
# Variable declarations
# ---------------------------------------------------------------------------


def test_loads_variable_bare():
    text = "#!QProgram 1.0\n\nbody:\n  var freq\n"
    p = loads(text)
    assert p.variables[0].id == "freq"


def test_loads_variable_with_metadata():
    text = '#!QProgram 1.0\n\nbody:\n  var freq label="L" units="Hz"\n'
    p = loads(text)
    v = p.variables[0]
    assert v.label == "L"
    assert v.units == "Hz"


def test_loads_variable_invalid_id_format():
    text = "#!QProgram 1.0\n\nbody:\n  var 1bad\n"
    with pytest.raises(ParseError, match="must match"):
        loads(text)


def test_loads_variable_reserved_id():
    text = "#!QProgram 1.0\n\nbody:\n  var if\n"
    with pytest.raises((ParseError, Exception)):
        loads(text)


def test_loads_variable_unquoted_attr_value():
    text = "#!QProgram 1.0\n\nbody:\n  var x label=foo\n"
    with pytest.raises(ParseError):
        loads(text)


def test_loads_variable_unknown_attr():
    text = '#!QProgram 1.0\n\nbody:\n  var x foo="bar"\n'
    with pytest.raises(ParseError, match="unknown variable attribute"):
        loads(text)


def test_loads_variable_duplicate_attr():
    text = '#!QProgram 1.0\n\nbody:\n  var x label="a" label="b"\n'
    with pytest.raises(ParseError, match="duplicate variable attribute"):
        loads(text)


def test_loads_variable_unexpected_token():
    text = '#!QProgram 1.0\n\nbody:\n  var x label="a" garbage\n'
    with pytest.raises(ParseError, match="unexpected token"):
        loads(text)


def test_loads_variable_bare_var_is_silently_skipped():
    """`var` alone (no id) doesn't match `var ` prefix-detection and falls
    through to the operation branch, which returns None for unknown ops.
    Effectively: no parse error, no variable declared. Documented here so
    future work that tightens this surface has a test to flip."""
    text = "#!QProgram 1.0\n\nbody:\n  var\n"
    p = loads(text)
    assert p.variables == []


def test_loads_variable_duplicate_id():
    text = "#!QProgram 1.0\n\nbody:\n  var x\n  var x\n"
    with pytest.raises((ParseError, Exception)):
        loads(text)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def test_loads_play_with_string_alias():
    text = '#!QProgram 1.0\n\nbody:\n  play "drive" "pi"\n'
    p = loads(text)
    assert dumps(p) == text


def test_loads_play_with_inline_waveform():
    text = '#!QProgram 1.0\n\nbody:\n  play "drive" Square(amplitude=0.5, duration=100)\n'
    p = loads(text)
    op = p.body.elements[0]
    assert isinstance(op.waveform, Square)


def test_loads_measure_default_returns():
    text = '#!QProgram 1.0\n\nbody:\n  measure "readout" "r" "w" "m0"\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.returns == ("iq",)
    assert op.name == "m0"


def test_loads_measure_with_returns_kwarg():
    text = '#!QProgram 1.0\n\nbody:\n  measure "readout" "r" "w" "m0" returns="iq,raw"\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.returns == ("iq", "raw")


def test_loads_wait_with_int():
    text = '#!QProgram 1.0\n\nbody:\n  wait "bus" 100\n'
    p = loads(text)
    assert p.body.elements[0].duration == 100


def test_loads_wait_with_variable_ref():
    text = '#!QProgram 1.0\n\nbody:\n  var t\n  wait "bus" t\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.duration is p.variables[0]


def test_loads_sync_no_args():
    text = "#!QProgram 1.0\n\nbody:\n  sync\n"
    p = loads(text)
    op = p.body.elements[0]
    assert op.targets is None


def test_loads_sync_with_buses():
    text = '#!QProgram 1.0\n\nbody:\n  sync "a" "b"\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.targets == ["a", "b"]


def test_loads_set_frequency():
    text = '#!QProgram 1.0\n\nbody:\n  set_frequency "bus" 5000000000.0\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.frequency == 5e9


def test_loads_set_phase_with_var():
    text = '#!QProgram 1.0\n\nbody:\n  var phi\n  set_phase "bus" phi\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.phase is p.variables[0]


def test_loads_reset_phase():
    text = '#!QProgram 1.0\n\nbody:\n  reset_phase "bus"\n'
    p = loads(text)
    assert p.body.elements[0].bus == "bus"


def test_loads_set_gain():
    text = '#!QProgram 1.0\n\nbody:\n  set_gain "bus" 0.5\n'
    p = loads(text)
    assert p.body.elements[0].gain == 0.5


def test_loads_set_offset_one_path():
    text = '#!QProgram 1.0\n\nbody:\n  set_offset "bus" 0.1\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.offset_path0 == 0.1
    assert op.offset_path1 is None


def test_loads_set_offset_two_paths_kwarg_form():
    text = '#!QProgram 1.0\n\nbody:\n  set_offset "bus" 0.1 offset_path1=0.2\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.offset_path1 == 0.2


def test_loads_set_parameter():
    text = '#!QProgram 1.0\n\nbody:\n  set_parameter "alias" "param" 5000000000.0\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.alias == "alias"


def test_loads_set_parameter_with_channel_id():
    text = '#!QProgram 1.0\n\nbody:\n  set_parameter "alias" "param" 5.0 channel_id=3\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.channel_id == 3


def test_loads_get_parameter_arrow():
    text = '#!QProgram 1.0\n\nbody:\n  get_parameter "alias" "param" -> result\n'
    p = loads(text)
    op = p.body.elements[0]
    assert op.alias == "alias"
    assert op.variable.id == "result"


def test_loads_get_parameter_arrow_missing_var_raises():
    text = '#!QProgram 1.0\n\nbody:\n  get_parameter "alias" "param"\n'
    with pytest.raises(ParseError, match="-> <var>"):
        loads(text)


def test_loads_get_parameter_missing_alias_raises():
    text = "#!QProgram 1.0\n\nbody:\n  get_parameter -> result\n"
    with pytest.raises(ParseError, match="alias and parameter"):
        loads(text)


def test_loads_set_crosstalk_stub():
    text = "#!QProgram 1.0\n\nbody:\n  set_crosstalk crosstalk\n"
    p = loads(text)
    assert p.body.elements[0] is not None


def test_loads_skips_unknown_operation():
    """An operation name not in any registry is silently skipped."""
    text = '#!QProgram 1.0\n\nbody:\n  unknown_op "bus" 42\n'
    p = loads(text)
    # No ops produced.
    assert len(p.body.elements) == 0


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------


def test_loads_average_block():
    text = '#!QProgram 1.0\n\nbody:\n  average 1000:\n    play "bus" "wf"\n'
    p = loads(text)
    avg = p.body.elements[0]
    assert avg.shots == 1000
    assert len(avg.elements) == 1


def test_loads_average_invalid_shots_raises():
    text = "#!QProgram 1.0\n\nbody:\n  average abc:\n"
    with pytest.raises(ParseError, match="invalid shots"):
        loads(text)


def test_loads_average_missing_shots_raises():
    text = "#!QProgram 1.0\n\nbody:\n  average:\n"
    with pytest.raises(ParseError, match="requires a shot count"):
        loads(text)


def test_loads_for_range_two_args():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in range(0, 10):\n    wait "bus" 100\n'
    p = loads(text)
    fl = p.body.elements[0]
    assert fl.start == 0
    assert fl.stop == 10
    assert fl.step == 1


def test_loads_for_range_three_args():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in range(0.0, 1.0, 0.1):\n    wait "bus" 100\n'
    p = loads(text)
    fl = p.body.elements[0]
    assert fl.step == 0.1


def test_loads_for_range_bad_arity_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in range(1):\n    wait "bus" 100\n'
    with pytest.raises(ParseError, match="2 or 3"):
        loads(text)


def test_loads_for_values_list():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in [0.0, 0.5, 1.0]:\n    wait "bus" 100\n'
    p = loads(text)
    lp = p.body.elements[0]
    assert np.array_equal(lp.values, np.array([0.0, 0.5, 1.0]))


def test_loads_for_invalid_header_raises():
    text = '#!QProgram 1.0\n\nbody:\n  for in range(0,1):\n    wait "bus" 100\n'
    with pytest.raises(ParseError):
        loads(text)


def test_loads_for_unknown_generator_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in bogus(0,1):\n    wait "bus" 100\n'
    with pytest.raises(ParseError, match="unknown sweep generator"):
        loads(text)


def test_loads_for_unknown_source_form_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  for x in something:\n    wait "bus" 100\n'
    with pytest.raises(ParseError, match="unknown sweep source"):
        loads(text)


def test_loads_parallel_loops():
    text = (
        "#!QProgram 1.0\n\n"
        "body:\n"
        "  var x\n"
        "  var y\n"
        "  for x in range(0, 10) | for y in range(0, 10):\n"
        '    wait "bus" 100\n'
    )
    p = loads(text)
    par = p.body.elements[0]
    assert len(par.loops) == 2


def test_loads_block_scope():
    text = '#!QProgram 1.0\n\nbody:\n  block:\n    wait "bus" 100\n'
    p = loads(text)
    block = p.body.elements[0]
    assert len(block.elements) == 1


def test_loads_nested_blocks():
    text = (
        '#!QProgram 1.0\n\nbody:\n  var x\n  average 100:\n    for x in range(0.0, 1.0, 0.1):\n      wait "bus" 100\n'
    )
    p = loads(text)
    assert len(p.body.elements) == 1


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------


def test_loads_binary_arithmetic():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_frequency "bus" (x + 5)\n'
    p = loads(text)
    op = p.body.elements[0]
    # Result is a BinaryOp expression.

    assert isinstance(op.frequency, BinaryOp)


def test_loads_unary_neg():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_phase "bus" (-x)\n'
    p = loads(text)
    op = p.body.elements[0]

    assert isinstance(op.phase, UnaryOp)


def test_loads_comparison():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" where((x < 5), x, 0)\n'
    p = loads(text)
    op = p.body.elements[0]
    assert isinstance(op.offset_path0, Where)
    assert isinstance(op.offset_path0.condition, Comparison)


def test_loads_logical_and():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  var y\n  set_offset "bus" where(((x == 1) and (y == 1)), 1, 0)\n'
    p = loads(text)
    op = p.body.elements[0]
    cond = op.offset_path0.condition
    assert isinstance(cond, LogicalBinaryOp)


def test_loads_logical_not():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" where((not (x == 1)), 1, 0)\n'
    p = loads(text)
    op = p.body.elements[0]
    assert isinstance(op.offset_path0.condition, LogicalNot)


def test_loads_math_func():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_frequency "bus" sin(x)\n'
    p = loads(text)
    op = p.body.elements[0]
    assert isinstance(op.frequency, MathFunc)
    assert op.frequency.name == "sin"


def test_loads_where():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" where((x < 5), 1, 0)\n'
    p = loads(text)
    op = p.body.elements[0]
    assert isinstance(op.offset_path0, Where)


def test_loads_where_wrong_arity_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" where(x, 1)\n'
    with pytest.raises(ParseError, match="3 arguments"):
        loads(text)


def test_loads_empty_paren_expression_raises():
    text = '#!QProgram 1.0\n\nbody:\n  set_offset "bus" ()\n'
    with pytest.raises(ParseError, match="empty expression"):
        loads(text)


def test_loads_paren_expression_unrecognized_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" (x bogus 5)\n'
    with pytest.raises(ParseError, match="unknown operator"):
        loads(text)


def test_loads_paren_expression_single_token_not_unary_raises():
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" (x)\n'
    # ``(x)`` is one token without a binary op and not a leading sign.
    with pytest.raises(ParseError, match="could not parse"):
        loads(text)


# ---------------------------------------------------------------------------
# Parse context helpers
# ---------------------------------------------------------------------------


def test_parse_context_parse_value_empty_raises():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    with pytest.raises(ParseError, match="empty argument token"):
        p.parse_value("")


def test_parse_context_parse_value_quoted_string():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    assert p.parse_value('"hello"') == "hello"


def test_parse_context_parse_value_true():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    assert p.parse_value("true") is True


def test_parse_context_get_or_declare_variable_creates_new():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    v = p.get_or_declare_variable("auto")
    assert v.id == "auto"


def test_parse_context_get_or_declare_variable_reuses():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    v1 = p.get_or_declare_variable("x")
    v2 = p.get_or_declare_variable("x")
    assert v1 is v2


def test_parse_context_declared_variable():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    p._parse_header()
    assert p.declared_variable("ghost") is None
    p.get_or_declare_variable("ghost")
    assert p.declared_variable("ghost") is not None


def test_parse_context_line_num():
    p = _Parser("#!QProgram 1.0\nbody:\n")
    assert p.line_num == 1


# ---------------------------------------------------------------------------
# load (from file)
# ---------------------------------------------------------------------------


def test_load_from_file(tmp_path, rabi_program):
    out = tmp_path / "rabi.qp"
    out.write_text(dumps(rabi_program))
    p = load(str(out))
    assert dumps(p) == dumps(rabi_program)


# ---------------------------------------------------------------------------
# Comments and blank lines
# ---------------------------------------------------------------------------


def test_loads_handles_inline_comments():
    text = '#!QProgram 1.0\n\nbody:\n  var x   # a comment\n  set_frequency "bus" 5e9  # another\n'
    p = loads(text)
    assert len(p.variables) == 1


def test_loads_handles_blank_lines():
    text = "#!QProgram 1.0\n\n\nbody:\n\n  var x\n\n"
    p = loads(text)
    assert len(p.variables) == 1


# ---------------------------------------------------------------------------
# Bus path resolution
# ---------------------------------------------------------------------------


def test_loads_resolves_bus_path_against_schema():
    text = (
        "#!QProgram 1.0\n\n"
        "schema:\n"
        "  element q:\n"
        "    drive info=IQ\n"
        "    readout info=IQ+acquires\n"
        "\n"
        "body:\n"
        '  play q[0].drive "wf"\n'
    )
    p = loads(text)

    op = p.body.elements[0]
    assert isinstance(op.bus, BusRef)
    assert op.bus.element == "q"
    assert op.bus.idx == 0
    assert op.bus.kind == "drive"


def test_loads_bus_path_tuple_index():
    text = "#!QProgram 1.0\n\nschema:\n  element c:\n    flux info=single\n\nbody:\n  set_offset c[0,1].flux 0.5\n"
    p = loads(text)

    op = p.body.elements[0]
    assert isinstance(op.bus, BusRef)
    assert op.bus.idx == (0, 1)


def test_loads_invalid_bus_path_raises():
    text = '#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ\n\nbody:\n  play q[0].nonexistent "wf"\n'
    with pytest.raises(ParseError, match="does not resolve"):
        loads(text)
