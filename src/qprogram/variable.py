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
"""Symbolic expression system for QProgram.

Anywhere QProgram accepts a numeric value, it also accepts an [`Expression`][qprogram.Expression] — a literal, a
variable bound at runtime, or any composition of them. [`Expression.evaluate`][qprogram.Expression.evaluate] takes no
arguments: each [`Variable`][qprogram.Variable] carries its own current value, set by the runtime per loop
iteration. The `UNASSIGNED` sentinel propagates upward when any variable is unbound.

Equality across the AST is structural. For [`Variable`][qprogram.Variable] this means by ``id`` — two variables
compare equal iff their ``id`` matches, which is what lets a program survive ``deepcopy`` /
``qp.loads(qp.dumps(...))`` and still compare equal to the original.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Self, TypeAlias

from qprogram._reserved import RESERVED_KEYWORDS

# Imported both for use below and so that ``from qprogram.variable import InvalidVariableIdError``
# resolves.
from qprogram.errors import InvalidVariableIdError, UnassignedVariableError

if TYPE_CHECKING:
    from qprogram.result import MeasurementHandle

# Variable ids are Python-style identifiers — they're used verbatim as tokens in the .qp file format
# and must be safe to embed without quoting.
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

BinaryOperator: TypeAlias = Literal["+", "-", "*", "/"]
UnaryOperator: TypeAlias = Literal["-", "+"]
ComparisonOperator: TypeAlias = Literal["==", "!=", "<", "<=", ">", ">="]
LogicalBinaryOperator: TypeAlias = Literal["and", "or"]

# A runtime set rather than a Literal type: the function name also arrives as a string from the ``.qp``
# parser, which needs a value it can test membership against.
_MATH_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {"sin", "cos", "tan", "exp", "log", "sqrt", "abs", "minimum", "maximum"},
)


# ----------------------------------------------------------------------------
# UNASSIGNED sentinel — represents a variable with no value yet
# ----------------------------------------------------------------------------


class _UnassignedType:
    """Type of the ``UNASSIGNED`` sentinel.

    A singleton: constructing the class returns the shared `UNASSIGNED` instance rather than a
    new object, so ``is`` comparisons against the sentinel are sound.
    """

    _instance: _UnassignedType | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            # No inline annotation here — the class-level ``_instance`` declaration above already
            # types it, and annotating an attribute assignment is rejected by the type checker.
            cls._instance = super().__new__(cls)
        return cls._instance  # ty:ignore[invalid-return-type]

    def __repr__(self) -> str:
        return "UNASSIGNED"

    def __bool__(self) -> bool:
        return False


UNASSIGNED: Final[_UnassignedType] = _UnassignedType()
"""Sentinel returned by ``evaluate()`` when a variable in the expression has no value."""


# ----------------------------------------------------------------------------
# Expression: abstract base
# ----------------------------------------------------------------------------


class Expression(ABC):
    """Abstract base for symbolic expressions.

    Subclasses split into four families:

    - **Leaves** — [`Variable`][qprogram.Variable], [`Constant`][qprogram.Constant],
      [`MeasurementRef`][qprogram.MeasurementRef].
    - **Arithmetic** — [`BinaryOp`][qprogram.BinaryOp] (``+ - * /``), [`UnaryOp`][qprogram.UnaryOp] (``- +``).
    - **Comparison & logical** — [`Comparison`][qprogram.Comparison], [`LogicalBinaryOp`][qprogram.LogicalBinaryOp],
      [`LogicalNot`][qprogram.LogicalNot].
    - **Math & conditional** — [`MathFunc`][qprogram.MathFunc], [`Where`][qprogram.Where].

    Operators that map cleanly onto Python syntax build the corresponding AST nodes uniformly across
    every subclass. The exceptions are ``==`` / ``!=`` (use `eq` / `ne` so [`Variable`][qprogram.Variable]
    can keep a plain-bool ``__eq__`` for set / dict-key use) and ``and`` / ``or`` / ``not`` (Python
    keywords, unoverloadable — use ``& | ~`` or the named helpers `and_` / `or_` /
    `not_`).

    Why ``__bool__`` raises: ``if freq < 5e9:`` would otherwise silently test the truthiness of the
    [`Comparison`][qprogram.Comparison] instance (always true) instead of building the conditional the user almost
    certainly meant. Mirrors SymPy.
    """

    @abstractmethod
    def evaluate(self) -> int | float | _UnassignedType:
        """Compute the value of the expression.

        Returns:
            A numeric result for arithmetic / math expressions, a ``bool`` for comparisons and logical
            expressions (``bool`` is an ``int`` subclass), or `UNASSIGNED` when any referenced
            variable is currently unassigned.
        """
        ...

    def evaluate_or_raise(self) -> int | float:
        """Compute the value of the expression, raising instead of returning `UNASSIGNED`.

        Returns:
            The numeric value.

        Raises:
            UnassignedVariableError: When any referenced variable is unassigned. Use this when the
                caller (e.g. waveform envelope computation) requires a concrete value.
            ZeroDivisionError: When the expression divides by an operand that evaluates to zero.
        """
        result = self.evaluate()
        if isinstance(result, _UnassignedType):
            raise UnassignedVariableError(self)
        return result

    @abstractmethod
    def variables(self) -> set[Variable]:
        """Return the free [`Variable`][qprogram.Variable] s appearing in this expression.

        Every node unions the sets reported by its children, so one call covers the whole subtree.

        Returns:
            The variables reachable from this node; an empty set for leaves that hold none.
        """
        ...

    def __bool__(self) -> bool:
        msg = (
            "Expression has no truth value — use .evaluate()/.evaluate_or_raise() "
            "to compute it, or qprogram.where(cond, then, else_) to build a "
            "conditional expression."
        )
        raise TypeError(msg)

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

    # Comparison nodes.
    # ``==`` / ``!=`` are NOT overloaded: Variable's __eq__ must return bool so Variables can live in
    # sets (notably ``expression.variables()``) and dict keys. Users build equality comparisons via the
    # named helpers ``qprogram.eq`` / ``qprogram.ne``.

    def __lt__(self, other: Expression | float) -> Comparison:
        return Comparison("<", self, _wrap(other))

    def __le__(self, other: Expression | float) -> Comparison:
        return Comparison("<=", self, _wrap(other))

    def __gt__(self, other: Expression | float) -> Comparison:
        return Comparison(">", self, _wrap(other))

    def __ge__(self, other: Expression | float) -> Comparison:
        return Comparison(">=", self, _wrap(other))

    # Logical operators: NumPy/SymPy convention ``& | ~`` because ``and`` / ``or`` / ``not`` are
    # unoverloadable Python keywords. Precedence gotcha: ``& |`` bind tighter than ``< == >``, so users
    # must parenthesize comparisons (``(freq < 5e9) & (gain > 0.5)``). Both operands must be Expression
    # — supporting bool literals would conflict with Constant's numeric-only contract.

    def __and__(self, other: Expression) -> LogicalBinaryOp:
        # Defensive runtime check: Python operators dispatch dynamically and bad operands at the binding
        # site beat a confusing failure later in the AST.
        if not isinstance(other, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
            return NotImplemented
        return LogicalBinaryOp("and", self, other)

    def __or__(self, other: Expression) -> LogicalBinaryOp:
        if not isinstance(other, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
            return NotImplemented
        return LogicalBinaryOp("or", self, other)

    def __invert__(self) -> LogicalNot:
        return LogicalNot(self)

    def __abs__(self) -> MathFunc:
        return MathFunc("abs", (self,))


# ----------------------------------------------------------------------------
# Leaves
# ----------------------------------------------------------------------------


class Variable(Expression):
    """A symbolic variable.

    Leaf node of the expression AST, holding a value that starts out `UNASSIGNED`.

    Equality and hashing are structural over ``id``: two variables compare equal when their ``id`` matches. Within one
    program ids are unique ([`QProgram.variable`][qprogram.QProgram.variable] rejects duplicates), so id-equality
    coincides with identity there; across programs it is what lets a program survive ``deepcopy`` /
    ``qp.loads(qp.dumps(...))`` and still compare equal to the original.

    The runtime sets the value via `set_value` per loop iteration; reading `value` (or
    calling `evaluate`) returns the current value or `UNASSIGNED`.

    Args:
        id (str): Short name matching ``[A-Za-z_][A-Za-z0-9_]*``. Doubles as the identifier in the
            ``.qp`` format, so no spaces or punctuation.
        label (str | None): Human-readable name for axis labels, plot titles, and the like.
        units (str | None): Unit string, such as ``"Hz"``, ``"ns"``, or ``"V"``.
        description (str | None): Longer free-form description.

    Raises:
        InvalidVariableIdError: If ``id`` violates the pattern or is reserved
            (see [`RESERVED_KEYWORDS`][qprogram.RESERVED_KEYWORDS]).
    """

    def __init__(
        self,
        id: str,  # ruff: ignore[builtin-argument-shadowing]
        *,
        label: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> None:
        if not _ID_RE.match(id):
            raise InvalidVariableIdError(id)
        if id in RESERVED_KEYWORDS:
            raise InvalidVariableIdError(id, reserved=True)
        self._id: str = id
        self._label: str | None = label
        self._units: str | None = units
        self._description: str | None = description
        self._value: int | float | _UnassignedType = UNASSIGNED

    @property
    def id(self) -> str:
        """The identifier, emitted verbatim as the variable's token in the ``.qp`` format."""
        return self._id

    @property
    def label(self) -> str | None:
        """The human-readable name for axis labels and plot titles, or ``None``."""
        return self._label

    @property
    def units(self) -> str | None:
        """The unit the variable's values are expressed in, or ``None``."""
        return self._units

    @property
    def description(self) -> str | None:
        """The free-form description, or ``None``."""
        return self._description

    @property
    def value(self) -> int | float | _UnassignedType:
        """The current value, or `UNASSIGNED` if no value has been set."""
        return self._value

    def set_value(self, value: float) -> None:
        """Set the variable's current value.

        Args:
            value (float): The value to bind. The runtime writes it once per loop iteration.
        """
        self._value = value

    def reset(self) -> None:
        """Clear the variable's value, returning it to `UNASSIGNED`."""
        self._value = UNASSIGNED

    def evaluate(self) -> int | float | _UnassignedType:
        """Return the variable's current value.

        Returns:
            The bound value, or `UNASSIGNED` while nothing is bound.
        """
        return self._value

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            A single-element set holding this variable.
        """
        return {self}

    def __hash__(self) -> int:
        return hash(("Variable", self._id))

    def __eq__(self, other: object) -> bool:
        # Structural by id. Within one QProgram, ids are unique (``QProgram.variable`` rejects
        # duplicates), so id-equality ≡ identity. Across programs (deepcopy, .qp load), id-equality
        # is what lets whole-program structural comparison work. The runtime reads ``self._value`` on
        # the instance directly, so equality never interferes with per-iteration value writes.
        return isinstance(other, Variable) and self._id == other._id

    def __repr__(self) -> str:
        return f"Variable('{self._id}')"


class MeasurementRef(Expression):
    """Reference to a field of a measurement result, used inside conditional expressions.

    Built implicitly by the proxy that `MeasurementHandle.state` returns when the user writes
    ``handle.state == 0``; direct construction is rarely needed.

    Equality is structural over ``(handle.name, field)``: distinct instances built at different sites
    refer to the same logical reference whenever their names match. Mirrors [`Variable`][qprogram.Variable]'s
    cross-program equality story so programs survive ``deepcopy`` / ``qp.loads(qp.dumps(...))``.

    Args:
        handle (MeasurementHandle): The producing measurement's
            [`MeasurementHandle`][qprogram.MeasurementHandle].
        field (str): Field name. ``"state"`` is the only accepted value.

    Raises:
        ValueError: If ``field`` is not in the allowed set.
    """

    # A deliberately narrower set than `MeasurementField`: that enum is what a
    # measurement may *produce*, this is what a condition may *branch on*. Only a classified scalar
    # qualifies, so ``"raw"`` and ``"iq"`` are excluded by design, not by omission. Spelled as a
    # plain string because `qprogram.operations.operation` (where the enum lives) imports this
    # module — the dependency only runs one way.
    _ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset({"state"})

    def __init__(self, handle: MeasurementHandle, field: str) -> None:
        if field not in self._ALLOWED_FIELDS:
            msg = f"MeasurementRef field must be one of {sorted(self._ALLOWED_FIELDS)}, got {field!r}"
            raise ValueError(msg)
        self.handle: MeasurementHandle = handle
        self.field: str = field

    def evaluate(self) -> int | float | _UnassignedType:
        """Return the current value of the referenced measurement field.

        Returns:
            The value the runtime recorded on the handle for this field, or `UNASSIGNED` before
            the measurement has produced one.
        """
        return self.handle._value_for(self.field)  # ruff: ignore[private-member-access]

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        A measurement reference is its own binding kind, distinct from [`Variable`][qprogram.Variable], so it takes
        no part in the loop-counter variable walk the rest of the AST feeds.

        Returns:
            An empty set.
        """
        return set()

    def __hash__(self) -> int:
        return hash(("MeasurementRef", self.handle.name, self.field))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MeasurementRef) and self.handle.name == other.handle.name and self.field == other.field

    def __repr__(self) -> str:
        return f"MeasurementRef({self.handle.name!r}, {self.field!r})"


