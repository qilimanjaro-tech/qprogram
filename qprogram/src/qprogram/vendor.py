from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qprogram.buses import BusRef
from qprogram.result import MeasurementHandle

if TYPE_CHECKING:
    from qprogram.operations.operation import MeasurementOperation, Operation
    from qprogram.qprogram import QProgram


class VendorNamespace:
    """Base class for vendor operation namespaces.

    Vendors subclass this and add typed methods that instantiate
    Operation subclasses and append them to the program.
    """

    def __init__(self, program: QProgram) -> None:
        self._program = program

    def _append(self, operation: Operation) -> None:
        """Append an operation to the program's active block.

        Before appending, any BusRef-typed attributes on the operation are
        run through ``QProgram._validate_bus`` so vendor ops can't sneak in a
        bus from a different schema. Plain-string attributes are ignored
        (they aren't buses) and BusRefs without metadata pass through.
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
        """Append a measurement-producing vendor operation and return its handle.

        Allocates a measurement name from the program (sharing the per-qubit
        counter with core ``QProgram.measure`` so handles never collide),
        constructs the operation with that name plus the supplied attribute
        kwargs, runs the same bus validation as :meth:`_append`, and
        returns a fresh :class:`MeasurementHandle`.

        Vendor authors implementing a measurement op call this instead of
        :meth:`_append` and return its result, e.g.::

            def acquire(self, bus, weights, save_adc=False, *, name=None):
                return self._append_measurement(
                    Acquire,
                    bus=bus,
                    weights=weights,
                    save_adc=save_adc,
                    name=name,
                )
        """
        allocated = self._program._allocate_measurement_name(bus, requested=name)  # noqa: SLF001
        handle = MeasurementHandle(allocated)
        # ``MeasurementOperation`` is a marker base; concrete subclasses
        # always accept ``bus`` and ``handle`` plus per-class kwargs.
        # Static analysers can't see that through the type variable — a
        # Protocol with a generic constructor would, but it's more
        # machinery than this single call site warrants.
        op = op_cls(bus=bus, handle=handle, **kwargs)  # type: ignore[call-arg]  # ty:ignore[unknown-argument]
        self._append(op)
        return handle
