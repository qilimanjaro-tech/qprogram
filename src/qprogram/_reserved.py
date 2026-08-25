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
"""Reserved keywords for future QProgram syntax.

Why this list exists: a ``Variable("if")`` becomes syntactically ambiguous the moment the format
grows an ``if`` block, so rejecting the id up front keeps every ``.qp`` file that parses today
parsing tomorrow. Same reasoning for vendor namespace names.

Reservations are case-sensitive (``If`` is fine) and apply to [`Variable`][qprogram.Variable] ids,
[`Fragment`][qprogram.Fragment] names, and vendor namespace names. Operation, block, and sweep-source
names are the syntax these keywords are held back for, so they are not checked against this list.
"""

from __future__ import annotations

from typing import Final

RESERVED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # Keywords the format already uses — an id like ``for`` would collide with the loop
        # grammar (`for for in range(...)`) and a ``var var`` declaration is unreadable.
        "var",
        "for",
        "in",
        # Expression keywords already in use (`(a and b)`, `(not x)`)
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
"""Identifiers reserved for future QProgram syntax — rejected as [`Variable`][qprogram.Variable] ids,
[`Fragment`][qprogram.Fragment] names, and vendor namespace names."""


RESERVED_VENDOR_NAMES: Final[frozenset[str]] = frozenset({"core"}) | RESERVED_KEYWORDS
"""Strings rejected wherever a vendor namespace is named — [`register_vendor`][qprogram.QProgram.register_vendor],
`register_vendor_operation`, `register_vendor_block`, and
`register_vendor_version`. Equals [`RESERVED_KEYWORDS`][qprogram.RESERVED_KEYWORDS] plus the ``"core"``
sentinel for "no-vendor" core operations."""


def is_reserved_keyword(name: str) -> bool:
    """Return whether ``name`` is a reserved identifier keyword.

    Args:
        name (str): Candidate [`Variable`][qprogram.Variable] id. Matched case-sensitively, so ``"If"``
            is not reserved.

    Returns:
        ``True`` when ``name`` is in [`RESERVED_KEYWORDS`][qprogram.RESERVED_KEYWORDS].
    """
    return name in RESERVED_KEYWORDS


def is_reserved_vendor(name: str) -> bool:
    """Return whether ``name`` is reserved as a vendor namespace.

    Args:
        name (str): Candidate vendor namespace name. Matched case-sensitively.

    Returns:
        ``True`` when ``name`` is in `RESERVED_VENDOR_NAMES`.
    """
    return name in RESERVED_VENDOR_NAMES
