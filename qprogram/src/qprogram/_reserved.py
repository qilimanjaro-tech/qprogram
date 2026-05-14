"""Reserved keywords for future QProgram syntax.

The :data:`RESERVED_KEYWORDS` set lists identifier-shaped names that future
QProgram versions are likely to introduce as block keywords, modifiers, or
literals. The core library rejects them today in two places where a
collision would cost us later:

- :class:`~qprogram.Variable` ids — a variable named ``if`` would become
  syntactically ambiguous the moment a future minor version adds an
  ``if`` block. By rejecting ``Variable("if")`` now, we keep the future
  free to land ``program.if_(predicate)`` without breaking any existing
  ``.qp`` file.

- Vendor namespace names — ``register_vendor_operation("if", ...)`` would
  produce ``.qp`` lines like ``if.acquire ...`` that read as
  pseudo-keywords. Reserving the keyword set as vendor names keeps the
  vendor namespace surface predictable.

The sentinel name ``"core"`` is also reserved as a vendor namespace
specifically — core operations have ``vendor=None`` on the wire (no
prefix), and ``core.foo`` shouldn't be reachable from any vendor
registration.

Reservations are intentionally **not** applied to operation, block, or
sweep-generator names. Those *are* the future syntax: ``register_block(
"if", IfBlock)`` is the eventual landing site. Reserving keywords against
ops/blocks would be self-defeating.

Reservations are case-sensitive. ``If`` is not reserved; only ``if``.
"""

from __future__ import annotations

from typing import Final

RESERVED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # Conditional / iteration control flow
        "if", "else", "elif",
        "while", "until",
        "break", "continue", "return",
        # Definitions and reusable fragments
        "fragment", "def", "gate",
        # Pattern matching
        "case", "match",
        # Timing / scheduling
        "repeat", "barrier", "align", "align_left", "align_right", "parallel",
        # Conditional expression (already exists as a helper; reserve the bare keyword too)
        "where",
        # Bindings
        "let", "const",
        # Imports / aliases (future module system)
        "import", "from", "as",
        # Literals
        "true", "false", "null",
    },
)
"""Identifiers reserved for future QProgram syntax.

Variable ids cannot be any of these strings. Vendor namespace names
cannot be any of these strings either, nor the sentinel ``"core"``.
Operation names, block keyword names, and sweep generator names are
unaffected — those are the registration sites the future syntax will
use.
"""


RESERVED_VENDOR_NAMES: Final[frozenset[str]] = frozenset({"core"}) | RESERVED_KEYWORDS
"""Strings that cannot be used as the ``vendor`` argument to
:func:`~qprogram.register_vendor_operation`. The keyword set is included so
no vendor can shadow future syntax; ``"core"`` is the sentinel for
"no-vendor" core operations and is kept out of the vendor namespace by
construction."""


def is_reserved_keyword(name: str) -> bool:
    """Return ``True`` if ``name`` is a reserved identifier keyword."""
    return name in RESERVED_KEYWORDS


def is_reserved_vendor(name: str) -> bool:
    """Return ``True`` if ``name`` is reserved as a vendor namespace."""
    return name in RESERVED_VENDOR_NAMES
