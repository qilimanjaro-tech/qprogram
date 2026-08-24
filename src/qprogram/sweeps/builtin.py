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
"""The built-in sweep sources.

:class:`Range`, :class:`Values`, :class:`Linspace`, :class:`Logspace`, :class:`File`.

Grouped in one module rather than one file per class (the convention
:mod:`qprogram.waveforms` follows) because these are parameter-only value objects of a dozen lines
each with no per-class algorithm to house — the sweep math is a single expression in every case.
:mod:`qprogram.sweeps.combinators` holds the sources that wrap other sources.
"""
# Every source declares a ``TOKEN`` class attribute, which ruff reads as a hardcoded credential.
# ruff: file-ignore[hardcoded-password-string]

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from qprogram.errors import ValidationError
from qprogram.sweeps.source import SweepSource

if TYPE_CHECKING:
    import numpy.typing as npt

    from qprogram.protocol import SweepKind


def _require_finite_number(value: object, *, cls_name: str, name: str) -> float:
    """Reject non-numeric, boolean, and non-finite sweep bounds.

    A ``bool`` is an ``int`` subclass but never a meaningful bound; ``inf`` / ``nan`` would create a
    sweep that can neither execute nor round-trip through ``.qp``.

    Args:
        value (object): The candidate bound, as the caller passed it.
        cls_name (str): Name of the source class, used in the error message.
        name (str): Name of the parameter being checked, used in the error message.

    Returns:
        The bound, converted to ``float``.

    Raises:
        ValidationError: If ``value`` is not an ``int`` or ``float``, is a ``bool``, or is not finite.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        msg = f"{cls_name} {name} must be an int or float, got {type(value).__name__}"
        raise ValidationError(msg)
    if not math.isfinite(value):
        msg = f"{cls_name} {name} must be finite, got {value!r}"
        raise ValidationError(msg)
    return float(value)


def _require_positive_count(num: object, *, cls_name: str) -> int:
    """Reject a point count that isn't an integer >= 1.

    Args:
        num (object): The candidate point count, as the caller passed it.
        cls_name (str): Name of the source class, used in the error message.

    Returns:
        The point count, as an ``int``.

    Raises:
        ValidationError: If ``num`` is not an ``int``, is a ``bool``, or is less than ``1``.
    """
    if not isinstance(num, int) or isinstance(num, bool):
        msg = f"{cls_name} num must be an int, got {type(num).__name__}"
        raise ValidationError(msg)
    if num < 1:
        msg = f"{cls_name} num must be >= 1, got {num} (an empty sweep never executes its body)"
        raise ValidationError(msg)
    return num


class Range(SweepSource):
    """A linear ramp from ``start`` toward ``stop`` in increments of ``step``.

    The source a hardware sequencer can usually run without a value table: one loop register plus an
    increment. Prefer :class:`Linspace` when you know the number of points rather than the spacing.

    The ramp always begins at ``start`` and holds ``round((stop - start) / step) + 1`` points, so it
    lands on ``stop`` only when ``step`` divides ``stop - start`` evenly. Otherwise the last point
    stops short of ``stop`` — ``Range(0, 1, 0.3)`` ends at ``0.9``, ``Range(0, 0.4, 1)`` is the single
    point ``0.0`` — or steps past it, as ``Range(0, 1, 0.6)`` does by ending at ``1.2``. Reach for
    :class:`Linspace` when the last point has to land exactly on ``stop``.

    Args:
        start (float): First value, always produced.
        stop (float): The value the ramp runs toward, produced only when ``step`` divides
            ``stop - start`` evenly.
        step (float, optional): Increment between consecutive points. Defaults to ``1``.

    Raises:
        ValidationError: If any bound is non-numeric or non-finite, if ``step`` is zero (an infinite
            sweep), or if ``step`` points away from ``stop`` (an empty sweep).
    """

    KIND: ClassVar[SweepKind] = "linear"
    TOKEN: ClassVar[str] = "sweep.range"

    def __init__(self, start: float, stop: float, step: float = 1) -> None:
        cls_name = type(self).__name__
        start = _require_finite_number(start, cls_name=cls_name, name="start")
        stop = _require_finite_number(stop, cls_name=cls_name, name="stop")
        step = _require_finite_number(step, cls_name=cls_name, name="step")
        if step == 0:
            msg = f"{cls_name} step must be non-zero (a zero step never reaches stop)"
            raise ValidationError(msg)
        if (stop - start) * step < 0:
            msg = (
                f"{cls_name} step {step!r} moves away from stop ({start!r} -> {stop!r}); "
                f"flip the step sign or swap the bounds"
            )
            raise ValidationError(msg)
        self.start = start
        self.stop = stop
        self.step = step

    def length(self) -> int:
        """Return the number of points in the ramp.

        The count is ``round((stop - start) / step) + 1`` — the rounding absorbs floating-point
        division noise for ranges like ``(0.0, 1.0, 0.01)``, and it is also what lets the last point
        stop short of ``stop`` or step past it when ``step`` does not divide ``stop - start`` evenly.

        Returns:
            The number of points in the ramp, counting the one at ``start``.
        """
        return round((self.stop - self.start) / self.step) + 1

    def values(self) -> np.ndarray:
        """Return the ramp's points, in iteration order.

        Returns:
            ``start + step * arange(length())`` — consistent with :meth:`length` by construction.
        """
        return self.start + self.step * np.arange(self.length())


class Values(SweepSource):
    """An explicit list of sweep points.

    Use it when the points don't fit a regular pattern: calibrated values, a measured table, the
    output of a computation you ran yourself. Note that it is :attr:`~SweepSource.KIND`
    ``"arbitrary"`` even when the values happen to be evenly spaced — the source proves nothing about
    their regularity to a compiler, so reach for :class:`Range` or :class:`Linspace` when the sweep
    really is a ramp and you want a platform to be able to compile it as one.

    Args:
        points (ArrayLike): Sequence of values to iterate through. Anything :func:`numpy.asarray`
            accepts. Named ``points`` rather than ``values`` so it doesn't collide with
            :meth:`~qprogram.sweeps.SweepSource.values`; on the wire it is almost always written as
            the bracket literal ``[...]`` anyway.

    Raises:
        ValidationError: If ``points`` is empty or not 1-D.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.values"

    def __init__(self, points: npt.ArrayLike) -> None:
        array = np.asarray(points, dtype=float)
        if array.ndim != 1:
            msg = f"Values must be a 1-D sequence, got a {array.ndim}-D array"
            raise ValidationError(msg)
        if array.size == 0:
            msg = "Values must be non-empty (an empty sweep never executes its body)"
            raise ValidationError(msg)
        self.points = array

    def length(self) -> int:
        """Return the number of points, one per element of the stored array.

        Returns:
            The size of the stored array.
        """
        return int(self.points.size)

    def values(self) -> np.ndarray:
        """Return the points given at construction.

        Returns:
            The stored ``float`` array itself, not a copy — treat it as read-only, since the source
            is a value object whose equality and hash are derived from it.
        """
        return self.points