class _HandleFieldAccess:
    """Throwaway proxy returned by `MeasurementHandle.state`.

    Why it exists: ``handle.state == 0`` needs to build a [`Comparison`][qprogram.Comparison], but overloading ``==`` on
    [`MeasurementRef`][qprogram.MeasurementRef] itself would clash with the AST's structural equality walk (which would
    try to evaluate ``bool(Comparison)`` and trip `Expression.__bool__`). The proxy is consumed immediately by the
    comparison and never stored on the AST.

    ``==`` and ``!=`` accept ``int`` literals, another ``_HandleFieldAccess`` (for
    ``m1.state == m2.state``), or a concrete [`MeasurementRef`][qprogram.MeasurementRef].

    Hashing is disabled because ``==`` builds a [`Comparison`][qprogram.Comparison] rather than returning a ``bool``,
    which is not the equality a hash-based container needs; the proxy therefore cannot be a set member
    or a dict key. Holding one instead of consuming it fails just as loudly:
    ``s = handle.state; s == s`` yields a [`Comparison`][qprogram.Comparison] whose ``bool`` raises.

    Args:
        handle (MeasurementHandle): The measurement whose field the comparison refers to.
        field (str): Name of the field being accessed, in practice ``"state"``. The proxy stores it
            as given; it is validated when the comparison resolves it to a [`MeasurementRef`][qprogram.MeasurementRef].
    """

    __slots__ = ("_field", "_handle")

    def __init__(self, handle: MeasurementHandle, field: str) -> None:
        self._handle = handle
        self._field = field

    def _as_ref(self) -> MeasurementRef:
        return MeasurementRef(self._handle, self._field)

    def _comparison(self, op: ComparisonOperator, other: object) -> Comparison:
        if isinstance(other, bool):
            msg = f"handle.{self._field} cannot be compared to a bool; use 0 or 1 to compare against a classified state"
            raise TypeError(msg)
        if isinstance(other, int):
            return Comparison(op, self._as_ref(), Constant(other))
        if isinstance(other, _HandleFieldAccess):
            return Comparison(op, self._as_ref(), other._as_ref())  # ruff: ignore[private-member-access]
        if isinstance(other, MeasurementRef):
            return Comparison(op, self._as_ref(), other)
        msg = (
            f"handle.{self._field} can only be compared to int, another "
            f"handle.<field>, or a MeasurementRef; got "
            f"{type(other).__name__}"
        )
        raise TypeError(msg)

    # Return type is annotated Any rather than Comparison because object.__eq__ returns bool and the
    # override must be LSP-compatible. At run time ``handle.state == 1`` builds a Comparison. Any is the
    # standard escape hatch the typing community settled on for this DSL pattern (cf. SQLAlchemy
    # columns, numpy arrays). The ``any-type`` ignore is targeted at this contract; flagging ``Any``
    # elsewhere stays valuable.
    def __eq__(self, other: object) -> Any:  # ruff: ignore[any-type]
        return self._comparison("==", other)

    def __ne__(self, other: object) -> Any:  # ruff: ignore[any-type]
        return self._comparison("!=", other)

    __hash__ = None

    def __repr__(self) -> str:
        return f"_HandleFieldAccess({self._handle.name!r}, {self._field!r})"


