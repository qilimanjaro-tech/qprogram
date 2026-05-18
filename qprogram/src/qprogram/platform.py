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

    The protocol covers two orthogonal duties:

    1. **Resource discovery** — :meth:`get_bus_schema`, :meth:`get_buses`,
       :meth:`get_parameters`, :meth:`get_global_parameters`. These answer
       "what knobs / wires does this hardware expose right now?"
    2. **Capability + execution** — :attr:`capabilities` and :meth:`validate`
       answer "which features of the QProgram DSL does this backend
       support?"; :meth:`execute` runs the program.

    A typical :meth:`execute` implementation calls :meth:`validate` first
    and raises :class:`~qprogram.UnsupportedOperationError` on any
    diagnostic, then lowers the program. Concrete platforms are not
    required to do so — the contract is documented, not enforced in this
    base — but skipping it means users hit cryptic compiler errors instead
    of structured diagnostics.
    """

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

    @property
    @abstractmethod
    def capabilities(self) -> CompilerCapabilities:
        """Return the capability descriptor for this platform.

        Built from a registered :class:`~qprogram.protocol.Profile` (see
        :meth:`CompilerCapabilities.from_profile`), optionally with a
        concrete device's tighter ``limit_overrides``. Users introspect
        this to know what the platform supports; the validator consumes
        the same object.
        """
        ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        """Run capability validation against this platform's capabilities.

        Default implementation delegates to
        :func:`qprogram.validation.validate`. Platforms may override to
        prepend device-specific predicates or to short-circuit on the
        first error — most won't need to.
        """
        from qprogram.validation import validate as _validate  # noqa: PLC0415

        return _validate(qprogram, self.capabilities)

    @abstractmethod
    def execute(self, qprogram: QProgram, **kwargs) -> QProgramResult:
        """Execute a QProgram and return results."""
        ...

    def stream(self, qprogram: QProgram, **kwargs) -> Iterator[QProgramResult]:
        """Execute and yield partial results. Optional - raises NotImplementedError by default."""
        msg = "Streaming not supported by this platform"
        raise NotImplementedError(msg)
