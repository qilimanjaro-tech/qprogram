"""Symbolic expression system for QProgram.

This module defines the AST for symbolic expressions: variables, constants, and
arithmetic operations. Anywhere QProgram accepts a numeric value, it also accepts
an ``Expression`` — meaning the value can be a literal, a variable bound at runtime,
or any expression composed from them.

Design (standard AST pattern):

- ``Expression`` — abstract base. Anything usable where a number is expected.
- ``Variable`` — leaf node. Symbolic placeholder that **holds a value**.
  Initially ``UNASSIGNED``; set via ``set_value()``.
- ``Constant`` — leaf node. A concrete numeric value.
- ``BinaryOp`` — internal node. Operator (``+``, ``-``, ``*``, ``/``) over two expressions.
- ``UnaryOp`` — internal node. Unary operator (``-``, ``+``) over one expression.

Evaluation
----------
``evaluate()`` takes **no arguments**. Each ``Variable`` carries its own current
value. The evaluator walks the tree, reads ``Variable.value`` as needed, and
propagates the ``UNASSIGNED`` sentinel if any variable is unbound::

    freq = Variable("freq")
    expr = freq * 2 + 100

    expr.evaluate()  # -> UNASSIGNED  (freq has no value)
    freq.set_value(50)
    expr.evaluate()  # -> 200
    freq.reset()
    expr.evaluate()  # -> UNASSIGNED again

The runtime executor sets variable values per loop iteration; expressions can
then be evaluated naturally without threading bindings through every call.

Use the ``resolve()`` helper to coerce ``int | float | Expression`` to a
concrete numeric value (or raise if any variable is unassigned).

Variables use **identity-based** equality (each ``Variable("freq")`` is distinct).
Other nodes use **structural** equality (two ``Constant(5)`` are equal; two
structurally identical ``BinaryOp`` are equal).
"""

from __future__ import annotations

import itertools
import re
from abc import ABC, abstractmethod
from typing import Final, Literal, Self

# Valid variable labels: Python-style identifiers — letter/underscore start,
# then letters/digits/underscores. Labels are used verbatim as identifiers in
# the .qp file format, so they must be safe to embed without quoting.
_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InvalidVariableLabelError(ValueError):
    """Raised when a Variable label does not match ``[A-Za-z_][A-Za-z0-9_]*``."""

    def __init__(self, label: str) -> None:
        super().__init__(
            f"Variable label {label!r} is invalid: must match [A-Za-z_][A-Za-z0-9_]* "
            f"(letters, digits, underscores only; cannot start with a digit, no spaces "
            f"or special characters). Use the optional `long_name` for human-readable names."
        )
        self.label = label

# Operators supported by BinaryOp and UnaryOp.
type BinaryOperator = Literal["+", "-", "*", "/"]
type UnaryOperator = Literal["-", "+"]


# ----------------------------------------------------------------------------
# UNASSIGNED sentinel — represents a variable with no value yet
# ----------------------------------------------------------------------------


class _UnassignedType:
    """Type of the ``UNASSIGNED`` sentinel. Singleton; do not instantiate directly."""

    _instance: _UnassignedType | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance: _UnassignedType = super().__new__(cls)
        return cls._instance  # ty:ignore[invalid-return-type]

    def __repr__(self) -> str:
        return "UNASSIGNED"

    def __bool__(self) -> bool:
        return False


UNASSIGNED: Final[_UnassignedType] = _UnassignedType()
"""Sentinel returned by ``evaluate()`` when a variable in the expression has no value."""


class UnassignedVariableError(ValueError):
    """Raised by ``evaluate_or_raise()`` when an expression contains an unassigned variable.

    Subclasses ``ValueError`` for compatibility with existing exception-handling code.
    """

    def __init__(self, expression: Expression) -> None:
        free = expression.variables()
        super().__init__(
            f"Cannot evaluate expression {expression!r}: unassigned variable(s) {free!r}",
        )
        self.expression = expression
        self.free_variables = free


