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
"""Restating a quantity for a figure: the numbers and the words that name them, held together.

A result carries hertz because the instrument takes hertz, and the figure of it wants gigahertz.
That is two changes at once — arithmetic on the numbers and a new unit on the axis — and doing
either without the other is how an axis comes to read ``(Hz)`` over values running 4.6 to 5.4.
[`Quantity`][qprogram.plotting.Quantity] carries the pair, which is what lets the builder refuse the
half-done version.

The module imports numpy and the error hierarchy and nothing else, so it stays on the numeric side
of the seam that [`build_figure`][qprogram.plotting.build_figure] sits on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Quantity:
    """One quantity restated: what to call it, what unit to read it in, and how to get there.

    A ``Quantity`` describes presentation only. It never touches the array, so
    ``data.coords["freq"]`` is still in hertz after the figure of it has been drawn in gigahertz.
    The two sides part company there: the drawn numbers moved, so anything handed to the axes
    afterwards is in the figure's units, and a frequency read back off the array needs the same
    arithmetic the figure got.

    One rule is enforced, in both directions: **a change of unit and a change of numbers travel
    together.** A ``transform`` over values that carry a unit must say what the unit is now, and a
    ``units`` that contradicts the one already there must come with the arithmetic that earns it.
    Either half alone would produce an axis whose numbers and label disagree, which is the
    plausible-looking wrong figure this type exists to prevent. What cannot be checked is whether
    the arithmetic matches the unit: ``Quantity(units="GHz", transform=lambda v: v / 1e6)`` is a lie
    no check here can catch, because ``Variable.units`` is free-form text and legitimately holds
    ``"arb"``, ``"counts"`` and ``"shots"``.

    Attributes:
        label (str | None): Name for the quantity, replacing the coordinate's ``long_name`` or the
            name the channel implies. ``None`` keeps the inherited one.
        units (str | None): Unit to read the numbers in, replacing the coordinate's ``units``
            attribute. ``None`` keeps the inherited one, and ``""`` says the numbers now carry no
            unit at all — a ratio, a normalised population — which over values that arrived with a
            unit is a change like any other and comes with the ``transform`` that made them one.
        transform (Callable[[numpy.ndarray], numpy.ndarray] | None): Arithmetic on the values,
            called with the whole array that would otherwise have been drawn and returning one real
            number per value. It gets a copy, so an in-place transform cannot reach the stored
            result, and it is called once per drawn series, so write it pure.

    Raises:
        ValidationError: If all three are ``None``, so the object restates nothing; if ``label`` or
            ``units`` is not a string; or if ``transform`` is not callable.
    """

    label: str | None = None
    units: str | None = None
    transform: Callable[[np.ndarray], np.ndarray] | None = None

    def __post_init__(self) -> None:
        """Reject a ``Quantity`` that could not restate anything, before any data exists.

        Raises:
            ValidationError: As documented on the class.
        """
        if self.label is None and self.units is None and self.transform is None:
            msg = (
                "Quantity() restates nothing. Give it a label=, units=, a transform=, or some of "
                "the three; leave the argument out entirely to keep what the array already says."
            )
            raise ValidationError(msg)
        for name, value in (("label", self.label), ("units", self.units)):
            if value is not None and not isinstance(value, str):
                msg = f"Quantity {name} must be a string, got {type(value).__name__}"
                raise ValidationError(msg)
        if self.transform is not None and not callable(self.transform):
            msg = (
                f"Quantity transform must be callable, got {type(self.transform).__name__}. It is "
                f"the arithmetic that restates the values, so write it as a function of the array."
            )
            raise ValidationError(msg)


def checked(value: object, where: str) -> Quantity | None:
    """Return ``value`` if it is a [`Quantity`][qprogram.plotting.Quantity] or ``None``, and raise otherwise.

    The gate runs before anything reads a field off the argument, so a wrong type gives the message
    below rather than an ``AttributeError`` from somewhere further in.

    Args:
        value (object): Whatever the caller passed.
        where (str): How to name the argument in the error, e.g. ``"value="`` or ``"coords['freq']"``.

    Returns:
        The quantity, or ``None``.

    Raises:
        ValidationError: If ``value`` is anything else.
    """
    if value is None or isinstance(value, Quantity):
        return value
    if isinstance(value, str):
        hint = (
            f"A bare string names the quantity without saying what unit to read it in; write it as Quantity({value!r})."
        )
    elif callable(value):
        hint = (
            "A bare function rescales the numbers without saying what they are now, which is how "
            "an axis comes to read '(Hz)' over gigahertz; wrap it as "
            "Quantity(units='GHz', transform=f)."
        )
    else:
        hint = "Wrap it as Quantity(label=..., units=..., transform=...)."
    msg = f"{where} must be a Quantity, got {type(value).__name__}. {hint}"
    raise ValidationError(msg)


def restated(quantity: Quantity | None, values: np.ndarray, where: str) -> np.ndarray:
    """Run one transform over an array and check what came back.

    A transform is handed a copy: the arrays reaching here share memory with the stored result, and
    the ordinary numpy spelling of a baseline (``v -= v[0]``) would otherwise rewrite the
    measurement the figure is of. Where there is no transform there is nothing to guard against and
    ``values`` is handed back as it stands, so the copy is the transform's, not the return value's.

    What is checked is the transform's shape and dtype, and whether it turned a finite value into a
    non-finite one. A value the measurement itself carries as NaN — the executor writes one into a
    grid point a conditional arm never reached — passes through untouched.

    Args:
        quantity (Quantity | None): The restatement, or ``None`` for none.
        values (numpy.ndarray): The numbers to restate.
        where (str): How to name the argument that carried it, in any error.

    Returns:
        The restated numbers, or ``values`` itself when there is no transform.

    Raises:
        ValidationError: If the transform raises, returns a different shape, returns something other
            than real numbers, or introduces a non-finite value.
    """
    if quantity is None or quantity.transform is None:
        return values
    try:
        restated_values = np.asarray(quantity.transform(np.array(values, copy=True)))
    except Exception as exc:
        msg = (
            f"{where} raised {type(exc).__name__}: {exc}. It is called once with a numpy array of "
            f"shape {values.shape} and must return one number per value, so write it as arithmetic "
            f"over the whole array rather than over a single point."
        )
        raise ValidationError(msg) from exc
    if restated_values.shape != values.shape:
        msg = (
            f"{where} returned shape {restated_values.shape} for an input of shape {values.shape}. "
            f"A transform restates each value in place; select points with data.sel() before "
            f"plotting rather than inside it."
        )
        raise ValidationError(msg)
    if not _is_real(restated_values):
        msg = (
            f"{where} returned dtype {restated_values.dtype}, and an axis is drawn on real numbers. "
            f"Return the restated values themselves — numpy.abs(v) or v.real for a complex "
            f"result — and use label= for the words."
        )
        raise ValidationError(msg)
    _check_finite(values, restated_values, where)
    return restated_values


def text(quantity: Quantity | None, label: str, units: str | None, where: str) -> str:
    """Compose the text for one quantity, with the restatement applied.

    This holds the rule the type exists for: a change of unit and a change of numbers travel
    together. It fires only where there is a claim to falsify — a non-empty inherited unit — so a
    coordinate that declared none, or a demodulated magnitude that has none to declare, takes a bare
    transform or a bare ``units`` without complaint. Over values that did come with a unit, ``""``
    is a restatement like any other and needs the arithmetic that earns it: an axis whose numbers
    are still hertz reads as dimensionless once the unit is dropped from under them.

    Args:
        quantity (Quantity | None): The restatement, or ``None`` for none.
        label (str): The inherited name.
        units (str | None): The inherited unit, or ``None`` for none.
        where (str): How to name the argument that carried it, in any error.

    Returns:
        ``"Label (unit)"``, or the label alone when there is no unit.

    Raises:
        ValidationError: If a transform rescales values whose inherited unit was not restated with
            it, or if a unit contradicts the inherited one with no arithmetic to earn the change.
    """
    if quantity is None:
        return _joined(label, units)
    if quantity.units is None:
        if quantity.transform is not None and units:
            msg = (
                f"{where} rescales the values, so the inherited unit {units!r} no longer describes "
                f"them and the axis would read ({units}) over numbers that are not in {units}. Pass "
                f"units= for what they are now, units='' if they are now a bare ratio, or "
                f"units={units!r} if the transform leaves the unit alone, as subtracting a baseline "
                f"does."
            )
            raise ValidationError(msg)
        return _joined(quantity.label if quantity.label is not None else label, units)
    if quantity.transform is None and units and quantity.units != units:
        reads = f"read ({quantity.units})" if quantity.units else "carry no unit at all"
        msg = (
            f"{where} restates the unit from {units!r} to {quantity.units!r} without changing the "
            f"numbers, so the axis would {reads} over values still in {units}. Pass the transform= "
            f"that converts them, or correct the unit on the variable the coordinate came from."
        )
        raise ValidationError(msg)
    return _joined(quantity.label if quantity.label is not None else label, quantity.units)


def _joined(label: str, units: str | None) -> str:
    """Put a label and a unit together the way an axis reads them.

    Args:
        label (str): The quantity's name.
        units (str | None): Its unit, or ``None`` or ``""`` for none.

    Returns:
        ``"Label (unit)"``, or the label alone.
    """
    return f"{label} ({units})" if units else label


def _is_real(values: np.ndarray) -> bool:
    """Report whether an array holds real numbers, which is what an axis is drawn on.

    Args:
        values (numpy.ndarray): The array to inspect.

    Returns:
        ``True`` for a real numeric dtype, ``False`` for complex, text, or anything else.
    """
    return np.issubdtype(values.dtype, np.number) and not np.issubdtype(values.dtype, np.complexfloating)


def _check_finite(values: np.ndarray, restated_values: np.ndarray, where: str) -> None:
    """Raise when the transform turned a finite value non-finite.

    The comparison is elementwise and against the input, so a NaN the measurement already carried is
    not blamed on the transform, and one pre-existing NaN does not disable the check for the rest of
    the array. It is skipped when the input is not real numbers, since `numpy.isfinite` has
    nothing to say about a text coordinate.

    Args:
        values (numpy.ndarray): What the transform was given.
        restated_values (numpy.ndarray): What it returned.
        where (str): How to name the argument that carried it, in the error.

    Raises:
        ValidationError: If any value that was finite no longer is.
    """
    if not _is_real(values):
        return
    introduced = np.isfinite(values) & ~np.isfinite(restated_values)
    if not introduced.any():
        return
    first = int(np.flatnonzero(introduced.reshape(-1))[0])
    before = values.reshape(-1)[first]
    after = restated_values.reshape(-1)[first]
    msg = (
        f"{where} turned {int(introduced.sum())} of {int(np.isfinite(values).sum())} finite values "
        f"into inf or nan, the first at index {first} of the flattened array ({before} became "
        f"{after}). Check for a division by zero or a logarithm of a non-positive value, and clip "
        f"or shift the input first if the transform has a domain."
    )
    raise ValidationError(msg)
