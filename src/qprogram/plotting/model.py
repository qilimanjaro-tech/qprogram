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
"""What a figure *is*, with nothing about how it is drawn.

A [`Figure`][qprogram.plotting.Figure] is a sequence of marks and the two axis labels they share. A mark is one of
three shapes — a [`Line`][qprogram.plotting.Line], a [`Points`][qprogram.plotting.Points] cloud, or a
[`Mesh`][qprogram.plotting.Mesh] — each holding plain numpy arrays. There is no colour here, no figure size, and no
reference to a plotting library: those belong to [`Style`][qprogram.plotting.Style] and to the renderer.

Keeping the description separate is what makes a second renderer possible at all. It is also what
lets a caller inspect what would be drawn without drawing it, which is how the tests in
``tests/test_plotting.py`` check the shape of a figure without a display.

The dataclasses are frozen and compare by identity: a field holding a numpy array has no equality
that answers `True` or `False`, so an ``==`` between two figures would raise rather than
report a difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True, eq=False)
class Line:
    """A polyline through ``(x, y)``, drawn in the order the samples are given.

    Attributes:
        x (numpy.ndarray): Positions along the x axis. Same length as ``y``.
        y (numpy.ndarray): The values plotted against them.
        label (str | None): Legend entry. ``None`` for a figure whose single line needs no name.
    """

    x: np.ndarray
    y: np.ndarray
    label: str | None = None


@dataclass(frozen=True, eq=False)
class Points:
    """An unordered cloud of ``(x, y)`` samples — a scatter.

    Attributes:
        x (numpy.ndarray): Positions along the x axis. Same length as ``y``.
        y (numpy.ndarray): The values plotted against them.
        label (str | None): Legend entry, or ``None``.
    """

    x: np.ndarray
    y: np.ndarray
    label: str | None = None


@dataclass(frozen=True, eq=False)
class Mesh:
    """A rectangular grid of values, coloured by magnitude — a heatmap.

    Attributes:
        x (numpy.ndarray): Column positions, of length ``values.shape[1]``.
        y (numpy.ndarray): Row positions, of length ``values.shape[0]``.
        values (numpy.ndarray): The grid itself, indexed ``[row, column]`` — the layout
            `xarray.DataArray.values` gives for dimensions ordered ``(y, x)``.
        label (str | None): Colour-bar label, or ``None``.
    """

    x: np.ndarray
    y: np.ndarray
    values: np.ndarray
    label: str | None = None


Mark: TypeAlias = Line | Points | Mesh
"""Any one of the three shapes a figure is built from."""


@dataclass(frozen=True, eq=False)
class Figure:
    """A set of marks sharing one pair of axes.

    Attributes:
        marks (tuple[Mark, ...]): What to draw, in drawing order. A renderer dispatches on the type
            of each one, so a figure may mix a [`Mesh`][qprogram.plotting.Mesh] with the
            [`Line`][qprogram.plotting.Line] s drawn over it.
        x_label (str): Text for the x axis, already carrying its units.
        y_label (str): Text for the y axis, already carrying its units.
        title (str | None): Title above the axes, or ``None`` for none.
    """

    marks: tuple[Mark, ...]
    x_label: str
    y_label: str
    title: str | None = None
