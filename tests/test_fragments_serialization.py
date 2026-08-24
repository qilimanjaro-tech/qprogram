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
"""Tests for fragments through the ``.qp`` text format — round-trip, strict errors, vendor ``require`` lines."""

from __future__ import annotations

import numpy as np
import pytest

import qprogram as qp
from qprogram import Fragment, QProgram, fragment
from qprogram.buses import BusSchema
from qprogram.operations import Call
from qprogram.serialization.parser import ParseError
from qprogram.sweeps import Range, Values
from qprogram.waveforms import FlatTop, Gaussian, IQPair, Square

# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def _build_composed_program() -> QProgram:
    @fragment
    def x_pulse(f, drive, amp):
        f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))

    @fragment
    def echo(f, drive, t):
        f.wait(drive, t)
        f.play(drive, "pi")
        f.wait(drive, (t + 4))

    @fragment
    def cz(f, flux, drive, amp, t):
        f.play(flux, FlatTop(amplitude=amp, duration=200, smooth_duration=5))
        f.call(echo, drive, t)

    scan = Fragment("scan_point")
    ro = scan.parameter("readout")
    n = scan.variable("n", label="inner sweep", units="ns")
    with scan.sweep(n, Range(0, 10, 1)):
        scan.wait("aux", n)
    scan.measure(ro, "ro_wf", "weights")

    p = QProgram(label="composed", description="fragment round-trip")
    g = p.variable("g")
    with p.average(1000), p.sweep(g, Range(0, 1, 0.01)):
        p.call(x_pulse, "drive_q0", g)
        p.call(cz, "flux_c01", "drive_q1", (g * 2), 120)
        p.call(scan, readout="readout_q0")
        p.call(scan, readout="readout_q1")
    return p


def test_round_trip_structural_equality():
    p = _build_composed_program()
    p2 = qp.loads(qp.dumps(p))
    assert p2.label == p.label
    assert p2.description == p.description
    assert p2.variables == p.variables
    assert p2.body == p.body
    assert p2.fragments == p.fragments


def test_round_trip_byte_stability():
    text = qp.dumps(_build_composed_program())
    assert qp.dumps(qp.loads(text)) == text


def test_round_trip_expansion_parity():
    p = _build_composed_program()
    p2 = qp.loads(qp.dumps(p))
    e1, e2 = p.expand(), p2.expand()
    assert e1.body == e2.body
    assert e1.variables == e2.variables


def test_fragment_defs_emitted_in_dependency_order():
    inner = Fragment("zz_inner")  # name sorts after the caller, so order must come from deps
    inner.sync()
    outer = Fragment("aa_outer")
    outer.call(inner)
    p = QProgram()
    p.call(outer)
    text = qp.dumps(p)
    assert text.index("fragment zz_inner") < text.index("fragment aa_outer")
    assert list(qp.loads(text).fragments) == ["zz_inner", "aa_outer"]


def test_unused_fragment_definition_round_trips():
    text = '#!QProgram 1.0\n\nfragment unused(bus):\n  sync\n\nbody:\n  wait "drive" 4\n'
    p = qp.loads(text)
    assert "unused" in p.fragments
    assert qp.dumps(qp.loads(qp.dumps(p))) == qp.dumps(p)


def test_schema_busref_call_argument_round_trips():
    schema = BusSchema.transmon()

    @fragment
    def readout_block(f, ro):
        f.sync()
        f.measure(ro, "ro_wf", "weights")

    p = QProgram(schema=schema)
    p.play(schema.q[0].drive, IQPair(Square(1.0, 10), Square(0.0, 10)))
    p.call(readout_block, schema.q[0].readout)
    text = qp.dumps(p)
    assert "readout_block(q[0].readout)" in text
    p2 = qp.loads(text)
    assert p2.body == p.body
    assert p2.fragments == p.fragments
    call = next(n for n in p2.body.walk() if isinstance(n, Call))
    from qprogram.buses import BusRef  # ruff: ignore[import-outside-top-level]

    assert isinstance(call.arguments["ro"], BusRef)


