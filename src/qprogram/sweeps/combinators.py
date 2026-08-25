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
"""Sweep sources built from other sweep sources: [`Repeat`][qprogram.Repeat], [`Rotate`][qprogram.Rotate], [`Concat`][qprogram.Concat].

Combinators are where the one-block, many-sources design pays off: composition lives in the source,
not in the block, so each one is a dozen lines, composes with every registered source, and needs no
``Block`` subclass, no ``block.*`` token, and no parse/write callback pair of its own.

Two rules they all follow:

- **Kind degrades, conservatively.** Wrapping never makes a sweep *more* compilable, and all three
  report `KIND` ``"arbitrary"``. That includes [`Repeat`][qprogram.Repeat] of a
  linear source: a tiled ramp is re-runnable as a *nested* loop, but it is not itself
  ``start + step * i``, and ``sweep.linear`` means exactly that. Under-claiming costs a platform one
  optimization; over-claiming would have it emit a single ramp for a sweep that isn't one.
- **Tokens accumulate.** [`tokens`][qprogram.SweepSource.tokens] unions the wrapped sources', so a
  platform that cannot generate a [`Logspace`][qprogram.Logspace] also refuses a rotation of one.

Nesting more than two deep is a signal to stop composing and write a named
[`SweepSource`][qprogram.SweepSource] subclass instead — it serializes just as well and reads far
better at the call site.
"""
# Every source declares a ``TOKEN`` class attribute, which ruff reads as a hardcoded credential.
# ruff: file-ignore[hardcoded-password-string]

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from qprogram.errors import ValidationError
from qprogram.sweeps.source import SweepSource

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.protocol import SweepKind


def _require_source(value: object, *, cls_name: str, name: str = "source") -> SweepSource:
    """Coerce a combinator argument to a [`SweepSource`][qprogram.SweepSource].

    A bare sequence is wrapped in [`Values`][qprogram.Values] as a convenience, so
    ``Rotate([0.0, 1.57, 3.14], by=1)`` works without the extra constructor. Anything else that isn't
    a source is an error — notably a callable, which the AST deliberately cannot hold.

    Args:
        value (object): The argument to coerce: a sweep source, or a 1-D sequence of values.
        cls_name (str): Name of the combinator class, used in the error message.
        name (str, optional): Name of the parameter being coerced, used in the error message.
            Defaults to ``"source"``.

    Returns:
        ``value`` unchanged when it is already a sweep source, otherwise a
        [`Values`][qprogram.Values] wrapping it.

    Raises:
        ValidationError: If ``value`` is a callable, or is neither a sweep source nor a 1-D sequence
            of values.
    """
    # Imported here rather than at module load, which would be a cycle.
    from qprogram.sweeps.builtin import Values  # ruff: ignore[import-outside-top-level]

    if isinstance(value, SweepSource):
        return value
    if callable(value):
        msg = (
            f"{cls_name} {name} must be a SweepSource, not a callable. A sweep source describes its "
            f"values statically (length, kind, and a serializable parameterization); a function can "
            f"answer none of those before the program runs. Materialize it — Values(f(...)) — or "
            f"declare a SweepSource subclass with the parameters it needs."
        )
        raise ValidationError(msg)
    try:
        return Values(value)  # ty:ignore[invalid-argument-type]
    except (ValidationError, TypeError, ValueError) as e:
        msg = f"{cls_name} {name} must be a SweepSource or a 1-D sequence of values, got {value!r}"
        raise ValidationError(msg) from e


