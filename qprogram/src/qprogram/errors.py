"""QProgram exception hierarchy.

Every error that originates inside QProgram — at construction time in the
core library, at parse time when loading a ``.qp`` file, or at compile /
execution time inside a platform — is a subclass of
:class:`QProgramError`. User code can catch the right level of granularity:

- ``except QProgramError``      — anything QProgram raised.
- ``except ValidationError``    — construction-time validation failed.
- ``except ParseError``         — couldn't parse a ``.qp`` file.
- ``except WaveformResolutionError`` (etc.) — one specific failure mode.

The platform-side classes (``UnsupportedOperationError``,
``BusNotAvailableError``, ``WaveformResolutionError``, ``CompilationError``,
``HardwareError``) are *defined* here but not raised by core QProgram itself.
They are the contract concrete platforms (qililab, qblox-platform, …) use
when reporting errors back to the user, so a single ``except
CompilationError`` works uniformly across every backend.

Two construction-time subclasses (:class:`InvalidVariableIdError`,
:class:`UnassignedVariableError`) also subclass :class:`ValueError`. They
historically lived in :mod:`qprogram.variable` and predate this hierarchy;
keeping the :class:`ValueError` parent lets existing ``except ValueError``
code keep working for those specific exceptions. The bare ``ValueError`` /
``TypeError`` raises that previously came from the core's validation helpers
are now :class:`ValidationError` only — pre-1.0 we don't shim a second
parent onto the base class.
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

    Catch this if you want to know "QProgram raised something" without
    caring why; prefer a more specific subclass when you do care.
    """


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class ValidationError(QProgramError):
    """Construction-time validation failed.

    Raised by core QProgram code when a program is being assembled and an
    operation rejects its arguments — e.g. an IQ waveform on a single-channel
    bus, a ``measure()`` on a bus that doesn't acquire, two variables with
    the same id in one program, or a name collision on
    :class:`~qprogram.MeasurementHandle` allocation.

    Two existing subclasses also extend :class:`ValueError` for legacy
    reasons (see :class:`InvalidVariableIdError`,
    :class:`UnassignedVariableError`). The base ``ValidationError`` does not
    extend ``ValueError`` itself, so plain ``except ValueError`` is no
    longer a catch-all for QProgram's construction validation; use
    ``except ValidationError`` (or ``except QProgramError``) instead.
    """


class InvalidVariableIdError(ValidationError, ValueError):
    """Variable id failed validation.

    Two failure modes share this exception class:

    - **Pattern**: ``id`` doesn't match ``[A-Za-z_][A-Za-z0-9_]*``.
    - **Reserved**: ``id`` matches the pattern but is one of the
      :data:`~qprogram.RESERVED_KEYWORDS` reserved for future syntax
      (``if``, ``while``, ``repeat``, …). Use :attr:`reserved` to
      distinguish at catch time.

    Subclasses :class:`ValueError` for back-compat with code that
    predates the QProgram exception hierarchy.
    """

    def __init__(self, id: str, *, reserved: bool = False) -> None:  # noqa: A002
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
    """Expression contains a Variable whose value isn't bound.

    Raised by ``Expression.evaluate_or_raise()``. Subclasses
    :class:`ValueError` for back-compat with code that predates the
    QProgram exception hierarchy.
    """

    def __init__(self, expression: Expression) -> None:
        free = expression.variables()
        super().__init__(
            f"Cannot evaluate expression {expression!r}: unassigned variable(s) {free!r}",
        )
        self.expression: Expression = expression
        self.free_variables: set[Variable] = free


# ---------------------------------------------------------------------------
# Platform-side error contracts
# ---------------------------------------------------------------------------
#
# Core QProgram does not raise these — they are the agreed-on exception
# classes concrete platforms (qililab, qblox-platform, vendor backends, …)
# raise back to users. Defining them in core gives every platform a single
# import path and lets user code write one ``except`` per failure mode.


class UnsupportedOperationError(QProgramError):
    """Platform-side: operation not implementable on this backend.

    Raised by a platform when the program contains an operation it cannot
    lower to its hardware (e.g., a vendor op the backend doesn't
    implement, or a control-flow construct the compiler can't realise).
    """


class BusNotAvailableError(QProgramError):
    """Platform-side: program references a bus this backend doesn't expose.

    Distinct from :class:`ValidationError` — the program is structurally
    well-formed, just incompatible with this particular platform.
    """


class WaveformResolutionError(QProgramError):
    """Platform-side: a string waveform alias remained unresolved at execution.

    Typically means the user forgot to wire the alias through
    :meth:`~qprogram.QProgram.with_waveforms` (or the calibration library
    handing waveforms to the platform omitted it).
    """


class CompilationError(QProgramError):
    """Platform-side: the lowered representation failed to compile.

    Use this for backend-internal failures that aren't categorised by the
    other platform-side errors (timing constraints, resource
    over-allocation, code-generation bugs, etc.).
    """


class HardwareError(QProgramError):
    """Platform-side: an instrument-level runtime failure occurred.

    Use this for anything that surfaces *during* execution rather than at
    compile/validate time (driver errors, SCPI failures, lost trigger
    pulses, etc.).
    """
