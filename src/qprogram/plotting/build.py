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
"""Turning a result array into a [`Figure`][qprogram.plotting.Figure].

This is the whole of the reasoning about *what* to draw, and it uses numpy and xarray only. It reads
the dimensions the executor gave the array, decides which of them is an axis and which is a series,
pulls the axis labels off the coordinate attributes a swept variable left there, and hands back a
figure a renderer can draw without knowing any of it.

The ``"IQ"`` dimension is the one that is never an axis. It carries the two quadratures of a single
measured point, so it becomes the series of a line figure, the two axes of a scatter, or a single
derived surface for a heatmap — see the ``channels`` argument. Every other dimension, ``"time"``
included, is a plot dimension, which is what makes a raw trace plot against time on its own.

A dimension a parallel composition built carries one coordinate per composed variable rather than
one for itself. The first two become an axis and its [`Twin`][qprogram.plotting.Twin], in the order
the dimension name gives them, because the loops advanced in lockstep and both readings describe the
same samples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import numpy as np

from qprogram.errors import ValidationError
from qprogram.plotting.model import Figure, Line, Mesh, Points, Twin
from qprogram.plotting.quantity import Quantity, checked, restated, text

if TYPE_CHECKING:
    from collections.abc import Mapping

    import xarray as xr

    from qprogram.plotting.model import Mark

IQ_DIM = "IQ"
"""The dimension carrying the in-phase and quadrature halves of one measured point."""

# How the measured quantity's argument is named in an error. A coordinate's restatement is named by
# the key that carried it, ``coords['freq']``; the measured quantity has no key, only this argument.
_VALUE = "value="

KINDS = ("line", "heatmap", "scatter")
"""The figure shapes [`build_figure`][qprogram.plotting.build_figure] knows how to build."""

CHANNELS = ("iq", "i", "q", "magnitude", "phase")
"""The ways the ``"IQ"`` dimension can be turned into something plottable."""

# Channel name -> the (label, units) of the quantity it produces, for an array that names neither.
# The unit is held apart from the label rather than written into it, so that restating it is a
# change one rule can see: ``Quantity(units="deg", transform=numpy.degrees)`` composes "Phase (deg)"
# rather than "Phase (rad) (deg)", and forgetting the units= there fails like anywhere else. Only
# ``phase`` names a unit of its own — the others carry whatever the measured values carry.
_CHANNEL_LABELS = {
    "iq": ("Signal", None),
    "i": ("I", None),
    "q": ("Q", None),
    "magnitude": ("Magnitude", None),
    "phase": ("Phase", "rad"),
}


@dataclass(frozen=True, eq=False)
class _Resolved:
    """One axis after resolution, with its label and unit still held apart.

    Attributes:
        name (str): What this axis resolved to — the coordinate drawn on it, or the dimension when
            no coordinate is. This is the name a ``coords`` key has to match.
        values (numpy.ndarray): The positions along the axis.
        label (str): The name inherited from the coordinate, before any restatement.
        units (str | None): The unit inherited with it, or ``None`` for none.
    """

    name: str
    values: np.ndarray
    label: str
    units: str | None


@dataclass(frozen=True, eq=False)
class _Axis:
    """One finished axis: the numbers to draw on it, the words for it, and the twin beside it.

    Attributes:
        positions (numpy.ndarray): The positions along the axis, restated.
        label (str): The text for the axis, restated.
        twin (Twin | None): The second scale a composed dimension left over, or ``None``.
        drawn (tuple[tuple[str, str], ...]): The ``(name, role)`` of everything this axis consumed a
            ``coords`` key for — one entry, or two when it carries a twin. `_unused` lists these back
            to a caller whose key reached nothing.
    """

    positions: np.ndarray
    label: str
    twin: Twin | None
    drawn: tuple[tuple[str, str], ...]


def build_figure(  # ruff: ignore[too-many-arguments]  # every argument is one decision about the figure
    data: xr.DataArray,
    *,
    kind: str | None = None,
    x: str | None = None,
    y: str | None = None,
    channels: str | None = None,
    coords: Mapping[str, Quantity] | None = None,
    value: Quantity | None = None,
    title: str | None = None,
) -> Figure:
    """Describe the figure that ``data`` should be drawn as.

    Args:
        data (xarray.DataArray): A measurement field array, as
            [`QProgramResult.get`][qprogram.QProgramResult.get] returns it. Any array with at most one
            ``"IQ"`` dimension works.
        kind (str | None): One of `KINDS`. ``None`` infers it from the shape: one plot
            dimension gives ``"line"``, two give ``"heatmap"``. ``"scatter"`` is never inferred —
            plotting I against Q is a choice no shape implies.
        x (str | None): Dimension or coordinate to put on the x axis, drawn alone — naming an axis
            is what settles it, so no [`Twin`][qprogram.plotting.Twin] follows. ``None`` takes the
            dimension's own coordinate, or for a dimension a parallel composition built, the first
            two coordinates in the order the dimension name gives them: the first on the axis and
            the second as its twin.
        y (str | None): The same for the y axis of a ``"heatmap"``. The two arguments name different
            dimensions; naming one leaves the other for the axis not named. Only a heatmap has a
            second axis to choose, so a line or a scatter rejects it rather than ignoring it.
        channels (str | None): One of `CHANNELS`, deciding what the ``"IQ"`` dimension
            becomes. ``"iq"`` draws a line per quadrature, ``"i"`` and ``"q"`` one of them,
            ``"magnitude"`` gives ``hypot(I, Q)`` and ``"phase"`` gives ``arctan2(Q, I)``. ``None``
            takes ``"iq"`` for a line and ``"magnitude"`` for a heatmap, which needs a single
            surface and has no rotation to prefer I with. Rejected for an array with no ``"IQ"``
            dimension, such as a ``state`` field.
        coords (collections.abc.Mapping[str, Quantity] | None): Restatements for the swept
            coordinates, keyed by the name each axis or [`Twin`][qprogram.plotting.Twin] resolved to
            — the same string ``x=`` or ``y=`` takes, or the coordinate's own name when neither was
            given. Each
            [`Quantity`][qprogram.plotting.Quantity] carries the arithmetic and the words it produces
            together, so ``{"freq": Quantity(units="GHz", transform=lambda v: v / 1e9)}`` puts
            gigahertz on the axis and says so. A key naming no axis this figure draws raises: a
            figure that ignored it would print the axis it was asked to change.
        value (Quantity | None): Restatement for the measured quantity — the y axis of a line, the
            colour bar of a heatmap, both axes of a scatter. Its transform runs over each drawn
            series separately, once per quadrature for a line and over the ``[row, column]`` grid
            for a mesh, so ``lambda v: v - v[0]`` baselines each series against its own first point
            and ``lambda v: v - v[:, :1]`` is the per-row spelling.
        title (str | None): Title for the figure. No title by default.

    Returns:
        The [`Figure`][qprogram.plotting.Figure] to hand a renderer.

    Raises:
        ValidationError: If ``kind`` or ``channels`` is not a known name; if the array's shape does
            not suit the requested kind; if ``x`` or ``y`` names something that is not a coordinate
            on a plot dimension; if ``coords`` or ``value`` holds anything but a
            [`Quantity`][qprogram.plotting.Quantity]; if a ``coords`` key names no axis this figure
            draws; or if a restatement changes the numbers without the unit, or the unit without the
            numbers.
    """
    if kind is not None and kind not in KINDS:
        msg = f"kind must be one of {', '.join(KINDS)}, got {kind!r}"
        raise ValidationError(msg)
    if channels is not None and channels not in CHANNELS:
        msg = f"channels must be one of {', '.join(CHANNELS)}, got {channels!r}"
        raise ValidationError(msg)
    rescales = _rescales(coords)
    value = checked(value, _VALUE)

    dims = tuple(str(dim) for dim in data.dims if dim != IQ_DIM)
    kind = kind or _infer_kind(data, dims)
    if kind == "scatter":
        return _scatter(data, x, y, channels, rescales, value, title)
    if kind == "line":
        return _lines(data, dims, x, y, channels, rescales, value, title)
    return _heatmap(data, dims, x, y, channels, rescales, value, title)


def _rescales(coords: Mapping[str, Quantity] | None) -> dict[str, Quantity]:
    """Copy the ``coords`` mapping, checking every value is a [`Quantity`][qprogram.plotting.Quantity].

    The copy is what the builders pop from as they consume keys, so whatever is left at the end is
    by definition a key that named nothing the figure drew.

    Args:
        coords (collections.abc.Mapping[str, Quantity] | None): The caller's mapping, or ``None``.

    Returns:
        A mutable copy keyed by string.

    Raises:
        ValidationError: If ``coords`` is not a mapping, or holds anything but quantities.
    """
    if coords is None:
        return {}
    try:
        items = list(coords.items())
    except AttributeError as exc:
        msg = (
            f"coords= must be a mapping of coordinate name to Quantity, got "
            f"{type(coords).__name__}, e.g. {{'freq': Quantity(units='GHz', transform=f)}}."
        )
        raise ValidationError(msg) from exc
    return {str(name): cast("Quantity", checked(q, f"coords[{name!r}]")) for name, q in items}


def _infer_kind(data: xr.DataArray, dims: tuple[str, ...]) -> str:
    """Choose a figure shape from the dimensions left once ``"IQ"`` is set aside.

    Args:
        data (xarray.DataArray): The array being plotted, used only to phrase the error.
        dims (tuple[str, ...]): The plot dimensions, in array order.

    Returns:
        ``"line"`` for one dimension, ``"heatmap"`` for two.

    Raises:
        ValidationError: For no dimensions at all, or more than two.
    """
    if len(dims) == 1:
        return "line"
    if len(dims) == 2:
        return "heatmap"
    if not dims:
        msg = (
            f"Nothing to plot: {_shape(data)} is a single measured point with no dimension to plot "
            f"it against. Sweep a variable, or plot the raw trace, which carries a 'time' dimension."
        )
        raise ValidationError(msg)
    msg = (
        f"Cannot infer a figure for {_shape(data)}: {len(dims)} dimensions besides 'IQ' "
        f"({', '.join(dims)}) is more than a line or a heatmap can show. Select one down first, "
        f"with data.sel({dims[0]}=...), or pass kind='scatter' to plot I against Q."
    )
    raise ValidationError(msg)


def _shape(data: xr.DataArray) -> str:
    """Describe an array's dimensions the way an error message wants to read them.

    Args:
        data (xarray.DataArray): The array to describe.

    Returns:
        A parenthesised dimension list, e.g. ``"an array with dims ('gain', 'IQ')"``.
    """
    return f"an array with dims {tuple(str(dim) for dim in data.dims)}"


def _lines(  # ruff: ignore[too-many-arguments]
    data: xr.DataArray,
    dims: tuple[str, ...],
    x: str | None,
    y: str | None,
    channels: str | None,
    rescales: dict[str, Quantity],
    value: Quantity | None,
    title: str | None,
) -> Figure:
    """Build a line per channel against a single plot dimension.

    Args:
        data (xarray.DataArray): The array to plot.
        dims (tuple[str, ...]): Its plot dimensions.
        x (str | None): Dimension or coordinate for the x axis.
        y (str | None): Must be ``None``: a line figure's y axis is the measured value.
        channels (str | None): Requested channel treatment.
        rescales (dict[str, Quantity]): Restatements keyed by coordinate, consumed as they are used.
        value (Quantity | None): Restatement for the measured quantity.
        title (str | None): Figure title.

    Returns:
        A figure of [`Line`][qprogram.plotting.Line] marks.

    Raises:
        ValidationError: If the array does not have exactly one plot dimension, or if ``y`` was
            given.
    """
    if y is not None:
        msg = f"y={y!r} chooses a second dimension, which only a heatmap has; a line's y axis is the measured value."
        raise ValidationError(msg)
    if len(dims) != 1:
        msg = (
            f"kind='line' needs exactly one dimension besides 'IQ'; {_shape(data)} has "
            f"{len(dims)} ({', '.join(dims) or 'none'}). Select the others down with data.sel()."
        )
        raise ValidationError(msg)
    dim = dims[0]
    across = _axis(data, dim, x, "x", rescales)
    _unused(rescales, across.drawn, data, "line")
    channels = channels or _default_channels(data, "line")
    label, units = _measured(data, channels)
    marks: tuple[Mark, ...] = tuple(
        Line(
            x=across.positions,
            y=restated(value, np.asarray(values.transpose(dim).values), _VALUE),
            label=series,
        )
        for series, values in _channels(data, channels, "line")
    )
    return Figure(
        marks=marks,
        x_label=across.label,
        y_label=text(value, label, units, _VALUE),
        title=title,
        x_twin=across.twin,
    )


def _heatmap(  # ruff: ignore[too-many-arguments]
    data: xr.DataArray,
    dims: tuple[str, ...],
    x: str | None,
    y: str | None,
    channels: str | None,
    rescales: dict[str, Quantity],
    value: Quantity | None,
    title: str | None,
) -> Figure:
    """Build a single coloured surface over two plot dimensions.

    Args:
        data (xarray.DataArray): The array to plot.
        dims (tuple[str, ...]): Its plot dimensions.
        x (str | None): Dimension or coordinate for the x axis.
        y (str | None): Dimension or coordinate for the y axis.
        channels (str | None): Requested channel treatment; must name a single surface.
        rescales (dict[str, Quantity]): Restatements keyed by coordinate, consumed as they are used.
        value (Quantity | None): Restatement for the coloured values and the colour bar.
        title (str | None): Figure title.

    Returns:
        A figure holding one [`Mesh`][qprogram.plotting.Mesh].

    Raises:
        ValidationError: If the array does not have exactly two plot dimensions, or if ``x`` and
            ``y`` name the same one.
    """
    if len(dims) != 2:
        msg = (
            f"kind='heatmap' needs exactly two dimensions besides 'IQ'; {_shape(data)} has "
            f"{len(dims)} ({', '.join(dims) or 'none'})."
        )
        raise ValidationError(msg)
    x_dim, y_dim = _mesh_dims(data, dims, x, y)
    across = _axis(data, x_dim, x, "x", rescales)
    up = _axis(data, y_dim, y, "y", rescales)
    _unused(rescales, across.drawn + up.drawn, data, "heatmap")
    channels = channels or _default_channels(data, "heatmap")
    ((_, values),) = _channels(data, channels, "heatmap")
    label, units = _measured(data, channels)
    mesh = Mesh(
        x=across.positions,
        y=up.positions,
        values=restated(value, np.asarray(values.transpose(y_dim, x_dim).values), _VALUE),
        label=text(value, label, units, _VALUE),
    )
    return Figure(
        marks=(mesh,),
        x_label=across.label,
        y_label=up.label,
        title=title,
        x_twin=across.twin,
        y_twin=up.twin,
    )


def _consume(rescales: dict[str, Quantity], resolved: _Resolved) -> tuple[np.ndarray, str]:
    """Take the restatement for one resolved axis out of the mapping and apply it.

    Popping as the figure is built is what makes a silent no-op inexpressible: a key is either
    consumed by an axis or it is left over for `_unused` to raise on.

    Args:
        rescales (dict[str, Quantity]): The remaining restatements. Mutated.
        resolved (_Resolved): The axis to draw.

    Returns:
        The positions along the axis and the text to label it with.

    Raises:
        ValidationError: If the restatement changes the numbers without the unit, or the unit
            without the numbers, or its transform misbehaves.
    """
    where = f"coords[{resolved.name!r}]"
    quantity = rescales.pop(resolved.name, None)
    return (
        restated(quantity, resolved.values, where),
        text(quantity, resolved.label, resolved.units, where),
    )


def _unused(
    rescales: dict[str, Quantity],
    drawn: tuple[tuple[str, str], ...],
    data: xr.DataArray,
    kind: str,
) -> None:
    """Raise for every ``coords`` key no axis consumed, all of them in one message.

    Three mistakes end up here and they have three different fixes: a typo, a real coordinate that
    lost the axis to a sibling on a composed dimension, and a dimension name where the coordinate
    along it is what gets drawn. The message tells them apart and then lists what this figure
    actually draws, so the caller does not have to work out the difference.

    Args:
        rescales (dict[str, Quantity]): Whatever is left after every axis has taken its own.
        drawn (tuple[tuple[str, str], ...]): The ``(name, role)`` of each axis this figure draws.
        data (xarray.DataArray): The array being plotted, for the listing of what is on it.
        kind (str): The figure kind, to name it in the message.

    Raises:
        ValidationError: If anything is left.
    """
    if not rescales:
        return
    reasons = "; ".join(_unused_reason(key, data) for key in sorted(rescales))
    listing = ", ".join(f"{name!r} ({role})" for name, role in drawn)
    msg = (
        f"{reasons}. This {kind} draws: {listing}. The measured quantity is not keyed here — name "
        f"it with value=Quantity(...). Coordinates on this result: "
        f"{', '.join(str(name) for name in data.coords) or 'none'}."
    )
    raise ValidationError(msg)


def _unused_reason(key: str, data: xr.DataArray) -> str:
    """Say which of the three mistakes one leftover key is.

    Args:
        key (str): The unconsumed ``coords`` key.
        data (xarray.DataArray): The array being plotted.

    Returns:
        A clause naming what the key turned out to be.
    """
    prefix = f"coords[{key!r}]"
    if key in data.dims:
        return f"{prefix} names a dimension, and this figure draws a coordinate along it, not the sweep index"
    if key in data.coords:
        return f"{prefix} names a coordinate this figure does not draw"
    return f"{prefix} names nothing on this result"


def _mesh_dims(
    data: xr.DataArray,
    dims: tuple[str, ...],
    x: str | None,
    y: str | None,
) -> tuple[str, str]:
    """Decide which plot dimension goes on which axis of a heatmap.

    The default puts the innermost sweep on the x axis and the outermost on the y axis, so the
    picture matches the loop nesting: the variable that changes fastest runs left to right.

    Args:
        data (xarray.DataArray): The array being plotted.
        dims (tuple[str, ...]): Its two plot dimensions, in array order.
        x (str | None): Dimension or coordinate requested for the x axis.
        y (str | None): Dimension or coordinate requested for the y axis.

    Returns:
        The ``(x_dim, y_dim)`` pair.

    Raises:
        ValidationError: If both arguments resolve to the same dimension.
    """
    outer, inner = dims
    x_dim = _dim_of(data, dims, x, "x") if x is not None else None
    y_dim = _dim_of(data, dims, y, "y") if y is not None else None
    if x_dim is not None and y_dim is not None and x_dim == y_dim:
        msg = f"x={x!r} and y={y!r} both name dimension {x_dim!r}; a heatmap needs one on each axis"
        raise ValidationError(msg)
    if x_dim is None:
        x_dim = outer if y_dim == inner else inner
    if y_dim is None:
        y_dim = inner if x_dim == outer else outer
    return x_dim, y_dim


def _dim_of(data: xr.DataArray, dims: tuple[str, ...], name: str, argument: str) -> str:
    """Return the plot dimension ``name`` sits on.

    Args:
        data (xarray.DataArray): The array being plotted.
        dims (tuple[str, ...]): Its plot dimensions.
        name (str): A dimension name or the name of a coordinate on one.
        argument (str): ``"x"`` or ``"y"``, so the error names the argument the caller passed.

    Returns:
        The dimension name.

    Raises:
        ValidationError: If ``name`` is neither a plot dimension nor a coordinate on one.
    """
    if name in dims:
        return name
    coord = data.coords.get(name)
    if coord is not None and len(coord.dims) == 1 and str(coord.dims[0]) in dims:
        return str(coord.dims[0])
    msg = (
        f"{argument}={name!r} is not a dimension or a coordinate of this result. "
        f"Dimensions: {', '.join(dims)}. Coordinates: {', '.join(str(c) for c in data.coords) or 'none'}."
    )
    raise ValidationError(msg)


def _axis(
    data: xr.DataArray,
    dim: str,
    requested: str | None,
    argument: str,
    rescales: dict[str, Quantity],
) -> _Axis:
    """Resolve one axis of a figure, restate it, and pick up any twin beside it.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The plot dimension this axis shows.
        requested (str | None): The dimension or coordinate the caller named for this axis, if any.
        argument (str): ``"x"`` or ``"y"``, for the error messages and the roles in them.
        rescales (dict[str, Quantity]): Restatements keyed by coordinate. Mutated: this axis and its
            twin each pop their own.

    Returns:
        The finished axis.

    Raises:
        ValidationError: If ``requested`` is not a coordinate on ``dim``, or if a restatement this
            axis consumed changes the numbers without the unit, or the unit without the numbers.
    """
    primary, partner = _coordinates(data, dim, requested, argument)
    positions, label = _consume(rescales, primary)
    role = f"the {argument} axis"
    if partner is None:
        return _Axis(positions=positions, label=label, twin=None, drawn=((primary.name, role),))
    values, twin_label = _consume(rescales, partner)
    return _Axis(
        positions=positions,
        label=label,
        twin=Twin(positions=positions, values=values, label=twin_label),
        drawn=((primary.name, role), (partner.name, f"the twin of {role}")),
    )


def _coordinates(
    data: xr.DataArray,
    dim: str,
    requested: str | None,
    argument: str,
) -> tuple[_Resolved, _Resolved | None]:
    """Choose the coordinate an axis draws, and the one that doubles it.

    A dimension a parallel composition built carries one coordinate per composed variable and none
    of its own. Its loops advanced in lockstep, though, so every one of those coordinates describes
    the same samples, and the second reading of them is a twin axis rather than a choice to be made:
    the first two coordinates go on the axis and opposite it. Naming ``x=`` or ``y=`` draws that one
    alone, which is how an axis with nothing above it is asked for.

    A composition of three or more leaves the rest undrawn. Two scales on one axis is already the
    most a reader can follow, and a third would be a legend rather than an axis; the coordinates are
    all still on the array for a caller who wants one of them instead.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The plot dimension this axis shows.
        requested (str | None): The dimension or coordinate the caller named for this axis, if any.
        argument (str): ``"x"`` or ``"y"``, for the error message.

    Returns:
        The coordinate on the axis, and the one to twin it with or ``None``.

    Raises:
        ValidationError: If ``requested`` is not a coordinate on ``dim``.
    """
    if requested is not None:
        _dim_of(data, (dim,), requested, argument)
        return _coordinate(data, dim, requested), None
    if dim in data.coords:
        return _coordinate(data, dim, dim), None
    candidates = _candidates(data, dim)
    if not candidates:
        return _Resolved(name=dim, values=np.arange(data.sizes[dim]), label=dim, units=None), None
    primary = _coordinate(data, dim, candidates[0])
    if len(candidates) == 1:
        return primary, None
    return primary, _coordinate(data, dim, candidates[1])


def _candidates(data: xr.DataArray, dim: str) -> list[str]:
    """List the coordinates lying along ``dim``, in the order the dimension name gives them.

    A parallel composition names its dimension by joining the ids of the variables it composes with
    ``"|"``, so the name is the declaration order of the loops — which is the order the axis and its
    twin are wanted in, and not what a mapping of coordinates hands back. A name that spells none of
    them out, which is any array not built by the executor, leaves the array's own order alone.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The dimension to look along.

    Returns:
        The coordinate names, the ones the dimension name spells out first.
    """
    along = [str(name) for name, coord in data.coords.items() if coord.dims == (dim,)]
    ranked = [part for part in dim.split("|") if part in along]
    return ranked + [name for name in along if name not in ranked]


def _coordinate(data: xr.DataArray, dim: str, name: str) -> _Resolved:
    """Return one coordinate's values, with its name and unit still held apart.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The dimension the coordinate lies along.
        name (str): The coordinate's name, or ``dim`` itself for a dimension with no coordinate.

    Returns:
        What this axis draws, still unrestated.
    """
    if name not in data.coords or data.coords[name].dims != (dim,):
        # A dimension carrying no coordinate of its own, or a name that belongs to another
        # dimension: either way this axis has no values but its own index. xarray allows a
        # coordinate named after one dimension to live on a second, and the executor builds exactly
        # that when one variable is swept at two nesting levels, so taking the name at face value
        # here would draw the other dimension's values under this one's label.
        return _Resolved(name=name, values=np.arange(data.sizes[dim]), label=name, units=None)
    coord = data.coords[name]
    label = coord.attrs.get("long_name")
    units = coord.attrs.get("units")
    return _Resolved(
        name=name,
        values=np.asarray(coord.values),
        label=str(label) if label else name,
        units=str(units) if units else None,
    )


def _default_channels(data: xr.DataArray, kind: str) -> str | None:
    """Choose what to do with the quadratures when the caller did not say.

    A line figure draws both, which is the pair a measurement produced. A heatmap colours one
    surface and has to reduce them: it takes the magnitude, the one function of I and Q that does
    not depend on a readout rotation the executor never applied.

    Args:
        data (xarray.DataArray): The array being plotted.
        kind (str): The figure kind being built.

    Returns:
        The channel name, or ``None`` for an array with no ``"IQ"`` dimension to reduce.
    """
    if IQ_DIM not in data.dims:
        return None
    return "magnitude" if kind == "heatmap" else "iq"


def _channels(data: xr.DataArray, channels: str | None, kind: str) -> list[tuple[str | None, xr.DataArray]]:
    """Turn the ``"IQ"`` dimension into the series a figure draws.

    Args:
        data (xarray.DataArray): The array to read the quadratures from.
        channels (str | None): One of `CHANNELS`, or ``None`` for an array with no quadratures.
        kind (str): The figure kind, so the error can say why a pair will not do.

    Returns:
        One ``(label, values)`` pair per series, each ``values`` array without the ``"IQ"``
        dimension.

    Raises:
        ValidationError: If the array has no ``"IQ"`` dimension to reduce, or if a heatmap was asked
            for both quadratures at once.
    """
    if IQ_DIM not in data.dims:
        if channels is not None:
            msg = (
                f"channels={channels!r} needs an 'IQ' dimension; {_shape(data)} has none. "
                f"A 'state' field is already one number per point."
            )
            raise ValidationError(msg)
        return [(None, data)]
    if channels == "iq" and kind == "heatmap":
        msg = "A heatmap colours one surface, and channels='iq' is two. Pass channels='magnitude', 'phase', 'i' or 'q'."
        raise ValidationError(msg)
    in_phase = data.sel({IQ_DIM: "I"})
    quadrature = data.sel({IQ_DIM: "Q"})
    if channels == "iq":
        return [("I", in_phase), ("Q", quadrature)]
    if channels == "i":
        return [("I", in_phase)]
    if channels == "q":
        return [("Q", quadrature)]
    # numpy's ufuncs dispatch through xarray and hand back a DataArray with the coordinates intact;
    # the cast is only to say so, since their annotations stop at ndarray.
    if channels == "magnitude":
        return [("Magnitude", cast("xr.DataArray", np.hypot(in_phase, quadrature)))]
    return [("Phase", cast("xr.DataArray", np.arctan2(quadrature, in_phase)))]


def _measured(data: xr.DataArray, channels: str | None) -> tuple[str, str | None]:
    """Return the inherited label and unit of the measured quantity.

    The array's own ``long_name`` is read first, then its name, then the name the channel implies.
    The unit is the channel's when the channel defines one — only ``"phase"`` does, since an
    arctangent is radians whatever went into it — and otherwise the array's own.

    Args:
        data (xarray.DataArray): The array being plotted.
        channels (str | None): The channel treatment in force.

    Returns:
        The ``(label, units)`` a [`Quantity`][qprogram.plotting.Quantity] restates.
    """
    label, units = _CHANNEL_LABELS.get(channels, ("Value", None)) if channels is not None else ("Value", None)
    if units is not None:
        # A channel declares a unit only where it made a new quantity out of the pair, and there its
        # own name belongs with its own unit: the array names the values that went in, not the
        # arctangent that came out.
        return label, units
    attribute = data.attrs.get("units")
    inherited = data.attrs.get("long_name") or data.name
    return (str(inherited) if inherited else label), (str(attribute) if attribute else None)


def _scatter(  # ruff: ignore[too-many-arguments]
    data: xr.DataArray,
    x: str | None,
    y: str | None,
    channels: str | None,
    rescales: dict[str, Quantity],
    value: Quantity | None,
    title: str | None,
) -> Figure:
    """Build I against Q, every other dimension flattened into the cloud.

    Args:
        data (xarray.DataArray): The array to plot.
        x (str | None): Must be ``None``: the x axis of a scatter is the in-phase quadrature.
        y (str | None): Must be ``None``: the y axis is the other one.
        channels (str | None): Must be ``None``: the axes of a scatter *are* the quadratures.
        rescales (dict[str, Quantity]): Must be empty: a scatter draws no coordinate.
        value (Quantity | None): Restatement for the pair. Its ``units`` and ``transform`` reach
            both axes; a ``label`` is refused, since I and Q already name themselves.
        title (str | None): Figure title.

    Returns:
        A figure holding one [`Points`][qprogram.plotting.Points] mark.

    Raises:
        ValidationError: If the array has no ``"IQ"`` dimension, if any of the arguments that choose
            an axis was given, or if ``value`` carries a label.
    """
    if IQ_DIM not in data.dims:
        msg = f"kind='scatter' plots I against Q, and {_shape(data)} has no 'IQ' dimension."
        raise ValidationError(msg)
    named = [name for name, given in (("x", x), ("y", y), ("channels", channels)) if given is not None]
    if rescales:
        named.append("coords")
    if named:
        pointer = " A scatter draws no coordinate; restate the quadratures with value=." if rescales else ""
        msg = (
            f"kind='scatter' already puts I on one axis and Q on the other; "
            f"{', '.join(named)} has nothing left to choose.{pointer}"
        )
        raise ValidationError(msg)
    if value is not None and value.label is not None:
        msg = (
            "value=Quantity(label=...) names one quantity, and a scatter's two axes are I and Q, "
            "which already name themselves — one label on both would hide which is which. Pass "
            "units= and transform= to restate the pair, and title= to name the figure."
        )
        raise ValidationError(msg)
    units = data.attrs.get("units")
    units = str(units) if units else None
    in_phase = restated(value, np.asarray(data.sel({IQ_DIM: "I"}).values).reshape(-1), _VALUE)
    quadrature = restated(value, np.asarray(data.sel({IQ_DIM: "Q"}).values).reshape(-1), _VALUE)
    return Figure(
        marks=(Points(x=in_phase, y=quadrature),),
        x_label=text(value, "I", units, _VALUE),
        y_label=text(value, "Q", units, _VALUE),
        title=title,
    )
