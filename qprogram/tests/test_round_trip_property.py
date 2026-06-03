"""Property-based round-trip tests: ``loads(dumps(p))`` equals ``p`` over generated programs.

These are the mechanical safety net behind the format's central guarantee — any structure the
builder can produce must survive the text round-trip bit-for-bit (structural equality) and the
re-emitted text must be byte-stable. Each strategy is deliberately adversarial about the things
that have broken in the past: long arrays, quotes/backslashes/``#``/newlines in metadata text,
path-shaped raw bus strings, ints vs floats, and deep block nesting.
"""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from qprogram import CrosstalkMatrix, QProgram, dumps, loads
from qprogram.buses import BusSchema
from qprogram.waveforms import Arbitrary, Gaussian, IQDrag, IQPair, Square

# ---------------------------------------------------------------------------
# Value strategies
# ---------------------------------------------------------------------------

# Finite floats only: the builder rejects non-finite loop bounds, and `nan != nan` would make
# structural equality vacuously fail for reasons unrelated to serialization.
finite_floats = st.floats(allow_nan=False, allow_infinity=False, width=64)
small_ints = st.integers(min_value=-(10**9), max_value=10**9)
numbers = st.one_of(small_ints, finite_floats)

# Free-form human text — printable unicode plus the historically dangerous characters.
human_text = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")),
    min_size=0,
    max_size=40,
).map(lambda s: s + "")
spicy_text = st.one_of(
    human_text,
    st.sampled_from(['say "hi"', "back\\slash", "a # not-a-comment", 'mix\\"of"both', "tab\tand\nnewline"]),
)

# Raw-string bus names: printable, plus adversarial path-shaped strings that must NOT be
# promoted to BusRefs on reload.
bus_names = st.one_of(
    st.from_regex(r"[A-Za-z][A-Za-z0-9_/]{0,15}", fullmatch=True),
    st.sampled_from(["q[0].drive", "c[0,1].flux", "weird bus name", 'qu"oted']),
)

waveform_aliases = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,12}", fullmatch=True)


def _var_ids(n: int) -> list[str]:
    return [f"v{i}" for i in range(n)]


# ---------------------------------------------------------------------------
# Waveform strategies
# ---------------------------------------------------------------------------


@st.composite
def single_waveforms(draw: st.DrawFn) -> object:
    kind = draw(st.sampled_from(["square", "gaussian", "arbitrary"]))
    if kind == "square":
        return Square(draw(numbers), draw(st.integers(min_value=1, max_value=4000)))
    if kind == "gaussian":
        return Gaussian(
            amplitude=draw(finite_floats),
            duration=draw(st.integers(min_value=1, max_value=4000)),
            sigma=draw(st.floats(min_value=0.1, max_value=100, allow_nan=False)),
        )
    n = draw(st.integers(min_value=1, max_value=120))  # deliberately beyond any truncation cutoff
    return Arbitrary(np.asarray(draw(st.lists(finite_floats, min_size=n, max_size=n))))


@st.composite
def iq_waveforms(draw: st.DrawFn) -> object:
    if draw(st.booleans()):
        dur = draw(st.integers(min_value=1, max_value=2000))
        return IQPair(Square(draw(finite_floats), dur), Square(draw(finite_floats), dur))
    return IQDrag(
        amplitude=draw(finite_floats),
        duration=draw(st.integers(min_value=1, max_value=2000)),
        sigma=draw(st.floats(min_value=0.1, max_value=100, allow_nan=False)),
        beta=draw(finite_floats),
    )


# ---------------------------------------------------------------------------
# The program strategy
# ---------------------------------------------------------------------------