def test_quoted_path_like_argument_stays_string():
    schema = BusSchema.transmon()

    @fragment
    def f1(f, bus):
        f.wait(bus, 4)

    p = QProgram(schema=schema)
    p.play(schema.q[0].drive, IQPair(Square(1.0, 10), Square(0.0, 10)))  # attach schema in file
    p.call(f1, "q[0].drive")  # quoted on the wire — must NOT promote to a BusRef
    p2 = qp.loads(qp.dumps(p))
    call = next(n for n in p2.body.walk() if isinstance(n, Call))
    from qprogram.buses import BusRef  # ruff: ignore[import-outside-top-level]

    assert not isinstance(call.arguments["bus"], BusRef)
    assert str(call.arguments["bus"]) == "q[0].drive"


def test_keyword_arguments_parse():
    text = '#!QProgram 1.0\n\nfragment f1(bus, t):\n  wait bus t\n\nbody:\n  f1(t=8, bus="drive")\n'
    p = qp.loads(text)
    call = p.body.elements[0]
    assert isinstance(call, Call)
    assert call.arguments == {"bus": "drive", "t": 8}


def test_expression_and_waveform_arguments_parse():
    text = (
        "#!QProgram 1.0\n"
        "\n"
        "fragment f1(wf, t):\n"
        '  play "drive" wf\n'
        '  wait "drive" (t * 2)\n'
        "\n"
        "body:\n"
        "  var g\n"
        "  for g in Range(start=0, stop=1, step=0.1):\n"
        "    f1(Gaussian(amplitude=0.5, duration=40, sigma=8), (g + 1))\n"
    )
    p = qp.loads(text)
    call = next(n for n in p.body.walk() if isinstance(n, Call))
    assert call.arguments["wf"] == Gaussian(amplitude=0.5, duration=40, sigma=8)
    expanded = p.expand()
    assert qp.dumps(expanded)  # expansion of parsed program serializes cleanly


def test_fragment_with_vendor_op_emits_require(dummy_vendor):  # ruff: ignore[unused-function-argument]
    frag = Fragment("uses_vendor")
    bus = frag.parameter("bus")
    frag.dummy.set_markers(bus, "0001")
    p = QProgram()
    p.call(frag, "drive_q0")
    text = qp.dumps(p)
    assert "require dummy 0.1" in text
    p2 = qp.loads(text)
    assert p2.fragments == p.fragments
    assert p2.body == p.body


def test_fragment_measurement_auto_name_allocates_per_fragment():
    text = (
        "#!QProgram 1.0\n"
        "\n"
        "fragment ro(bus):\n"
        '  measure bus "wf" "w"\n'  # no name= -> auto-allocated within the fragment
        "\n"
        "body:\n"
        '  ro("readout_q0")\n'
    )
    p = qp.loads(text)
    frag = p.fragments["ro"]
    measure = frag.body.elements[0]
    assert measure.name == "m0"


# ---------------------------------------------------------------------------
# Strict errors
# ---------------------------------------------------------------------------


def test_unknown_fragment_call_raises():
    text = '#!QProgram 1.0\n\nbody:\n  mystery("drive", 4)\n'
    with pytest.raises(ParseError, match="unknown fragment 'mystery'"):
        qp.loads(text)


def test_waveform_constructor_as_statement_raises():
    text = "#!QProgram 1.0\n\nbody:\n  Gaussian(amplitude=0.5, duration=40, sigma=8)\n"
    with pytest.raises(ParseError, match="cannot stand alone as a statement"):
        qp.loads(text)


def test_fragment_after_body_raises():
    text = '#!QProgram 1.0\n\nbody:\n  wait "d" 4\n\nfragment f1(bus):\n  sync\n'
    with pytest.raises(ParseError, match="before the `body:` section"):
        qp.loads(text)


def test_duplicate_fragment_definition_raises():
    text = "#!QProgram 1.0\n\nfragment f1(a):\n  sync\n\nfragment f1(b):\n  sync\n\nbody:\n  f1(1)\n"
    with pytest.raises(ParseError, match="duplicate fragment definition"):
        qp.loads(text)


def test_malformed_fragment_header_raises():
    text = "#!QProgram 1.0\n\nfragment f1 a b:\n  sync\n\nbody:\n"
    with pytest.raises(ParseError, match="invalid fragment header"):
        qp.loads(text)


