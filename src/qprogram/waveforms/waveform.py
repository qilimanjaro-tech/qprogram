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
"""Abstract bases for waveform types.

Waveforms are conceptually immutable values; once a waveform has been used as a ``set`` / ``dict`` key or
otherwise hashed, do not mutate its attributes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from qprogram._structural import ast_eq, ast_hash
from qprogram.errors import ValidationError
from qprogram.plotting.model import Figure, Line
from qprogram.plotting.renderers import DEFAULT_RENDERER, resolve_renderer
from qprogram.plotting.theme import DARK, ENVELOPE_SIZE, IQ_ENVELOPE_SIZE, LIGHT, Style

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure as MatplotlibFigure


class _StructuralEqMixin:
    """Structural ``__eq__`` / ``__hash__`` over ``vars(self)``.

    Why this is a mixin: symbolic parameter references (``Variable``, ``Constant``) and ``numpy`` arrays
    do not compose under Python's default identity equality, so every waveform subclass would need
    bespoke ``__eq__`` otherwise. Using ``ast_eq`` makes nested waveforms (``IQPair``) recurse correctly.
    """

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))


class Waveform(_StructuralEqMixin, ABC):
    """Abstract base for single-channel (real-valued) waveforms.

    A concrete shape supplies `envelope` and `get_duration`; the analysis and plotting helpers
    here are derived from the sampled envelope, so they work for any subclass. Equality and hashing are
    structural over the constructor attributes, which lets a waveform be used as a dictionary key and lets
    two independently built shapes compare equal.
    """

    @abstractmethod
    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the pulse envelope sampled at ``resolution``-ns steps.

        Args:
            resolution (int, optional): Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D array of ``duration / resolution`` samples. A shape whose parameters are all
            floats yields a float array; one built from integers may yield an integer array.
        """
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration in nanoseconds.
        """
        ...

    def __add__(self, other: Waveform) -> Waveform:
        """Concatenate two waveforms in time.

        Flattens when either operand is already a [`Chained`][qprogram.waveforms.Chained], so ``a + b + c``
        produces a single three-element chain rather than a nested structure. An operand that is not a
        waveform yields ``NotImplemented``, leaving Python to fall back to the reflected operation.

        Args:
            other (Waveform): Waveform played immediately after ``self``.

        Returns:
            A [`Chained`][qprogram.waveforms.Chained] waveform containing both envelopes.
        """
        from qprogram.waveforms.chained import Chained  # ruff: ignore[import-outside-top-level]

        if not isinstance(other, Waveform):
            return NotImplemented
        left = self.waveforms if isinstance(self, Chained) else [self]
        right = other.waveforms if isinstance(other, Chained) else [other]
        return Chained(left + right)

    # -- analysis ----------------------------------------------------------

    def peak_amplitude(self, resolution: int = 1) -> float:
        """Return ``max(|envelope|)`` at the given sample resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The largest absolute sample value of the envelope.

        Raises:
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        return float(np.max(np.abs(self.envelope(resolution=resolution))))

    def rms_amplitude(self, resolution: int = 1) -> float:
        """Return the root-mean-square amplitude of the envelope at the given resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The root mean square of the envelope samples.

        Raises:
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        env = self.envelope(resolution=resolution)
        return float(np.sqrt(np.mean(env**2)))

    def area(self, resolution: int = 1) -> float:
        """Return the integrated envelope ``∫ envelope(t) dt`` in nanosecond-amplitude units.

        Useful for pulse-area calibration: a π rotation on a transmon corresponds to a fixed area
        independent of pulse shape (modulo nonlinear corrections).

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The trapezoidal integral of the envelope over the pulse.

        Raises:
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        env = self.envelope(resolution=resolution)
        return float(np.trapezoid(env, dx=resolution))

    def spectrum(self, resolution: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Return the one-sided frequency spectrum of the envelope.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            ``(frequencies_hz, complex_spectrum)`` from `numpy.fft.rfft`.

        Raises:
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        env = self.envelope(resolution=resolution)
        freqs = np.fft.rfftfreq(len(env), d=resolution * 1e-9)
        spectrum = np.fft.rfft(env)
        return freqs, spectrum

    # -- visualization -----------------------------------------------------

    def plot(
        self,
        resolution: int = 1,
        *,
        style: Style | None = None,
        renderer: str | None = None,
        target: object = None,
    ) -> Any:  # ruff: ignore[any-type]  # whatever handle the renderer gives back
        """Plot the envelope.

        The envelope is described as a [`Figure`][qprogram.plotting.Figure] and handed to a renderer,
        which is how [`QProgramResult.plot`][qprogram.QProgramResult.plot] draws too, so a pulse and
        the sweep it produced read as one experiment rather than as two libraries. The default
        renderer is matplotlib, from the ``viz`` extra, and it returns the `Axes` it drew on.

        Args:
            resolution (int, optional): Sample period in nanoseconds.
            style (Style | None): Palette and drawing weights. Defaults to
                [`Style`][qprogram.plotting.Style]``()``, which is the light theme; a style that
                names no size of its own is drawn at
                [`ENVELOPE_SIZE`][qprogram.plotting.ENVELOPE_SIZE].
            renderer (str | None): A name passed to
                [`resolve_renderer`][qprogram.plotting.resolve_renderer]. Defaults to ``"matplotlib"``.
            target (object): An existing surface for the renderer to draw on — a matplotlib
                `Axes` for the default one. A new figure is made when omitted.

        Returns:
            Whatever the renderer returns: the `Axes` for the matplotlib one.

        Raises:
            KeyError: When ``renderer`` names none that is registered.
            ModuleNotFoundError: When the matplotlib renderer is used without matplotlib
                installed — install ``qprogram[viz]``.
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        env = self.envelope(resolution=resolution)
        figure = Figure(
            marks=(Line(np.arange(len(env)) * resolution, env),),
            x_label="Time (ns)",
            y_label="Amplitude",
            title=type(self).__name__,
        )
        return resolve_renderer(renderer)(figure, (style or Style()).sized(ENVELOPE_SIZE), target)

    def _repr_html_(self) -> str:
        """Return the envelope as an image for Jupyter display.

        Returns:
            The markup for one cell, holding the figure drawn for a light surface and for a dark one.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        name = type(self).__name__
        return _envelope_html(lambda style: self.plot(style=style), f"{name} envelope")


class IQWaveform(_StructuralEqMixin, ABC):
    """Abstract base for IQ (two-channel, complex-valued) waveforms.

    A concrete shape supplies `get_I`, `get_Q` and `get_duration`; the analysis and
    plotting helpers here work on the complex envelope ``I + jQ``. Equality and hashing are structural over
    the constructor attributes, recursing into the component waveforms.
    """

    @abstractmethod
    def get_I(self) -> Waveform:
        """Return the in-phase component as a single-channel [`Waveform`][qprogram.waveforms.Waveform].

        Returns:
            The waveform played on the I channel.
        """
        ...

    @abstractmethod
    def get_Q(self) -> Waveform:
        """Return the quadrature component as a single-channel [`Waveform`][qprogram.waveforms.Waveform].

        Returns:
            The waveform played on the Q channel.
        """
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration in nanoseconds.
        """
        ...

    def _complex_envelope(self, resolution: int = 1) -> np.ndarray:
        """Return ``I + jQ`` sampled at ``resolution``-ns steps.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            A 1-D complex array combining the two channel envelopes.

        Raises:
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        return self.get_I().envelope(resolution=resolution) + 1j * self.get_Q().envelope(resolution=resolution)

    # -- analysis ----------------------------------------------------------

    def peak_amplitude(self, resolution: int = 1) -> float:
        """Return the peak magnitude ``max(|I + jQ|)`` at the given sample resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The largest magnitude of the complex envelope.

        Raises:
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        return float(np.max(np.abs(self._complex_envelope(resolution=resolution))))

    def rms_amplitude(self, resolution: int = 1) -> float:
        """Return the RMS magnitude of the complex envelope at the given resolution.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The root mean square of the complex envelope magnitudes.

        Raises:
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        env = self._complex_envelope(resolution=resolution)
        return float(np.sqrt(np.mean(np.abs(env) ** 2)))

    def area(self, resolution: int = 1) -> float:
        """Return the integrated magnitude ``∫ |I + jQ| dt`` in nanosecond-amplitude units.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            The trapezoidal integral of the complex envelope magnitude over the pulse.

        Raises:
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        env = self._complex_envelope(resolution=resolution)
        return float(np.trapezoid(np.abs(env), dx=resolution))

    def spectrum(self, resolution: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Return the two-sided frequency spectrum of the complex envelope.

        IQ waveforms are complex, so a two-sided `numpy.fft.fft` is more informative than a
        real-only one-sided transform.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            ``(frequencies_hz, complex_spectrum)``, both shifted so zero frequency sits in the middle.

        Raises:
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        env = self._complex_envelope(resolution=resolution)
        freqs = np.fft.fftshift(np.fft.fftfreq(len(env), d=resolution * 1e-9))
        spectrum = np.fft.fftshift(np.fft.fft(env))
        return freqs, spectrum

    # -- visualization -----------------------------------------------------

    def plot(
        self,
        resolution: int = 1,
        *,
        style: Style | None = None,
        renderer: str | None = None,
        target: tuple[object, object] | None = None,
    ) -> tuple[Any, Any]:
        """Plot the I and Q channels on two stacked panels.

        Each channel is described as a [`Figure`][qprogram.plotting.Figure] and handed to a renderer,
        the second asking for the theme's second colour so the pair does not read as one series. The
        panels share an x axis, so only the lower one is labelled.

        The panels themselves are the one thing here a renderer does not decide: two axes sharing a
        scale is a matplotlib layout, and it is the only one this knows how to build, so any other
        renderer has to be given the surfaces to draw on.

        Args:
            resolution (int, optional): Sample period in nanoseconds.
            style (Style | None): Palette and drawing weights. Defaults to
                [`Style`][qprogram.plotting.Style]``()``, which is the light theme; a style that
                names no size of its own is drawn at
                [`IQ_ENVELOPE_SIZE`][qprogram.plotting.IQ_ENVELOPE_SIZE].
            renderer (str | None): A name passed to
                [`resolve_renderer`][qprogram.plotting.resolve_renderer]. Defaults to ``"matplotlib"``.
            target (tuple[object, object] | None): The ``(I, Q)`` surfaces to draw the two panels
                on — a pair of matplotlib `Axes` for the default renderer. A new two-panel figure is
                made when omitted.

        Returns:
            What the renderer gave back for each panel, ``(I, Q)``: the two `Axes` for the matplotlib
            one.

        Raises:
            KeyError: When ``renderer`` names none that is registered.
            ValidationError: When a renderer other than the built-in one is asked for with no
                ``target``, since the panels it would be handed are matplotlib's.
            ModuleNotFoundError: When the matplotlib renderer is used without matplotlib
                installed — install ``qprogram[viz]``.
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        draw = resolve_renderer(renderer)
        style = (style or Style()).sized(IQ_ENVELOPE_SIZE)
        panels = target if target is not None else _stacked_panels(style, renderer)
        i_env = self.get_I().envelope(resolution=resolution)
        q_env = self.get_Q().envelope(resolution=resolution)
        t = np.arange(len(i_env)) * resolution
        i_figure = Figure(marks=(Line(t, i_env),), x_label="", y_label="I", title=type(self).__name__)
        q_figure = Figure(marks=(Line(t, q_env),), x_label="Time (ns)", y_label="Q", series=1)
        return draw(i_figure, style, panels[0]), draw(q_figure, style, panels[1])

    def _repr_html_(self) -> str:
        """Return the I and Q envelopes as an image for Jupyter display.

        Returns:
            The markup for one cell, holding the figure drawn for a light surface and for a dark one.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        name = type(self).__name__
        return _envelope_html(lambda style: self.plot(style=style)[0], f"{name} I and Q envelopes")


def _stacked_panels(style: Style, renderer: str | None) -> tuple[Axes, Axes]:
    """Make the two axes an IQ envelope is drawn on, stacked and sharing an x axis.

    Args:
        style (Style): Read for the figure size and the surface colour behind the panels.
        renderer (str | None): The renderer the caller asked for, read only to refuse the ones whose
            surfaces this cannot make.

    Returns:
        The ``(I, Q)`` axes, in that order.

    Raises:
        ValidationError: When ``renderer`` names anything but the built-in one.
        ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
    """
    if renderer is not None and renderer != DEFAULT_RENDERER:
        msg = (
            f"renderer {renderer!r} has to be given target=(I, Q) to draw on: two panels sharing an "
            f"x axis is a matplotlib layout, and it is the only pair this knows how to make"
        )
        raise ValidationError(msg)
    import matplotlib.pyplot as plt  # ruff: ignore[import-outside-top-level]

    figure, axes = plt.subplots(2, 1, sharex=True, figsize=style.size)
    figure.set_facecolor(style.theme.surface)
    return axes[0], axes[1]


def _envelope_html(draw: Callable[[Style], Axes], alt: str) -> str:
    """Draw an envelope for both surfaces and wrap the pair for a Jupyter cell.

    A figure drawn for a white surface is a white rectangle in a dark notebook, and the display
    protocol takes no argument to say which surface is being read on. So both are drawn and the
    browser picks: ``<picture>`` with a ``prefers-color-scheme`` source is the plain HTML way to ask,
    which is the notebook's own theme under VS Code and the operating system's under JupyterLab. A
    host that strips the ``<source>`` is left with the light figure, which is what a cell had before.

    Args:
        draw (Callable[[Style], Axes]): Draws the envelope with the style it is handed and returns an
            axes on the figure to serialize. Both styles name no size, so it draws at whichever of
            the two envelope sizes suits it.
        alt (str): Alt text naming what the figure shows.

    Returns:
        The markup for one cell.

    Raises:
        ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
    """
    dark = _envelope_data_uri(draw, Style(theme=DARK))
    light = _envelope_data_uri(draw, Style(theme=LIGHT))
    return (
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="{dark}">'
        f'<img src="{light}" alt="{alt}" style="max-width:100%"></picture>'
    )


def _envelope_data_uri(draw: Callable[[Style], Axes], style: Style) -> str:
    """Draw an envelope once and return the figure as an SVG ``data:`` URI.

    The figure is closed after serialization, so a notebook cell does not also render it through the
    pyplot display hook and a session that displays many waveforms does not trip matplotlib's
    open-figure warning.

    Args:
        draw (Callable[[Style], Axes]): Draws the envelope and returns an axes on the figure wanted.
        style (Style): The style to draw with.

    Returns:
        A ``data:image/svg+xml`` URI holding the figure.

    Raises:
        ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
    """
    import base64  # ruff: ignore[import-outside-top-level]
    import io  # ruff: ignore[import-outside-top-level]

    import matplotlib.pyplot as plt  # ruff: ignore[import-outside-top-level]

    # An Axes always belongs to a figure; the annotation admits None for an axes under teardown.
    figure = cast("MatplotlibFigure", draw(style).get_figure())
    buf = io.BytesIO()
    figure.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(figure)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