@st.composite
def programs(draw: st.DrawFn) -> QProgram:
    p = QProgram(label=draw(spicy_text), description=draw(st.one_of(st.none(), spicy_text)))
    n_vars = draw(st.integers(min_value=0, max_value=3))
    variables = [
        p.variable(vid, label=draw(st.one_of(st.none(), spicy_text)), units=draw(st.one_of(st.none(), spicy_text)))
        for vid in _var_ids(n_vars)
    ]

    def expression(depth: int = 0) -> object:
        """A numeric value, a variable, or a small arithmetic tree over them."""
        if not variables or depth > 1 or draw(st.booleans()):
            return draw(numbers)
        left = draw(st.sampled_from(variables))
        choice = draw(st.sampled_from(["bare", "add", "mul", "neg"]))
        if choice == "bare":
            return left
        if choice == "add":
            return left + expression(depth + 1)
        if choice == "mul":
            return left * draw(numbers)
        return -left

    def emit_ops(depth: int) -> None:
        for _ in range(draw(st.integers(min_value=1, max_value=3))):
            kind = draw(
                st.sampled_from(
                    ["play", "play_iq", "wait", "sync", "set_frequency", "set_gain", "set_offset", "measure"],
                ),
            )
            bus = draw(bus_names)
            if kind == "play":
                p.play(bus, draw(st.one_of(waveform_aliases, single_waveforms())))
            elif kind == "play_iq":
                p.play(bus, draw(iq_waveforms()))
            elif kind == "wait":
                p.wait(bus, draw(st.one_of(st.integers(min_value=0, max_value=10**6), st.just(expression()))))
            elif kind == "sync":
                p.sync(draw(st.one_of(st.none(), st.lists(bus_names, min_size=1, max_size=3))))
            elif kind == "set_frequency":
                p.set_frequency(bus, expression())
            elif kind == "set_gain":
                p.set_gain(bus, expression())
            elif kind == "set_offset":
                p.set_offset(bus, expression(), draw(st.one_of(st.none(), finite_floats)))
            elif kind == "measure":
                p.measure(bus, draw(waveform_aliases), draw(waveform_aliases))
        if depth < 2 and draw(st.booleans()):
            emit_block(depth + 1)

    def emit_block(depth: int) -> None:
        choice = draw(st.sampled_from(["average", "block", "for", "loop"]))
        if choice == "average":
            with p.average(draw(st.integers(min_value=1, max_value=100000))):
                emit_ops(depth)
        elif choice == "block":
            with p.block():
                emit_ops(depth)
        elif choice == "for" and variables:
            v = draw(st.sampled_from(variables))
            start = draw(finite_floats)
            step = draw(st.one_of(st.floats(min_value=0.001, max_value=1e6), st.integers(min_value=1, max_value=100)))
            with p.for_loop(v, start, start + step * draw(st.integers(min_value=1, max_value=50)), step):
                emit_ops(depth)
        elif choice == "loop" and variables:
            v = draw(st.sampled_from(variables))
            n = draw(st.integers(min_value=1, max_value=120))
            with p.loop(v, np.asarray(draw(st.lists(finite_floats, min_size=n, max_size=n)))):
                emit_ops(depth)
        else:
            with p.block():
                emit_ops(depth)

    emit_ops(0)
    return p


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(programs())
@settings(max_examples=60, deadline=None)
def test_round_trip_structural_equality(p: QProgram) -> None:
    reloaded = loads(dumps(p))
    assert reloaded.label == p.label
    assert reloaded.description == p.description
    assert reloaded.variables == p.variables
    assert reloaded.body == p.body


@given(programs())
@settings(max_examples=60, deadline=None)
def test_round_trip_byte_stability(p: QProgram) -> None:
    text = dumps(p)
    assert dumps(loads(text)) == text


@given(
    st.dictionaries(
        st.from_regex(r"[a-z][a-z0-9_/]{0,8}", fullmatch=True),
        st.dictionaries(st.from_regex(r"[a-z][a-z0-9_/]{0,8}", fullmatch=True), finite_floats, max_size=3),
        min_size=1,
        max_size=3,
    ),
    st.dictionaries(st.from_regex(r"[a-z][a-z0-9_/]{0,8}", fullmatch=True), finite_floats, max_size=2),
)
@settings(max_examples=40, deadline=None)
def test_round_trip_crosstalk_property(matrix: dict, offsets: dict) -> None:
    m = CrosstalkMatrix()
    for src, row in matrix.items():
        m[src] = dict(row)
    m.set_offset(offsets)
    p = QProgram()
    p.set_crosstalk(m)
    reloaded = loads(dumps(p))
    assert reloaded.body == p.body


@given(st.lists(finite_floats, min_size=51, max_size=200))
@settings(max_examples=25, deadline=None)
def test_round_trip_long_sweeps_property(values: list[float]) -> None:
    p = QProgram()
    v = p.variable("amp")
    with p.loop(v, np.asarray(values)):
        p.set_gain("drive", v)
    reloaded = loads(dumps(p))
    assert np.array_equal(reloaded.body.elements[0].values, np.asarray(values))


@given(st.sampled_from(["q[0].drive", "c[0,1].flux", "x[9].y"]))
@settings(max_examples=10, deadline=None)
def test_path_shaped_raw_bus_string_survives_with_schema(raw_bus: str) -> None:
    """A quoted raw-string bus that *looks* like a path must stay a plain string even when the
    program carries a schema the path would resolve against."""
    schema = BusSchema.transmon()
    p = QProgram(schema=schema)
    p.play(schema.q[0].drive, "wf")
    p.play(raw_bus, "wf")
    reloaded = loads(dumps(p))
    assert reloaded.body == p.body
    assert str(reloaded.body.elements[1].bus) == raw_bus
