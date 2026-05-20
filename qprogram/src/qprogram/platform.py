from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.buses import BusSchema
    from qprogram.protocol import CompilerCapabilities, Diagnostic
    from qprogram.qprogram import QProgram
    from qprogram.result import QProgramResult


class PlatformProtocol(ABC):
    """Abstract interface that execution platforms must implement.

    Splits into resource discovery (``get_*``) and capability + execution (:attr:`capabilities`,
    :meth:`validate`, :meth:`execute`). The convention is that :meth:`execute` calls :meth:`validate`
    first and raises :class:`~qprogram.UnsupportedOperationError` on any diagnostic — concrete platforms
    aren't forced to follow it, but skipping the check means cryptic compiler errors in place of
    structured diagnostics.
    """

    @abstractmethod
    def get_bus_schema(self) -> BusSchema:
        """Return the :class:`~qprogram.BusSchema` for this platform's chip."""
        ...

    @abstractmethod
    def get_buses(self) -> list[str]:
        """Return the names of every bus this platform exposes."""
        ...

    @abstractmethod
    def get_parameters(self, bus: str) -> list[str]:
        """Return the parameter names supported on ``bus``."""
        ...

    @abstractmethod
    def get_global_parameters(self) -> list[str]:
        """Return the parameter names that are not bound to any specific bus."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> CompilerCapabilities:
        """Return the capability descriptor for this platform.

        Built from a registered :class:`~qprogram.protocol.Profile` (see
        :meth:`CompilerCapabilities.from_profile`), optionally with device-specific ``limit_overrides``.
        Users introspect this to know what the platform supports; the validator consumes the same object.
        """
        ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        """Validate a program against this platform's capabilities.

        Default delegates to :func:`qprogram.validation.validate`. Platforms may override to prepend
        device-specific predicates or short-circuit on the first error.

        Args:
            qprogram: Program to validate.

        Returns:
            List of diagnostics; empty when the program is fully supported.
        """
        from qprogram.validation import validate as _validate  # noqa: PLC0415

        return _validate(qprogram, self.capabilities)

    @abstractmethod
    def execute(self, qprogram: QProgram, **kwargs) -> QProgramResult:
        """Execute a program and return its results."""
        ...

    def stream(self, qprogram: QProgram, **kwargs) -> Iterator[QProgramResult]:
        """Execute and yield partial results as they become available.

        Optional — the default raises :exc:`NotImplementedError`. Platforms that don't support
        streaming can leave this alone.

        Raises:
            NotImplementedError: Always, in the default implementation.
        """
        msg = "Streaming not supported by this platform"
        raise NotImplementedError(msg)
