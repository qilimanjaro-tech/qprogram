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
    """Abstract base for single-channel (real-valued) waveforms."""

    @abstractmethod
    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the pulse envelope sampled at ``resolution``-ns steps.

        Args:
            resolution: Sample period in nanoseconds. ``1`` returns one sample per ns.

        Returns:
            A 1-D float array of length ``duration / resolution``.
        """
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds."""
        ...

    def __add__(self, other: Waveform) -> Waveform:
        """Concatenate two waveforms in time. Returns a :class:`Chained` containing both envelopes.

        Flattens when either operand is already a :class:`Chained`, so ``a + b + c`` produces a single
        three-element chain rather than a nested structure.

        Args:
            other: Waveform played immediately after ``self``.

        Returns:
            A :class:`Chained` waveform containing both envelopes.
        """
        from qprogram.waveforms.chained import Chained  # noqa: PLC0415

        if not isinstance(other, Waveform):
            return NotImplemented
        left = self.waveforms if isinstance(self, Chained) else [self]
        right = other.waveforms if isinstance(other, Chained) else [other]
        return Chained(left + right)

    # -- analysis ----------------------------------------------------------

    def peak_amplitude(self, resolution: int = 1) -> float:
        """Return ``max(|envelope|)`` at the given sample resolution."""
        return float(np.max(np.abs(self.envelope(resolution=resolution))))

    def rms_amplitude(self, resolution: int = 1) -> float:
        """Return the root-mean-square amplitude of the envelope at the given resolution."""
        env = self.envelope(resolution=resolution)
        return float(np.sqrt(np.mean(env**2)))

    def area(self, resolution: int = 1) -> float:
        """Return the integrated envelope ``∫ envelope(t) dt`` in nanosecond-amplitude units.

        Useful for pulse-area calibration: a π rotation on a transmon corresponds to a fixed area
        independent of pulse shape (modulo nonlinear corrections).

        Args:
            resolution: Sample period in nanoseconds.
        """
        env = self.envelope(resolution=resolution)
        return float(np.trapezoid(env, dx=resolution))

    def spectrum(self, resolution: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Return the one-sided frequency spectrum of the envelope.

        Args:
            resolution: Sample period in nanoseconds.

        Returns:
            ``(frequencies_hz, complex_spectrum)`` from :func:`numpy.fft.rfft`.
        """
        env = self.envelope(resolution=resolution)
        freqs = np.fft.rfftfreq(len(env), d=resolution * 1e-9)
        spectrum = np.fft.rfft(env)
        return freqs, spectrum

    # -- visualisation ----------------------------------------------------

    def plot(self, resolution: int = 1, ax: Axes | None = None) -> Axes:
        """Plot the envelope on a matplotlib :class:`~matplotlib.axes.Axes`.

        Args:
            resolution: Sample period in nanoseconds.
            ax: Axes to draw on. A fresh figure+axes is created when ``None``.

        Returns:
            The :class:`~matplotlib.axes.Axes` containing the plot.
        """
        import matplotlib.pyplot as plt  # noqa: PLC0415

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
        """Render an inline SVG of the envelope for Jupyter display."""
        return _waveform_svg(self.plot)


class IQWaveform(_StructuralEqMixin, ABC):
    """Abstract base for IQ (two-channel, complex-valued) waveforms."""

    @abstractmethod
    def get_I(self) -> Waveform:
        """Return the in-phase component as a single-channel :class:`Waveform`."""
        ...

    @abstractmethod
    def get_Q(self) -> Waveform:
        """Return the quadrature component as a single-channel :class:`Waveform`."""
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds."""
        ...

    def _complex_envelope(self, resolution: int = 1) -> np.ndarray:
        """Return ``I + jQ`` sampled at ``resolution``-ns steps."""
        return self.get_I().envelope(resolution=resolution) + 1j * self.get_Q().envelope(resolution=resolution)

    # -- analysis ----------------------------------------------------------

    def peak_amplitude(self, resolution: int = 1) -> float:
        """Return the peak magnitude ``max(|I + jQ|)`` at the given sample resolution."""
        return float(np.max(np.abs(self._complex_envelope(resolution=resolution))))

    def rms_amplitude(self, resolution: int = 1) -> float:
        """Return the RMS magnitude of the complex envelope at the given resolution."""
        env = self._complex_envelope(resolution=resolution)
        return float(np.sqrt(np.mean(np.abs(env) ** 2)))

    def area(self, resolution: int = 1) -> float:
        """Return the integrated magnitude ``∫ |I + jQ| dt`` in nanosecond-amplitude units."""
        env = self._complex_envelope(resolution=resolution)
        return float(np.trapezoid(np.abs(env), dx=resolution))

    def spectrum(self, resolution: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Return the two-sided frequency spectrum of the complex envelope.

        IQ waveforms are complex, so a two-sided :func:`numpy.fft.fft` is more informative than a
        real-only one-sided transform.

        Args:
            resolution: Sample period in nanoseconds.

        Returns:
            ``(frequencies_hz, complex_spectrum)``.
        """
        env = self._complex_envelope(resolution=resolution)
        freqs = np.fft.fftshift(np.fft.fftfreq(len(env), d=resolution * 1e-9))
        spectrum = np.fft.fftshift(np.fft.fft(env))
        return freqs, spectrum

    # -- visualisation ----------------------------------------------------

    def plot(
        self,
        resolution: int = 1,
        axes: tuple[Axes, Axes] | None = None,
    ) -> tuple[Axes, Axes]:
        """Plot the I and Q channels on two stacked matplotlib axes.

        Args:
            resolution: Sample period in nanoseconds.
            axes: Pair of axes to draw on. A fresh figure with two stacked axes is created when ``None``.

        Returns:
            The ``(I_axes, Q_axes)`` pair used for the plot.
        """
        import matplotlib.pyplot as plt  # noqa: PLC0415

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
        """Render an inline SVG of the I/Q envelopes for Jupyter display."""
        return _waveform_svg(self.plot)


def _waveform_svg(plot_fn: Callable[[], object]) -> str:
    """Capture ``plot_fn()``'s output as an inline SVG string for Jupyter ``_repr_html_``."""
    import io  # noqa: PLC0415

    import matplotlib.pyplot as plt  # noqa: PLC0415

    plot_fn()
    buf = io.StringIO()
    plt.gcf().savefig(buf, format="svg", bbox_inches="tight")
    plt.close(plt.gcf())
    return buf.getvalue()
