"""Result types for executed QPrograms.

This module exposes three things:

- :class:`MeasurementHandle` — the value returned by ``QProgram.measure(...)``
  and by vendor measurement operations. A handle is just a stable name; it
  identifies a measurement across the lifetime of a program — from
  construction (where a Python local captures it), through ``.qp``
  serialization (the name is part of the line), through execution (the
  runtime tags each measurement in the result with the same name), and
  retrieval (``result.get(handle)``).

- :class:`MeasurementResult` — the per-measurement record produced by
  the runtime: the bus name, the result data, and the measurement's name.

- :class:`QProgramResult` — the in-memory container of measurements.
  Indexed by handle, by name string, or by integer position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from qprogram.errors import ValidationError

if TYPE_CHECKING:
    import xarray as xr

    from qprogram.variable import _HandleFieldAccess, _UnassignedType


class MeasurementHandle:
    """A reference to a measurement performed by a :class:`QProgram`.

    Carries a stable ``name`` that identifies the measurement across the
    program's full lifetime:

    - At construction time, the name is auto-assigned (``q0_m0``, ``c0_1_m0``,
      ``m0`` for raw-string buses, …) or user-supplied via ``name=``.
    - The name is emitted into the ``.qp`` file as part of the ``measure``
      / ``<vendor>.<op>`` / … line, so :func:`qprogram.loads` reconstructs
      the same name.
    - At result-retrieval time, ``QProgramResult.get(handle)`` looks up the
      measurement by name.

    Handles use **structural** equality: two handles with the same name
    refer to the same measurement. This matters for the post-load story —
    after ``qp.loads(...)`` the original handle Python objects are gone,
    but the names survive in the AST, and ``MeasurementHandle("q0_m0")``
    constructed anywhere compares equal to whatever the program produced.

    Per-measurement values written by the runtime (e.g. the classified
    qubit state when ``returns`` includes ``"state"``) live in a private
    ``_values`` dict, indexed by field name. They participate in
    Python-side :class:`MeasurementRef` evaluation but are *not* part of
    handle identity — two handles with the same name compare equal
    regardless of their value state.
    """

    __slots__ = ("_values", "name")

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            msg = f"MeasurementHandle name must be a non-empty string, got {name!r}"
            raise ValidationError(msg)
        self.name = name
        self._values: dict[str, int | float] = {}

    @property
    def state(self) -> _HandleFieldAccess:
        """Reference the classified state of this measurement.

        Returns a throwaway proxy whose ``==`` / ``!=`` operators build
        :class:`~qprogram.Comparison` nodes. Use in a condition::

            with program.if_(handle.state == 0):
                ...

        The measurement op must request state classification (its
        ``returns`` must include ``"state"``) or the validator will
        emit a ``missing-classification`` diagnostic. See the spec
        section on conditionals.
        """
        from qprogram.variable import _HandleFieldAccess  # noqa: PLC0415

        return _HandleFieldAccess(self, "state")  # pyright: ignore[reportArgumentType]

    def _value_for(self, field: str) -> int | float | _UnassignedType:
        """Read a per-measurement field value (used by :class:`MeasurementRef`).

        Returns :data:`~qprogram.UNASSIGNED` until the runtime sets the
        value via :meth:`_set_value`.
        """
        from qprogram.variable import UNASSIGNED  # noqa: PLC0415

        return self._values.get(field, UNASSIGNED)

    def _set_value(self, field: str, value: float) -> None:
        """Set a per-measurement field value. Called by the runtime."""
        self._values[field] = value

    def __repr__(self) -> str:
        return f"MeasurementHandle({self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MeasurementHandle) and self.name == other.name

    def __hash__(self) -> int:
        return hash(("MeasurementHandle", self.name))


@dataclass
class MeasurementResult:
    """Result of a single measurement operation.

    Carries the bus the measurement was taken on, the measurement's
    :class:`MeasurementHandle` name (assigned at program construction time),
    and the raw data.
    """

    bus: str
    name: str
    data: xr.DataArray


class QProgramResult:
    """In-memory result of executing a :class:`QProgram`.

    Each measurement operation produces an :class:`xarray.DataArray` with
    dimensions named after the enclosing loops (outermost first) and a
    trailing ``"IQ"`` dimension with coordinates ``["I", "Q"]``.

    Results are stored in construction order and addressable three ways:

    - **By handle** — the primary form. ``result.get(m0)``.
    - **By name string** — same lookup with a bare name.
      ``result.get("q0_m0")``. Useful after a ``loads()`` round-trip where
      handles are reconstructed via :meth:`QProgram.measurement_handles`.
    - **By integer position** — sugar over the underlying list.
      ``result.get(0)`` returns the first measurement in declaration order.

    All three lookup forms also accept an optional ``bus=`` filter, which
    narrows the search to measurements on that bus before applying the
    handle/name/position lookup.
    """

    def __init__(self) -> None:
        self._measurements: list[MeasurementResult] = []

    def append_measurement(self, bus: str, name: str, data: xr.DataArray) -> None:
        """Append a measurement result. ``name`` is the handle name from the AST."""
        self._measurements.append(MeasurementResult(bus=bus, name=name, data=data))

    @property
    def measurements(self) -> list[MeasurementResult]:
        """All measurement results in construction order."""
        return self._measurements

    def get(
        self,
        measurement: MeasurementHandle | str | int = 0,
        bus: str | None = None,
    ) -> xr.DataArray:
        """Get a measurement result.

        Args:
            measurement: The measurement to retrieve.

                - :class:`MeasurementHandle`: looked up by name.
                - ``str``: looked up by name (equivalent to passing a handle).
                - ``int``: positional sugar — returns the N-th measurement
                  in declaration order (or N-th measurement on ``bus`` if
                  the filter is given). Kept for migration convenience;
                  prefer handle or name in new code.

            bus: Optional bus name filter. When set, only measurements on
                that bus are considered.

        Returns:
            The measurement's :class:`xarray.DataArray`.

        Raises:
            KeyError: ``measurement`` was a handle/name and no matching
                measurement exists in scope.
            IndexError: ``measurement`` was an integer and is out of range.
        """
        candidates = self._measurements if bus is None else [m for m in self._measurements if m.bus == bus]

        if isinstance(measurement, MeasurementHandle):
            return self._lookup_by_name(candidates, measurement.name, bus)
        if isinstance(measurement, str):
            return self._lookup_by_name(candidates, measurement, bus)
        # int — positional sugar.
        if measurement >= len(candidates):
            scope = f" for bus '{bus}'" if bus is not None else ""
            msg = f"Measurement index {measurement} out of range{scope} ({len(candidates)} measurements)"
            raise IndexError(msg)
        return candidates[measurement].data

    @staticmethod
    def _lookup_by_name(
        candidates: list[MeasurementResult],
        name: str,
        bus: str | None,
    ) -> xr.DataArray:
        for m in candidates:
            if m.name == name:
                return m.data
        scope = f" on bus '{bus}'" if bus is not None else ""
        msg = f"No measurement named {name!r}{scope}"
        raise KeyError(msg)

    def __len__(self) -> int:
        return len(self._measurements)

    def __repr__(self) -> str:
        names = [m.name for m in self._measurements]
        return f"QProgramResult({len(self._measurements)} measurements, names={names})"
