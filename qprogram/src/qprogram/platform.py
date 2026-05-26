from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.buses import BusSchema
    from qprogram.protocol import Diagnostic, ExecutionPlan, PlatformCapabilities
    from qprogram.qprogram import QProgram
    from qprogram.result import QProgramResult


class PlatformProtocol(ABC):
    """Abstract interface that execution platforms must implement.

    Splits into resource discovery (``get_*``) and capability + execution (:attr:`capabilities`,
    :meth:`validate`, :meth:`plan`, :meth:`execute`). The convention is that :meth:`execute` calls
    :meth:`validate` first and raises :class:`~qprogram.UnsupportedOperationError` on any
    ``severity="error"`` diagnostic — concrete platforms aren't forced to follow it, but skipping
    the check means cryptic compiler errors in place of structured diagnostics.
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
    def capabilities(self) -> PlatformCapabilities:
        """Return the capability descriptor for this platform.

        A :class:`~qprogram.PlatformCapabilities` carries per-``(element, bus_kind)`` bus profiles, a
        platform-level profile (block / expression / bus-less ops), and a default-bus-profile fallback
        for raw-string buses. Each slot is a :class:`~qprogram.BusCapabilities` with hw / sw halves.
        Users introspect this to know what the platform supports; the validator consumes the same
        object.
        """
        ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        """Validate a program against this platform's capabilities.

        Default delegates to :func:`qprogram.validation.validate` and discards the execution-plan
        half of the return value. Platforms may override to prepend device-specific predicates or
        short-circuit on the first error.

        Args:
            qprogram: Program to validate.

        Returns:
            List of diagnostics; empty when the program is fully supported with no forced-software
            fallback events.
        """
        from qprogram.validation import validate as _validate  # noqa: PLC0415

        diagnostics, _ = _validate(qprogram, self.capabilities)
        return diagnostics

    def plan(self, qprogram: QProgram) -> ExecutionPlan:
        """Return the execution-domain plan for ``qprogram``.

        Default delegates to :func:`qprogram.validation.validate` and discards the diagnostic half.
        Callers who want both — e.g. ``execute()`` implementations that gate on diagnostics and then
        compile against the plan — should call :func:`qprogram.validation.validate` directly to
        avoid the duplicated walk.

        Args:
            qprogram: Program to classify.

        Returns:
            Mapping from each AST node to its final domain set.
        """
        from qprogram.validation import validate as _validate  # noqa: PLC0415

        _, plan = _validate(qprogram, self.capabilities)
        return plan

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
