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
"""QProgram: A Domain-Specific Language for Pulse-Level Quantum Programming.

This module is the package's whole public surface: the [`QProgram`][qprogram.QProgram] builder, the expression and
waveform vocabularies, sweep sources, fragments, the capability protocol and its validator, the
reference software executor, and the ``.qp`` reader and writer.

``loads``, ``load``, and ``ParseError`` resolve on first attribute access instead of at import time —
the parser imports [`QProgram`][qprogram.QProgram], so importing it eagerly here would close a cycle. They are
imported from ``qprogram`` like every other name.
"""

from importlib.metadata import PackageNotFoundError, version

from qprogram._reserved import RESERVED_KEYWORDS
from qprogram.buses import BusNaming, BusRef, BusSchema
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
    simulate,
)
from qprogram.explain import explain
from qprogram.fragments import Fragment, Parameter, fragment
from qprogram.operations.call import Call
from qprogram.operations.operation import MeasurementField
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
    register_sweep_source,
    register_vendor_block,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
    save,
    try_activate_vendor,
)
from qprogram.sweeps import (
    Concat,
    File,
    Linspace,
    Logspace,
    Range,
    Repeat,
    Rotate,
    SweepSource,
    Values,
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
from qprogram.waveform_library import WaveformLibrary

try:
    __version__ = version("qprogram")
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = "0.0.0"

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
    "Concat",
    "Constant",
    "Diagnostic",
    "Domain",
    "DomainConstraint",
    "ExecutionPlan",
    "ExecutionWarning",
    "Expression",
    "File",
    "Fragment",
    "HardwareError",
    "InvalidVariableIdError",
    "Linspace",
    "LogicalBinaryOp",
    "LogicalNot",
    "Logspace",
    "MathFunc",
    "MeasurementField",
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
    "Range",
    "ReferencePlatform",
    "Repeat",
    "Rotate",
    "SerializationError",
    "SweepKind",
    "SweepSource",
    "UnaryOp",
    "UnassignedVariableError",
    "UnsupportedOperationError",
    "ValidationContext",
    "ValidationError",
    "Values",
    "Variable",
    "VendorActivationError",
    "VendorNamespace",
    "WaveformLibrary",
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
    "register_sweep_source",
    "register_vendor_block",
    "register_vendor_operation",
    "register_vendor_version",
    "register_waveform",
    "register_waveform_token",
    "resolve_path",
    "resolve_profile",
    "save",
    "simulate",
    "sin",
    "sqrt",
    "tan",
    "try_activate_vendor",
    "validate",
    "where",
]


def __getattr__(name: str):  # ruff: ignore[missing-return-type-private-function]
    """Resolve the lazily-imported parser names on first access.

    Args:
        name (str): Attribute being looked up on the package.

    Returns:
        The parser's ``loads``, ``load``, or ``ParseError``.

    Raises:
        AttributeError: When ``name`` is anything else, as a module attribute lookup must.
    """
    if name in {"loads", "load", "ParseError"}:
        from qprogram.serialization.parser import ParseError, load, loads  # ruff: ignore[import-outside-top-level]

        return {"loads": loads, "load": load, "ParseError": ParseError}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
