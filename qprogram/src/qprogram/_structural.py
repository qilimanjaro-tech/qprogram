"""Shared helpers for structural equality and hashing of AST nodes.

:class:`Operation`, :class:`Block`, and :class:`Waveform` all walk ``vars(self)`` and ask these helpers
for the per-value verdict, so the three contracts stay consistent without per-class boilerplate.

The helpers handle the container shapes that show up inside AST attributes (``ndarray``, ``list``,
``dict``) explicitly and defer to the value's own ``==`` / ``hash`` for everything else — which means
:class:`Variable` keeps its identity-based equality, :class:`Constant` / :class:`BusRef` keep their
structural equality, and nested AST nodes recurse correctly.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def ast_eq(a: Any, b: Any) -> bool:  # noqa: ANN401
    """Structural equality for AST-attached values.

    Recurses into ``ndarray`` / ``list`` / ``dict`` containers; delegates to ``a == b`` otherwise.
    """
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and bool(np.array_equal(a, b))
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            return False
        return all(ast_eq(x, y) for x, y in zip(a, b, strict=False))
    if isinstance(a, dict):
        if not isinstance(b, dict) or a.keys() != b.keys():
            return False
        return all(ast_eq(a[k], b[k]) for k in a)
    return bool(a == b)


def ast_hash(value: Any) -> int:  # noqa: ANN401
    """Stable hash for an AST-attached value, consistent with :func:`ast_eq`.

    Arrays hash by ``(shape, .tobytes())``; lists hash as tuples of recursively-hashed elements; dicts
    hash as a sorted tuple of ``(key, hashed_value)``. Anything else defers to ``hash(value)``.
    """
    if isinstance(value, np.ndarray):
        return hash((value.shape, value.tobytes()))
    if isinstance(value, list):
        return hash(tuple(ast_hash(v) for v in value))
    if isinstance(value, dict):
        return hash(tuple(sorted((k, ast_hash(v)) for k, v in value.items())))
    return hash(value)
