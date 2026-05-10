"""QProgram: A Domain-Specific Language for Pulse-Level Quantum Programming."""

from qprogram.buses import IQ, IQ_ACQUIRES, SINGLE, BusInfo, BusNaming, BusRef, BusSchema
from qprogram.crosstalk_matrix import CrosstalkMatrix
from qprogram.platform import PlatformProtocol
from qprogram.qprogram import QProgram
from qprogram.result import MeasurementResult, QProgramResult
from qprogram.serialization import dumps, register_vendor_operation, register_vendor_version, register_waveform, save
from qprogram.variable import (
    UNASSIGNED,
    BinaryOp,
    Constant,
    Expression,
    InvalidVariableLabelError,
    UnaryOp,
    UnassignedVariableError,
    Variable,
)
from qprogram.vendor import VendorNamespace

__all__ = [
    "IQ",
    "IQ_ACQUIRES",
    "SINGLE",
    "UNASSIGNED",
    "BinaryOp",
    "BusInfo",
    "BusNaming",
    "BusRef",
    "BusSchema",
    "Constant",
    "CrosstalkMatrix",
    "Expression",
    "InvalidVariableLabelError",
    "MeasurementResult",
    "PlatformProtocol",
    "QProgram",
    "QProgramResult",
    "UnaryOp",
    "UnassignedVariableError",
    "Variable",
    "VendorNamespace",
    "dumps",
    "load",
    "loads",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "save",
]


def __getattr__(name: str):  # noqa: ANN202
    # Lazy import parser to avoid circular deps at import time
    if name in ("loads", "load"):
        from qprogram.serialization.parser import load, loads  # noqa: PLC0415

        return {"loads": loads, "load": load}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
