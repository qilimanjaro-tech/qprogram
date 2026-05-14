from __future__ import annotations

import numpy as np

from qprogram._structural import ast_eq, ast_hash


class CrosstalkMatrix:
    """Models flux crosstalk between buses.

    Structural equality and hashing are inherited from the shared
    :mod:`qprogram._structural` helpers, so two matrices with identical
    bus mappings / offsets / resistances compare equal and hash equal —
    the contract :class:`~qprogram.operations.SetCrosstalk` relies on for
    its own structural equality.
    """

    def __init__(self) -> None:
        self.matrix: dict[str, dict[str, float]] = {}
        self.flux_offsets: dict[str, float] = {}
        self.resistances: dict[str, float | None] = {}

    def __getitem__(self, bus: str) -> dict[str, float]:
        return self.matrix[bus]

    def __setitem__(self, key: str, value: dict[str, float]) -> None:
        self.matrix[key] = value

    def __repr__(self) -> str:
        return f"CrosstalkMatrix(buses={list(self.matrix.keys())})"

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))

    def to_array(self) -> np.ndarray:
        buses = sorted(self.matrix.keys())
        n = len(buses)
        arr = np.zeros((n, n))
        for i, bus_i in enumerate(buses):
            for j, bus_j in enumerate(buses):
                arr[i, j] = self.matrix.get(bus_i, {}).get(bus_j, 0.0)
        return arr

    def inverse(self) -> CrosstalkMatrix:
        buses = sorted(self.matrix.keys())
        inv_arr = np.linalg.inv(self.to_array())
        return CrosstalkMatrix.from_array(buses, inv_arr)

    def set_offset(self, offset: dict[str, float]) -> None:
        self.flux_offsets.update(offset)

    def set_resistances(self, resistances: dict[str, float]) -> None:
        self.resistances.update(resistances)

    @classmethod
    def from_array(cls, buses: list[str], matrix_array: np.ndarray) -> CrosstalkMatrix:
        xtalk = cls()
        for i, bus_i in enumerate(buses):
            xtalk.matrix[bus_i] = {}
            for j, bus_j in enumerate(buses):
                xtalk.matrix[bus_i][bus_j] = float(matrix_array[i, j])
        return xtalk

    @classmethod
    def from_buses(cls, buses: dict[str, dict[str, float]]) -> CrosstalkMatrix:
        xtalk = cls()
        xtalk.matrix = dict(buses)
        return xtalk
