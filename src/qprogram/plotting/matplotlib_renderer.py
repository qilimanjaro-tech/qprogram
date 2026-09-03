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
"""The matplotlib renderer — the one implementation of [`Renderer`][qprogram.plotting.Renderer] that ships.

Nothing else in the package imports it. It is loaded the first time something resolves the
``"matplotlib"`` renderer, which is what keeps ``import qprogram`` free of a plotting library;
matplotlib itself comes with the ``viz`` extra, so a missing install surfaces here as a plain
`ModuleNotFoundError`.

The frame it draws is deliberately quiet: no top or right spine, ticks with no marks, grid lines
behind the data, and a legend with no box. What should carry the eye is the data. The exception is a
[`Twin`][qprogram.plotting.Twin], which puts a spine back on the side it reads from, because a
second scale that does not announce itself is worse than no second scale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from qprogram.plotting.model import Line, Points

if TYPE_CHECKING:
    from typing import Literal

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure as MatplotlibFigure
    from numpy.typing import ArrayLike

    from qprogram.plotting.model import Figure, Mesh, Twin
    from qprogram.plotting.theme import Style

# Data sits above the grid and below the annotations that point at it.
_DATA_LAYER = 3

# Gap between the axes and the colour bar, in fractions of the axes width. The twinned figure needs
# the wider one: its y twin reads up the right-hand side, where the narrow gap would put the colour
# bar through the tick labels.
_COLORBAR_PAD = 0.02
_TWINNED_COLORBAR_PAD = 0.13

# Significant figures on a twin's tick labels. The ticks sit at samples rather than at round
# numbers, so most of them are values no formatter would have chosen and four digits is where they
# stop growing the axis without saying more.
_TWIN_TICK_DIGITS = 4


def render(figure: Figure, style: Style, target: Axes | None = None) -> Axes:
    """Draw ``figure`` on a matplotlib `Axes`.

    Args:
        figure (Figure): What to draw.
        style (Style): The palette and the weights to draw it with.
        target (matplotlib.axes.Axes | None): An `Axes` to draw on. A fresh figure is created when
            ``None``, sized by `Style.size` and filled with the theme's surface colour.

    Returns:
        The `Axes` the marks were drawn on.
    """
    ax = target
    if ax is None:
        # A twin puts a scale where the default margins have nothing reserved — above the title, or
        # between the axes and the colour bar — so a figure carrying one is laid out constrained.
        # A figure the caller brought keeps whatever layout the caller gave it.
        twinned = figure.x_twin is not None or figure.y_twin is not None
        fig, ax = plt.subplots(figsize=style.size, layout="constrained" if twinned else None)
        fig.set_facecolor(style.theme.surface)
    _frame(ax, style)

    series = 0
    for mark in figure.marks:
        if isinstance(mark, Line):
            _line(ax, mark, style, series)
            series += 1
        elif isinstance(mark, Points):
            _points(ax, mark, style, series)
            series += 1
        else:
            # ``Mark`` is a closed union of the three, so what is left is a Mesh.
            _mesh(ax, mark, style, _TWINNED_COLORBAR_PAD if figure.y_twin else _COLORBAR_PAD)

    ax.set_xlabel(figure.x_label)
    ax.set_ylabel(figure.y_label)
    if figure.title:
        ax.set_title(figure.title, color=style.theme.text, fontsize=10, loc="left", pad=6)
    _legend(ax, figure, style)
    # Last, because a twin is furniture over a finished axis: it reads the scale and the limits the
    # marks and the labels above have already settled.
    _twin(ax, figure.x_twin, style, "x")
    _twin(ax, figure.y_twin, style, "y")
    return ax


def _frame(ax: Axes, style: Style) -> None:
    """Push the axes furniture back so the data reads first.

    Args:
        ax (Axes): The axes to restyle.
        style (Style): The palette and whether a grid is wanted.
    """
    theme = style.theme
    ax.set_facecolor(theme.surface)
    # Line properties alongside ``visible=False`` are what matplotlib warns about, so the off case
    # passes nothing but the switch.
    if style.grid:
        ax.grid(visible=True, color=theme.grid, linewidth=0.8, zorder=0)
    else:
        ax.grid(visible=False)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme.grid)
    ax.tick_params(colors=theme.muted, labelsize=9, length=0)
    ax.xaxis.label.set_color(theme.muted)
    ax.yaxis.label.set_color(theme.muted)
    ax.xaxis.label.set_fontsize(10)
    ax.yaxis.label.set_fontsize(10)


def _line(ax: Axes, mark: Line, style: Style, series: int) -> None:
    """Draw one polyline.

    Args:
        ax (Axes): The axes to draw on.
        mark (Line): The samples and the legend entry.
        style (Style): The palette and the stroke weight.
        series (int): Which categorical colour slot this mark takes.
    """
    ax.plot(
        mark.x,
        mark.y,
        color=style.color(series),
        linewidth=style.linewidth,
        marker="o" if style.markers else None,
        markersize=style.markersize,
        label=mark.label,
        zorder=_DATA_LAYER,
    )


def _points(ax: Axes, mark: Points, style: Style, series: int) -> None:
    """Draw one cloud of samples.

    Args:
        ax (Axes): The axes to draw on.
        mark (Points): The samples and the legend entry.
        style (Style): The palette, the point size, and the opacity that keeps a dense cloud
            readable.
        series (int): Which categorical colour slot this mark takes.
    """
    ax.scatter(
        mark.x,
        mark.y,
        s=style.point_size,
        alpha=style.point_alpha,
        linewidths=0,
        color=style.color(series),
        label=mark.label,
        zorder=_DATA_LAYER,
    )


def _mesh(ax: Axes, mark: Mesh, style: Style, pad: float) -> None:
    """Draw one coloured surface, with its colour bar.

    Args:
        ax (Axes): The axes to draw on.
        mark (Mesh): The grid and its colour-bar label.
        style (Style): The palette the ramp is built from, and whether a colour bar is wanted.
        pad (float): Gap between the axes and the colour bar, as a fraction of the axes width.
    """
    theme = style.theme
    cmap = LinearSegmentedColormap.from_list("qprogram-sequential", theme.ramp)
    mesh = ax.pcolormesh(mark.x, mark.y, mark.values, cmap=cmap, shading="nearest", rasterized=True)
    if not style.colorbar:
        return
    # An Axes always belongs to a figure; the annotation admits None for an axes under teardown.
    bar = cast("MatplotlibFigure", ax.get_figure()).colorbar(mesh, ax=ax, pad=pad)
    if mark.label:
        bar.set_label(mark.label, color=theme.muted, fontsize=10)
    bar.ax.tick_params(colors=theme.muted, labelsize=9, length=0)
    bar.outline.set_edgecolor(theme.grid)
    bar.outline.set_linewidth(0.8)


def _identity(values: ArrayLike) -> ArrayLike:
    """Return ``values`` unchanged.

    A secondary axis is defined by the pair of functions mapping between its scale and the parent's.
    A twin shares the parent's samples, so the pair is this function twice and the ticks are placed
    at positions the builder already worked out.

    Args:
        values (numpy.typing.ArrayLike): Positions on either scale.

    Returns:
        The same positions.
    """
    return values


def _twin(ax: Axes, twin: Twin | None, style: Style, orientation: Literal["x", "y"]) -> None:
    """Draw the second scale a parallel composition left on one axis.

    matplotlib's `Axes.secondary_xaxis` rather than `Axes.twiny`: a secondary axis is an artist of
    the axes it doubles, so it follows the position a colour bar shrank and the limits a later
    ``set_xlim`` changes, where a twinned Axes is a second Axes that has to be kept in step by hand.
    The scale is the identity and the ticks are fixed at the samples, since the two variables
    advanced in lockstep and every tick is a measurement rather than a round number.

    Args:
        ax (Axes): The axes to add the scale to.
        twin (Twin | None): The scale to draw, or ``None`` to draw none.
        style (Style): The palette, and how many ticks a twin gets.
        orientation (Literal["x", "y"]): ``"x"`` for a scale along the top, ``"y"`` for one up the
            right side.
    """
    if twin is None:
        return
    theme = style.theme
    if orientation == "x":
        secondary = ax.secondary_xaxis("top", functions=(_identity, _identity))
        side, axis = "top", secondary.xaxis
    else:
        secondary = ax.secondary_yaxis("right", functions=(_identity, _identity))
        side, axis = "right", secondary.yaxis
    positions, labels = _twin_ticks(twin, style.twin_ticks)
    secondary.set_ticks(positions, labels=labels)
    axis.set_label_text(twin.label)
    axis.label.set_color(theme.muted)
    axis.label.set_fontsize(10)
    secondary.tick_params(colors=theme.muted, labelsize=9, length=0)
    # The secondary axes is a sliver the height of a hairline, so its patch would paint a line of
    # surface colour over the spine it sits on.
    secondary.patch.set_visible(False)
    secondary.spines[side].set_color(theme.grid)


def _twin_ticks(twin: Twin, wanted: int) -> tuple[np.ndarray, list[str]]:
    """Choose where to tick a twin scale and what to write at each tick.

    The ticks land on samples, evenly spaced through the sweep and always including its ends. A
    parallel sweep need not be linear in either variable, and putting the ticks anywhere else would
    mean interpolating between measured points to label a position nothing was measured at.

    Args:
        twin (Twin): The scale being drawn.
        wanted (int): How many ticks to aim for. A sweep with fewer samples than that gets one per
            sample.

    Returns:
        The tick positions, on the parent axis's scale, and the text for each.
    """
    total = len(twin.positions)
    indices = np.unique(np.linspace(0, total - 1, min(max(wanted, 2), total)).round().astype(int))
    return twin.positions[indices], [f"{value:.{_TWIN_TICK_DIGITS}g}" for value in twin.values[indices]]


def _legend(ax: Axes, figure: Figure, style: Style) -> None:
    """Add a legend when there is more than one named mark to tell apart.

    A single named series needs no legend: the axis label already says what the curve is.

    Args:
        ax (Axes): The axes to draw on.
        figure (Figure): Read for how many of its marks carry a label.
        style (Style): The palette, and whether a legend is wanted at all.
    """
    named = [mark for mark in figure.marks if mark.label]
    if not style.legend or len(named) < 2:
        return
    legend = ax.legend(frameon=False, fontsize=9)
    for text in legend.get_texts():
        text.set_color(style.theme.text)
