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
"""Tests for the canonical ``qp.lark`` grammar, cross-checked against the production parser.

Two directions:

- **Positive**: every text the writer emits must parse under the grammar — exercised by a
  hand-built corpus covering each feature and by the round-trip hypothesis strategies.
- **Negative**: a curated corpus of *syntactically* malformed inputs must be rejected by both
  the grammar and the production parser. (Semantic errors — unknown operations, duplicate
  variables, unresolvable bus paths — are intentionally out of the grammar's scope: it
  over-approximates them as valid syntax; the parser rejects them post-parse.)
"""

from __future__ import annotations

import lark
import numpy as np
import pytest
from hypothesis import given, settings
from test_round_trip import UNICODE_LINE_BREAKS
from test_round_trip_property import fragment_programs, programs

import qprogram as qp
from qprogram import Fragment, QProgram, fragment
from qprogram.buses import BusSchema
from qprogram.grammar import grammar_text, parser
from qprogram.sweeps import Range, Values
from qprogram.waveforms import Arbitrary, FlatTop, Gaussian, IQDrag, IQPair, Square

REFERENCE_PARSER = parser()


def assert_grammatical(text: str) -> None:
    try:
        REFERENCE_PARSER.parse(text)
    except lark.exceptions.LarkError as e:  # pragma: no cover — failure path
        pytest.fail(f"writer output rejected by qp.lark:\n{text}\n--- {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Positive corpus — one builder per feature family
# ---------------------------------------------------------------------------


def _full_feature_program() -> QProgram:
    p = QProgram(label='say "hi" #notcomment', description="multi\nline\tdesc")
    g = p.variable("g", label="Gain", units="a.u.", description="swept")
    t = p.variable("t", units="ns")
    p.play("drive_q0", "pi_alias")
    p.play("drive_q0", Gaussian(amplitude=0.5, duration=40, sigma=8.0))
    p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1))
    p.play("flux_c01", FlatTop(amplitude=0.3, duration=200, smooth_duration=5))
    p.play("flux_c01", Arbitrary(np.linspace(0, 1, 60)))
    p.wait("drive_q0", 100)
    p.wait("drive_q0", t + 4)
    p.wait("drive_q0", (100 - t))
    p.sync()
    p.sync(["drive_q0", "readout_q0"])
    p.set_frequency("drive_q0", 5e9)
    p.set_frequency("drive_q0", (g * 2) + 1e6)
    p.set_phase("drive_q0", 1.5708)
    p.reset_phase("drive_q0")
    p.set_gain("drive_q0", qp.where(g < 0.5, g, 0.5))
    p.set_gain("drive_q0", qp.sin(t))
    p.set_gain("drive_q0", qp.minimum(g, 0.9))
    p.set_gain("drive_q0", -g)
    p.set_offset("flux_q0", 0.1, 0.2)
    p.set_parameter("cluster", "lo_frequency", 5e9)
    p.get_parameter("cluster", "lo_frequency")
    with p.average(1000), p.sweep(g, Range(0.0, 1.0, 0.01)):
        p.set_gain("drive_q0", g)
        m = p.measure(
            "readout_q0",
            IQPair(Square(1.0, 2000), Square(0.0, 2000)),
            "weights",
            fields=("iq", "state"),
        )
        with p.if_(m.state == 0):
            p.play("drive_q0", "id")
        with p.elif_(m.state == 1):
            p.play("drive_q0", "pi")
        with p.else_():
            p.sync()
    h = p.variable("h")
    k = p.variable("k")
    with p.sweep(h, Range(0.0, 1.0, 0.5)) | p.sweep(k, Range(5.0, 10.0, 2.5)):
        p.set_gain("drive_q0", h)
    with p.sweep(t, Values(np.asarray([1.0, 2.0, 4.0]))), p.block():
        p.wait("aux", t)
    return p


def _schema_program() -> QProgram:
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})
    schema.add_element("c", {"flux": ("single", False)})
    p = QProgram(schema=schema)
    f = p.variable("f")
    with p.sweep(f, Range(4e9, 5e9, 0.5e9)):
        p.set_frequency(schema.q[0].drive, f)
        p.play(schema.q[0].drive, IQPair(Square(1.0, 10), Square(0.0, 10)))
        p.measure(schema.q[0].readout, IQPair(Square(1.0, 10), Square(0.0, 10)), "w")
    p.set_offset(schema.c[0, 1].flux, 0.5)  # tuple-index bus path: c[0,1].flux
    return p


def _fragment_program() -> QProgram:
    @fragment
    def x_pulse(f, drive, amp):
        f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))

    @fragment
    def echo(f, drive, t):
        f.wait(drive, t)
        f.call(x_pulse, drive, 1.0)
        f.wait(drive, (t + 4))

    scan = Fragment("scan_point")
    ro = scan.parameter("readout")
    n = scan.variable("n", label="inner")
    with scan.sweep(n, Range(0, 10, 1)):
        scan.wait("aux", n)
    scan.measure(ro, "ro_wf", "weights")

    p = QProgram(label="composed")
    g = p.variable("g")
    with p.average(100), p.sweep(g, Range(0.0, 1.0, 0.25)):
        p.call(echo, "drive_q0", 60)
        p.call(scan, readout="readout_q0")
        p.call(scan, readout="readout_q1")
    return p


def _vendor_program(dummy_vendor_active: bool = True) -> QProgram:  # ruff: ignore[unused-function-argument]
    p = QProgram()
    p.dummy.set_markers("drive_q0", "0001")
    p.dummy.acquire("readout_q0", "weights")
    return p


