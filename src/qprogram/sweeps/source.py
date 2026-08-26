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
"""Abstract base for sweep sources — the value description a [`Sweep`][qprogram.blocks.Sweep] binds.

A [`SweepSource`][qprogram.SweepSource] is to [`Sweep`][qprogram.blocks.Sweep] what a
[`Waveform`][qprogram.waveforms.Waveform] is to [`Play`][qprogram.operations.Play]: a small, immutable,
registered value object that serializes as a constructor call and carries its own capability tokens.
The AST stores the *description* of the values, never a producer of them — which is what keeps a
program inspectable by [`validate`][qprogram.validate], renderable by `explain`, and
round-trippable through ``.qp``.

Every source must answer three questions **without executing the program**, because three existing
consumers ask them at build or validate time:

- [`SweepSource.length`][qprogram.SweepSource.length] — [`Parallel`][qprogram.blocks.Parallel] refuses to compose sweeps
  of differing length, and the reference executor sizes every result array before the first shot.
- `SweepSource.KIND` — a platform reads it through
  [`sweep_kind_of`][qprogram.ValidationContext.sweep_kind_of] to tell a hardware ramp (one loop register plus
  an increment) from a value table or host-side dispatch.
- [`SweepSource.values`][qprogram.SweepSource.values] — the interpreter, the result coordinates, and
  [`optimize`][qprogram.optimize] all need the concrete numbers.

That contract is also the reason a source cannot wrap an arbitrary callable: a deferred function can
answer none of the three ahead of time. Custom generation is expressed by *subclassing* with declared,
serializable parameters — see [`Logspace`][qprogram.Logspace] for the shape to copy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    from qprogram.protocol import SweepKind


class SweepSource(ABC):
    """How a [`Sweep`][qprogram.blocks.Sweep] generates the values it binds to its variable.

    Subclasses declare `KIND` and `TOKEN`, implement `length` and `values`,
    and store their parameters as public instance attributes (which is what makes the ``.qp``
    serialization signature-driven and free).

    Equality and hashing are structural over those attributes, so two sources built the same way
    compare equal and a program survives ``deepcopy`` / ``loads(dumps(...))`` comparison. Sources are
    conceptually immutable: once one has been used in a program, do not mutate its attributes.
    """

    KIND: ClassVar[SweepKind]
    """Whether the values form an exact ``start + step * i`` ramp (``"linear"``) or not
    (``"arbitrary"``).

    This is a claim about **compilability**, not a description of the numbers: a platform may compile
    a linear sweep to a loop register with an increment, while an arbitrary one needs a value table or
    a host-side dispatch per point. Two sources can produce identical values and still differ here —
    [`Values`][qprogram.Values] listing an even ramp is still ``"arbitrary"``, because nothing
    about the source proves the regularity to a compiler.
    """

    TOKEN: ClassVar[str]
    """This source's own ``sweep.<name>`` capability token, registered with the global registry when
    the class is registered. A platform that can generate this source natively declares the token; one
    that cannot omits it and the validator reports it, rather than the platform silently materializing
    the points into a table."""

    @abstractmethod
    def length(self) -> int:
        """Return the number of sweep points.

        Must be answerable statically — without running the program, and without side effects beyond
        reading this source's own parameters. It must also be at least one: a sweep with no points
        never executes its body, so a built-in source rejects an empty parameterization as soon as
        it can see one, at construction for the sources that carry their values and on first read
        for [`File`][qprogram.File], which learns the length only when it loads the array.
        `validate_source` holds a subclass to the same rule.

        Returns:
            The number of points the sweep iterates through.
        """

    @abstractmethod
    def values(self) -> np.ndarray:
        """Return the 1-D array of values this source sweeps, in iteration order.

        ``len(values()) == length()`` is an invariant; `validate_source` checks it for the
        built-ins' tests and any subclass that wants the same guard.

        Returns:
            A 1-D array of `length` values, in the order the sweep binds them.
        """

    def tokens(self) -> set[str]:
        """Return every capability token this source requires.

        The source's own `TOKEN` plus its ``sweep.<kind>`` token. Combinators override this to
        union their wrapped sources' tokens too — a platform that cannot generate
        [`Logspace`][qprogram.Logspace] also cannot generate a rotation of one.

        Returns:
            The capability tokens a platform must declare to generate this source.
        """
        return {self.TOKEN, f"sweep.{self.KIND}"}

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in vars(self).items() if not k.startswith("_"))
        return f"{type(self).__name__}({args})"


def validate_source(source: SweepSource) -> None:
    """Assert the [`SweepSource.length`][qprogram.SweepSource.length] / [`SweepSource.values`][qprogram.SweepSource.values] invariants.

    Not called on the hot path — it materializes the values. Tests and source authors use it to check
    a new subclass honors the contract.

    Args:
        source (SweepSource): The source to check.

    Raises:
        AssertionError: If the values are not a non-empty 1-D array whose length matches
            [`SweepSource.length`][qprogram.SweepSource.length].
    """
    array = np.asarray(source.values())
    assert array.ndim == 1, f"{type(source).__name__}.values() must be 1-D, got {array.ndim}-D"  # ruff: ignore[assert]
    assert array.size > 0, f"{type(source).__name__}.values() must be non-empty"  # ruff: ignore[assert]
    assert array.size == source.length(), (  # ruff: ignore[assert]
        f"{type(source).__name__}.length() is {source.length()} but values() has {array.size} points"
    )


__all__ = ["SweepSource", "validate_source"]
