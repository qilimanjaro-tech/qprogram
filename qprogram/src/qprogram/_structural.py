"""Shared helpers for structural equality and hashing of AST nodes.

:class:`~qprogram.operations.Operation`, :class:`~qprogram.blocks.Block`,
and :class:`~qprogram.waveforms.waveform.Waveform` all compare and hash by
walking their ``vars()`` and asking these helpers for the per-value verdict.
Putting the logic in one place lets the three contracts stay consistent and
gives vendor extensions a single place to look up "what does AST equality
mean here".

The helpers cope with the four shapes that show up inside AST attribute
values:

- :class:`numpy.ndarray` — arrays compare elementwise (``np.array_equal``)
  and hash by ``(shape, .tobytes())``.
- ``list`` — element-wise equality; hashed by converting to a tuple of
  recursively-hashed elements (the underlying list stays mutable in the
  AST; we just need a stable snapshot for the hash).
- ``dict`` — key + value-wise; hashed by sorting items.
- anything else — defer to the value's own ``==`` / ``hash``. This covers
  ``str``, ``int``, ``float``, ``Variable`` (identity-based), ``Constant``
  /``Expression`` (structural), ``BusRef`` (string-based),
  ``MeasurementHandle`` (structural), nested ``Waveform`` / ``Operation`` /
  ``Block`` (structural — they call back into here).

The hash invariant requires nodes to be effectively immutable once
hashed. Operations and Blocks don't enforce this at runtime; the contract
is documented on their base classes. Callers like
:func:`QProgram.with_bus_mapping` that mutate AST nodes always do so on a
fresh ``deepcopy``, so they don't break this in practice.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def ast_eq(a: Any, b: Any) -> bool:  # noqa: ANN401
    """Structural equality for AST-attached values.

    Used by :meth:`Operation.__eq__`, :meth:`Block.__eq__`, and
    :meth:`Waveform.__eq__`. Recurses into containers; for everything else,
    delegates to the value's own ``__eq__``.
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
    """Hashable representation of an AST-attached value.

    The result is paired with the dict-style key it came from inside
    ``__hash__`` so the per-attribute hash is order-stable.

    Lists are hashed as tuples of recursively-hashed elements. Dicts are
    hashed as a sorted tuple of (key, hashed-value). Arrays are hashed by
    ``(shape, .tobytes())``. Anything else defers to the value's own
    ``__hash__`` (which is what makes :class:`Variable`'s identity-based
    hash and :class:`Constant`'s structural hash both work transparently).
    """
    if isinstance(value, np.ndarray):
        return hash((value.shape, value.tobytes()))
    if isinstance(value, list):
        return hash(tuple(ast_hash(v) for v in value))
    if isinstance(value, dict):
        return hash(tuple(sorted((k, ast_hash(v)) for k, v in value.items())))
    return hash(value)