class Constant(Expression):
    """Concrete numeric leaf with structural equality (``Constant(5) == Constant(5)``).

    Numeric literals appearing in expressions are auto-wrapped to [`Constant`][qprogram.Constant] by the operator
    methods on [`Expression`][qprogram.Expression].

    Args:
        value (float): The number to hold; an ``int`` is kept as an ``int``. ``bool`` is rejected —
            booleans would silently coerce to 0/1 and obscure intent.

    Raises:
        TypeError: If ``value`` is not an ``int`` or ``float`` (or is a ``bool``).
    """

    def __init__(self, value: float) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            msg = f"Constant value must be int or float, got {type(value).__name__}"
            raise TypeError(msg)
        self.value: int | float = value

    def evaluate(self) -> int | float:
        """Return the constant's value.

        Returns:
            The stored number. A constant is always bound, so this is never `UNASSIGNED`.
        """
        return self.value

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            An empty set.
        """
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
    """Binary arithmetic node — ``left op right`` for one of ``+ - * /``.

    Constructed by the arithmetic operators on [`Expression`][qprogram.Expression]. Numeric literals are auto-wrapped
    to [`Constant`][qprogram.Constant], so both operands are always [`Expression`][qprogram.Expression] instances.

    Args:
        op (BinaryOperator): Operator symbol — one of ``+``, ``-``, ``*``, ``/``.
        left (Expression): Left operand.
        right (Expression): Right operand.
    """

    def __init__(self, op: BinaryOperator, left: Expression, right: Expression) -> None:
        self.op: BinaryOperator = op
        self.left: Expression = left
        self.right: Expression = right

    def evaluate(self) -> int | float | _UnassignedType:
        """Apply the arithmetic operator to both operands.

        Returns:
            The numeric result, or `UNASSIGNED` when either operand is unassigned.

        Raises:
            ZeroDivisionError: If the operator is ``/`` and the right operand evaluates to zero.
        """
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
        """Return the free variables in this expression.

        Returns:
            The union of both operands' variables.
        """
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
    """Unary arithmetic node — ``op operand`` for one of ``- +``.

    Args:
        op (UnaryOperator): Operator symbol — ``-`` or ``+``.
        operand (Expression): The operand.
    """

    def __init__(self, op: UnaryOperator, operand: Expression) -> None:
        self.op: UnaryOperator = op
        self.operand: Expression = operand

    def evaluate(self) -> int | float | _UnassignedType:
        """Apply the sign operator to the operand.

        Returns:
            The negated value for ``-`` and the operand's own value for ``+``, or `UNASSIGNED`
            when the operand is unassigned.
        """
        operand_value = self.operand.evaluate()
        if isinstance(operand_value, _UnassignedType):
            return UNASSIGNED
        return -operand_value if self.op == "-" else +operand_value

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The operand's variables.
        """
        return self.operand.variables()

    def __hash__(self) -> int:
        return hash(("UnaryOp", self.op, self.operand))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UnaryOp) and self.op == other.op and self.operand == other.operand

    def __repr__(self) -> str:
        return f"({self.op}{self.operand})"


