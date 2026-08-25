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
"""QProgram exception hierarchy.

Every error that originates inside QProgram (core construction, ``.qp`` parsing, or platform compile /
execution) is a subclass of [`QProgramError`][qprogram.QProgramError]. User code catches at the granularity it needs:
``except QProgramError`` for "anything QProgram", ``except ValidationError`` for construction-time
issues, ``except ParseError`` for ``.qp`` files, or one of the platform-side classes (``HardwareError``,
``CompilationError``, ...) for runtime failures.

The platform-side classes are *defined* here but never raised by core QProgram — they are the contract
that concrete platforms use so a single ``except`` works uniformly across every backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.variable import Expression, Variable


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class QProgramError(Exception):
    """Root of the QProgram exception hierarchy.

    Catch this for "QProgram raised something"; prefer a more specific subclass when you do care why.
    """


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class ValidationError(QProgramError):
    """A construction-time validation failure.

    Raised when a program is being assembled and an operation rejects its arguments — e.g. an IQ
    waveform on a single-channel bus, a ``measure()`` on a bus without ADC, duplicate variable ids, or
    a measurement-handle name collision.

    Deliberately not a `ValueError`: construction validation deserves a catch of its own, and inheriting `ValueError`
    would turn ``except ValueError`` into an accidental catch-all for it. Two subclasses do extend `ValueError` —
    [`InvalidVariableIdError`][qprogram.InvalidVariableIdError] and
    [`UnassignedVariableError`][qprogram.UnassignedVariableError] — because a malformed identifier and an expression
    that has no value really are bad-value errors in the ordinary Python sense.
    """


class InvalidVariableIdError(ValidationError, ValueError):
    """A variable id rejected on its pattern or because it is reserved.

    Also a `ValueError`, since a malformed identifier is a bad value in the ordinary Python
    sense — ``except ValueError`` around variable construction catches it.

    Args:
        id (str): The offending identifier. Available at catch-time as `id`.
        reserved (bool): ``True`` if ``id`` matches the identifier pattern but is one of
            [`RESERVED_KEYWORDS`][qprogram.RESERVED_KEYWORDS]; ``False`` for an outright pattern violation. Available
            at catch-time as `reserved`.
    """

    def __init__(self, id: str, *, reserved: bool = False) -> None:  # ruff: ignore[builtin-argument-shadowing]
        if reserved:
            message = (
                f"Variable id {id!r} is reserved for future QProgram syntax "
                f"(see qprogram.RESERVED_KEYWORDS). Pick a non-reserved id "
                f"such as {id + '_var'!r}, or carry the original name in the "
                f"optional `label` argument."
            )
        else:
            message = (
                f"Variable id {id!r} is invalid: must match "
                f"[A-Za-z_][A-Za-z0-9_]* (letters, digits, underscores only; "
                f"cannot start with a digit, no spaces or special characters). "
                f"Use the optional `label` for human-readable names."
            )
        super().__init__(message)
        self.id = id
        self.reserved = reserved


class UnassignedVariableError(ValidationError, ValueError):
    """An [`Expression`][qprogram.Expression] evaluated while it still references unbound [`Variable`][qprogram.Variable] s.

    Raised by [`Expression.evaluate_or_raise`][qprogram.Expression.evaluate_or_raise]. Also a `ValueError`, since an
    expression over unbound variables has no value to give.

    Args:
        expression (Expression): The expression that failed to evaluate. Kept as
            `expression`.

    Attributes:
        free_variables (set[Variable]): The unbound variables that caused the failure, collected
            from ``expression`` at construction.
    """

    def __init__(self, expression: Expression) -> None:
        free = expression.variables()
        super().__init__(
            f"Cannot evaluate expression {expression!r}: unassigned variable(s) {free!r}",
        )
        self.expression: Expression = expression
        self.free_variables: set[Variable] = free


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class SerializationError(QProgramError):
    """A program the ``.qp`` writer cannot faithfully serialize.

    Raised instead of emitting lossy or unparseable output — e.g. an operation or block class that
    was never registered with the serialization registry, a vendor operation whose extension forgot
    to call `register_vendor_version`, or an attribute value of a type the format
    has no representation for. The write-side counterpart of ``ParseError``: a ``.qp`` file that was
    produced without error is guaranteed to parse back into an equal program.
    """


class VendorActivationError(QProgramError):
    """A discovered vendor extension that could not be activated.

    Raised by `try_activate_vendor` (and, during ``.qp`` parsing, wrapped into a
    `ParseError`) when a ``qprogram.vendors`` entry point's import target raises, or imports
    without calling `register_vendor_version` — i.e. the package is installed but
    broken. A vendor that is not installed at all is *not* this error: discovery reports "no
    matching extension".
    """


# ---------------------------------------------------------------------------
# Platform-side error contracts
# ---------------------------------------------------------------------------
#
# Core QProgram does not raise these; concrete platforms (vendor backends) raise them so user code can
# write one ``except`` per failure mode regardless of which backend is in use.


class UnsupportedOperationError(QProgramError):
    """Platform-side: an operation the backend cannot lower to its hardware.

    Typical causes: a vendor op the backend doesn't implement, or a control-flow construct outside
    the platform's supported feature set.
    """


class BusNotAvailableError(QProgramError):
    """Platform-side: a bus the program references but this backend doesn't expose.

    The program is structurally well-formed; the incompatibility is with this particular platform.
    Use [`ValidationError`][qprogram.ValidationError] for construction-time bus issues.
    """


class WaveformResolutionError(QProgramError):
    """Platform-side: a string waveform name that reached execution without a concrete waveform.

    Resolve names before execution with [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms] (or
    [`WaveformLibrary.apply`][qprogram.WaveformLibrary.apply]); this is raised when one was missed or the
    [`WaveformLibrary`][qprogram.WaveformLibrary] used had no entry for it.
    """


class CompilationError(QProgramError):
    """Platform-side: a lowered representation that failed to compile.

    Catch-all for backend-internal failures that don't fit the other platform errors (timing
    constraints, resource over-allocation, code-generation bugs).
    """


class HardwareError(QProgramError):
    """Platform-side: an instrument-level failure during execution.

    Covers driver errors, SCPI failures, lost trigger pulses, and anything else surfacing *during*
    execution rather than at compile or validate time.
    """
