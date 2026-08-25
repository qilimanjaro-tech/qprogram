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
"""``.qp`` serialization layer.

Importing this package activates the registry-driven dispatch: built-in waveforms, operations,
blocks, and sweep sources register themselves with `qprogram.serialization.registry`.

The lazy ``__getattr__`` for ``loads`` / ``load`` / ``ParseError`` breaks the import cycle between
the parser (which constructs [`QProgram`][qprogram.QProgram] instances) and the rest of the package.
"""

from qprogram.serialization import _specs as _core_specs
from qprogram.serialization.registry import (
    BlockSpec,
    OperationSpec,
    known_sweep_sources,
    register_block,
    register_operation,
    register_sweep_source,
    register_vendor_block,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
    try_activate_vendor,
)
from qprogram.serialization.writer import dumps, save

# Idempotent — registers the built-in operation, block, and sweep-source specs from `_specs`.
_core_specs._register_core_specs()  # ruff: ignore[private-member-access]

__all__ = [
    "BlockSpec",
    "OperationSpec",
    "ParseError",
    "dumps",
    "known_sweep_sources",
    "load",
    "loads",
    "register_block",
    "register_operation",
    "register_sweep_source",
    "register_vendor_block",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "save",
    "try_activate_vendor",
]


def __getattr__(name: str):  # ruff: ignore[missing-return-type-private-function]
    """Resolve the parser's public names on first access.

    The parser module constructs [`QProgram`][qprogram.QProgram] instances, so importing it eagerly
    from this package would close an import cycle. Deferring that import until the attribute is
    actually read keeps ``loads`` / ``load`` / ``ParseError`` on the package surface without one.

    Args:
        name (str): Attribute being looked up on this module.

    Returns:
        The requested parser attribute.

    Raises:
        AttributeError: If ``name`` is not one of the lazily resolved parser names.
    """
    if name in {"loads", "load", "ParseError"}:
        from qprogram.serialization.parser import ParseError, load, loads  # ruff: ignore[import-outside-top-level]

        return {"loads": loads, "load": load, "ParseError": ParseError}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
