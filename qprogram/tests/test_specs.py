"""Tests for the default/special-case serialize/parse callbacks in _specs."""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import (
    CrosstalkMatrix,
    MeasurementHandle,
    QProgram,
    Variable,
)
from qprogram.blocks import Average, ForLoop, Loop
from qprogram.operations import GetParameter, SetCrosstalk, Sync
from qprogram.serialization._specs import (
    average_parse_header,
    average_serialize_header,
    default_parse_operation,
    default_serialize_operation,
    file_parse,
    get_parameter_parse,
    get_parameter_serialize,
    range_parse,
    range_write,
    set_crosstalk_parse,
    set_crosstalk_serialize,
    sync_parse,
    sync_serialize,
    values_parse,
    values_write,
)
from qprogram.serialization.parser import _Parser
from qprogram.serialization.registry import get_operation_spec
from qprogram.serialization.writer import _Writer


def _writer() -> _Writer:
    p = QProgram(label="x")
    p.variable("freq")
    p.variable("loopvar")
    w = _Writer(p)
    w._allocate_var_idents()
    return w


def _parser(body: str = "") -> _Parser:
    text = f"#!QProgram 1.0\n\nbody:\n{body}"
    p = _Parser(text)
    p._parse_header()
    return p


# ---------------------------------------------------------------------------
# default_serialize_operation / default_parse_operation
# ---------------------------------------------------------------------------


def test_default_serialize_operation_required_positional():
    spec = get_operation_spec(None, "play")
    op = spec.cls("bus", "wf")  # type: ignore[union-attr]
    text = default_serialize_operation(op, spec, _writer())  # type: ignore[arg-type]
    assert text.startswith("play")
    assert '"bus"' in text
    assert '"wf"' in text


def test_default_serialize_operation_optional_kwarg_skipped_at_default():
    """When an optional parameter is at its default, the serializer omits it."""
    # Measure.save_adc is gone; returns=("iq",) is the default.
    spec = get_operation_spec(None, "measure")
    op = spec.cls("bus", "wf", "weights", handle=MeasurementHandle("m0"))  # type: ignore[union-attr]
    text = default_serialize_operation(op, spec, _writer())  # type: ignore[arg-type]
    assert "returns=" not in text


def test_default_serialize_operation_optional_kwarg_emitted_when_non_default():
    spec = get_operation_spec(None, "measure")
    op = spec.cls("bus", "wf", "weights", handle=MeasurementHandle("m0"), returns=("iq", "raw"))  # type: ignore[union-attr]
    text = default_serialize_operation(op, spec, _writer())  # type: ignore[arg-type]
    assert 'returns="iq,raw"' in text


def test_default_parse_operation_positional():
    spec = get_operation_spec(None, "set_frequency")
    op = default_parse_operation(spec, ['"bus"', "5000000000.0"], _parser())  # type: ignore[arg-type]
    assert op.bus == "bus"
    assert op.frequency == 5e9


def test_default_parse_operation_kwarg():
    spec = get_operation_spec(None, "set_parameter")
    op = default_parse_operation(spec, ['"a"', '"p"', "5.0", "channel_id=3"], _parser())  # type: ignore[arg-type]
    assert op.channel_id == 3


def test_default_parse_operation_extra_positional_ignored():
    """Tokens past the signature length are silently dropped (pre-1.0 leniency)."""
    spec = get_operation_spec(None, "reset_phase")
    op = default_parse_operation(spec, ['"bus"', "garbage"], _parser())  # type: ignore[arg-type]
    assert op.bus == "bus"


# ---------------------------------------------------------------------------
# sync_serialize / sync_parse
# ---------------------------------------------------------------------------


def test_sync_serialize_empty():
    op = Sync()
    assert sync_serialize(op, _writer()) == "sync"


def test_sync_serialize_with_targets():
    op = Sync(["a", "b"])
    assert sync_serialize(op, _writer()) == 'sync "a" "b"'


def test_sync_parse_empty():
    op = sync_parse([], _parser())
    assert op.targets is None


def test_sync_parse_with_tokens():
    op = sync_parse(['"a"', '"b"'], _parser())
    assert op.targets == ["a", "b"]


# ---------------------------------------------------------------------------
# get_parameter_serialize / get_parameter_parse
# ---------------------------------------------------------------------------


def test_get_parameter_serialize_basic():
    p = QProgram()
    var = p.variable("freq")
    w = _Writer(p)
    w._allocate_var_idents()
    op = GetParameter(variable=var, alias="cluster", parameter="lo")
    text = get_parameter_serialize(op, w)
    assert "->" in text
    assert "freq" in text
    assert '"cluster"' in text