@pytest.mark.parametrize(
    "builder",
    [_full_feature_program, _schema_program, _fragment_program],
    ids=["full-feature", "schema", "fragments"],
)
def test_writer_output_is_grammatical(builder):
    assert_grammatical(qp.dumps(builder()))


def test_vendor_program_is_grammatical(dummy_vendor):  # ruff: ignore[unused-function-argument]
    assert_grammatical(qp.dumps(_vendor_program()))


def test_empty_body_and_header_only_forms():
    assert_grammatical("#!QProgram 1.0\n\nbody:\n")
    assert_grammatical("#!QProgram 1.0\nbody:\n")
    assert_grammatical("\n\n#!QProgram 1.0\n\nbody:\n")  # leading blank lines tolerated


def test_comments_anywhere_are_transparent():
    text = (
        "#!QProgram 1.0\n"
        "# top comment\n"
        "\n"
        "body:\n"
        "  # comment at statement indent\n"
        '  play "drive" "pi"  # trailing comment with "quotes"\n'
        "      # over-indented comment must not confuse the indenter\n"
        '  wait "drive" 4\n'
        "  average 10:\n"
        "    # comment inside a block\n"
        "    sync\n"
        "  # outdented comment between statements\n"
        '  play "drive" "pi2"\n'
    )
    qp.loads(text)  # the production parser accepts it...
    assert_grammatical(text)  # ...and so must the grammar


def test_hand_written_spacing_variants():
    # The parser tolerates a space before the fragment paren; so does the grammar.
    assert_grammatical('#!QProgram 1.0\n\nfragment f1 (bus):\n  sync\n\nbody:\n  f1("drive")\n')


@pytest.mark.parametrize("char", UNICODE_LINE_BREAKS)
def test_string_holding_a_unicode_line_break(char):
    """`STRING` admits these raw, so both readers must keep them inside their line.

    ``str.splitlines`` breaks on all eight, which is the trap the production parser used to fall
    into while the grammar read the document correctly.
    """
    p = qp.QProgram(label=f"a{char}b")
    p.variable("v", units=char)
    p.play("drive", "pi")
    text = qp.dumps(p)
    qp.loads(text)  # the production parser accepts it...
    assert_grammatical(text)  # ...and so must the grammar


# ---------------------------------------------------------------------------
# Positive — hypothesis: anything the writer can emit parses under the grammar
# ---------------------------------------------------------------------------


@given(programs())
@settings(max_examples=40, deadline=None)
def test_property_generated_programs_are_grammatical(p: QProgram) -> None:
    assert_grammatical(qp.dumps(p))


@given(fragment_programs())
@settings(max_examples=25, deadline=None)
def test_property_fragment_programs_are_grammatical(p: QProgram) -> None:
    assert_grammatical(qp.dumps(p))


# ---------------------------------------------------------------------------
# Negative — syntactic malformations rejected by BOTH grammar and parser
# ---------------------------------------------------------------------------

_SYNTACTIC_REJECTS = {
    "missing-header": 'body:\n  play "d" "p"\n',
    "require-without-version": "#!QProgram 1.0\n\nrequire qblox\n\nbody:\n",
    "block-missing-colon": '#!QProgram 1.0\n\nbody:\n  average 10\n    play "d" "p"\n',
    "unquoted-metadata-label": "#!QProgram 1.0\n\nmetadata:\n  label: rabi experiment\n\nbody:\n",
    "var-with-spaces": "#!QProgram 1.0\n\nbody:\n  var Wait Duration (ns)\n",
    "var-id-starts-digit": "#!QProgram 1.0\n\nbody:\n  var 1freq\n",
    "unparenthesized-expression": '#!QProgram 1.0\n\nbody:\n  wait "d" 100 - t\n',
    "unterminated-string": '#!QProgram 1.0\n\nbody:\n  play "drive\n',
    "for-without-in": "#!QProgram 1.0\n\nbody:\n  for g range(0, 1, 0.1):\n    sync\n",
    "if-without-condition": "#!QProgram 1.0\n\nbody:\n  if:\n    sync\n",
    "else-with-condition": ("#!QProgram 1.0\n\nbody:\n  if m0.state == 0:\n    sync\n  else m0.state:\n    sync\n"),
    "dangling-dict": '#!QProgram 1.0\n\nbody:\n  set_parameter "a" "b" matrix={"a": 1.0\n',
    "fragment-missing-parens": "#!QProgram 1.0\n\nfragment f1:\n  sync\n\nbody:\n",
}


@pytest.mark.parametrize("text", _SYNTACTIC_REJECTS.values(), ids=_SYNTACTIC_REJECTS.keys())
def test_syntactic_malformations_rejected_by_both(text: str) -> None:
    with pytest.raises(qp.ParseError):
        qp.loads(text)
    with pytest.raises(lark.exceptions.LarkError):
        REFERENCE_PARSER.parse(text)


# ---------------------------------------------------------------------------
# Grammar hygiene
# ---------------------------------------------------------------------------


def test_grammar_text_ships_with_package():
    text = grammar_text()
    assert "start:" in text
    assert "_INDENT" in text


def test_reserved_format_keywords_match_grammar_keywords():
    """Every hard keyword the grammar declares is reserved as an identifier in the DSL.

    Otherwise ``var for`` would be accepted by the parser but rejected by the grammar.
    """
    from qprogram import RESERVED_KEYWORDS  # ruff: ignore[import-outside-top-level]

    hard_keywords = {"var", "for", "in", "if", "elif", "else", "and", "or", "not", "true", "false", "null", "fragment"}
    assert hard_keywords <= RESERVED_KEYWORDS
