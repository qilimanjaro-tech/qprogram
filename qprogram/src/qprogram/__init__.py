"""QProgram: A Domain-Specific Language for Pulse-Level Quantum Programming."""

from qprogram._reserved import RESERVED_KEYWORDS
from qprogram.buses import BusNaming, BusRef, BusSchema
from qprogram.crosstalk_matrix import CrosstalkMatrix
from qprogram.errors import (
    BusNotAvailableError,
    CompilationError,
    HardwareError,
    InvalidVariableIdError,
    QProgramError,
    UnassignedVariableError,
    UnsupportedOperationError,
    ValidationError,
    WaveformResolutionError,
)
from qprogram.platform import PlatformProtocol
from qprogram.qprogram import QProgram
from qprogram.result import MeasurementHandle, MeasurementResult, QProgramResult
from qprogram.serialization import dumps, register_vendor_operation, register_vendor_version, register_waveform, save
from qprogram.variable import (
    UNASSIGNED,
    BinaryOp,
    Comparison,
    Constant,
    Expression,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    UnaryOp,
    Variable,
    Where,
    and_,
    cos,
    eq,
    exp,
    log,
    maximum,
    minimum,
    ne,
    not_,
    or_,
    sin,
    sqrt,
    tan,
    where,
)
from qprogram.vendor import VendorNamespace

__all__ = [
    "RESERVED_KEYWORDS",
    "UNASSIGNED",
    "BinaryOp",
    "BusNaming",
    "BusNotAvailableError",
    "BusRef",
    "BusSchema",
    "Comparison",
    "CompilationError",
    "Constant",
    "CrosstalkMatrix",
    "Expression",
    "HardwareError",
    "InvalidVariableIdError",
    "LogicalBinaryOp",
    "LogicalNot",
    "MathFunc",
    "MeasurementHandle",
    "MeasurementResult",
    "ParseError",
    "PlatformProtocol",
    "QProgram",
    "QProgramError",
    "QProgramResult",
    "UnaryOp",
    "UnassignedVariableError",
    "UnsupportedOperationError",
    "ValidationError",
    "Variable",
    "VendorNamespace",
    "WaveformResolutionError",
    "Where",
    "and_",
    "cos",
    "dumps",
    "eq",
    "exp",
    "load",
    "loads",
    "log",
    "maximum",
    "minimum",
    "ne",
    "not_",
    "or_",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "save",
    "sin",
    "sqrt",
    "tan",
    "where",
]


def __getattr__(name: str):  # noqa: ANN202
    # Lazy import parser bits to avoid circular deps at import time
    if name in ("loads", "load", "ParseError"):
        from qprogram.serialization.parser import ParseError, load, loads  # noqa: PLC0415

        return {"loads": loads, "load": load, "ParseError": ParseError}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
