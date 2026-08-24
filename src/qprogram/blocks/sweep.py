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
"""The one sweep block. See :mod:`qprogram.sweeps` for the value sources it binds."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError
from qprogram.sweeps.source import SweepSource

if TYPE_CHECKING:
    from qprogram.variable import Variable


class Sweep(Block):
    """A loop binding ``variable`` to each value a :class:`~qprogram.sweeps.SweepSource` produces.

    The DSL's single sweep construct. It carries no notion of *how* the values are generated — that is
    entirely the source's business, which is what lets ``Range``, ``Values``, ``Logspace``, a file, and
    any composition of those be peers rather than separate block types.

    Args:
        variable (Variable): The :class:`~qprogram.Variable` rebound on each iteration.
        source (SweepSource): The :class:`~qprogram.sweeps.SweepSource` describing the values. A bare
            1-D sequence is accepted as a shorthand for :class:`~qprogram.sweeps.Values`.

    Raises:
        ValidationError: If ``source`` is neither a source nor a sequence of values.
    """

    REPEATS: ClassVar[bool] = True
    """This block re-runs its body — it occupies a repetition level (see :attr:`Block.REPEATS`)."""

    def __init__(self, variable: Variable, source: SweepSource) -> None:
        super().__init__()
        self.variable = variable
        self.source = _coerce_source(source)

    def num_iterations(self) -> int:
        """Return the number of sweep points, delegated to the source.

        Answerable without executing the program, which is what lets
        :class:`~qprogram.blocks.Parallel` check lockstep lengths at construction time and the executor
        size a composed parallel axis before the first shot. Cheap for every built-in source except
        :class:`~qprogram.sweeps.File`, which reads its array to answer.

        Returns:
            The number of points the bound source produces.

        Raises:
            ValidationError: If the bound source cannot describe its points — a
                :class:`~qprogram.sweeps.File` holding an array that is not 1-D, or holding none.
            OSError: If the bound source reads a file whose path does not exist or cannot be read.
        """
        return self.source.length()

    def variables(self) -> set[Variable]:
        """Return every :class:`~qprogram.Variable` referenced by the body, plus the swept one.

        Returns:
            The body's variables together with :attr:`variable`.
        """
        return super().variables() | {self.variable}

    def required_capabilities(self) -> set[str]:
        """Return ``block.sweep`` plus the bound source's own tokens.

        A platform therefore declares both the loop and the specific value source it is asked for: the
        source's class token and its ``sweep.<kind>``, unioned across everything a combinator wraps.

        Returns:
            The identity token ``block.sweep`` together with :meth:`SweepSource.tokens`.
        """
        return {"block.sweep"} | self.source.tokens()


def _coerce_source(source: object) -> SweepSource:
    """Return ``source`` as a :class:`~qprogram.sweeps.SweepSource`, wrapping a bare sequence.

    A sequence is the shorthand spelling of :class:`~qprogram.sweeps.Values`. A callable is refused
    outright: a deferred function can report neither its length nor its kind before the program runs,
    and cannot be serialized to ``.qp``.

    Args:
        source (object): A sweep source, or a 1-D sequence of values to wrap.

    Returns:
        The source unchanged, or a :class:`~qprogram.sweeps.Values` over the given points.

    Raises:
        ValidationError: If ``source`` is a callable, or is not a 1-D sequence of values.
    """
    # qprogram.sweeps imports this module, so the shorthand's import stays lazy.
    from qprogram.sweeps.builtin import Values  # ruff: ignore[import-outside-top-level]

    if isinstance(source, SweepSource):
        return source
    if callable(source):
        msg = (
            "Sweep source must be a SweepSource, not a callable. A source describes its values "
            "statically (length, kind, and a serializable parameterization); a function can answer "
            "none of those before the program runs. Materialize it — Values(f(...)) — or declare a "
            "SweepSource subclass with the parameters it needs."
        )
        raise ValidationError(msg)
    try:
        return Values(source)  # ty:ignore[invalid-argument-type]
    except (ValidationError, TypeError, ValueError) as e:
        msg = f"Sweep source must be a SweepSource or a 1-D sequence of values, got {source!r}"
        raise ValidationError(msg) from e