# ----------------------------------------------------------------------------
# Comparison and logical nodes
# ----------------------------------------------------------------------------


class Comparison(Expression):
    """Binary comparison node — ``left op right`` for one of ``== != < <= > >=``.

    Constructed by the comparison operators on [`Expression`][qprogram.Expression] (``< <= > >=``) and by the helper
    functions `eq` / `ne` (``== !=``; see [`Expression`][qprogram.Expression] for why these are not
    overloaded directly).

    `evaluate` returns ``bool`` (an ``int`` subclass) or `UNASSIGNED` if either operand is
    unassigned.

    Args:
        op (ComparisonOperator): Operator symbol — one of ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``.
        left (Expression): Left operand.
        right (Expression): Right operand.

    Raises:
        ValueError: If ``op`` is not a recognized comparison operator (defensive — callers should pass
            a Literal).
    """

    _OPS: ClassVar[frozenset[str]] = frozenset({"==", "!=", "<", "<=", ">", ">="})

    def __init__(self, op: ComparisonOperator, left: Expression, right: Expression) -> None:
        if op not in self._OPS:  # defensive: callers should pass a Literal
            msg = f"Comparison op must be one of {sorted(self._OPS)}, got {op!r}"
            raise ValueError(msg)
        self.op: ComparisonOperator = op
        self.left: Expression = left
        self.right: Expression = right

    def evaluate(self) -> bool | _UnassignedType:
        """Compare the two operands.

        Returns:
            The boolean outcome of the comparison, or `UNASSIGNED` when either operand is
            unassigned.
        """
        left_value = self.left.evaluate()
        if isinstance(left_value, _UnassignedType):
            return UNASSIGNED
        right_value = self.right.evaluate()
        if isinstance(right_value, _UnassignedType):
            return UNASSIGNED
        if self.op == "==":
            return left_value == right_value
        if self.op == "!=":
            return left_value != right_value
        if self.op == "<":
            return left_value < right_value
        if self.op == "<=":
            return left_value <= right_value
        if self.op == ">":
            return left_value > right_value
        return left_value >= right_value

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The union of both operands' variables.
        """
        return self.left.variables() | self.right.variables()

    def __hash__(self) -> int:
        return hash(("Comparison", self.op, self.left, self.right))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Comparison)
            and self.op == other.op
            and self.left == other.left
            and self.right == other.right
        )

    def __repr__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


class LogicalBinaryOp(Expression):
    """Binary logical node — ``left op right`` for ``and`` or ``or``.

    Constructed by ``&`` and ``|`` on [`Expression`][qprogram.Expression], or by the named helpers `and_` /
    `or_`.

    Why this never short-circuits: any `UNASSIGNED` operand propagates upwards. Matches the rest
    of the evaluator and makes unbound-variable diagnostics deterministic rather than position-dependent.

    Args:
        op (LogicalBinaryOperator): Operator symbol — ``and`` or ``or``.
        left (Expression): Left operand.
        right (Expression): Right operand.

    Raises:
        ValueError: If ``op`` is not a recognized logical operator (defensive).
        TypeError: If either operand is not an [`Expression`][qprogram.Expression].
    """

    _OPS: ClassVar[frozenset[str]] = frozenset({"and", "or"})

    def __init__(self, op: LogicalBinaryOperator, left: Expression, right: Expression) -> None:
        if op not in self._OPS:  # defensive
            msg = f"LogicalBinaryOp op must be one of {sorted(self._OPS)}, got {op!r}"
            raise ValueError(msg)
        _require_expression(left, where="LogicalBinaryOp left operand")
        _require_expression(right, where="LogicalBinaryOp right operand")
        self.op: LogicalBinaryOperator = op
        self.left: Expression = left
        self.right: Expression = right

    def evaluate(self) -> bool | _UnassignedType:
        """Combine the truthiness of both operands.

        Returns:
            The boolean outcome of ``and`` / ``or``, or `UNASSIGNED` when either operand is
            unassigned. Both operands are always evaluated.
        """
        left_value = self.left.evaluate()
        if isinstance(left_value, _UnassignedType):
            return UNASSIGNED
        right_value = self.right.evaluate()
        if isinstance(right_value, _UnassignedType):
            return UNASSIGNED
        if self.op == "and":
            return bool(left_value) and bool(right_value)
        return bool(left_value) or bool(right_value)

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The union of both operands' variables.
        """
        return self.left.variables() | self.right.variables()

    def __hash__(self) -> int:
        return hash(("LogicalBinaryOp", self.op, self.left, self.right))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, LogicalBinaryOp)
            and self.op == other.op
            and self.left == other.left
            and self.right == other.right
        )

    def __repr__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


