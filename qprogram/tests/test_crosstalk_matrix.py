"""Tests for CrosstalkMatrix."""

from __future__ import annotations

import numpy as np

from qprogram import CrosstalkMatrix


def test_construction_starts_empty():
    m = CrosstalkMatrix()
    assert m.matrix == {}
    assert m.flux_offsets == {}
    assert m.resistances == {}


def test_setitem_getitem():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0, "b": 0.1}
    assert m["a"] == {"a": 1.0, "b": 0.1}


def test_repr_includes_bus_names():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0}
    r = repr(m)
    assert "a" in r
    assert "CrosstalkMatrix" in r


def test_to_array_zeros_when_empty():
    m = CrosstalkMatrix()
    arr = m.to_array()
    assert arr.shape == (0, 0)


def test_to_array_diagonal():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0, "b": 0.0}
    m["b"] = {"a": 0.0, "b": 1.0}
    arr = m.to_array()
    assert arr.shape == (2, 2)
    assert np.allclose(arr, np.eye(2))


def test_to_array_off_diagonal():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0, "b": 0.1}
    m["b"] = {"a": 0.2, "b": 1.0}
    arr = m.to_array()
    # Buses are sorted: a (0), b (1).
    assert arr[0, 1] == 0.1
    assert arr[1, 0] == 0.2


def test_to_array_missing_entries_zero():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0}      # no "b" entry
    m["b"] = {"b": 1.0}
    arr = m.to_array()
    assert arr[0, 1] == 0.0
    assert arr[1, 0] == 0.0


def test_inverse_round_trip():
    m = CrosstalkMatrix()
    m["a"] = {"a": 1.0, "b": 0.1}
    m["b"] = {"a": 0.1, "b": 1.0}
    inv = m.inverse()
    arr = m.to_array() @ inv.to_array()
    assert np.allclose(arr, np.eye(2), atol=1e-10)


def test_set_offset():
    m = CrosstalkMatrix()
    m.set_offset({"a": 0.5, "b": -0.1})
    assert m.flux_offsets == {"a": 0.5, "b": -0.1}


def test_set_offset_updates_existing():
    m = CrosstalkMatrix()
    m.set_offset({"a": 0.5})
    m.set_offset({"a": 0.7, "b": 0.2})
    assert m.flux_offsets == {"a": 0.7, "b": 0.2}


def test_set_resistances():
    m = CrosstalkMatrix()
    m.set_resistances({"a": 100.0})
    assert m.resistances == {"a": 100.0}


def test_from_array():
    arr = np.array([[1.0, 0.1], [0.1, 1.0]])
    m = CrosstalkMatrix.from_array(["a", "b"], arr)
    assert m["a"] == {"a": 1.0, "b": 0.1}
    assert m["b"] == {"a": 0.1, "b": 1.0}


def test_from_buses():
    data = {"a": {"a": 1.0, "b": 0.1}, "b": {"a": 0.1, "b": 1.0}}
    m = CrosstalkMatrix.from_buses(data)
    assert m["a"] == data["a"]


# ---------------------------------------------------------------------------
# Structural equality (from §11)
# ---------------------------------------------------------------------------


def test_structural_equality_empty():
    assert CrosstalkMatrix() == CrosstalkMatrix()


def test_structural_equality_populated():
    a = CrosstalkMatrix()
    a["x"] = {"x": 1.0, "y": 0.1}
    b = CrosstalkMatrix()
    b["x"] = {"x": 1.0, "y": 0.1}
    assert a == b
    assert hash(a) == hash(b)


def test_inequality_different_matrix():
    a = CrosstalkMatrix()
    a["x"] = {"x": 1.0}
    b = CrosstalkMatrix()
    b["x"] = {"x": 0.5}
    assert a != b


def test_inequality_different_offsets():
    a = CrosstalkMatrix()
    a.set_offset({"x": 0.1})
    b = CrosstalkMatrix()
    b.set_offset({"x": 0.2})
    assert a != b


def test_unequal_to_other_type():
    assert CrosstalkMatrix() != "anything"
