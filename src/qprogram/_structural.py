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
"""Shared helpers for structural equality and hashing of AST nodes.

:class:`Operation`, :class:`Block`, and :class:`Waveform` all walk ``vars(self)`` and ask these helpers
for the per-value verdict, so the three contracts stay consistent without per-class boilerplate.

The helpers handle the container shapes that show up inside AST attributes (``ndarray``, ``list``,
``dict``) explicitly and defer to the value's own ``==`` / ``hash`` for everything else — which means
:class:`Variable` keeps its equality by string id, :class:`Constant` / :class:`BusRef` keep their
structural equality, and nested AST nodes recurse correctly.
"""

from __future__ import annotations

from itertools import starmap
from typing import Any

import numpy as np


def ast_eq(a: Any, b: Any) -> bool:  # ruff: ignore[any-type]
    """Compare two AST-attached values structurally.

    Recurses into ``ndarray`` / ``list`` / ``dict`` containers; delegates to ``a == b`` otherwise.
    An array only ever compares equal to another array, so a list of samples and the equivalent
    ``ndarray`` are held distinct.

    Args:
        a (Any): Left-hand value, as read from an AST node's ``vars()``.
        b (Any): Right-hand value to compare it against.

    Returns:
        ``True`` when the two values are structurally equal.
    """
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and bool(np.array_equal(a, b))
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            return False
        return all(starmap(ast_eq, zip(a, b, strict=False)))
    if isinstance(a, dict):
        if not isinstance(b, dict) or a.keys() != b.keys():
            return False
        return all(ast_eq(a[k], b[k]) for k in a)
    return bool(a == b)


def ast_hash(value: Any) -> int:  # ruff: ignore[any-type]
    """Return a stable hash for an AST-attached value.

    Arrays hash by ``(shape, .tobytes())``; lists hash as tuples of recursively-hashed elements; dicts
    hash as a sorted tuple of ``(key, hashed_value)``. Anything else defers to ``hash(value)``.

    The array rule is dtype-sensitive where :func:`ast_eq` compares contents only, so two nodes that
    differ only in a sample array's dtype compare equal yet hash apart, and land in different buckets
    of a ``dict`` or ``set``.

    Args:
        value (Any): A value, as read from an AST node's ``vars()``.

    Returns:
        A hash of the value's structure, recursing through the container shapes :func:`ast_eq`
        recurses through.

    Raises:
        TypeError: When ``value`` is neither one of the handled container shapes nor hashable.
    """
    if isinstance(value, np.ndarray):
        return hash((value.shape, value.tobytes()))
    if isinstance(value, list):
        return hash(tuple(ast_hash(v) for v in value))
    if isinstance(value, dict):
        return hash(tuple(sorted((k, ast_hash(v)) for k, v in value.items())))
    return hash(value)
