"""QProgram exception hierarchy.

Every error that originates inside QProgram (core construction, ``.qp`` parsing, or platform compile /
execution) is a subclass of :class:`QProgramError`. User code catches at the granularity it needs:
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
    """Construction-time validation failed.

    Raised when a program is being assembled and an operation rejects its arguments — e.g. an IQ
    waveform on a single-channel bus, a ``measure()`` on a bus without ADC, duplicate variable ids, or
    a measurement-handle name collision.

    Why this does not also extend :class:`ValueError`: plain ``except ValueError`` is no longer a
    catch-all for QProgram's construction validation. Two legacy subclasses
    (:class:`InvalidVariableIdError`, :class:`UnassignedVariableError`) still inherit :class:`ValueError`
    for back-compat with code predating this hierarchy.
    """


class InvalidVariableIdError(ValidationError, ValueError):
    """Variable id failed validation, either on pattern or because it is reserved.

    Why this also subclasses :class:`ValueError`: back-compat with code that predates the QProgram
    exception hierarchy and catches ``ValueError`` for invalid identifiers.

    Args:
        id: The offending identifier.
        reserved: ``True`` if ``id`` matches the identifier pattern but is one of
            :data:`~qprogram.RESERVED_KEYWORDS`; ``False`` for an outright pattern violation. Available
            at catch-time as :attr:`reserved`.
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
    """An :class:`Expression` was evaluated while it still references unbound :class:`Variable` s.

    Raised by :meth:`Expression.evaluate_or_raise`. Subclasses :class:`ValueError` for back-compat.

    Attributes:
        expression: The expression that failed to evaluate.
        free_variables: The set of unbound variables that caused the failure.
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
# Core QProgram does not raise these; concrete platforms (vendor backends) raise them so user code can
# write one ``except`` per failure mode regardless of which backend is in use.


class UnsupportedOperationError(QProgramError):
    """Platform-side: the backend cannot lower an operation to its hardware.

    Typical causes: a vendor op the backend doesn't implement, or a control-flow construct outside
    the platform's supported feature set.
    """


class BusNotAvailableError(QProgramError):
    """Platform-side: the program references a bus this backend doesn't expose.

    The program is structurally well-formed; it's just incompatible with this particular platform.
    Use :class:`ValidationError` for construction-time bus issues.
    """


class WaveformResolutionError(QProgramError):
    """Platform-side: a string waveform alias remained unresolved at execution time.

    Typically the user forgot to wire the alias through :meth:`QProgram.with_waveforms` or the
    calibration library handing waveforms to the platform omitted it.
    """


class CompilationError(QProgramError):
    """Platform-side: the lowered representation failed to compile.

    Catch-all for backend-internal failures that don't fit the other platform errors (timing
    constraints, resource over-allocation, code-generation bugs).
    """


class HardwareError(QProgramError):
    """Platform-side: an instrument-level runtime failure surfaced during execution.

    Covers driver errors, SCPI failures, lost trigger pulses, and anything else surfacing *during*
    execution rather than at compile or validate time.
    """
