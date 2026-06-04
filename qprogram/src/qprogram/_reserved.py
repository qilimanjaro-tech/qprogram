"""Reserved keywords for future QProgram syntax.

Why this list exists: a ``Variable("if")`` today would become syntactically ambiguous the moment a
future minor version adds an ``if`` block; rejecting it now keeps existing ``.qp`` files forward-
compatible. Same reasoning for vendor namespace names.

Reservations are case-sensitive (``If`` is fine) and only apply to ``Variable`` ids and vendor names —
operation, block, and sweep-generator names are where the reserved keywords will eventually land.
"""

from __future__ import annotations

from typing import Final

RESERVED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # Keywords the format uses TODAY — an id like ``for`` would collide with the loop
        # grammar (`for for in range(...)`) and a ``var var`` declaration is unreadable.
        "var",
        "for",
        "in",
        # Expression keywords in use today (`(a and b)`, `(not x)`)
        "and",
        "or",
        "not",
        # Conditional / iteration control flow
        "if",
        "else",
        "elif",
        "while",
        "until",
        "break",
        "continue",
        "return",
        # Definitions and reusable fragments
        "fragment",
        "def",
        "gate",
        # Pattern matching
        "case",
        "match",
        # Timing / scheduling
        "repeat",
        # Conditional expression (already exists as a helper; reserve the bare keyword too)
        "where",
        # Bindings
        "let",
        "const",
        # Imports / aliases (future module system)
        "import",
        "from",
        "as",
        # Literals
        "true",
        "false",
        "null",
    },
)
"""Identifiers reserved for future QProgram syntax — rejected as :class:`~qprogram.Variable` ids and
vendor namespace names."""


RESERVED_VENDOR_NAMES: Final[frozenset[str]] = frozenset({"core"}) | RESERVED_KEYWORDS
"""Strings rejected as the ``vendor`` argument to :func:`~qprogram.register_vendor_operation`. Equals
:data:`RESERVED_KEYWORDS` plus the ``"core"`` sentinel for "no-vendor" core operations."""


def is_reserved_keyword(name: str) -> bool:
    """Return ``True`` if ``name`` is a reserved identifier keyword."""
    return name in RESERVED_KEYWORDS


def is_reserved_vendor(name: str) -> bool:
    """Return ``True`` if ``name`` is reserved as a vendor namespace."""
    return name in RESERVED_VENDOR_NAMES
