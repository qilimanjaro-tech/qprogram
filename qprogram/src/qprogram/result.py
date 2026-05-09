from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import xarray as xr


@dataclass
class MeasurementResult:
    """Result of a single measure() operation."""

    bus: str
    data: xr.DataArray


class QProgramResult:
    """In-memory result of executing a QProgram.

    Each measure() operation produces an xarray.DataArray with:
    - Dimensions named after the loop variables (outermost first)
    - Last dimension "IQ" with coordinates ["I", "Q"]
    - Coordinates carrying the swept values
    """

    def __init__(self) -> None:
        self._measurements: list[MeasurementResult] = []

    def append_measurement(self, bus: str, data: xr.DataArray) -> None:
        """Append a measurement result."""
        self._measurements.append(MeasurementResult(bus=bus, data=data))

    @property
    def measurements(self) -> list[MeasurementResult]:
        """All measurement results in order."""
        return self._measurements

    def get(self, measurement: int = 0, bus: str | None = None) -> xr.DataArray:
        """Get a measurement result as xarray.DataArray.

        Args:
            measurement: Index of the measurement (0-based). If bus is provided,
                this is the index among measurements on that bus.
            bus: Optional bus name filter.

        Returns:
            xarray.DataArray with loop dimensions + "IQ" dimension.
        """
        if bus is not None:
            bus_measurements = [m for m in self._measurements if m.bus == bus]
            if measurement >= len(bus_measurements):
                msg = f"Measurement index {measurement} out of range for bus '{bus}' ({len(bus_measurements)} measurements)"
                raise IndexError(
                    msg,
                )
            return bus_measurements[measurement].data

        if measurement >= len(self._measurements):
            msg = f"Measurement index {measurement} out of range ({len(self._measurements)} measurements)"
            raise IndexError(msg)
        return self._measurements[measurement].data

    def __len__(self) -> int:
        return len(self._measurements)

    def __repr__(self) -> str:
        buses = {m.bus for m in self._measurements}
        return f"QProgramResult({len(self._measurements)} measurements, buses={buses})"