class Repeat(SweepSource):
    """An inner source's points repeated ``times`` times, back to back.

    ``Repeat(Values([0, 1]), times=3)`` sweeps ``0, 1, 0, 1, 0, 1``. Note this is *not* averaging:
    each repetition is a distinct sweep point with its own result entry. Use
    [`average`][qprogram.QProgram.average] when you want the repetitions collapsed.

    Reports ``KIND = "arbitrary"`` even around a linear source — see the module docstring for why the
    conservative direction is the correct one.

    Args:
        source (SweepSource): The source to repeat. A bare sequence is accepted and wrapped in
            [`Values`][qprogram.Values].
        times (int): How many times to run it. Must be an int >= 1.

    Raises:
        ValidationError: If ``source`` isn't a source or sequence, or if ``times`` is not an
            ``int``, is a ``bool``, or is less than ``1``.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.repeat"

    def __init__(self, source: SweepSource, times: int) -> None:
        cls_name = type(self).__name__
        self.source = _require_source(source, cls_name=cls_name)
        if not isinstance(times, int) or isinstance(times, bool) or times < 1:
            msg = f"{cls_name} times must be an int >= 1, got {times!r}"
            raise ValidationError(msg)
        self.times = times

    def length(self) -> int:
        """Return the number of points across every repetition.

        Returns:
            ``source.length() * times``.
        """
        return self.source.length() * self.times

    def values(self) -> np.ndarray:
        """Return the repeated points.

        Returns:
            The wrapped source's values tiled ``times`` times.
        """
        return np.tile(self.source.values(), self.times)

    def tokens(self) -> set[str]:
        """Return every capability token this source requires.

        Returns:
            ``sweep.repeat`` and ``sweep.arbitrary``, plus everything the wrapped source needs.
        """
        return {self.TOKEN, f"sweep.{self.KIND}"} | self.source.tokens()


class Rotate(SweepSource):
    """An inner source's points cyclically shifted left by ``by`` positions.

    ``Rotate(Values([0, 1, 2, 3]), by=1)`` sweeps ``1, 2, 3, 0``. The point count is unchanged. Use it
    with [`Concat`][qprogram.Concat] to build the phase-cycling pattern where a sequence is swept once per starting
    offset::

        Concat(Rotate(base, by=i) for i in range(base.length()))

    Args:
        source (SweepSource): The source to rotate. A bare sequence is accepted and wrapped in
            [`Values`][qprogram.Values].
        by (int, optional): Number of positions to shift left. May be negative (shifts right) or
            exceed the length (wraps, as `numpy.roll` does). Defaults to ``1``.

    Raises:
        ValidationError: If ``source`` isn't a source or sequence, or if ``by`` is not an ``int``
            or is a ``bool``.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.rotate"

    def __init__(self, source: SweepSource, by: int = 1) -> None:
        cls_name = type(self).__name__
        self.source = _require_source(source, cls_name=cls_name)
        if not isinstance(by, int) or isinstance(by, bool):
            msg = f"{cls_name} by must be an int, got {type(by).__name__}"
            raise ValidationError(msg)
        self.by = by

    def length(self) -> int:
        """Return the number of points, which rotation leaves unchanged.

        Returns:
            The wrapped source's length.
        """
        return self.source.length()

    def values(self) -> np.ndarray:
        """Return the rotated points.

        Returns:
            The wrapped source's values under `numpy.roll` by ``-by`` — a *left* shift, so
            ``by=1`` starts at the second point.
        """
        return np.roll(self.source.values(), -self.by)

    def tokens(self) -> set[str]:
        """Return every capability token this source requires.

        Returns:
            ``sweep.rotate`` and ``sweep.arbitrary``, plus everything the wrapped source needs.
        """
        return {self.TOKEN, f"sweep.{self.KIND}"} | self.source.tokens()


class Concat(SweepSource):
    """Several sources joined end to end into a single sweep.

    ``Concat([Range(0, 1, 0.5), Values([10, 20])])`` sweeps ``0, 0.5, 1, 10, 20``. Accepts any
    iterable, so a comprehension or generator expression works directly.

    Args:
        sources (Iterable[SweepSource]): The sources to concatenate, in order. At least one. Bare
            sequences among them are wrapped in [`Values`][qprogram.Values].

    Raises:
        ValidationError: If ``sources`` is a single source rather than an iterable of them, is empty,
            or holds something that is neither a source nor a sequence of values.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.concat"

    def __init__(self, sources: Iterable[SweepSource]) -> None:
        cls_name = type(self).__name__
        if isinstance(sources, SweepSource):
            msg = (
                f"{cls_name} takes an iterable of sources, not a single source. "
                f"Write Concat([a, b]) or Concat(gen_expr)."
            )
            raise ValidationError(msg)
        resolved = [_require_source(s, cls_name=cls_name, name=f"sources[{i}]") for i, s in enumerate(sources)]
        if not resolved:
            msg = f"{cls_name} needs at least one source (an empty sweep never executes its body)"
            raise ValidationError(msg)
        self.sources = resolved

    def length(self) -> int:
        """Return the number of points across every part.

        Returns:
            The sum of the wrapped sources' lengths.
        """
        return sum(source.length() for source in self.sources)

    def values(self) -> np.ndarray:
        """Return the concatenated points.

        Returns:
            The parts' values joined in order.
        """
        return np.concatenate([source.values() for source in self.sources])

    def tokens(self) -> set[str]:
        """Return every capability token this source requires.

        Returns:
            ``sweep.concat`` and ``sweep.arbitrary``, plus everything every wrapped source needs.
        """
        out = {self.TOKEN, f"sweep.{self.KIND}"}
        for source in self.sources:
            out |= source.tokens()
        return out


__all__ = ["Concat", "Repeat", "Rotate"]
