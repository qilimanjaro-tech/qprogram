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
"""Plotting for results, in two halves that never meet.

[`build_figure`][qprogram.plotting.build_figure] reads a measurement array and returns a
[`Figure`][qprogram.plotting.Figure]: marks holding numpy arrays, two axis labels, nothing about colour or canvas.
A [`Renderer`][qprogram.plotting.Renderer] takes that figure and a [`Style`][qprogram.plotting.Style] and draws it.
The seam is what lets a second backend exist, and what lets a test check the shape of a figure with
no display attached.

[`QProgramResult.plot`][qprogram.QProgramResult.plot] is the front door and runs both halves::

    result = qp.simulate(program)
    result.plot(m0)  # a line per quadrature, kind inferred
    result.plot(m0, channels="magnitude")  # hypot(I, Q)
    result.plot(m0, x="freq", style=Style(theme=DARK))

Only the drawing half needs matplotlib, which ships in the ``viz`` extra and is imported the first
time a figure is rendered.
"""

from __future__ import annotations

from qprogram.plotting.build import CHANNELS, IQ_DIM, KINDS, build_figure
from qprogram.plotting.model import Figure, Line, Mark, Mesh, Points, Twin
from qprogram.plotting.quantity import Quantity
from qprogram.plotting.renderers import (
    DEFAULT_RENDERER,
    Renderer,
    available_renderers,
    register_renderer,
    resolve_renderer,
)
from qprogram.plotting.theme import DARK, LIGHT, Style, Theme

__all__ = [
    "CHANNELS",
    "DARK",
    "DEFAULT_RENDERER",
    "IQ_DIM",
    "KINDS",
    "LIGHT",
    "Figure",
    "Line",
    "Mark",
    "Mesh",
    "Points",
    "Quantity",
    "Renderer",
    "Style",
    "Theme",
    "Twin",
    "available_renderers",
    "build_figure",
    "register_renderer",
    "resolve_renderer",
]