class Linspace(SweepSource):
    """``num`` evenly spaced points from ``start`` to ``stop``, both ends inclusive.

    The shape most sweeps actually want — you usually know how many points you can afford, not the
    spacing that lands on them. Linear, so a platform can compile it as a ramp: the derived step is
    ``(stop - start) / (num - 1)``.

    Args:
        start (float): First value (inclusive).
        stop (float): Final value (inclusive).
        num (int): Number of points. ``1`` yields ``[start]``.

    Raises:
        ValidationError: If a bound is non-numeric or non-finite, or ``num`` is not an int >= 1.
    """

    KIND: ClassVar[SweepKind] = "linear"
    TOKEN: ClassVar[str] = "sweep.linspace"

    def __init__(self, start: float, stop: float, num: int) -> None:
        cls_name = type(self).__name__
        self.start = _require_finite_number(start, cls_name=cls_name, name="start")
        self.stop = _require_finite_number(stop, cls_name=cls_name, name="stop")
        self.num = _require_positive_count(num, cls_name=cls_name)

    def length(self) -> int:
        """Return the number of points.

        Returns:
            ``num``, as given.
        """
        return self.num

    def values(self) -> np.ndarray:
        """Return the evenly spaced points.

        Returns:
            :func:`numpy.linspace` over the closed interval ``[start, stop]``.
        """
        return np.linspace(self.start, self.stop, self.num)

    def step(self) -> float:
        """Return the spacing this sweep resolves to, for a compiler that wants ``start``/``step`` form.

        Returns:
            ``(stop - start) / (num - 1)``, or ``0.0`` for a single-point sweep, where the spacing is
            undefined.
        """
        if self.num == 1:
            return 0.0
        return (self.stop - self.start) / (self.num - 1)


