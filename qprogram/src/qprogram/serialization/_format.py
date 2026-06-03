"""Shared ``.qp`` format constants.

The single source of truth for the format version emitted by the writer and accepted by the
parser. Lives in its own leaf module (no qprogram imports) so both sides can import it without
touching the writer↔parser import cycle.
"""

from __future__ import annotations

from typing import Final

FORMAT_VERSION: Final[str] = "1.0"
"""``major.minor`` version emitted in the ``#!QProgram`` header and accepted by the parser.

Compatibility contract (see ``.specs/qp-file-format.md`` §9.3): the parser rejects files whose
*major* version differs from this one; minor differences within the same major are accepted.
"""