class LogicalNot(Expression):
    """Logical negation node — ``not operand``.

    Constructed by ``~`` on [`Expression`][qprogram.Expression] or by `not_`.

    Args:
        operand (Expression): The expression to negate.

    Raises:
        TypeError: If ``operand`` is not an [`Expression`][qprogram.Expression].
    """

    def __init__(self, operand: Expression) -> None:
        _require_expression(operand, where="LogicalNot operand")
        self.operand: Expression = operand

    def evaluate(self) -> bool | _UnassignedType:
        """Negate the truthiness of the operand.

        Returns:
            ``True`` when the operand is falsy and ``False`` when it is truthy, or `UNASSIGNED`
            when the operand is unassigned.
        """
        operand_value = self.operand.evaluate()
        if isinstance(operand_value, _UnassignedType):
            return UNASSIGNED
        return not bool(operand_value)

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The operand's variables.
        """
        return self.operand.variables()

    def __hash__(self) -> int:
        return hash(("LogicalNot", self.operand))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LogicalNot) and self.operand == other.operand

    def __repr__(self) -> str:
        return f"(not {self.operand})"


# ----------------------------------------------------------------------------
# Math functions and conditional
# ----------------------------------------------------------------------------


class MathFunc(Expression):
    """Math-function application — ``name(arg1, arg2, ...)``.

    The function name selects the numeric implementation. Recognized names are listed in
    `_MATH_FUNCTIONS`. Each is dispatched via the lazy lookup in `_math_eval`, keeping
    this module free of a direct ``numpy``/``math`` import at the AST layer.

    Arity is not enforced beyond requiring at least one operand: ``minimum`` and ``maximum`` fold
    every operand, and every other function reads the first and ignores the rest. The module-level
    builders (`sin`, `minimum`, ...) are the arity-checked way in.

    Args:
        name (str): Function name, such as ``"sin"`` or ``"minimum"``.
        operands (tuple[Expression, ...]): Operand expressions.

    Raises:
        ValueError: If ``name`` is not a recognized math function, or if ``operands`` is empty.
    """

    def __init__(self, name: str, operands: tuple[Expression, ...]) -> None:
        if name not in _MATH_FUNCTIONS:
            msg = f"Unknown math function {name!r}; known: {sorted(_MATH_FUNCTIONS)}"
            raise ValueError(msg)
        if not operands:
            msg = f"Math function {name!r} requires at least one operand"
            raise ValueError(msg)
        self.name: str = name
        self.operands: tuple[Expression, ...] = operands

    def evaluate(self) -> int | float | _UnassignedType:
        """Evaluate every operand and apply the named function.

        Returns:
            The numeric result, or `UNASSIGNED` as soon as any operand is unassigned.
        """
        values: list[int | float] = []
        for op in self.operands:
            v = op.evaluate()
            if isinstance(v, _UnassignedType):
                return UNASSIGNED
            values.append(v)
        return _math_eval(self.name, values)

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The union of every operand's variables.
        """
        out: set[Variable] = set()
        for op in self.operands:
            out |= op.variables()
        return out

    def __hash__(self) -> int:
        return hash(("MathFunc", self.name, self.operands))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MathFunc) and self.name == other.name and self.operands == other.operands

    def __repr__(self) -> str:
        args = ", ".join(repr(op) for op in self.operands)
        return f"{self.name}({args})"


