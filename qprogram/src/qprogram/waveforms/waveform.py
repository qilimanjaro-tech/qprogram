from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class Waveform(ABC):
    """Abstract base for single-channel waveforms."""

    @abstractmethod
    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Returns the pulse amplitude for each time step."""
        ...

    @abstractmethod
    def get_duration(self) -> int:
        """Duration in nanoseconds."""
        ...


class IQWaveform(ABC):
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
