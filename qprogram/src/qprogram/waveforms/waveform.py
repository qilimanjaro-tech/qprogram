from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    import numpy as np


class _StructuralEqMixin:
    """Provides structural ``__eq__`` / ``__hash__`` based on ``vars(self)``.

    Mixed into the :class:`Waveform` and :class:`IQWaveform` bases so every
    waveform subclass compares structurally without per-class boilerplate.
    Two waveform instances are equal iff they are of the same concrete
    class and their public+private attribute values compare equal under
    :func:`qprogram._structural.ast_eq` — which means symbolic parameter
    references (``Variable``, ``Constant``, …) flow through with their
    own equality semantics, ``numpy`` arrays compare elementwise, and
    nested waveforms (e.g. :class:`IQPair`) recurse.

    Mutation contract: once a waveform has been used as a ``set`` /
    ``dict`` key or otherwise hashed, do not mutate its attributes.
    Waveforms are conceptually values; the codebase never mutates them
    after construction.
    """

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))


class Waveform(_StructuralEqMixin, ABC):
    """Abstract base for single-channel waveforms."""

    @abstractmethod
    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Returns the pulse amplitude for each time step."""
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Duration in nanoseconds."""
        ...


class IQWaveform(_StructuralEqMixin, ABC):
    """Abstract base for IQ (two-channel) waveforms."""

    @abstractmethod
    def get_I(self) -> Waveform:
        """In-phase component."""
        ...

    @abstractmethod
    def get_Q(self) -> Waveform:
        """Quadrature component."""
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Duration in nanoseconds."""
        ...
