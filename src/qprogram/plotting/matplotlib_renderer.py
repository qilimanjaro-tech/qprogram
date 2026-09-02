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
behind the data, and a legend with no box. What should carry the eye is the data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from qprogram.plotting.model import Line, Points

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure as MatplotlibFigure

    from qprogram.plotting.model import Figure, Mesh
    from qprogram.plotting.theme import Style

# Data sits above the grid and below the annotations that point at it.
_DATA_LAYER = 3


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
        fig, ax = plt.subplots(figsize=style.size)
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
            _mesh(ax, mark, style)

    ax.set_xlabel(figure.x_label)
    ax.set_ylabel(figure.y_label)
    if figure.title:
        ax.set_title(figure.title, color=style.theme.text, fontsize=10, loc="left", pad=6)
    _legend(ax, figure, style)
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


def _mesh(ax: Axes, mark: Mesh, style: Style) -> None:
    """Draw one coloured surface, with its colour bar.

    Args:
        ax (Axes): The axes to draw on.
        mark (Mesh): The grid and its colour-bar label.
        style (Style): The palette the ramp is built from, and whether a colour bar is wanted.
    """
    theme = style.theme
    cmap = LinearSegmentedColormap.from_list("qprogram-sequential", theme.ramp)
    mesh = ax.pcolormesh(mark.x, mark.y, mark.values, cmap=cmap, shading="nearest", rasterized=True)
    if not style.colorbar:
        return
    # An Axes always belongs to a figure; the annotation admits None for an axes under teardown.
    bar = cast("MatplotlibFigure", ax.get_figure()).colorbar(mesh, ax=ax, pad=0.02)
    if mark.label:
        bar.set_label(mark.label, color=theme.muted, fontsize=10)
    bar.ax.tick_params(colors=theme.muted, labelsize=9, length=0)
    bar.outline.set_edgecolor(theme.grid)
    bar.outline.set_linewidth(0.8)


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