class Where(Expression):
    """Ternary conditional expression — ``where(condition, then, else_)``.

    Short-circuits: the unchosen branch is not evaluated, so it may safely reference unassigned
    variables. If ``condition`` itself is unassigned the whole expression evaluates to
    `UNASSIGNED`.

    Users normally construct via the `where` helper.

    Args:
        condition (Expression): The predicate expression.
        then (Expression): Returned when ``condition`` evaluates to truthy.
        else_ (Expression): Returned when ``condition`` evaluates to falsy.

    Raises:
        TypeError: If any argument is not an [`Expression`][qprogram.Expression].
    """

    def __init__(self, condition: Expression, then: Expression, else_: Expression) -> None:
        _require_expression(condition, where="Where condition")
        _require_expression(then, where="Where 'then' branch")
        _require_expression(else_, where="Where 'else_' branch")
        self.condition: Expression = condition
        self.then: Expression = then
        self.else_: Expression = else_

    def evaluate(self) -> int | float | _UnassignedType:
        """Evaluate the condition, then the one branch it selects.

        Returns:
            The chosen branch's value. `UNASSIGNED` when the condition is unassigned, or when
            the chosen branch is; the branch that is not taken is never evaluated.
        """
        cond_value = self.condition.evaluate()
        if isinstance(cond_value, _UnassignedType):
            return UNASSIGNED
        chosen = self.then if cond_value else self.else_
        return chosen.evaluate()

    def variables(self) -> set[Variable]:
        """Return the free variables in this expression.

        Returns:
            The union of the condition's and both branches' variables — the branch that evaluation
            skips still contributes.
        """
        return self.condition.variables() | self.then.variables() | self.else_.variables()

    def __hash__(self) -> int:
        return hash(("Where", self.condition, self.then, self.else_))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Where)
            and self.condition == other.condition
            and self.then == other.then
            and self.else_ == other.else_
        )

    def __repr__(self) -> str:
        return f"where({self.condition!r}, {self.then!r}, {self.else_!r})"


# ----------------------------------------------------------------------------
# Math function evaluator (lazy numpy import)
# ----------------------------------------------------------------------------


