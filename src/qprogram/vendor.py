# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The base class for a vendor extension's runtime namespace.

A vendor extension groups its operations as methods on a [`VendorNamespace`][qprogram.VendorNamespace] subclass and
registers that subclass under a namespace name via [`QProgram.register_vendor`][qprogram.QProgram.register_vendor]. The
program instantiates it lazily on first attribute access, so ``program.<vendor>.<operation>(...)`` reaches vendor code
without core qprogram knowing that the vendor exists.

The two helpers here are the whole contract a namespace method needs: `VendorNamespace._append`
for a plain operation, `VendorNamespace._append_measurement` for one that yields a
[`MeasurementHandle`][qprogram.MeasurementHandle].
"""

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

    Vendors subclass this and add typed methods that construct [`Operation`][qprogram.operations.Operation] instances
    and append them to the program via `_append` (or `_append_measurement` for measurement ops).

    Args:
        program (QProgram): The program whose currently active block receives the appended
            operations. Held for the namespace's lifetime, which the program ties to its own.
    """

    def __init__(self, program: QProgram) -> None:
        self._program = program

    def _append(self, operation: Operation) -> None:
        """Append a vendor operation to the program's active block.

        [`BusRef`][qprogram.BusRef] attributes (and lists thereof) are run through
        `QProgram._validate_bus` so vendor ops can't sneak in a bus from a different schema.
        Plain-string attributes are not validated.

        Args:
            operation (Operation): The vendor operation instance to append.

        Raises:
            ValidationError: When one of the operation's bus references belongs to a different
                [`BusSchema`][qprogram.BusSchema] than the one attached to the program.
        """
        for value in vars(operation).values():
            if isinstance(value, BusRef):
                self._program._validate_bus(value)  # ruff: ignore[private-member-access]
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, BusRef):
                        self._program._validate_bus(item)  # ruff: ignore[private-member-access]
        self._program._append_to_active(operation)  # ruff: ignore[private-member-access]

    def _append_measurement(
        self,
        op_cls: type[MeasurementOperation],
        *,
        bus: str,
        name: str | None = None,
        **kwargs: Any,
    ) -> MeasurementHandle:
        """Allocate a handle, build a vendor measurement op, append it, and return the handle.

        Shares the per-bus name counter with [`QProgram.measure`][qprogram.QProgram.measure] so vendor and core
        measurements on the same bus never collide. Vendor measurement methods call this in place of `_append` so users
        receive a usable [`MeasurementHandle`][qprogram.MeasurementHandle].

        Args:
            op_cls (type[MeasurementOperation]): The concrete `MeasurementOperation` subclass
                to instantiate.
            bus (str): Bus the measurement runs on.
            name (str | None): Explicit handle name; auto-allocated when omitted.
            **kwargs (Any): Remaining keyword arguments forwarded to ``op_cls(...)``.

        Returns:
            The freshly-allocated [`MeasurementHandle`][qprogram.MeasurementHandle].

        Raises:
            ValidationError: When ``name`` is empty, not a string, or already used by another
                measurement in the program, or when the operation carries a bus reference from a
                foreign [`BusSchema`][qprogram.BusSchema].

        Example::

            def acquire(self, bus, weights, *, name=None):
                return self._append_measurement(Acquire, bus=bus, weights=weights, name=name)
        """
        allocated = self._program._allocate_measurement_name(bus, requested=name)  # ruff: ignore[private-member-access]
        handle = MeasurementHandle(allocated)
        # MeasurementOperation is a marker base — the cast lets the dynamic constructor go through
        # without ty falling back to the empty object.__init__ signature.
        factory = cast("Callable[..., MeasurementOperation]", op_cls)
        op = factory(bus=bus, handle=handle, **kwargs)
        self._append(op)
        return handle
