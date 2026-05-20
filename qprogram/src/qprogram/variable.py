"""Symbolic expression system for QProgram.

Anywhere QProgram accepts a numeric value, it also accepts an :class:`Expression` — a literal, a
variable bound at runtime, or any composition of them. :meth:`Expression.evaluate` takes no
arguments: each :class:`Variable` carries its own current value, set by the runtime per loop
iteration. The :data:`UNASSIGNED` sentinel propagates upward when any variable is unbound.

Equality across the AST is structural. For :class:`Variable` this means by ``id`` — two variables
compare equal iff their ``id`` matches, which is what lets a program survive ``deepcopy`` /
``qp.loads(qp.dumps(...))`` and still compare equal to the original.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Self

# Re-exported from qprogram.errors so legacy ``from qprogram.variable import InvalidVariableIdError``
# imports keep working.
from qprogram._reserved import RESERVED_KEYWORDS
from qprogram.errors import InvalidVariableIdError, UnassignedVariableError

if TYPE_CHECKING:
    from qprogram.result import MeasurementHandle

# Variable ids are Python-style identifiers — they're used verbatim as tokens in the .qp file format
# and must be safe to embed without quoting.
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

type BinaryOperator = Literal["+", "-", "*", "/"]
type UnaryOperator = Literal["-", "+"]
type ComparisonOperator = Literal["==", "!=", "<", "<=", ">", ">="]
type LogicalBinaryOperator = Literal["and", "or"]

# Why this is a runtime set rather than a Literal type: vendors / future extensions can register
# additional names at runtime without re-shipping the AST module.
_MATH_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {"sin", "cos", "tan", "exp", "log", "sqrt", "abs", "minimum", "maximum"},
)


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


# ----------------------------------------------------------------------------
# Expression: abstract base
# ----------------------------------------------------------------------------


class Expression(ABC):
    """Abstract base for symbolic expressions.

    Subclasses split into four families:

    - **Leaves** — :class:`Variable`, :class:`Constant`, :class:`MeasurementRef`.
    - **Arithmetic** — :class:`BinaryOp` (``+ - * /``), :class:`UnaryOp` (``- +``).
    - **Comparison & logical** — :class:`Comparison`, :class:`LogicalBinaryOp`, :class:`LogicalNot`.
    - **Math & conditional** — :class:`MathFunc`, :class:`Where`.

    Operators that map cleanly onto Python syntax build the corresponding AST nodes uniformly across
    every subclass. The exceptions are ``==`` / ``!=`` (use :func:`eq` / :func:`ne` so :class:`Variable`
    can keep a plain-bool ``__eq__`` for set / dict-key use) and ``and`` / ``or`` / ``not`` (Python
    keywords, unoverloadable — use ``& | ~`` or the named helpers :func:`and_` / :func:`or_` /
    :func:`not_`).

    Why ``__bool__`` raises: ``if freq < 5e9:`` would otherwise silently test the truthiness of the
    :class:`Comparison` instance (always true) instead of building the conditional the user almost
    certainly meant. Mirrors SymPy.
    """

    @abstractmethod
    def evaluate(self) -> int | float | _UnassignedType:
        """Compute the value of the expression.

        Returns:
            A numeric result for arithmetic / math expressions, a ``bool`` for comparisons and logical
            expressions (``bool`` is an ``int`` subclass), or :data:`UNASSIGNED` when any referenced
            variable is currently unassigned.
        """
        ...

    def evaluate_or_raise(self) -> int | float:
        """Compute the value of the expression, raising instead of returning :data:`UNASSIGNED`.

        Returns:
            The numeric value.

        Raises:
            UnassignedVariableError: When any referenced variable is unassigned. Use this when the
                caller (e.g. waveform envelope computation) requires a concrete value.
        """
        result = self.evaluate()
        if isinstance(result, _UnassignedType):
            raise UnassignedVariableError(self)
        return result

    @abstractmethod
    def variables(self) -> set[Variable]:
        """Return the set of free :class:`Variable` s appearing in this expression."""
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
    # must parenthesise comparisons (``(freq < 5e9) & (gain > 0.5)``). Both operands must be Expression
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
    """A symbolic variable. Leaf node holding a value, initially :data:`UNASSIGNED`.

    Equality is identity-based: each ``Variable(id)`` is a distinct instance, even if two share the
    same ``id``. The runtime sets the value via :meth:`set_value` per loop iteration; reading
    :attr:`value` (or calling :meth:`evaluate`) returns the current value or :data:`UNASSIGNED`.

    Args:
        id: Short name matching ``[A-Za-z_][A-Za-z0-9_]*``. Doubles as the identifier in ``.qp``, so
            no spaces or punctuation.
        label: Human-readable name for axis labels, plot titles, etc.
        units: Unit string (e.g. ``"Hz"``, ``"ns"``, ``"V"``).
        description: Longer free-form description.

    Raises:
        InvalidVariableIdError: If ``id`` violates the pattern or is reserved
            (see :data:`~qprogram.RESERVED_KEYWORDS`).
    """

    def __init__(
        self,
        id: str,  # noqa: A002
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
        return self._id

    @property
    def label(self) -> str | None:
        return self._label

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
        """Clear the variable's value, returning it to :data:`UNASSIGNED`."""
        self._value = UNASSIGNED

    def evaluate(self) -> int | float | _UnassignedType:
        return self._value

    def variables(self) -> set[Variable]:
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

    Built implicitly by the proxy that :attr:`MeasurementHandle.state` returns when the user writes
    ``handle.state == 0``; direct construction is rarely needed.

    Equality is structural over ``(handle.name, field)``: distinct instances built at different sites
    refer to the same logical reference whenever their names match. Mirrors :class:`Variable`'s
    cross-program equality story so programs survive ``deepcopy`` / ``qp.loads(qp.dumps(...))``.

    Args:
        handle: The producing measurement's :class:`~qprogram.MeasurementHandle`.
        field: Field name. Only ``"state"`` is supported today.

    Raises:
        ValueError: If ``field`` is not in the allowed set.
    """

    _ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset({"state"})

    def __init__(self, handle: MeasurementHandle, field: str) -> None:
        if field not in self._ALLOWED_FIELDS:
            msg = f"MeasurementRef field must be one of {sorted(self._ALLOWED_FIELDS)}, got {field!r}"
            raise ValueError(msg)
        self.handle: MeasurementHandle = handle
        self.field: str = field

    def evaluate(self) -> int | float | _UnassignedType:
        return self.handle._value_for(self.field)  # noqa: SLF001

    def variables(self) -> set[Variable]:
        # A MeasurementRef is its own binding kind, distinct from Variable; it doesn't contribute to
        # the loop-counter variable walk that the rest of the AST does.
        return set()

    def __hash__(self) -> int:
        return hash(("MeasurementRef", self.handle.name, self.field))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MeasurementRef) and self.handle.name == other.handle.name and self.field == other.field

    def __repr__(self) -> str:
        return f"MeasurementRef({self.handle.name!r}, {self.field!r})"


class _HandleFieldAccess:
    """Throwaway proxy returned by :attr:`MeasurementHandle.state`.

    Why it exists: ``handle.state == 0`` needs to build a :class:`Comparison`, but overloading ``==``
    on :class:`MeasurementRef` itself would clash with the AST's structural equality walk (which would
    try to evaluate ``bool(Comparison)`` and trip :meth:`Expression.__bool__`). The proxy is consumed
    immediately by the comparison and never stored on the AST.

    ``==`` and ``!=`` accept ``int`` literals, another ``_HandleFieldAccess`` (for
    ``m1.state == m2.state``), or a concrete :class:`MeasurementRef`. Hashing is disabled: storing
    ``s = handle.state; s == s`` would yield a Comparison whose ``bool`` raises, which catches misuse
    loudly — the intended outcome.
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
            return Comparison(op, self._as_ref(), other._as_ref())  # noqa: SLF001
        if isinstance(other, MeasurementRef):
            return Comparison(op, self._as_ref(), other)
        msg = (
            f"handle.{self._field} can only be compared to int, another "
            f"handle.<field>, or a MeasurementRef; got "
            f"{type(other).__name__}"
        )
        raise TypeError(msg)

    # Return type is annotated Any rather than Comparison because object.__eq__ returns bool and the
    # override must be LSP-compatible. Runtime behaviour is unchanged: ``handle.state == 1`` builds a
    # Comparison. Any is the standard escape hatch the typing community settled on for this DSL pattern
    # (cf. SQLAlchemy columns, numpy arrays). The ANN401 noqa is targeted at this contract; flagging
    # ``Any`` elsewhere stays valuable.
    def __eq__(self, other: object) -> Any:  # noqa: ANN401
        return self._comparison("==", other)

    def __ne__(self, other: object) -> Any:  # noqa: ANN401
        return self._comparison("!=", other)

    __hash__ = None

    def __repr__(self) -> str:
        return f"_HandleFieldAccess({self._handle.name!r}, {self._field!r})"


