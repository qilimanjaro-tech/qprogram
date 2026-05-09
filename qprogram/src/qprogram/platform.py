from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.buses import BusSchema
    from qprogram.qprogram import QProgram
    from qprogram.result import QProgramResult


class PlatformProtocol(ABC):
    """Abstract interface that execution platforms must implement."""

    @abstractmethod
    def get_bus_schema(self) -> BusSchema:
        """Return the bus schema for this platform/chip."""
        ...

    @abstractmethod
    def get_buses(self) -> list[str]:
        """Return available bus names."""
        ...

    @abstractmethod
    def get_parameters(self, bus: str) -> list[str]:
        """Return supported parameter names for a bus."""
        ...

    @abstractmethod
    def get_global_parameters(self) -> list[str]:
        """Return supported global parameter names."""
        ...

    @abstractmethod
    def execute(self, qprogram: QProgram, **kwargs) -> QProgramResult:
        """Execute a QProgram and return results."""
        ...

    def stream(self, qprogram: QProgram, **kwargs) -> Iterator[QProgramResult]:
        """Execute and yield partial results. Optional - raises NotImplementedError by default."""
        msg = "Streaming not supported by this platform"
        raise NotImplementedError(msg)
