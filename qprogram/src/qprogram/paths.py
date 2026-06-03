"""Structural AST paths — stable addresses for nodes that survive serialization.

A path is a tuple of segments rooted at ``program.body`` (whose own path is ``()``). Because the
``.qp`` round-trip preserves structure exactly, a path computed against one program resolves
against any structurally-equal copy — notably ``loads(dumps(p))``, whose
:attr:`~qprogram.QProgram.source_map` then maps the same path to a 1-based ``.qp`` line.

Segment vocabulary (matching the validator's child taxonomy):

- ``int i`` — ``elements[i]`` of the current node (plain :class:`~qprogram.blocks.Block`,
  ``ForLoop``/``Loop``/``Average``, and a ``Parallel``'s body elements).
- ``"arm:<i>"`` — the body of a :class:`~qprogram.blocks.Conditional`'s i-th arm.
- ``"else"`` — a Conditional's else body.
- ``"loop:<i>"`` — a :class:`~qprogram.blocks.Parallel`'s i-th composed loop header.

:func:`node_path` matches by **object identity** (two structurally identical ops at different
sites get different paths); :func:`resolve_path` is its inverse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.qprogram import QProgram

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.operations.operation import Operation

AstPath: TypeAlias = tuple[int | str, ...]
"""A structural node address: ``()`` is ``program.body``; see the module docstring for segments."""


def iter_child_edges(node: Block | Operation) -> Iterator[tuple[int | str, Block | Operation]]:
    """Yield ``(segment, child)`` for every structural child of ``node``, in document order.

    The single canonical child enumeration behind :func:`node_path` / :func:`resolve_path` —
    Conditional keeps its arm bodies on ``.arms`` / ``.else_body`` and Parallel its loop headers
    on ``.loops``, so a plain ``elements`` walk would miss them.
    """
    if isinstance(node, Conditional):
        for i, (_, body) in enumerate(node.arms):
            yield f"arm:{i}", body
        if node.else_body is not None:
            yield "else", node.else_body
        return
    if isinstance(node, Parallel):
        for i, loop in enumerate(node.loops):
            yield f"loop:{i}", loop
    if isinstance(node, Block):
        for i, child in enumerate(node.elements):
            yield i, child


def node_path(root: QProgram | Block, node: Block | Operation) -> AstPath | None:
    """Return the path of ``node`` under ``root``, or ``None`` if it isn't in the tree.

    Matching is by object **identity** — pass the same instance the tree holds (e.g. a
    ``Diagnostic.node``), not a structural twin.

    Args:
        root: A :class:`~qprogram.QProgram` (paths root at its body) or a :class:`Block`.
        node: The node instance to locate.
    """
    base = root.body if isinstance(root, QProgram) else root
    if node is base:
        return ()

    def search(current: Block | Operation, prefix: AstPath) -> AstPath | None:
        for segment, child in iter_child_edges(current):
            child_path = (*prefix, segment)
            if child is node:
                return child_path
            found = search(child, child_path)
            if found is not None:
                return found
        return None

    return search(base, ())


def resolve_path(root: QProgram | Block, path: AstPath) -> Block | Operation:
    """Return the node addressed by ``path`` under ``root`` — the inverse of :func:`node_path`.

    Args:
        root: A :class:`~qprogram.QProgram` (paths root at its body) or a :class:`Block`.
        path: The structural address to follow.

    Raises:
        KeyError: When any segment doesn't exist on the node reached so far (a dangling path —
            typically a path computed against a structurally different program).
    """
    current: Block | Operation = root.body if isinstance(root, QProgram) else root
    for depth, segment in enumerate(path):
        for child_segment, child in iter_child_edges(current):
            if child_segment == segment:
                current = child
                break
        else:
            taken = format_path(path[:depth])
            msg = f"path segment {segment!r} does not exist under {taken} ({type(current).__name__})"
            raise KeyError(msg)
    return current


def format_path(path: AstPath) -> str:
    """Render a path for humans: ``()`` → ``"body"``; ``(1, "arm:0", 2)`` → ``"body[1].arm:0[2]"``."""
    out = "body"
    for segment in path:
        out += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return out


__all__ = ["AstPath", "format_path", "iter_child_edges", "node_path", "resolve_path"]