def test_reserved_fragment_name_raises():
    text = "#!QProgram 1.0\n\nfragment match(a):\n  sync\n\nbody:\n"
    with pytest.raises(ParseError, match="reserved"):
        qp.loads(text)


def test_invalid_parameter_name_raises():
    text = "#!QProgram 1.0\n\nfragment f1(2bad):\n  sync\n\nbody:\n"
    with pytest.raises(ParseError, match="invalid fragment parameter"):
        qp.loads(text)


def test_duplicate_parameter_raises():
    text = "#!QProgram 1.0\n\nfragment f1(a, a):\n  sync\n\nbody:\n"
    with pytest.raises(ParseError, match="already declared"):
        qp.loads(text)


def test_argument_count_mismatch_raises():
    text = "#!QProgram 1.0\n\nfragment f1(a, b):\n  wait a b\n\nbody:\n  f1(1)\n"
    with pytest.raises(ParseError, match="missing argument"):
        qp.loads(text)


def test_unknown_keyword_argument_raises():
    text = "#!QProgram 1.0\n\nfragment f1(a):\n  wait a 4\n\nbody:\n  f1(a=1, b=2)\n"
    with pytest.raises(ParseError, match="no parameter 'b'"):
        qp.loads(text)


def test_positional_after_keyword_raises():
    text = "#!QProgram 1.0\n\nfragment f1(a, b):\n  wait a b\n\nbody:\n  f1(a=1, 2)\n"
    with pytest.raises(ParseError, match="positional argument after keyword"):
        qp.loads(text)


def test_call_to_later_defined_fragment_raises():
    """Define-before-use also applies between fragments — gives topological order for free."""
    text = (
        "#!QProgram 1.0\n"
        "\n"
        "fragment outer(bus):\n"
        "  inner(bus)\n"
        "\n"
        "fragment inner(bus):\n"
        "  sync\n"
        "\n"
        "body:\n"
        '  outer("drive")\n'
    )
    with pytest.raises(ParseError, match="unknown fragment 'inner'"):
        qp.loads(text)


def test_fragment_keyword_inside_body_raises():
    text = "#!QProgram 1.0\n\nbody:\n  fragment f1(a):\n    sync\n"
    with pytest.raises(ParseError, match="unknown block keyword 'fragment'"):
        qp.loads(text)


def test_dumps_rejects_bare_fragment():
    frag = Fragment("f1")
    frag.sync()
    with pytest.raises(qp.SerializationError, match="cannot serialize Fragment"):
        qp.dumps(frag)


# ---------------------------------------------------------------------------
# Hand-written file -> Python equivalence
# ---------------------------------------------------------------------------


def test_hand_written_file_matches_python_built_program():
    text = (
        "#!QProgram 1.0\n"
        "\n"
        "fragment x_pulse(drive, amp):\n"
        "  play drive Gaussian(amplitude=amp, duration=40, sigma=8)\n"
        "\n"
        "body:\n"
        "  var g\n"
        "  average 100:\n"
        "    for g in Range(start=0, stop=1, step=0.1):\n"
        '      x_pulse("drive_q0", g)\n'
    )

    @fragment
    def x_pulse(f, drive, amp):
        f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))

    p = QProgram()
    g = p.variable("g")
    with p.average(100), p.sweep(g, Range(0, 1, 0.1)):
        p.call(x_pulse, "drive_q0", g)

    loaded = qp.loads(text)
    assert loaded.body == p.body
    assert loaded.fragments == p.fragments
    assert loaded.expand().body == p.expand().body


def test_arbitrary_array_values_inside_fragment_round_trip():
    frag = Fragment("sweep")
    n = frag.variable("n")
    values = np.linspace(0.0, 1.0, 75)  # long array — must not truncate inside a fragment
    with frag.sweep(n, Values(values)):
        frag.set_gain("bus", n)
    p = QProgram()
    p.call(frag)
    p2 = qp.loads(qp.dumps(p))
    loaded_loop = p2.fragments["sweep"].body.elements[0]
    assert np.array_equal(loaded_loop.source.values(), values)