# ----------------------------------------------------------------------------
# Expression: abstract base
# ----------------------------------------------------------------------------


class Expression(ABC):
    """Base class for the symbolic expression AST.

    Subclasses (``Variable``, ``Constant``, ``BinaryOp``, ``UnaryOp``) implement
    ``evaluate()`` and ``variables()``. The arithmetic operators are defined here
    so they work uniformly across all expression types.
    """

    @abstractmethod
    def evaluate(self) -> int | float | _UnassignedType:
        """Compute the numeric value of the expression.

        Returns the numeric result, or ``UNASSIGNED`` if any variable in the
        expression is currently unassigned.
        """
        ...

    def evaluate_or_raise(self) -> int | float:
        """Compute the numeric value of the expression.

        Like :meth:`evaluate`, but raises :class:`UnassignedVariableError` instead
        of returning the ``UNASSIGNED`` sentinel. Useful when downstream code
        requires a concrete number (e.g. waveform envelope computation).
        """
        result = self.evaluate()
        if isinstance(result, _UnassignedType):
            raise UnassignedVariableError(self)
        return result

    @abstractmethod
    def variables(self) -> set[Variable]:
        """Return the set of free variables that appear in this expression."""
        ...

    # --- arithmetic operators ---

    def __add__(self, other: Expression | float) -> BinaryOp:
        return BinaryOp("+", self, _wrap(other))

    def __radd__(self, other: float) -> BinaryOp:
        return BinaryOp("+", _wrap(other), self)

    def __sub__(self, other: Expression | float) -> BinaryOp:
        return BinaryOp("-", self, _wrap(other))

    def __rsub__(self, other: float) -> BinaryOp:
        return BinaryOp("-", _wrap(other), self)

    def __mul__(self, other: Expression | float) -> BinaryOp:
        return BinaryOp("*", self, _wrap(other))

    def __rmul__(self, other: float) -> BinaryOp:
        return BinaryOp("*", _wrap(other), self)

    def __truediv__(self, other: Expression | float) -> BinaryOp:
        return BinaryOp("/", self, _wrap(other))

    def __rtruediv__(self, other: float) -> BinaryOp:
        return BinaryOp("/", _wrap(other), self)

    def __neg__(self) -> UnaryOp:
        return UnaryOp("-", self)

    def __pos__(self) -> UnaryOp:
        return UnaryOp("+", self)


# ----------------------------------------------------------------------------
# Leaves
# ----------------------------------------------------------------------------


