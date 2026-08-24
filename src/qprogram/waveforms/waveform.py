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
from typing import TYPE_CHECKING

import numpy as np

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.axes import Axes


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

    A concrete shape supplies :meth:`envelope` and :meth:`get_duration`; the analysis and plotting helpers
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

        Flattens when either operand is already a :class:`~qprogram.waveforms.Chained`, so ``a + b + c``
        produces a single three-element chain rather than a nested structure. An operand that is not a
        waveform yields ``NotImplemented``, leaving Python to fall back to the reflected operation.

        Args:
            other (Waveform): Waveform played immediately after ``self``.

        Returns:
            A :class:`~qprogram.waveforms.Chained` waveform containing both envelopes.
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
            ``(frequencies_hz, complex_spectrum)`` from :func:`numpy.fft.rfft`.

        Raises:
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        env = self.envelope(resolution=resolution)
        freqs = np.fft.rfftfreq(len(env), d=resolution * 1e-9)
        spectrum = np.fft.rfft(env)
        return freqs, spectrum

    # -- visualization -----------------------------------------------------

    def plot(self, resolution: int = 1, ax: Axes | None = None) -> Axes:
        """Plot the envelope on a matplotlib :class:`~matplotlib.axes.Axes`.

        Requires ``matplotlib``, which ships in the ``viz`` extra; it is imported inside the call so the
        rest of the package stays importable without it.

        Args:
            resolution (int, optional): Sample period in nanoseconds.
            ax (Axes | None): Axes to draw on. A fresh figure+axes is created when ``None``.

        Returns:
            The :class:`~matplotlib.axes.Axes` containing the plot.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        import matplotlib.pyplot as plt  # ruff: ignore[import-outside-top-level]

        if ax is None:
            _, ax = plt.subplots(figsize=(6, 2))
        env = self.envelope(resolution=resolution)
        t = np.arange(len(env)) * resolution
        ax.plot(t, env)
        ax.set_xlabel("Time (ns)")
        ax.set_ylabel("Amplitude")
        ax.set_title(type(self).__name__)
        return ax

    def _repr_html_(self) -> str:
        """Return an inline SVG of the envelope for Jupyter display.

        Returns:
            The SVG markup of the envelope plot.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If the envelope depends on a variable that has no value.
        """
        return _waveform_svg(self.plot)


class IQWaveform(_StructuralEqMixin, ABC):
    """Abstract base for IQ (two-channel, complex-valued) waveforms.

    A concrete shape supplies :meth:`get_I`, :meth:`get_Q` and :meth:`get_duration`; the analysis and
    plotting helpers here work on the complex envelope ``I + jQ``. Equality and hashing are structural over
    the constructor attributes, recursing into the component waveforms.
    """

    @abstractmethod
    def get_I(self) -> Waveform:
        """Return the in-phase component as a single-channel :class:`Waveform`.

        Returns:
            The waveform played on the I channel.
        """
        ...

    @abstractmethod
    def get_Q(self) -> Waveform:
        """Return the quadrature component as a single-channel :class:`Waveform`.

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

        IQ waveforms are complex, so a two-sided :func:`numpy.fft.fft` is more informative than a
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
        axes: tuple[Axes, Axes] | None = None,
    ) -> tuple[Axes, Axes]:
        """Plot the I and Q channels on two stacked matplotlib axes.

        Requires ``matplotlib``, which ships in the ``viz`` extra; it is imported inside the call so the
        rest of the package stays importable without it.

        Args:
            resolution (int, optional): Sample period in nanoseconds.
            axes (tuple[Axes, Axes] | None): Pair of axes to draw on. A fresh figure with two stacked axes
                is created when ``None``.

        Returns:
            The ``(I_axes, Q_axes)`` pair used for the plot.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        import matplotlib.pyplot as plt  # ruff: ignore[import-outside-top-level]

        if axes is None:
            _, ax_pair = plt.subplots(2, 1, sharex=True, figsize=(6, 3))
            axes = (ax_pair[0], ax_pair[1])
        i_env = self.get_I().envelope(resolution=resolution)
        q_env = self.get_Q().envelope(resolution=resolution)
        t = np.arange(len(i_env)) * resolution
        axes[0].plot(t, i_env)
        axes[0].set_ylabel("I")
        axes[0].set_title(type(self).__name__)
        axes[1].plot(t, q_env)
        axes[1].set_ylabel("Q")
        axes[1].set_xlabel("Time (ns)")
        return axes

    def _repr_html_(self) -> str:
        """Return an inline SVG of the I/Q envelopes for Jupyter display.

        Returns:
            The SVG markup of the stacked I and Q plots.

        Raises:
            ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
            UnassignedVariableError: If either channel's envelope depends on a variable that has no
                value.
        """
        return _waveform_svg(self.plot)


def _waveform_svg(plot_fn: Callable[[], object]) -> str:
    """Capture ``plot_fn()``'s output as an inline SVG string for Jupyter ``_repr_html_``.

    The figure is closed after serialization so a notebook cell does not also render it through the
    pyplot display hook.

    Args:
        plot_fn (Callable[[], object]): Plotting callable that leaves the figure it drew as pyplot's
            current figure — which is what creating one through :func:`matplotlib.pyplot.subplots`
            does. Its return value is discarded; the figure is picked up with
            :func:`matplotlib.pyplot.gcf`.

    Returns:
        The SVG markup of the figure ``plot_fn`` drew.

    Raises:
        ModuleNotFoundError: When ``matplotlib`` is not installed — install ``qprogram[viz]``.
    """
    import io  # ruff: ignore[import-outside-top-level]

    import matplotlib.pyplot as plt  # ruff: ignore[import-outside-top-level]

    plot_fn()
    buf = io.StringIO()
    plt.gcf().savefig(buf, format="svg", bbox_inches="tight")
    plt.close(plt.gcf())
    return buf.getvalue()
