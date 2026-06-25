"""Result types for executed QPrograms.

:class:`MeasurementHandle` is returned by :meth:`QProgram.measure` and identifies a measurement across
construction, ``.qp`` serialization, execution, and retrieval. :class:`MeasurementResult` is the runtime's
per-measurement record. :class:`QProgramResult` is the in-memory container of all measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qprogram.errors import ValidationError

if TYPE_CHECKING:
    import xarray as xr

    from qprogram.variable import _HandleFieldAccess, _UnassignedType


class MeasurementHandle:
    """A reference to a measurement performed by a :class:`QProgram`.

    Equality is **structural**: two handles with the same name refer to the same measurement. Why this
    matters: after a ``.qp`` round-trip the original Python objects are gone but names survive in the
    AST, so a freshly-constructed ``MeasurementHandle("q0_m0")`` compares equal to the original.

    Runtime-supplied values (e.g. the classified qubit state when ``returns`` includes ``"state"``) live
    in a private ``_values`` dict keyed by field name. They participate in :class:`MeasurementRef`
    evaluation but do not contribute to handle identity.

    Args:
        name: Stable identifier for the measurement. Auto-assigned by :meth:`QProgram.measure`
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
        """Return a proxy to reference this measurement's classified state in a conditional.

        The returned proxy is a throwaway whose ``==`` and ``!=`` operators build
        :class:`~qprogram.Comparison` AST nodes::

            with program.if_(handle.state == 0):
                ...

        The producing measurement op must request state classification (its ``returns`` must include
        ``"state"``); the validator emits ``missing-classification`` otherwise.
        """
        from qprogram.variable import _HandleFieldAccess  # noqa: PLC0415

        return _HandleFieldAccess(self, "state")  # pyright: ignore[reportArgumentType]

    def _value_for(self, field: str) -> int | float | _UnassignedType:
        from qprogram.variable import UNASSIGNED  # noqa: PLC0415

        return self._values.get(field, UNASSIGNED)

    def _set_value(self, field: str, value: float) -> None:
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
        bus: The bus the measurement was taken on.
        name: The measurement handle's name, as assigned at program construction.
        data: The **primary** result :class:`xarray.DataArray` — the ``"iq"`` field when the
            measurement requested it, else the first requested return token's array.
        fields: One :class:`xarray.DataArray` per requested return token. Shapes per spec §8:
            ``iq`` → ``(*sweeps, "IQ")``; ``state`` → ``(*sweeps)`` (excited-state population
            under averaging); ``raw`` → ``(*sweeps, "time", "IQ")``.
    """

    bus: str
    name: str
    data: xr.DataArray
    fields: dict[str, xr.DataArray] = field(default_factory=dict)


class QProgramResult:
    """In-memory result of executing a :class:`QProgram`.

    Each measurement produces an :class:`xarray.DataArray` whose dimensions are named after the enclosing
    loops (outermost first) followed by a trailing ``"IQ"`` dimension with coordinates ``["I", "Q"]``.

    Results are stored in construction order and addressable by handle, by name string, or by integer
    position via :meth:`get`.
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
            bus: The bus the measurement was taken on.
            name: The measurement handle name as it appears in the AST.
            data: The primary result data (the ``"iq"`` field when requested).
            fields: Per-return-token arrays. Defaults to ``{"iq": data}``-style single-field
                behaviour: when omitted, the record's :attr:`MeasurementResult.fields` is empty
                and only the primary array is addressable.
        """
        self._measurements.append(MeasurementResult(bus=bus, name=name, data=data, fields=dict(fields or {})))

    @property
    def measurements(self) -> list[MeasurementResult]:
        """All measurement records in construction order."""
        return self._measurements

    def get(
        self,
        measurement: MeasurementHandle | str | int = 0,
        bus: str | None = None,
        field: str | None = None,
    ) -> xr.DataArray:
        """Retrieve one measurement's data.

        Args:
            measurement: Which measurement to retrieve.

                - :class:`MeasurementHandle`: looked up by name.
                - ``str``: looked up by name. Useful after a ``loads()`` round-trip when the original
                  handle objects are gone but handles can be reconstructed via
                  :meth:`QProgram.measurement_handles`.
                - ``int``: positional sugar; returns the N-th measurement in declaration order, or N-th
                  on ``bus`` when the filter is given. Prefer handle or name in new code.

            bus: Optional bus name filter — narrows the search before the handle/name/position lookup.
            field: Optional return token (``"iq"``, ``"state"``, ``"raw"``, ...). ``None`` returns
                the primary array (the ``"iq"`` field when the measurement requested it).

        Returns:
            The measurement's :class:`xarray.DataArray`.

        Raises:
            KeyError: ``measurement`` was a handle or name and no match exists in scope, or
                ``field`` names a return token the measurement did not request.
            IndexError: ``measurement`` was an integer and is out of range.
        """
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
        if field is None:
            return record.data
        if field not in record.fields:
            available = ", ".join(sorted(record.fields)) or "none"
            msg = f"Measurement {record.name!r} has no field {field!r}; available: {available}"
            raise KeyError(msg)
        return record.fields[field]

    @staticmethod
    def _lookup_by_name(
        candidates: list[MeasurementResult],
        name: str,
        bus: str | None,
    ) -> MeasurementResult:
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