def _math_eval(name: str, values: list[int | float]) -> int | float:
    """Apply the named math function to already-evaluated operands.

    The numeric half of [`MathFunc.evaluate`][qprogram.MathFunc.evaluate]. `numpy` is imported lazily so AST
    construction stays free of the cost. ``abs`` preserves the int/float distinction; transcendental functions return
    Python floats.

    Args:
        name (str): One of the names in `_MATH_FUNCTIONS`.
        values (list[int | float]): The already-evaluated operands. ``minimum`` and ``maximum`` read
            all of them; every other function reads the first.

    Returns:
        The numeric result of applying ``name`` to ``values``.
    """
    import numpy as np  # ruff: ignore[import-outside-top-level]

    if name == "abs":
        return abs(values[0])
    if name == "minimum":
        return min(values)
    if name == "maximum":
        return max(values)
    fn = {
        "sin": np.sin,
        "cos": np.cos,
        "tan": np.tan,
        "exp": np.exp,
        "log": np.log,
        "sqrt": np.sqrt,
    }[name]
    return float(fn(values[0]))


# ----------------------------------------------------------------------------
# Module-level builders for ops Python syntax can't reach
# ----------------------------------------------------------------------------


def eq(
    left: Expression | float | _HandleFieldAccess,
    right: Expression | float | _HandleFieldAccess,
) -> Comparison:
    """Build an equality [`Comparison`][qprogram.Comparison].

    Why this exists rather than overloading ``==`` on [`Variable`][qprogram.Variable]: ``Variable.__eq__`` compares
    ids and must keep returning a plain ``bool``, so that variables stay usable in sets (notably the
    ``expression.variables()`` walk) and as dict keys — which rules out building a
    [`Comparison`][qprogram.Comparison] from ``==``. Numeric operands are wrapped as [`Constant`][qprogram.Constant];
    ``handle.<field>`` proxies resolve to [`MeasurementRef`][qprogram.MeasurementRef]. Equivalent to
    ``handle.state == 0`` when one operand is a field-access proxy.

    Args:
        left (Expression | float | _HandleFieldAccess): Left operand — an expression, a number, or a
            ``handle.<field>`` proxy.
        right (Expression | float | _HandleFieldAccess): Right operand, same forms as ``left``.

    Returns:
        A [`Comparison`][qprogram.Comparison] node for ``left == right``.

    Raises:
        TypeError: If either operand is a ``bool`` or any other type with no expression form.
    """
    return Comparison("==", _wrap(left), _wrap(right))


def ne(
    left: Expression | float | _HandleFieldAccess,
    right: Expression | float | _HandleFieldAccess,
) -> Comparison:
    """Build an inequality [`Comparison`][qprogram.Comparison] — counterpart of `eq`.

    Args:
        left (Expression | float | _HandleFieldAccess): Left operand — an expression, a number, or a
            ``handle.<field>`` proxy.
        right (Expression | float | _HandleFieldAccess): Right operand, same forms as ``left``.

    Returns:
        A [`Comparison`][qprogram.Comparison] node for ``left != right``.

    Raises:
        TypeError: If either operand is a ``bool`` or any other type with no expression form.
    """
    return Comparison("!=", _wrap(left), _wrap(right))


def and_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left and right`` — function form of the ``&`` operator.

    Precedence reminder: in Python ``&`` binds tighter than comparison operators, so
    ``a < b & c < d`` parses as ``a < (b & c) < d``. Parenthesize comparisons explicitly:
    ``(a < b) & (c < d)``.

    Args:
        left (Expression): Left operand.
        right (Expression): Right operand.

    Returns:
        A [`LogicalBinaryOp`][qprogram.LogicalBinaryOp] node for ``left and right``.

    Raises:
        TypeError: If either operand is not an [`Expression`][qprogram.Expression]. Numbers are not coerced here —
            a logical operand must already be an expression.
    """
    return LogicalBinaryOp("and", left, right)


def or_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left or right`` — function form of the ``|`` operator.

    Args:
        left (Expression): Left operand.
        right (Expression): Right operand.

    Returns:
        A [`LogicalBinaryOp`][qprogram.LogicalBinaryOp] node for ``left or right``.

    Raises:
        TypeError: If either operand is not an [`Expression`][qprogram.Expression].
    """
    return LogicalBinaryOp("or", left, right)


def not_(operand: Expression) -> LogicalNot:
    """Build ``not operand`` — function form of the ``~`` operator.

    Args:
        operand (Expression): The expression to negate.

    Returns:
        A [`LogicalNot`][qprogram.LogicalNot] node wrapping ``operand``.

    Raises:
        TypeError: If ``operand`` is not an [`Expression`][qprogram.Expression].
    """
    return LogicalNot(operand)


