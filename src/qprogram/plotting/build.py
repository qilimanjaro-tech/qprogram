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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np

from qprogram.errors import ValidationError
from qprogram.plotting.model import Figure, Line, Mesh, Points

if TYPE_CHECKING:
    import xarray as xr

    from qprogram.plotting.model import Mark

IQ_DIM = "IQ"
"""The dimension carrying the in-phase and quadrature halves of one measured point."""

KINDS = ("line", "heatmap", "scatter")
"""The figure shapes [`build_figure`][qprogram.plotting.build_figure] knows how to build."""

CHANNELS = ("iq", "i", "q", "magnitude", "phase")
"""The ways the ``"IQ"`` dimension can be turned into something plottable."""

# Channel name -> the label for the measured quantity, when the array itself carries none.
_CHANNEL_LABELS = {
    "iq": "Signal",
    "i": "I",
    "q": "Q",
    "magnitude": "Magnitude",
    "phase": "Phase (rad)",
}


def build_figure(  # ruff: ignore[too-many-arguments]  # every argument is one decision about the figure
    data: xr.DataArray,
    *,
    kind: str | None = None,
    x: str | None = None,
    y: str | None = None,
    channels: str | None = None,
    value_label: str | None = None,
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
        x (str | None): Dimension or coordinate to put on the x axis. Required when the dimension
            composes several swept variables, since it holds one coordinate per variable and no
            preference between them.
        y (str | None): The same for the y axis of a ``"heatmap"``. The two arguments name different
            dimensions; naming one leaves the other for the axis not named. Only a heatmap has a
            second axis to choose, so a line or a scatter rejects it rather than ignoring it.
        channels (str | None): One of `CHANNELS`, deciding what the ``"IQ"`` dimension
            becomes. ``"iq"`` draws a line per quadrature, ``"i"`` and ``"q"`` one of them,
            ``"magnitude"`` gives ``hypot(I, Q)`` and ``"phase"`` gives ``arctan2(Q, I)``. ``None``
            takes ``"iq"`` for a line and ``"magnitude"`` for a heatmap, which needs a single
            surface and has no rotation to prefer I with. Rejected for an array with no ``"IQ"``
            dimension, such as a ``state`` field.
        value_label (str | None): Label for the measured quantity — the y axis of a line figure, the
            colour bar of a heatmap. Defaults to the array's own ``long_name`` attribute, then to
            its name, then to what the channel implies.
        title (str | None): Title for the figure. No title by default.

    Returns:
        The [`Figure`][qprogram.plotting.Figure] to hand a renderer.

    Raises:
        ValidationError: If ``kind`` or ``channels`` is not a known name, if the array's shape does
            not suit the requested kind, if ``x`` or ``y`` names something that is not a coordinate
            on a plot dimension, or if a composed dimension is left without one.
    """
    if kind is not None and kind not in KINDS:
        msg = f"kind must be one of {', '.join(KINDS)}, got {kind!r}"
        raise ValidationError(msg)
    if channels is not None and channels not in CHANNELS:
        msg = f"channels must be one of {', '.join(CHANNELS)}, got {channels!r}"
        raise ValidationError(msg)

    dims = tuple(str(dim) for dim in data.dims if dim != IQ_DIM)
    kind = kind or _infer_kind(data, dims)
    if kind == "scatter":
        return _scatter(data, x, y, channels, title)
    if kind == "line":
        return _lines(data, dims, x, y, channels, value_label, title)
    return _heatmap(data, dims, x, y, channels, value_label, title)


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
    value_label: str | None,
    title: str | None,
) -> Figure:
    """Build a line per channel against a single plot dimension.

    Args:
        data (xarray.DataArray): The array to plot.
        dims (tuple[str, ...]): Its plot dimensions.
        x (str | None): Dimension or coordinate for the x axis.
        y (str | None): Must be ``None``: a line figure's y axis is the measured value.
        channels (str | None): Requested channel treatment.
        value_label (str | None): Label for the y axis.
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
    positions, x_label = _axis(data, dim, x, "x")
    channels = channels or _default_channels(data, "line")
    marks: tuple[Mark, ...] = tuple(
        Line(x=positions, y=np.asarray(values.transpose(dim).values), label=label)
        for label, values in _channels(data, channels, "line")
    )
    return Figure(marks=marks, x_label=x_label, y_label=_value_label(data, channels, value_label), title=title)


def _heatmap(  # ruff: ignore[too-many-arguments]
    data: xr.DataArray,
    dims: tuple[str, ...],
    x: str | None,
    y: str | None,
    channels: str | None,
    value_label: str | None,
    title: str | None,
) -> Figure:
    """Build a single coloured surface over two plot dimensions.

    Args:
        data (xarray.DataArray): The array to plot.
        dims (tuple[str, ...]): Its plot dimensions.
        x (str | None): Dimension or coordinate for the x axis.
        y (str | None): Dimension or coordinate for the y axis.
        channels (str | None): Requested channel treatment; must name a single surface.
        value_label (str | None): Label for the colour bar.
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
    columns, x_label = _axis(data, x_dim, x, "x")
    rows, y_label = _axis(data, y_dim, y, "y")
    channels = channels or _default_channels(data, "heatmap")
    ((_, values),) = _channels(data, channels, "heatmap")
    mesh = Mesh(
        x=columns,
        y=rows,
        values=np.asarray(values.transpose(y_dim, x_dim).values),
        label=_value_label(data, channels, value_label),
    )
    return Figure(marks=(mesh,), x_label=x_label, y_label=y_label, title=title)


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


def _axis(data: xr.DataArray, dim: str, requested: str | None, argument: str) -> tuple[np.ndarray, str]:
    """Resolve the positions and the label for one axis.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The plot dimension this axis shows.
        requested (str | None): The dimension or coordinate the caller named for this axis, if any.
        argument (str): ``"x"`` or ``"y"``, for the error messages.

    Returns:
        The positions along the axis and the text to label it with.

    Raises:
        ValidationError: If ``requested`` is not a coordinate on ``dim``, or if the dimension
            composes several variables and nothing chose between them.
    """
    if requested is not None:
        _dim_of(data, (dim,), requested, argument)
        return _coordinate(data, dim, requested)
    if dim in data.coords:
        return _coordinate(data, dim, dim)
    candidates = [str(name) for name, coord in data.coords.items() if coord.dims == (dim,)]
    if len(candidates) == 1:
        return _coordinate(data, dim, candidates[0])
    if not candidates:
        return np.arange(data.sizes[dim]), dim
    choices = " or ".join(f"{argument}={name!r}" for name in candidates)
    msg = (
        f"Dimension {dim!r} composes {len(candidates)} swept variables, so it carries no single "
        f"coordinate to put on the {argument} axis. Name one with {choices}, or {argument}={dim!r} "
        f"for the sweep index."
    )
    raise ValidationError(msg)


def _coordinate(data: xr.DataArray, dim: str, name: str) -> tuple[np.ndarray, str]:
    """Return one coordinate's values and its axis label.

    Args:
        data (xarray.DataArray): The array being plotted.
        dim (str): The dimension the coordinate lies along.
        name (str): The coordinate's name, or ``dim`` itself for a dimension with no coordinate.

    Returns:
        The values along the axis and the text to label it with.
    """
    if name not in data.coords:
        # A dimension named explicitly but carrying no coordinate: plot against the sweep index.
        return np.arange(data.sizes[dim]), name
    coord = data.coords[name]
    label = str(coord.attrs.get("long_name") or name)
    units = coord.attrs.get("units")
    return np.asarray(coord.values), f"{label} ({units})" if units else label


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
        if channels not in {"iq", None}:
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


def _value_label(data: xr.DataArray, channels: str | None, requested: str | None) -> str:
    """Choose the label for the measured quantity.

    Args:
        data (xarray.DataArray): The array being plotted, read for its own attributes.
        channels (str | None): The channel treatment in force.
        requested (str | None): The caller's label, which wins when given.

    Returns:
        The axis or colour-bar text.
    """
    if requested is not None:
        return requested
    long_name = data.attrs.get("long_name")
    if long_name:
        units = data.attrs.get("units")
        return f"{long_name} ({units})" if units else str(long_name)
    if data.name:
        return str(data.name)
    if channels is None:
        return "Value"
    return _CHANNEL_LABELS[channels]


def _scatter(
    data: xr.DataArray,
    x: str | None,
    y: str | None,
    channels: str | None,
    title: str | None,
) -> Figure:
    """Build I against Q, every other dimension flattened into the cloud.

    Args:
        data (xarray.DataArray): The array to plot.
        x (str | None): Must be ``None``: the x axis of a scatter is the in-phase quadrature.
        y (str | None): Must be ``None``: the y axis is the other one.
        channels (str | None): Must be ``None``: the axes of a scatter *are* the quadratures.
        title (str | None): Figure title.

    Returns:
        A figure holding one [`Points`][qprogram.plotting.Points] mark.

    Raises:
        ValidationError: If the array has no ``"IQ"`` dimension, or if any of the three arguments
            that choose an axis was given.
    """
    if IQ_DIM not in data.dims:
        msg = f"kind='scatter' plots I against Q, and {_shape(data)} has no 'IQ' dimension."
        raise ValidationError(msg)
    named = [name for name, value in (("x", x), ("y", y), ("channels", channels)) if value is not None]
    if named:
        msg = (
            f"kind='scatter' already puts I on one axis and Q on the other; "
            f"{', '.join(named)} has nothing left to choose."
        )
        raise ValidationError(msg)
    in_phase = np.asarray(data.sel({IQ_DIM: "I"}).values).reshape(-1)
    quadrature = np.asarray(data.sel({IQ_DIM: "Q"}).values).reshape(-1)
    return Figure(marks=(Points(x=in_phase, y=quadrature),), x_label="I", y_label="Q", title=title)
