"""``.qp`` serialization layer.

Importing this package activates the registry-driven dispatch: built-in waveforms, operations,
blocks, and sweep generators register themselves with :mod:`qprogram.serialization.registry`.

The lazy ``__getattr__`` for ``loads`` / ``load`` / ``ParseError`` breaks the import cycle between
the parser (which constructs :class:`QProgram` instances) and the rest of the package.
"""

from qprogram.serialization import _specs as _core_specs
from qprogram.serialization.registry import (
    BlockSpec,
    OperationSpec,
    SweepGeneratorSpec,
    register_block,
    register_operation,
    register_sweep_generator,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
)
from qprogram.serialization.writer import dumps, save

# Idempotent — registers the built-in operation, block, and sweep-generator specs from :mod:`_specs`.
_core_specs._register_core_specs()  # noqa: SLF001

__all__ = [
    "BlockSpec",
    "OperationSpec",
    "ParseError",
    "SweepGeneratorSpec",
    "dumps",
    "load",
    "loads",
    "register_block",
    "register_operation",
    "register_sweep_generator",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "save",
]


def __getattr__(name: str):  # noqa: ANN202
    # Lazy imports to avoid circular dependency (parser imports QProgram)
    if name in ("loads", "load", "ParseError"):
        from qprogram.serialization.parser import ParseError, load, loads  # noqa: PLC0415

        _lazy = {"loads": loads, "load": load, "ParseError": ParseError}
        return _lazy[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