def sin(x: Expression | float) -> MathFunc:
    """Build a symbolic ``sin(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``sin(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("sin", (_wrap(x),))


def cos(x: Expression | float) -> MathFunc:
    """Build a symbolic ``cos(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``cos(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("cos", (_wrap(x),))


def tan(x: Expression | float) -> MathFunc:
    """Build a symbolic ``tan(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``tan(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("tan", (_wrap(x),))


def exp(x: Expression | float) -> MathFunc:
    """Build a symbolic ``exp(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``exp(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("exp", (_wrap(x),))


def log(x: Expression | float) -> MathFunc:
    """Build a symbolic natural log ``log(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for the natural logarithm ``log(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("log", (_wrap(x),))


def sqrt(x: Expression | float) -> MathFunc:
    """Build a symbolic ``sqrt(x)``.

    Args:
        x (Expression | float): The operand. A number is wrapped as a [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``sqrt(x)``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    return MathFunc("sqrt", (_wrap(x),))


def minimum(*args: Expression | float) -> MathFunc:
    """Build a symbolic ``minimum(a, b, ...)``.

    Why this exists separately from `min`: the built-in compares with ``<``, which on an
    [`Expression`][qprogram.Expression] builds a [`Comparison`][qprogram.Comparison] node instead of a ``bool``, so
    ``min(var_a, var_b)`` raises `TypeError` from `Expression.__bool__` rather than
    building the symbolic minimum.

    Args:
        *args (Expression | float): At least two operands. Numbers are wrapped as [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``minimum(a, b, ...)``.

    Raises:
        TypeError: If fewer than two arguments are passed, or if an argument is a ``bool`` or any
            other type with no expression form.
    """
    if len(args) < 2:
        msg = "minimum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("minimum", tuple(_wrap(a) for a in args))


def maximum(*args: Expression | float) -> MathFunc:
    """Build a symbolic ``maximum(a, b, ...)`` — same rationale as `minimum`.

    Args:
        *args (Expression | float): At least two operands. Numbers are wrapped as [`Constant`][qprogram.Constant].

    Returns:
        A [`MathFunc`][qprogram.MathFunc] node for ``maximum(a, b, ...)``.

    Raises:
        TypeError: If fewer than two arguments are passed, or if an argument is a ``bool`` or any
            other type with no expression form.
    """
    if len(args) < 2:
        msg = "maximum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("maximum", tuple(_wrap(a) for a in args))


def where(condition: Expression, then: Expression | float, else_: Expression | float) -> Where:
    """Build a ternary [`Where`][qprogram.Where] expression — ``where(condition, then, else_)``.

    Only the chosen branch is evaluated, so the unchosen branch may reference variables that happen to
    be unassigned at evaluation time.

    Args:
        condition (Expression): Boolean expression, typically a [`Comparison`][qprogram.Comparison] or a
            [`LogicalBinaryOp`][qprogram.LogicalBinaryOp].
        then (Expression | float): Returned when ``condition`` is truthy. Numeric literals are wrapped
            as [`Constant`][qprogram.Constant].
        else_ (Expression | float): Returned when ``condition`` is falsy, wrapped the same way.

    Returns:
        A [`Where`][qprogram.Where] node over the condition and the two branches.

    Raises:
        TypeError: If ``condition`` is not an [`Expression`][qprogram.Expression], or if a branch is a ``bool`` or any
            other type with no expression form.
    """
    return Where(condition, _wrap(then), _wrap(else_))


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _wrap(x: Expression | float | _HandleFieldAccess) -> Expression:
    """Coerce a literal, proxy, or [`Expression`][qprogram.Expression] into a concrete [`Expression`][qprogram.Expression].

    Numeric literals become [`Constant`][qprogram.Constant]; ``handle.<field>`` proxies become
    [`MeasurementRef`][qprogram.MeasurementRef]. ``bool`` is rejected: it is an ``int`` subclass but means a different
    thing here.

    Args:
        x (Expression | float | _HandleFieldAccess): The value to coerce. An [`Expression`][qprogram.Expression] is
            returned unchanged.

    Returns:
        An expression node standing for ``x``.

    Raises:
        TypeError: If ``x`` is a ``bool`` or any other type with no expression form.
    """
    if isinstance(x, Expression):
        return x
    if isinstance(x, _HandleFieldAccess):
        return x._as_ref()  # ruff: ignore[private-member-access]
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return Constant(x)
    msg = f"Cannot use {type(x).__name__} in an Expression; expected Expression, handle.<field>, int, or float"
    raise TypeError(msg)


def _require_expression(value: object, *, where: str) -> None:
    """Raise `TypeError` if ``value`` is not an [`Expression`][qprogram.Expression].

    Used by constructors that take an [`Expression`][qprogram.Expression]-typed operand without numeric coercion
    (logical operators, [`Where`][qprogram.Where] condition). The error message points the user at
    `eq` / `ne` for the common ``var == literal`` pitfall.

    Args:
        value (object): The operand to check.
        where (str): Description of the operand position; it opens the error message.

    Raises:
        TypeError: If ``value`` is not an [`Expression`][qprogram.Expression].
    """
    if isinstance(value, Expression):
        return
    hint = ""
    if isinstance(value, bool):
        hint = (
            " — if you wrote `var == literal` or `var != literal`, use "
            "qprogram.eq(var, literal) / qprogram.ne(...) instead, since "
            "Variable's `==` returns a plain bool, not a Comparison."
        )
    msg = f"{where} must be an Expression; got {type(value).__name__}{hint}"
    raise TypeError(msg)
