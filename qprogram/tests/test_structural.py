"""Tests for the shared structural equality / hash helpers."""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import Variable
from qprogram._structural import ast_eq, ast_hash


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (1, 1, True),
        (1, 2, False),
        (1.0, 1.0, True),
        ("a", "a", True),
        ("a", "b", False),
        (True, True, True),
        (None, None, True),
        ((1, 2), (1, 2), True),
        ((1, 2), (1, 3), False),
        ([], [], True),
        ([1, 2, 3], [1, 2, 3], True),
        ([1, 2, 3], [1, 2, 4], False),
        ([1, 2], [1, 2, 3], False),
        ({}, {}, True),
        ({"a": 1}, {"a": 1}, True),
        ({"a": 1}, {"a": 2}, False),
        ({"a": 1}, {"b": 1}, False),
    ],
)
def test_ast_eq_basics(a, b, equal):
    assert ast_eq(a, b) is equal


def test_ast_eq_nested_lists():
    assert ast_eq([[1, 2], [3, 4]], [[1, 2], [3, 4]]) is True
    assert ast_eq([[1, 2], [3, 4]], [[1, 2], [3, 5]]) is False


def test_ast_eq_nested_dicts():
    assert ast_eq({"a": [1, 2]}, {"a": [1, 2]}) is True
    assert ast_eq({"a": [1, 2]}, {"a": [1, 3]}) is False


def test_ast_eq_ndarray_equal():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0, 3.0])
    assert ast_eq(a, b) is True


def test_ast_eq_ndarray_unequal():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0, 4.0])
    assert ast_eq(a, b) is False


def test_ast_eq_ndarray_vs_list():
    # Mixed types fall through to the non-ndarray branch only if both aren't arrays.
    # If only one is, the helper requires both to be ndarrays for a True.
    assert ast_eq(np.array([1, 2]), [1, 2]) is False
    assert ast_eq([1, 2], np.array([1, 2])) is False


def test_ast_eq_list_with_ndarray_elements():
    a = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    b = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    assert ast_eq(a, b) is True

    c = [np.array([1.0, 2.0]), np.array([3.0, 5.0])]
    assert ast_eq(a, c) is False


def test_ast_eq_dict_with_ndarray_values():
    a = {"k": np.array([1, 2, 3])}
    b = {"k": np.array([1, 2, 3])}
    assert ast_eq(a, b) is True


def test_ast_eq_handles_variable():
    # Variables compare by id (structural).
    v1 = Variable("x")
    v2 = Variable("x")
    v3 = Variable("y")
    assert ast_eq(v1, v2) is True
    assert ast_eq(v1, v3) is False


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        -1,
        3.14,
        "x",
        "",
        True,
        False,
        None,
        (),
        (1, 2, 3),
        frozenset({1, 2}),
    ],
)
def test_ast_hash_returns_int(value):
    assert isinstance(ast_hash(value), int)


def test_ast_hash_ndarray():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0, 3.0])
    # Same content -> same hash.
    assert ast_hash(a) == ast_hash(b)


def test_ast_hash_ndarray_different_content():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0, 4.0])
    assert ast_hash(a) != ast_hash(b)


def test_ast_hash_ndarray_different_shape():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([[1.0, 2.0, 3.0]])
    # Shape participates in the hash.
    assert ast_hash(a) != ast_hash(b)


def test_ast_hash_list_consistent_with_eq():
    a = [1, 2, 3]
    b = [1, 2, 3]
    assert ast_eq(a, b) is True
    assert ast_hash(a) == ast_hash(b)


def test_ast_hash_dict_order_independent():
    # Sorted-items implementation should yield the same hash regardless of insertion order.
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert ast_hash(a) == ast_hash(b)


def test_ast_hash_nested():
    a = {"outer": [np.array([1, 2]), {"inner": [3, 4]}]}
    b = {"outer": [np.array([1, 2]), {"inner": [3, 4]}]}
    assert ast_eq(a, b) is True
    assert ast_hash(a) == ast_hash(b)


def test_ast_hash_variable():
    v1 = Variable("x")
    v2 = Variable("x")
    assert ast_hash(v1) == ast_hash(v2)


def test_ast_eq_dict_different_keys_not_equal():
    assert ast_eq({"a": 1}, {"a": 1, "b": 2}) is False