class Constant(Expression):
    """Concrete numeric leaf with structural equality (``Constant(5) == Constant(5)``).

    Numeric literals appearing in expressions are auto-wrapped to :class:`Constant` by the operator
    methods on :class:`Expression`.

    Args:
        value: An ``int`` or ``float``. ``bool`` is rejected — booleans would silently coerce to 0/1
            and obscure intent.

    Raises:
        TypeError: If ``value`` is not an ``int`` or ``float`` (or is a ``bool``).
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
    """Binary arithmetic node — ``left op right`` for one of ``+ - * /``.

    Constructed by the arithmetic operators on :class:`Expression`. Numeric literals are auto-wrapped
    to :class:`Constant`, so both operands are always :class:`Expression` instances.

    Args:
        op: Operator symbol.
        left: Left operand.
        right: Right operand.
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
    """Unary arithmetic node — ``op operand`` for one of ``- +``.

    Args:
        op: Operator symbol.
        operand: The operand.
    """

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
# Comparison and logical nodes
# ----------------------------------------------------------------------------


class Comparison(Expression):
    """Binary comparison node — ``left op right`` for one of ``== != < <= > >=``.

    Constructed by the comparison operators on :class:`Expression` (``< <= > >=``) and by the helper
    functions :func:`eq` / :func:`ne` (``== !=``; see :class:`Expression` for why these are not
    overloaded directly).

    :meth:`evaluate` returns ``bool`` (an ``int`` subclass) or :data:`UNASSIGNED` if either operand is
    unassigned.

    Args:
        op: Operator symbol.
        left: Left operand.
        right: Right operand.

    Raises:
        ValueError: If ``op`` is not a recognised comparison operator (defensive — callers should pass
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

    Constructed by ``&`` and ``|`` on :class:`Expression`, or by the named helpers :func:`and_` /
    :func:`or_`.

    Why this never short-circuits: any :data:`UNASSIGNED` operand propagates upwards. Matches the rest
    of the evaluator and makes unbound-variable diagnostics deterministic rather than position-dependent.

    Args:
        op: Operator symbol.
        left: Left operand.
        right: Right operand.

    Raises:
        ValueError: If ``op`` is not a recognised logical operator (defensive).
        TypeError: If either operand is not an :class:`Expression`.
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

    Constructed by ``~`` on :class:`Expression` or by :func:`not_`.

    Args:
        operand: The expression to negate.

    Raises:
        TypeError: If ``operand`` is not an :class:`Expression`.
    """

    def __init__(self, operand: Expression) -> None:
        _require_expression(operand, where="LogicalNot operand")
        self.operand: Expression = operand

    def evaluate(self) -> bool | _UnassignedType:
        operand_value = self.operand.evaluate()
        if isinstance(operand_value, _UnassignedType):
            return UNASSIGNED
        return not bool(operand_value)

    def variables(self) -> set[Variable]:
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

    The function name determines arity and the numeric implementation. Built-in names are listed in
    :data:`_MATH_FUNCTIONS`. Each is dispatched via the lazy lookup in :func:`_math_eval`, keeping
    this module free of a direct ``numpy``/``math`` import at the AST layer.

    Args:
        name: Function name (e.g. ``"sin"``, ``"minimum"``).
        operands: Operand expressions.

    Raises:
        ValueError: If ``name`` is not a registered math function, or if ``operands`` is empty.
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
        values: list[int | float] = []
        for op in self.operands:
            v = op.evaluate()
            if isinstance(v, _UnassignedType):
                return UNASSIGNED
            values.append(v)
        return _math_eval(self.name, values)

    def variables(self) -> set[Variable]:
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
    :data:`UNASSIGNED`.

    Users normally construct via the :func:`where` helper.

    Args:
        condition: The predicate expression.
        then: Returned when ``condition`` evaluates to truthy.
        else_: Returned when ``condition`` evaluates to falsy.

    Raises:
        TypeError: If any argument is not an :class:`Expression`.
    """

    def __init__(self, condition: Expression, then: Expression, else_: Expression) -> None:
        _require_expression(condition, where="Where condition")
        _require_expression(then, where="Where 'then' branch")
        _require_expression(else_, where="Where 'else_' branch")
        self.condition: Expression = condition
        self.then: Expression = then
        self.else_: Expression = else_

    def evaluate(self) -> int | float | _UnassignedType:
        cond_value = self.condition.evaluate()
        if isinstance(cond_value, _UnassignedType):
            return UNASSIGNED
        chosen = self.then if cond_value else self.else_
        return chosen.evaluate()

    def variables(self) -> set[Variable]:
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
    """Numeric implementation behind :meth:`MathFunc.evaluate`.

    :mod:`numpy` is imported lazily so AST construction stays free of the cost. ``abs`` preserves the
    int/float distinction; transcendental functions return Python floats.
    """
    import numpy as np  # noqa: PLC0415

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
    """Build an equality :class:`Comparison`.

    Why this exists rather than overloading ``==`` on :class:`Variable`: Variable's identity-based
    ``__eq__`` is needed for set / dict-key use and the ``expression.variables()`` walk. Numeric
    operands are wrapped as :class:`Constant`; ``handle.<field>`` proxies resolve to
    :class:`MeasurementRef`. Equivalent to ``handle.state == 0`` when one operand is a
    field-access proxy.

    Args:
        left: Left operand.
        right: Right operand.

    Returns:
        ``Comparison(left == right)``.
    """
    return Comparison("==", _wrap(left), _wrap(right))


def ne(
    left: Expression | float | _HandleFieldAccess,
    right: Expression | float | _HandleFieldAccess,
) -> Comparison:
    """Build an inequality :class:`Comparison` — counterpart of :func:`eq`.

    Args:
        left: Left operand.
        right: Right operand.

    Returns:
        ``Comparison(left != right)``.
    """
    return Comparison("!=", _wrap(left), _wrap(right))


def and_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left and right`` — function form of the ``&`` operator.

    Precedence reminder: in Python ``&`` binds tighter than comparison operators, so
    ``a < b & c < d`` parses as ``a < (b & c) < d``. Parenthesise comparisons explicitly:
    ``(a < b) & (c < d)``.

    Args:
        left: Left operand.
        right: Right operand.

    Returns:
        ``LogicalBinaryOp("and", left, right)``.
    """
    return LogicalBinaryOp("and", left, right)


