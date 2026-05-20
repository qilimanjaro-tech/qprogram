"""Abstract bases for waveform types.

Waveforms are conceptually immutable values; once a waveform has been used as a ``set`` / ``dict`` key or
otherwise hashed, do not mutate its attributes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    import numpy as np


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
