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
"""Result types for executed QPrograms.

:class:`MeasurementHandle` is returned by :meth:`QProgram.measure` and identifies a measurement across
construction, ``.qp`` serialization, execution, and retrieval. :class:`MeasurementResult` is the runtime's
per-measurement record. :class:`QProgramResult` is the in-memory container of all measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qprogram.errors import ValidationError
from qprogram.operations.operation import MeasurementField

if TYPE_CHECKING:
    import xarray as xr

    from qprogram.variable import _HandleFieldAccess, _UnassignedType


class MeasurementHandle:
    """A reference to a measurement performed by a :class:`QProgram`.

    Equality is **structural**: two handles with the same name refer to the same measurement. Why this
    matters: after a ``.qp`` round-trip the original Python objects are gone but names survive in the
    AST, so a freshly-constructed ``MeasurementHandle("q0_m0")`` compares equal to the original.

    Runtime-supplied values (e.g. the classified qubit state when the measurement's ``fields``
    includes :attr:`~qprogram.MeasurementField.STATE`) live in a private ``_values`` dict keyed by
    field name. They participate in :class:`MeasurementRef`
    evaluation but do not contribute to handle identity.

    Args:
        name (str): Stable identifier for the measurement. Auto-assigned by :meth:`QProgram.measure`
            (``q0/readout/m0``, ``m0``, ...) or user-supplied. Emitted verbatim into ``.qp``.

    Raises:
        ValidationError: If ``name`` is not a non-empty string.

    Note:
        ``_auto_named`` records whether :meth:`QProgram.measure` allocated the name (``True``) or the
        user supplied it (``False``). It distinguishes a bus-derived auto-name (``q0/readout/m0``) that
        :meth:`QProgram.rebind` must re-derive when the bus changes from a deliberate user label that
        must be preserved — the two are byte-identical once serialized, so the flag is the only sound
        signal. It is in-memory state (like the platform parameter store): it is not serialized, so it
        defaults to ``False`` on a handle reconstructed from ``.qp`` (rebind before dumping).
    """

    __slots__ = ("_auto_named", "_values", "name")

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            msg = f"MeasurementHandle name must be a non-empty string, got {name!r}"
            raise ValidationError(msg)
        self.name = name
        self._values: dict[str, int | float] = {}
        self._auto_named: bool = False

    @property
    def state(self) -> _HandleFieldAccess:
        """A proxy for referencing this measurement's classified state in a conditional.

        The returned proxy is a throwaway whose ``==`` and ``!=`` operators build
        :class:`~qprogram.Comparison` AST nodes::

            with program.if_(handle.state == 0):
                ...

        The producing measurement op must request state classification (its ``fields`` must include
        :attr:`~qprogram.MeasurementField.STATE`); the validator emits ``missing-classification``
        otherwise.
        """
        from qprogram.variable import _HandleFieldAccess  # ruff: ignore[import-outside-top-level]

        return _HandleFieldAccess(self, "state")  # pyright: ignore[reportArgumentType]

    def _value_for(self, field: str) -> int | float | _UnassignedType:
        """Return the runtime value recorded for ``field``, or ``UNASSIGNED``.

        Read by :class:`~qprogram.MeasurementRef` evaluation, so an expression over a measurement
        that has not produced this field yet propagates ``UNASSIGNED`` rather than guessing.

        Args:
            field (str): Measurement field name, e.g. ``"state"``.

        Returns:
            The recorded value, or ``UNASSIGNED`` when the runtime has not written this field.
        """
        from qprogram.variable import UNASSIGNED  # ruff: ignore[import-outside-top-level]

        return self._values.get(field, UNASSIGNED)

    def _set_value(self, field: str, value: float) -> None:
        """Record ``value`` as this measurement's runtime outcome for ``field``.

        Called by the executor once per measurement event. Every reference to the measurement shares
        this handle instance, which is what makes conditional feedback work without any wiring.

        Args:
            field (str): Measurement field name, e.g. ``"state"``.
            value (float): The outcome to record. Overwrites any earlier shot's value.
        """
        self._values[field] = value

    def __repr__(self) -> str:
        return f"MeasurementHandle({self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MeasurementHandle) and self.name == other.name

    def __hash__(self) -> int:
        return hash(("MeasurementHandle", self.name))


@dataclass
class MeasurementResult:
    """One measurement record produced by the runtime.

    Attributes:
        bus (str): The bus the measurement was taken on.
        name (str): The measurement handle's name, as assigned at program construction.
        data (xarray.DataArray): The **primary** result array — the ``"iq"`` field when the
            measurement requested it, else the first requested field in canonical order (see
            :class:`~qprogram.MeasurementField`). Use it when you want *whatever* the measurement
            produced; :meth:`QProgramResult.get` names a field explicitly instead.
        fields (dict[str, xarray.DataArray]): One array per requested measurement field, keyed by
            field name. Shapes per spec §8: ``iq`` → ``(*sweeps, "IQ")``; ``state`` → ``(*sweeps)``
            (excited-state population under averaging); ``raw`` → ``(*sweeps, "time", "IQ")``.
            This is what :meth:`QProgramResult.get` reads.
    """

    bus: str
    name: str
    data: xr.DataArray
    fields: dict[str, xr.DataArray] = field(default_factory=dict)


class QProgramResult:
    """In-memory result of executing a :class:`QProgram`.

    Each measurement contributes one :class:`MeasurementResult` holding an :class:`xarray.DataArray`
    per requested field. Dimensions are named after the enclosing loops, outermost first; the ``iq``
    field carries a trailing ``"IQ"`` dimension with coordinates ``["I", "Q"]``.

    Results are stored in construction order and addressable by handle, by name string, or by integer
    position via :meth:`get`, which returns the :attr:`~qprogram.MeasurementField.IQ` field unless a
    different one is named.
    """

    def __init__(self) -> None:
        self._measurements: list[MeasurementResult] = []

    def append_measurement(
        self,
        bus: str,
        name: str,
        data: xr.DataArray,
        fields: dict[str, xr.DataArray] | None = None,
    ) -> None:
        """Append a measurement record.

        Args:
            bus (str): The bus the measurement was taken on.
            name (str): The measurement handle name as it appears in the AST.
            data (xarray.DataArray): The primary result data (the ``"iq"`` field when requested).
            fields (dict[str, xarray.DataArray] | None): Per-measurement-field arrays, keyed by field
                name. Omitting it records ``data`` as the :attr:`~qprogram.MeasurementField.IQ`
                field — the field a measurement requests when ``fields=`` is omitted, and the one
                :meth:`get` returns by default. A record whose primary array is *not* ``iq`` must
                pass the mapping explicitly, so that :meth:`get` never hands back an array under the
                wrong field name.
        """
        if not fields:
            fields = {MeasurementField.IQ.value: data}
        self._measurements.append(MeasurementResult(bus=bus, name=name, data=data, fields=dict(fields)))

    @property
    def measurements(self) -> list[MeasurementResult]:
        """All measurement records in construction order."""
        return self._measurements

    def get(
        self,
        measurement: MeasurementHandle | str | int = 0,
        bus: str | None = None,
        field: MeasurementField | str = MeasurementField.IQ,
    ) -> xr.DataArray:
        """Retrieve one measurement field's data.

        Args:
            measurement (MeasurementHandle | str | int): Which measurement to retrieve.

                - :class:`MeasurementHandle`: looked up by name.
                - ``str``: looked up by name. Useful after a ``loads()`` round-trip when the original
                  handle objects are gone but handles can be reconstructed via
                  :meth:`QProgram.measurement_handles`.
                - ``int``: positional sugar; returns the N-th measurement in declaration order, or N-th
                  on ``bus`` when the filter is given. A handle or a name says what it means and
                  survives reordering, so prefer either to a position.

            bus (str | None): Bus name filter — narrows the search before the handle / name /
                position lookup.
            field (MeasurementField | str): Which measurement field to return — a
                :class:`~qprogram.MeasurementField` member or a registered vendor field name (the
                members *are* strings, so both spell the same thing). Defaults to
                :attr:`~qprogram.MeasurementField.IQ`, matching the default of
                ``measure(..., fields=)``; a measurement that did not request the field raises
                ``KeyError`` rather than silently substituting another one. Reach for
                :attr:`MeasurementResult.data` when you want the record's primary array whatever the
                requested fields were.

        Returns:
            The field's :class:`xarray.DataArray`.

        Raises:
            KeyError: When ``measurement`` is a handle or name with no match in scope, or ``field``
                names a measurement field the measurement did not request.
            IndexError: When ``measurement`` is an integer position outside the range in scope.
            ValidationError: When ``field`` is ``None``. There is no spelling of "give me the primary
                array" here — read :attr:`MeasurementResult.data` for that.
        """
        if field is None:
            msg = (
                "field=None is not a valid field: get() returns the "
                f"{MeasurementField.IQ.value!r} field by default. Name the field explicitly "
                "(field=MeasurementField.STATE), or read MeasurementResult.data for the record's "
                "primary array."
            )
            raise ValidationError(msg)

        candidates = self._measurements if bus is None else [m for m in self._measurements if m.bus == bus]

        if isinstance(measurement, MeasurementHandle):
            record = self._lookup_by_name(candidates, measurement.name, bus)
        elif isinstance(measurement, str):
            record = self._lookup_by_name(candidates, measurement, bus)
        else:  # int — positional sugar.
            if measurement >= len(candidates):
                scope = f" for bus '{bus}'" if bus is not None else ""
                msg = f"Measurement index {measurement} out of range{scope} ({len(candidates)} measurements)"
                raise IndexError(msg)
            record = candidates[measurement]
        # ``str(...)`` normalizes a MeasurementField member to its wire name, so the lookup and the
        # error message read the same whichever spelling the caller used.
        name = str(field)
        if name not in record.fields:
            available = ", ".join(sorted(record.fields)) or "none"
            msg = f"Measurement {record.name!r} has no field {name!r}; available: {available}"
            raise KeyError(msg)
        return record.fields[name]

    @staticmethod
    def _lookup_by_name(
        candidates: list[MeasurementResult],
        name: str,
        bus: str | None,
    ) -> MeasurementResult:
        """Return the first record in ``candidates`` named ``name``.

        Args:
            candidates (list[MeasurementResult]): Records already narrowed by the ``bus`` filter.
            name (str): Measurement name to match exactly.
            bus (str | None): Bus the candidates were filtered to, used only to phrase the error.

        Returns:
            The matching record.

        Raises:
            KeyError: When no candidate carries that name.
        """
        for m in candidates:
            if m.name == name:
                return m
        scope = f" on bus '{bus}'" if bus is not None else ""
        msg = f"No measurement named {name!r}{scope}"
        raise KeyError(msg)

    def __len__(self) -> int:
        return len(self._measurements)

    def __repr__(self) -> str:
        names = [m.name for m in self._measurements]
        return f"QProgramResult({len(self._measurements)} measurements, names={names})"
