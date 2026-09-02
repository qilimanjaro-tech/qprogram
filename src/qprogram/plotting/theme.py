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
"""Colours and drawing settings, held apart from both the figure and the renderer.

A [`Theme`][qprogram.plotting.Theme] is the palette and nothing else; a [`Style`][qprogram.plotting.Style] is a theme
plus the handful of settings that decide how heavy the marks are. Neither imports a plotting library,
so a renderer for any backend reads the same two objects.

Two themes ship: [`LIGHT`][qprogram.plotting.LIGHT] and [`DARK`][qprogram.plotting.DARK]. Both are frozen dataclasses,
so a variant is one `dataclasses.replace` away and a whole palette of your own is a constructor call.

A style names no figure size of its own by default, so the same `Style()` suits a measurement and a
pulse: whatever is drawing fills in the size that suits it, from the three named here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Theme:
    """A palette: the surface a figure sits on, its text and frame, and the colours the data takes.

    Attributes:
        surface (str): Background of the figure and the axes.
        text (str): Titles and legend entries — the type meant to be read.
        muted (str): Axis labels, tick labels, and annotations, one step back from ``text``.
        grid (str): Grid lines and the axis spines, the furthest back of the three.
        series (tuple[str, ...]): Categorical slots, used in order, one per series. Cycled when a
            figure carries more series than the theme has slots.
        ramp (tuple[str, ...]): Sequential ramp for a [`Mesh`][qprogram.plotting.Mesh], darkest-to-lightest or the
            reverse, whichever runs away from ``surface``. Interpolated by the renderer.
    """

    surface: str
    text: str
    muted: str
    grid: str
    series: tuple[str, ...]
    ramp: tuple[str, ...]


LIGHT = Theme(
    surface="#ffffff",
    text="#0b0b0b",
    muted="#52514e",
    grid="#e6e6e3",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),
    ramp=("#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#256abf", "#184f95", "#0d366b"),
)
"""The palette for a light surface: four categorical hues and a ramp that darkens away from white."""

DARK = Theme(
    surface="#1e2129",
    text="#e2e4e9",
    muted="#a2a7b3",
    grid="#33373f",
    series=("#3987e5", "#d95926", "#199e70", "#c98500"),
    ramp=("#232733", "#1d3a63", "#1c5497", "#2a78d6", "#5598e7", "#9ec5f4", "#cde2fb"),
)
"""The palette for a dark surface. The same four hues, stepped for slate rather than flipped, and a
ramp that lightens away from it — so in both themes the strongest mark is the one furthest off the
page."""


DEFAULT_SIZE = (7.2, 4.0)
"""Figure size in inches a renderer makes a new figure at, which is what
[`QProgramResult.plot`][qprogram.QProgramResult.plot] draws a measurement at."""

ENVELOPE_SIZE = (6.0, 2.0)
"""Figure size for [`Waveform.plot`][qprogram.waveforms.Waveform.plot]. An envelope is one line over
a few hundred nanoseconds, so four inches of height would be mostly empty surface."""

IQ_ENVELOPE_SIZE = (6.0, 3.0)
"""Figure size for [`IQWaveform.plot`][qprogram.waveforms.IQWaveform.plot], an inch taller than
[`ENVELOPE_SIZE`][qprogram.plotting.ENVELOPE_SIZE] because it stacks two panels."""


@dataclass(frozen=True)
class Style:
    """A theme plus the settings that decide how the marks are drawn.

    Attributes:
        theme (Theme): The palette. Defaults to [`LIGHT`][qprogram.plotting.LIGHT].
        size (tuple[float, float] | None): Figure size in inches, ``(width, height)``, or ``None``
            for the size that suits what is being drawn:
            [`DEFAULT_SIZE`][qprogram.plotting.DEFAULT_SIZE] for a measurement,
            [`ENVELOPE_SIZE`][qprogram.plotting.ENVELOPE_SIZE] or
            [`IQ_ENVELOPE_SIZE`][qprogram.plotting.IQ_ENVELOPE_SIZE] for a waveform. Only consulted
            when the figure is made here; one the caller brought keeps the size it has.
        linewidth (float): Stroke width of a [`Line`][qprogram.plotting.Line].
        markers (bool): Draw a marker at every sample of a line. Worth turning on for a coarse
            sweep, where the points are the measurement and the line between them is interpolation.
        markersize (float): Size of those markers.
        point_size (float): Size of a [`Points`][qprogram.plotting.Points] mark.
        point_alpha (float): Opacity of a [`Points`][qprogram.plotting.Points] mark, which is what keeps a few thousand
            single shots readable where they pile up.
        grid (bool): Draw grid lines behind the data.
        legend (bool): Draw a legend when the figure has more than one labelled mark.
        colorbar (bool): Draw a colour bar beside a [`Mesh`][qprogram.plotting.Mesh].
        twin_ticks (int): How many ticks a [`Twin`][qprogram.plotting.Twin] scale gets. They land on
            samples rather than on round numbers, so this is the count the renderer aims for and a
            sweep shorter than it gets one tick per sample; two is the floor.
    """

    theme: Theme = LIGHT
    size: tuple[float, float] | None = None
    linewidth: float = 1.8
    markers: bool = False
    markersize: float = 3.5
    point_size: float = 5.0
    point_alpha: float = 0.45
    grid: bool = True
    legend: bool = True
    colorbar: bool = True
    twin_ticks: int = 5

    def color(self, index: int) -> str:
        """Return the categorical colour for series ``index``, cycling when the theme runs out.

        Args:
            index (int): Zero-based position of the series in the figure.

        Returns:
            One of the theme's `series` colours.
        """
        return self.theme.series[index % len(self.theme.series)]

    def sized(self, default: tuple[float, float]) -> Style:
        """Return this style with a figure size on it, ``default`` when it names none of its own.

        What a figure of a pulse should measure is not something a renderer can know, so a caller
        that knows fills it in before handing the style over. A style that already names a size is
        returned unchanged, which is what makes ``size=`` on a call the last word.

        Args:
            default (tuple[float, float]): Size in inches to use when this style names none.

        Returns:
            This style, or a copy of it carrying ``default``.
        """
        return self if self.size is not None else replace(self, size=default)
