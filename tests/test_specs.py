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
"""Tests for the default and special-case serialize/parse callbacks in :mod:`qprogram.serialization._specs`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from qprogram import (
    MeasurementHandle,
    QProgram,
)
from qprogram.blocks import Average
from qprogram.operations import GetParameter, SetFrequency, SetOffset, Sync
from qprogram.serialization._specs import (
    average_parse_header,
    average_serialize_header,
    default_parse_operation,
    default_serialize_operation,
    get_parameter_parse,
    get_parameter_serialize,
    sync_parse,
    sync_serialize,
)
from qprogram.serialization.parser import _Parser
from qprogram.serialization.registry import get_operation_spec
from qprogram.serialization.writer import _Writer

if TYPE_CHECKING:
    from collections.abc import Callable

    from qprogram.operations.operation import Operation


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
    assert spec is not None
    op = cast("Callable[..., Operation]", spec.cls)("bus", "wf")
    text = default_serialize_operation(op, spec, _writer())
    assert text.startswith("play")
    assert '"bus"' in text
    assert '"wf"' in text


def test_default_serialize_operation_optional_kwarg_skipped_at_default():
    """When an optional parameter is at its default, the serializer omits it."""
    # ``fields=("iq",)`` is the default, so it must not appear in the text.
    spec = get_operation_spec(None, "measure")
    assert spec is not None
    op = cast("Callable[..., Operation]", spec.cls)("bus", "wf", "weights", handle=MeasurementHandle("m0"))
    text = default_serialize_operation(op, spec, _writer())
    assert "fields=" not in text


def test_default_serialize_operation_optional_kwarg_emitted_when_non_default():
    spec = get_operation_spec(None, "measure")
    assert spec is not None
    op = cast("Callable[..., Operation]", spec.cls)(
        "bus", "wf", "weights", handle=MeasurementHandle("m0"), fields=("iq", "raw")
    )
    text = default_serialize_operation(op, spec, _writer())
    assert 'fields=["iq", "raw"]' in text


def test_default_parse_operation_positional():
    spec = get_operation_spec(None, "set_frequency")
    assert spec is not None
    op = default_parse_operation(spec, ['"bus"', "5000000000.0"], _parser())
    assert isinstance(op, SetFrequency)
    assert op.bus == "bus"
    assert op.frequency == 5e9


def test_default_parse_operation_kwarg():
    spec = get_operation_spec(None, "set_offset")
    assert spec is not None
    op = default_parse_operation(spec, ['"bus"', "0.2", "offset_path1=0.3"], _parser())
    assert isinstance(op, SetOffset)
    assert op.offset_path1 == 0.3


def test_default_parse_operation_extra_positional_raises():
    """Tokens past the signature length are a hard error.

    Dropping them silently would load a different program — ``wait "bus" 100 - t`` arriving as
    ``wait "bus" 100``.
    """
    spec = get_operation_spec(None, "reset_phase")
    assert spec is not None
    ctx = _parser()
    with pytest.raises(Exception, match="too many arguments"):
        default_parse_operation(spec, ['"bus"', "garbage"], ctx)


def test_default_parse_operation_unknown_kwarg_raises():
    """A kwarg the constructor rejects surfaces as a line-tagged parse error."""
    spec = get_operation_spec(None, "reset_phase")
    assert spec is not None
    ctx = _parser()
    with pytest.raises(Exception, match="cannot construct 'ResetPhase'"):
        default_parse_operation(spec, ['"bus"', "bogus=1"], ctx)


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
    op = GetParameter(variable=var, bus="cluster", parameter="lo")
    text = get_parameter_serialize(op, w)
    assert "->" in text
    assert "freq" in text
    assert '"cluster"' in text


def test_get_parameter_parse_with_arrow():
    p = _parser()
    op = get_parameter_parse(['"cluster"', '"param"', "->", "result"], p)
    assert isinstance(op, GetParameter)
    assert op.variable.id == "result"
    assert op.bus == "cluster"
    assert op.parameter == "param"


def test_get_parameter_parse_missing_arrow_raises():
    p = _parser()
    with pytest.raises(Exception, match=r"-> <var>"):
        get_parameter_parse(['"cluster"', '"param"'], p)


def test_get_parameter_parse_missing_bus_raises():
    p = _parser()
    # Arrow present but body before it is empty (< 2 tokens).
    with pytest.raises(Exception, match="bus and parameter"):
        get_parameter_parse(['"cluster"', "->", "result"], p)


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
    ctx = _parser()
    with pytest.raises(Exception, match="requires a shot count"):
        average_parse_header([], ctx)


def test_average_parse_header_invalid_int_raises():
    ctx = _parser()
    with pytest.raises(Exception, match="invalid shots"):
        average_parse_header(["abc"], ctx)
