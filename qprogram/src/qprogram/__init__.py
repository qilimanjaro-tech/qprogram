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
    VendorActivationError,
    WaveformResolutionError,
)
from qprogram.executor import (
    ExecutionWarning,
    MeasurementModel,
    MeasurementSample,
    MockMeasurementModel,
    ReferencePlatform,
    reference_capabilities,
    run,
)
from qprogram.explain import explain
from qprogram.fragments import Fragment, Parameter, fragment
from qprogram.operations.call import Call
from qprogram.optimization import optimize
from qprogram.paths import AstPath, format_path, node_path, resolve_path
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
from qprogram.serialization import (
    dumps,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
    save,
    try_activate_vendor,
)
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
    "AstPath",
    "BinaryOp",
    "BusCapabilities",
    "BusNaming",
    "BusNotAvailableError",
    "BusRef",
    "BusSchema",
    "BusSelector",
    "Call",
    "Comparison",
    "CompilationError",
    "CompilerCapabilities",
    "Constant",
    "CrosstalkMatrix",
    "Diagnostic",
    "Domain",
    "DomainConstraint",
    "ExecutionPlan",
    "ExecutionWarning",
    "Expression",
    "Fragment",
    "HardwareError",
    "InvalidVariableIdError",
    "LogicalBinaryOp",
    "LogicalNot",
    "MathFunc",
    "MeasurementHandle",
    "MeasurementModel",
    "MeasurementRef",
    "MeasurementResult",
    "MeasurementSample",
    "MockMeasurementModel",
    "Parameter",
    "ParseError",
    "PlatformCapabilities",
    "PlatformProtocol",
    "Predicate",
    "PredicateFn",
    "Profile",
    "QProgram",
    "QProgramError",
    "QProgramResult",
    "ReferencePlatform",
    "SerializationError",
    "SweepKind",
    "UnaryOp",
    "UnassignedVariableError",
    "UnsupportedOperationError",
    "ValidationContext",
    "ValidationError",
    "Variable",
    "VendorActivationError",
    "VendorNamespace",
    "WaveformResolutionError",
    "Where",
    "and_",
    "cos",
    "dumps",
    "eq",
    "exp",
    "explain",
    "format_path",
    "fragment",
    "load",
    "loads",
    "log",
    "maximum",
    "minimum",
    "ne",
    "node_path",
    "not_",
    "optimize",
    "or_",
    "reference_capabilities",
    "register_capability_tokens",
    "register_profile",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "register_waveform_token",
    "resolve_path",
    "resolve_profile",
    "run",
    "save",
    "sin",
    "sqrt",
    "tan",
    "try_activate_vendor",
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