def test_get_parameter_serialize_with_channel_id():
    p = QProgram()
    var = p.variable("freq")
    w = _Writer(p)
    w._allocate_var_idents()
    op = GetParameter(variable=var, alias="cluster", parameter="lo", channel_id=5)
    text = get_parameter_serialize(op, w)
    assert "channel_id=5" in text


def test_get_parameter_parse_with_arrow():
    p = _parser()
    op = get_parameter_parse(['"alias"', '"param"', "->", "result"], p)
    assert isinstance(op, GetParameter)
    assert op.variable.id == "result"
    assert op.alias == "alias"
    assert op.parameter == "param"


def test_get_parameter_parse_with_channel_id():
    p = _parser()
    op = get_parameter_parse(['"alias"', '"param"', "channel_id=3", "->", "result"], p)
    assert op.channel_id == 3


def test_get_parameter_parse_missing_arrow_raises():
    p = _parser()
    with pytest.raises(Exception, match=r"-> <var>"):
        get_parameter_parse(['"alias"', '"param"'], p)


def test_get_parameter_parse_missing_alias_raises():
    p = _parser()
    # Arrow present but body before it is empty (< 2 tokens).
    with pytest.raises(Exception, match="alias and parameter"):
        get_parameter_parse(['"alias"', "->", "result"], p)


# ---------------------------------------------------------------------------
# set_crosstalk
# ---------------------------------------------------------------------------


def test_set_crosstalk_serialize():

    op = SetCrosstalk(crosstalk=CrosstalkMatrix())
    assert set_crosstalk_serialize(op, _writer()) == "set_crosstalk crosstalk"


def test_set_crosstalk_parse():
    op = set_crosstalk_parse([], _parser())
    assert isinstance(op, SetCrosstalk)


# ---------------------------------------------------------------------------
# Average block header
# ---------------------------------------------------------------------------


def test_average_serialize_header():

    block = Average(shots=1000)
    assert average_serialize_header(block, _writer()) == "average 1000"


def test_average_parse_header():

    block = average_parse_header(["1000"], _parser())
    assert isinstance(block, Average)
    assert block.shots == 1000


def test_average_parse_header_empty_raises():
    with pytest.raises(Exception, match="requires a shot count"):
        average_parse_header([], _parser())


def test_average_parse_header_invalid_int_raises():
    with pytest.raises(Exception, match="invalid shots"):
        average_parse_header(["abc"], _parser())


# ---------------------------------------------------------------------------
# Sweep generators: range, values, file
# ---------------------------------------------------------------------------


def test_range_parse_two_args():
    v = Variable("x")
    fl = range_parse(v, "0, 10", _parser())
    assert fl.start == 0
    assert fl.stop == 10
    assert fl.step == 1


def test_range_parse_three_args():
    v = Variable("x")
    fl = range_parse(v, "0.0, 1.0, 0.1", _parser())
    assert fl.step == 0.1


def test_range_parse_wrong_arity_raises():
    v = Variable("x")
    with pytest.raises(Exception, match="2 or 3"):
        range_parse(v, "1", _parser())


def test_range_write():

    v = Variable("x")
    fl = ForLoop(v, 0.0, 1.0, 0.1)
    assert range_write(fl, _writer()) == "range(0.0, 1.0, 0.1)"


def test_values_parse():
    v = Variable("x")
    lp = values_parse(v, "[0.0, 0.5, 1.0]", _parser())
    assert np.array_equal(lp.values, np.array([0.0, 0.5, 1.0]))


def test_values_parse_without_brackets():
    """Resilient against missing brackets — just splits on commas."""
    v = Variable("x")
    lp = values_parse(v, "0.0, 0.5, 1.0", _parser())
    assert np.array_equal(lp.values, np.array([0.0, 0.5, 1.0]))


def test_values_write_short():

    v = Variable("x")
    lp = Loop(v, np.array([0.0, 0.5, 1.0]))
    assert values_write(lp, _writer()) == "[0.0, 0.5, 1.0]"


def test_values_write_long_truncated():

    v = Variable("x")
    lp = Loop(v, np.arange(100))
    out = values_write(lp, _writer())
    assert "..." in out


def test_file_parse_loads_npy(tmp_path):
    arr_path = tmp_path / "values.npy"
    np.save(arr_path, np.array([0.1, 0.2, 0.3]))
    v = Variable("x")
    lp = file_parse(v, f'"{arr_path}"', _parser())
    assert np.array_equal(lp.values, np.array([0.1, 0.2, 0.3]))
