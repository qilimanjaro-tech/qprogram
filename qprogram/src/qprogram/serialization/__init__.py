from qprogram.serialization.registry import register_vendor_operation, register_vendor_version, register_waveform
from qprogram.serialization.writer import dumps, save

__all__ = [
    "ParseError",
    "dumps",
    "load",
    "loads",
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