class Logspace(SweepSource):
    """``num`` points spaced evenly on a log scale between ``start`` and ``stop`` (both linear values).

    Note the argument convention: ``start`` and ``stop`` are the actual first and last values, not the
    exponents :func:`numpy.logspace` takes — a frequency sweep reads ``Logspace(1e6, 1e9, num=50)``
    rather than ``Logspace(6, 9, num=50)``.

    Args:
        start (float): First value (inclusive). Must be strictly positive.
        stop (float): Final value (inclusive). Must be strictly positive.
        num (int): Number of points.

    Raises:
        ValidationError: If a bound is non-positive, non-numeric or non-finite, or ``num`` is not an
            int >= 1.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.logspace"

    def __init__(self, start: float, stop: float, num: int) -> None:
        cls_name = type(self).__name__
        start = _require_finite_number(start, cls_name=cls_name, name="start")
        stop = _require_finite_number(stop, cls_name=cls_name, name="stop")
        if start <= 0 or stop <= 0:
            msg = (
                f"{cls_name} bounds must be strictly positive (log of a non-positive value), got {start!r} -> {stop!r}"
            )
            raise ValidationError(msg)
        self.start = start
        self.stop = stop
        self.num = _require_positive_count(num, cls_name=cls_name)

    def length(self) -> int:
        """Return the number of points.

        Returns:
            ``num``, as given.
        """
        return self.num

    def values(self) -> np.ndarray:
        """Return the log-spaced points.

        Returns:
            :func:`numpy.geomspace` over ``[start, stop]`` — evenly spaced in log space, inclusive of
            both bounds.
        """
        return np.geomspace(self.start, self.stop, self.num)


class File(SweepSource):
    """Sweep points loaded from a ``.npy`` file at the given path.

    The path is what the AST stores, so a ``.qp`` file records *where the points came from* rather
    than inlining them — which keeps the file small and the intent legible. The cost is that the file
    must be readable wherever the program is validated or run: :meth:`length` and :meth:`values` both
    load it, and neither caches (caching would put the loaded array into the structural equality of
    the source, so an already-loaded instance would stop comparing equal to a fresh one).

    Args:
        path (str): Path to a ``.npy`` file holding a 1-D array.

    Raises:
        ValidationError: If ``path`` is empty.
    """

    KIND: ClassVar[SweepKind] = "arbitrary"
    TOKEN: ClassVar[str] = "sweep.file"

    def __init__(self, path: str) -> None:
        if not isinstance(path, str) or not path:
            msg = f"File path must be a non-empty string, got {path!r}"
            raise ValidationError(msg)
        self.path = path

    def _load(self) -> np.ndarray:
        """Read the file and check that it holds a usable sweep.

        Returns:
            The file's contents as a 1-D ``float`` array.

        Raises:
            ValidationError: If the file holds an array that is not 1-D, or holds no values.
            OSError: If the path does not exist or cannot be read.
        """
        array = np.asarray(np.load(self.path), dtype=float)
        if array.ndim != 1:
            msg = f"File {self.path!r} must hold a 1-D array, got a {array.ndim}-D array"
            raise ValidationError(msg)
        if array.size == 0:
            msg = f"File {self.path!r} holds an empty array (an empty sweep never executes its body)"
            raise ValidationError(msg)
        return array

    def length(self) -> int:
        """Load the file and report its length.

        Returns:
            The number of values the file holds.

        Raises:
            ValidationError: If the file holds an array that is not 1-D, or holds no values.
            OSError: If the path does not exist or cannot be read.
        """
        return int(self._load().size)

    def values(self) -> np.ndarray:
        """Load the file and return its contents.

        Returns:
            The file's contents as a 1-D ``float`` array.

        Raises:
            ValidationError: If the file holds an array that is not 1-D, or holds no values.
            OSError: If the path does not exist or cannot be read.
        """
        return self._load()


__all__ = ["File", "Linspace", "Logspace", "Range", "Values"]
