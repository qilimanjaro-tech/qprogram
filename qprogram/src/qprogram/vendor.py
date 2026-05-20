from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from qprogram.buses import BusRef
from qprogram.result import MeasurementHandle

if TYPE_CHECKING:
    from collections.abc import Callable

    from qprogram.operations.operation import MeasurementOperation, Operation
    from qprogram.qprogram import QProgram


class VendorNamespace:
    """Base class for vendor operation namespaces.

    Vendors subclass this and add typed methods that construct :class:`Operation` instances and append
    them to the program via :meth:`_append` (or :meth:`_append_measurement` for measurement ops).
    """

    def __init__(self, program: QProgram) -> None:
        self._program = program

    def _append(self, operation: Operation) -> None:
        """Append a vendor operation to the program's active block.

        :class:`~qprogram.BusRef` attributes (and lists thereof) are run through
        :meth:`QProgram._validate_bus` so vendor ops can't sneak in a bus from a different schema.
        Plain-string attributes are not validated.

        Args:
            operation: The vendor operation instance to append.
        """
        for value in vars(operation).values():
            if isinstance(value, BusRef):
                self._program._validate_bus(value)  # noqa: SLF001
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, BusRef):
                        self._program._validate_bus(item)  # noqa: SLF001
        self._program._append_to_active(operation)  # noqa: SLF001

    def _append_measurement(
        self,
        op_cls: type[MeasurementOperation],
        *,
        bus: str,
        name: str | None = None,
        **kwargs: Any,
    ) -> MeasurementHandle:
        """Allocate a handle, build a vendor measurement op, append it, and return the handle.

        Shares the per-qubit name counter with :meth:`QProgram.measure` so vendor and core measurements
        on the same bus never collide. Vendor measurement methods call this in place of
        :meth:`_append` so users receive a usable :class:`MeasurementHandle`.

        Example:
            ```python
            def acquire(self, bus, weights, *, name=None):
                return self._append_measurement(Acquire, bus=bus, weights=weights, name=name)
            ```

        Args:
            op_cls: The concrete :class:`MeasurementOperation` subclass to instantiate.
            bus: Bus the measurement runs on.
            name: Optional explicit handle name; auto-allocated when omitted.
            **kwargs: Remaining keyword arguments forwarded to ``op_cls(...)``.

        Returns:
            The freshly-allocated :class:`MeasurementHandle`.
        """
        allocated = self._program._allocate_measurement_name(bus, requested=name)  # noqa: SLF001
        handle = MeasurementHandle(allocated)
        # MeasurementOperation is a marker base — the cast lets the dynamic constructor go through
        # without ty falling back to the empty object.__init__ signature.
        factory = cast("Callable[..., MeasurementOperation]", op_cls)
        op = factory(bus=bus, handle=handle, **kwargs)
        self._append(op)
        return handle
