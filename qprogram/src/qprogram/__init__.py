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
    SerializationError,
    UnassignedVariableError,
    UnsupportedOperationError,
    ValidationError,
    WaveformResolutionError,
)
from qprogram.platform import PlatformProtocol
from qprogram.profiles import QPROGRAM_BASE_V1
from qprogram.protocol import (
    BusCapabilities,
    BusSelector,
    CompilerCapabilities,
    Diagnostic,
    Domain,
    DomainConstraint,
    ExecutionPlan,
    PlatformCapabilities,
    Predicate,
    PredicateFn,
    Profile,
    SweepKind,
    ValidationContext,
    register_capability_tokens,
    register_profile,
    register_waveform_token,
    resolve_profile,
)
from qprogram.qprogram import QProgram
from qprogram.result import MeasurementHandle, MeasurementResult, QProgramResult
from qprogram.serialization import dumps, register_vendor_operation, register_vendor_version, register_waveform, save
from qprogram.validation import validate
from qprogram.variable import (
    UNASSIGNED,
    BinaryOp,
    Comparison,
    Constant,
    Expression,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    MeasurementRef,
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
    "QPROGRAM_BASE_V1",
    "RESERVED_KEYWORDS",
    "UNASSIGNED",
    "BinaryOp",
    "BusCapabilities",
    "BusNaming",
    "BusNotAvailableError",
    "BusRef",
    "BusSchema",
    "BusSelector",
    "Comparison",
    "CompilationError",
    "CompilerCapabilities",
    "Constant",
    "CrosstalkMatrix",
    "Diagnostic",
    "Domain",
    "DomainConstraint",
    "ExecutionPlan",
    "Expression",
    "HardwareError",
    "InvalidVariableIdError",
    "LogicalBinaryOp",
    "LogicalNot",
    "MathFunc",
    "MeasurementHandle",
    "MeasurementRef",
    "MeasurementResult",
    "ParseError",
    "PlatformCapabilities",
    "PlatformProtocol",
    "Predicate",
    "PredicateFn",
    "Profile",
    "QProgram",
    "QProgramError",
    "QProgramResult",
    "SerializationError",
    "SweepKind",
    "UnaryOp",
    "UnassignedVariableError",
    "UnsupportedOperationError",
    "ValidationContext",
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
    "register_capability_tokens",
    "register_profile",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "register_waveform_token",
    "resolve_profile",
    "save",
    "sin",
    "sqrt",
    "tan",
    "validate",
    "where",
]


def __getattr__(name: str):  # noqa: ANN202
    # Lazy import parser bits to avoid circular deps at import time
    if name in ("loads", "load", "ParseError"):
        from qprogram.serialization.parser import ParseError, load, loads  # noqa: PLC0415

        return {"loads": loads, "load": load, "ParseError": ParseError}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