def or_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left or right`` — function form of the ``|`` operator.

    Args:
        left: Left operand.
        right: Right operand.

    Returns:
        ``LogicalBinaryOp("or", left, right)``.
    """
    return LogicalBinaryOp("or", left, right)


def not_(operand: Expression) -> LogicalNot:
    """Build ``not operand`` — function form of the ``~`` operator."""
    return LogicalNot(operand)


def sin(x: Expression | float) -> MathFunc:
    """Build a symbolic ``sin(x)``."""
    return MathFunc("sin", (_wrap(x),))


def cos(x: Expression | float) -> MathFunc:
    """Build a symbolic ``cos(x)``."""
    return MathFunc("cos", (_wrap(x),))


def tan(x: Expression | float) -> MathFunc:
    """Build a symbolic ``tan(x)``."""
    return MathFunc("tan", (_wrap(x),))


def exp(x: Expression | float) -> MathFunc:
    """Build a symbolic ``exp(x)``."""
    return MathFunc("exp", (_wrap(x),))


def log(x: Expression | float) -> MathFunc:
    """Build a symbolic natural log ``log(x)``."""
    return MathFunc("log", (_wrap(x),))


def sqrt(x: Expression | float) -> MathFunc:
    """Build a symbolic ``sqrt(x)``."""
    return MathFunc("sqrt", (_wrap(x),))


def minimum(*args: Expression | float) -> MathFunc:
    """Build a symbolic ``minimum(a, b, ...)``.

    Why this exists separately from :func:`min`: the built-in uses ``<``, which on an
    :class:`Expression` builds a :class:`Comparison` node (always truthy), so ``min(var_a, var_b)``
    would silently return ``var_a`` instead of building the symbolic minimum.

    Args:
        *args: At least two operands.

    Raises:
        TypeError: If fewer than two arguments are passed.
    """
    if len(args) < 2:
        msg = "minimum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("minimum", tuple(_wrap(a) for a in args))


def maximum(*args: Expression | float) -> MathFunc:
    """Build a symbolic ``maximum(a, b, ...)`` — same rationale as :func:`minimum`.

    Args:
        *args: At least two operands.

    Raises:
        TypeError: If fewer than two arguments are passed.
    """
    if len(args) < 2:
        msg = "maximum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("maximum", tuple(_wrap(a) for a in args))


def where(condition: Expression, then: Expression | float, else_: Expression | float) -> Where:
    """Build a ternary :class:`Where` expression — ``where(condition, then, else_)``.

    Only the chosen branch is evaluated, so the unchosen branch may reference variables that happen to
    be unassigned at evaluation time.

    Args:
        condition: Boolean :class:`Expression` (typically a :class:`Comparison` or
            :class:`LogicalBinaryOp`).
        then: Returned when ``condition`` is truthy. Numeric literals are wrapped as :class:`Constant`.
        else_: Returned when ``condition`` is falsy.
    """
    return Where(condition, _wrap(then), _wrap(else_))


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _wrap(x: Expression | float | _HandleFieldAccess) -> Expression:
    """Coerce a literal, proxy, or :class:`Expression` into a concrete :class:`Expression`.

    Numeric literals become :class:`Constant`; ``handle.<field>`` proxies become
    :class:`MeasurementRef`. ``bool`` is rejected: it's an ``int`` subclass but means a different
    thing here.
    """
    if isinstance(x, Expression):
        return x
    if isinstance(x, _HandleFieldAccess):
        return x._as_ref()  # noqa: SLF001
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return Constant(x)
    msg = f"Cannot use {type(x).__name__} in an Expression; expected Expression, handle.<field>, int, or float"
    raise TypeError(msg)


def _require_expression(value: object, *, where: str) -> None:
    """Raise :class:`TypeError` if ``value`` is not an :class:`Expression`.

    Used by constructors that take an :class:`Expression`-typed operand without numeric coercion
    (logical operators, :class:`Where` condition). The error message points the user at
    :func:`eq` / :func:`ne` for the common ``var == literal`` pitfall.
    """
    if isinstance(value, Expression):
        return
    hint = ""
    if isinstance(value, bool):
        hint = (
            " — if you wrote `var == literal` or `var != literal`, use "
            "qprogram.eq(var, literal) / qprogram.ne(...) instead, since "
            "Variable's `==` returns identity-based bool, not a Comparison."
        )
    msg = f"{where} must be an Expression; got {type(value).__name__}{hint}"
    raise TypeError(msg)