class Variable(Expression):
    """A symbolic variable. Leaf node that holds a value (initially ``UNASSIGNED``).

    Identity-based equality: each ``Variable(label)`` is a distinct instance.
    Each variable gets an auto-assigned integer ID for hashing.

    The runtime executor sets the value via ``set_value()`` per loop iteration.
    Reading ``value`` (or calling ``evaluate()``) returns the current value or
    ``UNASSIGNED`` if no value has been set.

    Args:
        label: Mandatory short name. Must match ``[A-Za-z_][A-Za-z0-9_]*`` —
            it doubles as the identifier in the ``.qp`` file format and so
            cannot contain spaces or punctuation.
        long_name: Optional human-readable name (free-form string). Use this
            for axis labels, plot titles, etc.
        units: Optional unit string (e.g. ``"Hz"``, ``"ns"``, ``"V"``).
        description: Optional longer description.
    """

    _id_counter = itertools.count()

    def __init__(
        self,
        label: str,
        *,
        long_name: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> None:
        if not _LABEL_RE.match(label):
            raise InvalidVariableLabelError(label)
        self._id: int = next(Variable._id_counter)
        self._label: str = label
        self._long_name: str | None = long_name
        self._units: str | None = units
        self._description: str | None = description
        self._value: int | float | _UnassignedType = UNASSIGNED

    @property
    def id(self) -> int:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    @property
    def long_name(self) -> str | None:
        return self._long_name

    @property
    def units(self) -> str | None:
        return self._units

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def value(self) -> int | float | _UnassignedType:
        """The current value, or ``UNASSIGNED`` if no value has been set."""
        return self._value

    def set_value(self, value: float) -> None:
        """Set the variable's current value."""
        self._value = value

    def reset(self) -> None:
        """Clear the variable's value, returning it to ``UNASSIGNED``."""
        self._value = UNASSIGNED

    def evaluate(self) -> int | float | _UnassignedType:
        return self._value

    def variables(self) -> set[Variable]:
        return {self}

    def __hash__(self) -> int:
        return hash(self._id)

    def __eq__(self, other: object) -> bool:
        # Identity-based: two Variables are equal iff they are the same instance.
        return self is other

    def __repr__(self) -> str:
        return f"Variable('{self._label}')"


class Constant(Expression):
    """A concrete numeric value. Leaf node.

    Constants have **structural** equality: ``Constant(5) == Constant(5)``.
    Literals appearing in expressions are auto-wrapped to ``Constant`` by the
    arithmetic operators.
    """

    def __init__(self, value: float) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            msg = f"Constant value must be int or float, got {type(value).__name__}"
            raise TypeError(msg)
        self.value: int | float = value

    def evaluate(self) -> int | float:
        return self.value

    def variables(self) -> set[Variable]:
        return set()

    def __hash__(self) -> int:
        return hash(("Constant", self.value))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Constant) and self.value == other.value

    def __repr__(self) -> str:
        return f"Constant({self.value})"


# ----------------------------------------------------------------------------
# Internal nodes
# ----------------------------------------------------------------------------


class BinaryOp(Expression):
    """Binary operation node: ``left op right`` where ``op`` is +, -, *, or /.

    Constructed via the arithmetic operators on Expression. Both operands are
    always Expression instances — literals are auto-wrapped to ``Constant``.
    """

    def __init__(self, op: BinaryOperator, left: Expression, right: Expression) -> None:
        self.op: BinaryOperator = op
        self.left: Expression = left
        self.right: Expression = right

    def evaluate(self) -> int | float | _UnassignedType:
        left_value = self.left.evaluate()
        if isinstance(left_value, _UnassignedType):
            return UNASSIGNED
        right_value = self.right.evaluate()
        if isinstance(right_value, _UnassignedType):
            return UNASSIGNED
        if self.op == "+":
            return left_value + right_value
        if self.op == "-":
            return left_value - right_value
        if self.op == "*":
            return left_value * right_value
        return left_value / right_value

    def variables(self) -> set[Variable]:
        return self.left.variables() | self.right.variables()

    def __hash__(self) -> int:
        return hash(("BinaryOp", self.op, self.left, self.right))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, BinaryOp)
            and self.op == other.op
            and self.left == other.left
            and self.right == other.right
        )

    def __repr__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


class UnaryOp(Expression):
    """Unary operation node: ``op operand`` where ``op`` is ``-`` or ``+``."""

    def __init__(self, op: UnaryOperator, operand: Expression) -> None:
        self.op: UnaryOperator = op
        self.operand: Expression = operand

    def evaluate(self) -> int | float | _UnassignedType:
        operand_value = self.operand.evaluate()
        if isinstance(operand_value, _UnassignedType):
            return UNASSIGNED
        return -operand_value if self.op == "-" else +operand_value

    def variables(self) -> set[Variable]:
        return self.operand.variables()

    def __hash__(self) -> int:
        return hash(("UnaryOp", self.op, self.operand))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UnaryOp) and self.op == other.op and self.operand == other.operand

    def __repr__(self) -> str:
        return f"({self.op}{self.operand})"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _wrap(x: Expression | float) -> Expression:
    """Wrap a literal int/float as a Constant; pass Expressions through."""
    if isinstance(x, Expression):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return Constant(x)
    msg = f"Cannot use {type(x).__name__} in an Expression; expected Expression, int, or float"
    raise TypeError(msg)
