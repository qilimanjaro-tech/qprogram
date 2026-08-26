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
"""Tests for sweep sources — the value descriptions a :class:`~qprogram.blocks.Sweep` binds.

Covers the :class:`~qprogram.sweeps.SweepSource` contract (static length, kind, values, tokens), the
five built-in sources, the three combinators, and the rejection of anything that can't answer the
contract statically. Serialization round-trips live in ``test_round_trip.py`` / ``test_writer.py``;
the registry itself is in ``test_registry.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from qprogram.errors import ValidationError
from qprogram.sweeps import (
    Concat,
    File,
    Linspace,
    Logspace,
    Range,
    Repeat,
    Rotate,
    SweepSource,
    Values,
    validate_source,
)

ALL_BUILTIN = [
    Range(0.0, 1.0, 0.25),
    Values([0.1, 0.4, 0.9]),
    Linspace(0.0, 1.0, num=5),
    Logspace(1.0, 100.0, num=5),
    Repeat(Values([0.0, 1.0]), times=3),
    Rotate(Values([0.0, 1.0, 2.0]), by=1),
    Concat([Range(0.0, 1.0, 0.5), Values([9.0])]),
]


# ---------------------------------------------------------------------------
# The contract, held by every source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_BUILTIN, ids=lambda s: type(s).__name__)
def test_every_source_honours_the_length_values_invariant(source: SweepSource):
    validate_source(source)


@pytest.mark.parametrize("source", ALL_BUILTIN, ids=lambda s: type(s).__name__)
def test_every_source_declares_a_kind_and_a_token(source: SweepSource):
    assert source.KIND in {"linear", "arbitrary"}
    assert source.TOKEN.startswith("sweep.")
    assert source.tokens() >= {source.TOKEN, f"sweep.{source.KIND}"}


@pytest.mark.parametrize("source", ALL_BUILTIN, ids=lambda s: type(s).__name__)
def test_length_does_not_require_materializing_for_the_caller(source: SweepSource):
    """``length()`` is what Parallel and the executor call before anything runs."""
    assert isinstance(source.length(), int)
    assert source.length() >= 1


@pytest.mark.parametrize("source", ALL_BUILTIN, ids=lambda s: type(s).__name__)
def test_sources_are_structurally_equal_and_hashable(source: SweepSource):
    twin = type(source)(**dict(vars(source)))
    assert source == twin
    assert hash(source) == hash(twin)
    assert source != Values([42.0]) or isinstance(source, Values)


def test_different_classes_are_never_equal_even_with_equal_values():
    """Kind is a claim about compilability, so a ramp and a table listing it are not the same source."""
    ramp = Range(0.0, 1.0, 0.5)
    table = Values([0.0, 0.5, 1.0])
    assert np.array_equal(ramp.values(), table.values())
    assert ramp != table
    assert ramp.KIND == "linear"
    assert table.KIND == "arbitrary"


# ---------------------------------------------------------------------------
# Range
# ---------------------------------------------------------------------------


def test_range_values_and_length():
    src = Range(4e9, 6e9, 1e6)
    assert src.length() == 2001
    assert src.values()[0] == 4e9
    assert src.values()[-1] == 6e9


def test_range_length_absorbs_float_noise():
    assert Range(0.0, 1.0, 0.01).length() == 101
    assert Range(5.0, 5.0, 1.0).length() == 1


def test_range_descending_accepted():
    src = Range(10.0, 0.0, -2.0)
    assert src.length() == 6
    assert src.values()[-1] == 0.0


def test_range_default_step_is_one():
    assert Range(0, 4).length() == 5


def test_range_rejects_zero_step():
    with pytest.raises(ValidationError, match="non-zero"):
        Range(0.0, 1.0, 0)


def test_range_rejects_non_finite_bounds():
    with pytest.raises(ValidationError, match="finite"):
        Range(0.0, float("inf"), 1.0)
    with pytest.raises(ValidationError, match="finite"):
        Range(float("nan"), 1.0, 1.0)


def test_range_rejects_wrong_step_direction():
    with pytest.raises(ValidationError, match="moves away from stop"):
        Range(0.0, 10.0, -1.0)


def test_range_rejects_non_numeric_bounds():
    with pytest.raises(ValidationError, match="int or float"):
        Range("0", 10.0, 1.0)  # ty:ignore[invalid-argument-type]
    with pytest.raises(ValidationError, match="int or float"):
        Range(0.0, 10.0, True)  # ruff: ignore[boolean-positional-value-in-call] — bool-as-step is the case under test


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def test_values_accepts_any_array_like():
    assert Values([1, 2, 3]).length() == 3
    assert Values(np.array([1.0, 2.0])).length() == 2
    assert Values((1.0, 2.0, 3.0, 4.0)).length() == 4


def test_values_rejects_empty():
    with pytest.raises(ValidationError, match="non-empty"):
        Values([])


def test_values_rejects_2d():
    with pytest.raises(ValidationError, match="1-D"):
        Values(np.zeros((2, 2)))


# ---------------------------------------------------------------------------
# Linspace / Logspace
# ---------------------------------------------------------------------------


def test_linspace_hits_both_ends():
    src = Linspace(0.0, 1.0, num=5)
    assert src.length() == 5
    assert src.values()[0] == 0.0
    assert src.values()[-1] == 1.0


def test_linspace_is_linear_and_reports_its_step():
    src = Linspace(0.0, 1.0, num=5)
    assert src.KIND == "linear"
    assert src.step() == pytest.approx(0.25)


def test_linspace_single_point_has_undefined_step():
    src = Linspace(3.0, 3.0, num=1)
    assert src.values().tolist() == [3.0]
    assert src.step() == 0.0


def test_linspace_rejects_bad_num():
    with pytest.raises(ValidationError, match=">= 1"):
        Linspace(0.0, 1.0, num=0)
    with pytest.raises(ValidationError, match="must be an int"):
        Linspace(0.0, 1.0, num=2.5)  # ty:ignore[invalid-argument-type]


def test_logspace_spans_the_given_linear_bounds():
    """``start`` and ``stop`` are actual values, not exponents."""
    src = Logspace(1e6, 1e9, num=4)
    assert src.length() == 4
    assert src.values()[0] == pytest.approx(1e6)
    assert src.values()[-1] == pytest.approx(1e9)
    assert src.values()[1] == pytest.approx(1e7)


def test_logspace_is_arbitrary():
    assert Logspace(1.0, 10.0, num=3).KIND == "arbitrary"


def test_logspace_rejects_non_positive_bounds():
    with pytest.raises(ValidationError, match="strictly positive"):
        Logspace(0.0, 10.0, num=3)
    with pytest.raises(ValidationError, match="strictly positive"):
        Logspace(-1.0, 10.0, num=3)


# ---------------------------------------------------------------------------
# File
# ---------------------------------------------------------------------------


def test_file_loads_the_array(tmp_path):
    path = tmp_path / "sweep.npy"
    np.save(path, np.array([1.0, 2.0, 3.0]))
    src = File(str(path))
    assert src.length() == 3
    assert src.values().tolist() == [1.0, 2.0, 3.0]


def test_file_retains_the_path_rather_than_inlining_values(tmp_path):
    """The whole point of keeping File first-class: the AST records where the points came from."""
    path = tmp_path / "sweep.npy"
    np.save(path, np.arange(10.0))
    src = File(str(path))
    assert vars(src) == {"path": str(path)}
    assert src == File(str(path))


def test_file_rejects_empty_path():
    with pytest.raises(ValidationError, match="non-empty string"):
        File("")


def test_file_rejects_2d_contents(tmp_path):
    path = tmp_path / "bad.npy"
    np.save(path, np.zeros((2, 2)))
    source = File(str(path))
    with pytest.raises(ValidationError, match="1-D"):
        source.values()


def test_file_rejects_empty_contents(tmp_path):
    path = tmp_path / "empty.npy"
    np.save(path, np.array([]))
    source = File(str(path))
    with pytest.raises(ValidationError, match="empty array"):
        source.length()


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


def test_repeat_tiles_the_inner_values():
    src = Repeat(Values([0.0, 1.0]), times=3)
    assert src.length() == 6
    assert src.values().tolist() == [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]


def test_repeat_of_a_linear_source_is_conservatively_arbitrary():
    """A tiled ramp is not itself ``start + step * i``, so it cannot claim sweep.linear."""
    src = Repeat(Range(0.0, 1.0, 0.5), times=2)
    assert Range(0.0, 1.0, 0.5).KIND == "linear"
    assert src.KIND == "arbitrary"


def test_repeat_rejects_bad_times():
    source = Values([0.0])
    with pytest.raises(ValidationError, match=">= 1"):
        Repeat(source, times=0)


def test_rotate_shifts_left_and_preserves_length():
    src = Rotate(Values([0.0, 1.0, 2.0, 3.0]), by=1)
    assert src.length() == 4
    assert src.values().tolist() == [1.0, 2.0, 3.0, 0.0]


def test_rotate_by_zero_is_identity():
    assert Rotate(Values([0.0, 1.0]), by=0).values().tolist() == [0.0, 1.0]


def test_rotate_accepts_negative_and_wrapping_shifts():
    assert Rotate(Values([0.0, 1.0, 2.0]), by=-1).values().tolist() == [2.0, 0.0, 1.0]
    assert Rotate(Values([0.0, 1.0, 2.0]), by=4).values().tolist() == [1.0, 2.0, 0.0]


def test_concat_joins_in_order():
    src = Concat([Range(0.0, 1.0, 0.5), Values([9.0, 8.0])])
    assert src.length() == 5
    assert src.values().tolist() == [0.0, 0.5, 1.0, 9.0, 8.0]


def test_concat_accepts_a_generator_expression():
    base = Values([0.0, 1.0, 2.0])
    src = Concat(Rotate(base, by=i) for i in range(3))
    assert src.length() == 9
    assert src.values()[:3].tolist() == [0.0, 1.0, 2.0]
    assert src.values()[3:6].tolist() == [1.0, 2.0, 0.0]


def test_concat_rejects_a_single_source_not_in_a_list():
    source = Values([0.0])
    with pytest.raises(ValidationError, match="iterable of sources"):
        Concat(source)  # ty:ignore[invalid-argument-type]


def test_concat_rejects_empty():
    with pytest.raises(ValidationError, match="at least one source"):
        Concat([])


def test_combinators_wrap_bare_sequences():
    """``Rotate([0.0, 1.57], by=1)`` wraps the bare sequence, so no ``Values()`` is needed at the call site."""
    src = Rotate([0.0, 1.57], by=1)
    assert isinstance(src.source, Values)
    assert src.values().tolist() == [1.57, 0.0]


def test_combinator_tokens_include_the_wrapped_sources():
    """A platform that can't generate a Logspace can't generate a rotation of one either."""
    src = Rotate(Logspace(1.0, 100.0, num=3), by=1)
    assert src.tokens() == {"sweep.rotate", "sweep.arbitrary", "sweep.logspace"}


def test_nested_combinator_tokens_accumulate_through_every_level():
    src = Concat([Repeat(Range(0.0, 1.0, 0.5), times=2), Values([5.0])])
    assert src.tokens() == {
        "sweep.concat",
        "sweep.repeat",
        "sweep.range",
        "sweep.values",
        "sweep.arbitrary",
        "sweep.linear",
    }


# ---------------------------------------------------------------------------
# What a source may not be
# ---------------------------------------------------------------------------


def test_combinators_reject_a_callable_with_an_actionable_message():
    with pytest.raises(ValidationError, match="not a callable"):
        Rotate(lambda i: i, by=1)  # ty:ignore[invalid-argument-type]
    with pytest.raises(ValidationError, match=r"Values\(f\(\.\.\.\)\)"):
        Repeat(np.sin, times=2)  # ty:ignore[invalid-argument-type]


def test_combinators_reject_a_non_sequence_non_source():
    with pytest.raises(ValidationError, match="SweepSource or a 1-D sequence"):
        Rotate(object(), by=1)  # ty:ignore[invalid-argument-type]


def test_a_custom_source_needs_only_the_four_declarations():
    """The documented extension path: no registration required to *use* one, only to serialize it."""

    class Chevron(SweepSource):
        KIND = "arbitrary"
        TOKEN = "sweep.chevron"

        def __init__(self, center: float, span: float, num: int) -> None:
            self.center, self.span, self.num = center, span, num

        def length(self) -> int:
            return self.num

        def values(self) -> np.ndarray:
            return np.linspace(self.center - self.span / 2, self.center + self.span / 2, self.num)

    src = Chevron(center=5.0, span=2.0, num=5)
    validate_source(src)
    assert src.tokens() == {"sweep.chevron", "sweep.arbitrary"}
    assert src == Chevron(center=5.0, span=2.0, num=5)
    assert Concat([src, Values([9.0])]).length() == 6
