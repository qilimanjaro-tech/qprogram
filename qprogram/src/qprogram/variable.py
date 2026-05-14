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

Equality across the AST is **structural**. For :class:`Variable`, "structural"
means *by id*: two Variables compare equal iff their ``id`` matches, so a
program survives ``copy.deepcopy`` / ``qp.loads(qp.dumps(...))`` /
``with_bus_mapping`` and still compares equal to the original under whole-AST
equality. Within a single :class:`QProgram`, ``QProgram.variable`` enforces
unique ids, so identity and id-equality coincide in practice — the
distinction matters only when comparing across programs. Other nodes use
the obvious structural equality (two ``Constant(5)`` are equal; two
structurally identical ``BinaryOp`` are equal).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar, Final, Literal, Self

# InvalidVariableIdError and UnassignedVariableError live on the QProgram
# exception hierarchy in :mod:`qprogram.errors`. They are re-exported here
# so existing ``from qprogram.variable import InvalidVariableIdError``
# imports keep working.
from qprogram.errors import InvalidVariableIdError, UnassignedVariableError

# Valid variable ids: Python-style identifiers — letter/underscore start,
# then letters/digits/underscores. Ids are used verbatim as identifiers in
# the .qp file format, so they must be safe to embed without quoting.
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Operators supported by BinaryOp and UnaryOp.
type BinaryOperator = Literal["+", "-", "*", "/"]
type UnaryOperator = Literal["-", "+"]
# Comparison and logical operator alphabets.
type ComparisonOperator = Literal["==", "!=", "<", "<=", ">", ">="]
type LogicalBinaryOperator = Literal["and", "or"]
# Names recognised by MathFunc. Stays as a runtime check rather than a
# Literal type so vendors / future extensions can register additional names
# without re-shipping the AST module.
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
    """Base class for the symbolic expression AST.

    Subclasses split into four families:

    - **Leaves**: :class:`Variable`, :class:`Constant`.
    - **Arithmetic**: :class:`BinaryOp` (``+ - * /``), :class:`UnaryOp` (``- +``).
    - **Comparison & logical**: :class:`Comparison` (``== != < <= > >=``),
      :class:`LogicalBinaryOp` (``and or``), :class:`LogicalNot`. These
      ``evaluate()`` to bool (or :data:`UNASSIGNED`).
    - **Math & conditional**: :class:`MathFunc` (``sin cos tan exp log sqrt
      abs minimum maximum``), :class:`Where` (ternary select).

    All operators that map cleanly onto Python syntax are defined here so they
    work uniformly across every subclass: arithmetic (``+ - * /``), comparison
    (``< <= > >=`` only — equality uses :func:`~qprogram.eq` / :func:`~qprogram.ne`
    instead, so that ``Variable``'s own ``__eq__`` keeps returning a plain bool
    for use in sets, dict keys, and ``expression.variables()`` membership tests),
    logical (``& | ~`` following the NumPy/SymPy convention;
    ``and``/``or``/``not`` are Python keywords and cannot be overloaded), and
    :func:`abs`. Math functions like :func:`~qprogram.sin` and the conditional
    :func:`~qprogram.where` are module-level helpers.

    ``__bool__`` deliberately raises: an :class:`Expression` is symbolic
    data, not a runtime predicate. ``if freq < 5e9:`` would otherwise silently
    test the truthiness of a :class:`Comparison` instance (always true) rather
    than build the conditional the user almost certainly meant.
    """

    @abstractmethod
    def evaluate(self) -> int | float | _UnassignedType:
        """Compute the value of the expression.

        Returns a numeric result for arithmetic/math expressions, a boolean
        for comparisons and logical expressions (note ``bool`` is an ``int``
        subclass in Python, so the return annotation still fits), or
        :data:`UNASSIGNED` if any variable in the expression is currently
        unassigned.
        """
        ...

    def evaluate_or_raise(self) -> int | float:
        """Compute the value of the expression.

        Like :meth:`evaluate`, but raises :class:`UnassignedVariableError` instead
        of returning the ``UNASSIGNED`` sentinel. Useful when downstream code
        requires a concrete value (e.g. waveform envelope computation).
        """
        result = self.evaluate()
        if isinstance(result, _UnassignedType):
            raise UnassignedVariableError(self)
        return result

    @abstractmethod
    def variables(self) -> set[Variable]:
        """Return the set of free variables that appear in this expression."""
        ...

    def __bool__(self) -> bool:
        """Reject truth-value coercion of a symbolic expression.

        Catches the common bug where users write ``if freq < 5e9:`` expecting
        runtime control flow; the comparison builds a :class:`Comparison`
        node, and without this guard ``if`` would silently test its (always
        truthy) object identity. Mirrors SymPy's behaviour.
        """
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

    # --- comparison operators (build Comparison nodes) ---
    #
    # ``==`` and ``!=`` are deliberately NOT overloaded: ``Variable``'s
    # ``__eq__`` must return a plain bool so Variables can be used in sets
    # (notably the result of ``expression.variables()``) and as dict keys.
    # Building a ``Comparison`` for ``var == 5`` would conflict. Users
    # build equality comparisons with the :func:`qprogram.eq` /
    # :func:`qprogram.ne` helpers.

    def __lt__(self, other: Expression | float) -> Comparison:
        return Comparison("<", self, _wrap(other))

    def __le__(self, other: Expression | float) -> Comparison:
        return Comparison("<=", self, _wrap(other))

    def __gt__(self, other: Expression | float) -> Comparison:
        return Comparison(">", self, _wrap(other))

    def __ge__(self, other: Expression | float) -> Comparison:
        return Comparison(">=", self, _wrap(other))

    # --- logical operators (NumPy/SymPy convention: & | ~) ---
    #
    # ``and``/``or``/``not`` are Python keywords with short-circuit semantics
    # and cannot be overloaded; ``& | ~`` are the conventional substitutes.
    # Module-level :func:`qprogram.and_`, :func:`qprogram.or_`,
    # :func:`qprogram.not_` are provided for callers who prefer a named form.
    #
    # Precedence gotcha: ``&``/``|`` bind tighter than ``< == >``, so users
    # must parenthesise comparisons explicitly:
    # ``(freq < 5e9) & (gain > 0.5)``. The doc on :func:`qprogram.and_` calls
    # this out.
    #
    # Both operands must be :class:`Expression`. ``True & predicate`` is
    # unusual in practice, and supporting bool literals would conflict with
    # :class:`Constant`'s numeric-only contract.

    def __and__(self, other: Expression) -> LogicalBinaryOp:
        # Defensive runtime check — the declared type is honest but Python
        # operators dispatch dynamically; rejecting non-Expression operands
        # at the binding site beats a confusing failure later in the AST.
        if not isinstance(other, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
            return NotImplemented  # type: ignore[return-value]
        return LogicalBinaryOp("and", self, other)

    def __or__(self, other: Expression) -> LogicalBinaryOp:
        if not isinstance(other, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
            return NotImplemented  # type: ignore[return-value]
        return LogicalBinaryOp("or", self, other)

    def __invert__(self) -> LogicalNot:
        return LogicalNot(self)

    # --- abs (built-in) builds a MathFunc("abs", ...) ---

    def __abs__(self) -> MathFunc:
        return MathFunc("abs", (self,))


# ----------------------------------------------------------------------------
# Leaves
# ----------------------------------------------------------------------------


class Variable(Expression):
    """A symbolic variable. Leaf node that holds a value (initially ``UNASSIGNED``).

    Identity-based equality: each ``Variable(id)`` is a distinct instance.

    The runtime executor sets the value via ``set_value()`` per loop iteration.
    Reading ``value`` (or calling ``evaluate()``) returns the current value or
    ``UNASSIGNED`` if no value has been set.

    Args:
        id: Mandatory short name. Must match ``[A-Za-z_][A-Za-z0-9_]*`` —
            it doubles as the identifier in the ``.qp`` file format and so
            cannot contain spaces or punctuation.
        label: Optional human-readable name (free-form string). Use this
            for axis labels, plot titles, etc.
        units: Optional unit string (e.g. ``"Hz"``, ``"ns"``, ``"V"``).
        description: Optional longer description.
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
        """Clear the variable's value, returning it to ``UNASSIGNED``."""
        self._value = UNASSIGNED

    def evaluate(self) -> int | float | _UnassignedType:
        return self._value

    def variables(self) -> set[Variable]:
        return {self}

    def __hash__(self) -> int:
        return hash(("Variable", self._id))

    def __eq__(self, other: object) -> bool:
        # Structural by ``id``. Within a single :class:`QProgram`, ids are
        # unique (``QProgram.variable`` rejects duplicates), so two
        # Variables compare equal iff they refer to the same logical
        # binding. Across programs (deepcopy, .qp load, with_bus_mapping),
        # two Variables with the same id are also equal — which lets
        # whole-program structural comparison work after ``copy.deepcopy``
        # and ``qp.loads(qp.dumps(...))``. The runtime executor reads
        # ``self._value`` on the instance directly, so equality semantics
        # never interfere with per-iteration value assignment.
        return isinstance(other, Variable) and self._id == other._id

    def __repr__(self) -> str:
        return f"Variable('{self._id}')"


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
# Comparison and logical nodes
# ----------------------------------------------------------------------------


class Comparison(Expression):
    """Binary comparison node: ``left op right`` for ``== != < <= > >=``.

    Constructed by the comparison operators on :class:`Expression` (``<``,
    ``<=``, ``>``, ``>=``) and by the :func:`~qprogram.eq` /
    :func:`~qprogram.ne` helpers (``==``, ``!=``; see :class:`Expression`
    for why these are not overloaded directly).

    ``evaluate()`` returns ``bool`` (which Python treats as an ``int``
    subclass, hence the broader return annotation on the base class), or
    :data:`UNASSIGNED` if either operand is unassigned.
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
    """Binary logical node: ``left op right`` where ``op`` is ``and`` or ``or``.

    Constructed by ``&`` and ``|`` on :class:`Expression`, or by the
    :func:`~qprogram.and_` / :func:`~qprogram.or_` module helpers.

    Evaluation never short-circuits — any :data:`UNASSIGNED` operand
    propagates :data:`UNASSIGNED` upwards. This matches the rest of the
    evaluator (``BinaryOp``, ``Comparison``, ``UnaryOp``) and makes
    unbound-variable diagnostics deterministic instead of position-dependent.
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
    """Logical negation node: ``not operand``.

    Constructed by ``~`` on :class:`Expression` or by
    :func:`~qprogram.not_`.
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
    """Math function application: ``name(arg1, arg2, ...)``.

    Carries a function name plus a tuple of operand :class:`Expression`
    instances. The registered name determines arity (e.g. ``sin`` takes one
    argument, ``minimum`` takes two) and the numeric evaluator.

    Built-in names are listed in :data:`_MATH_FUNCTIONS`. Each is dispatched
    via the lazy lookup in :func:`_math_eval`, keeping this module free of a
    direct ``numpy``/``math`` import at the AST layer.
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
        return (
            isinstance(other, MathFunc)
            and self.name == other.name
            and self.operands == other.operands
        )

    def __repr__(self) -> str:
        args = ", ".join(repr(op) for op in self.operands)
        return f"{self.name}({args})"


class Where(Expression):
    """Ternary conditional: ``where(condition, then, else_)``.

    Evaluates ``condition``; if true, returns ``then``'s value, otherwise
    ``else_``'s. The unchosen branch is not evaluated, so it may reference
    unassigned variables without forcing the whole expression to
    :data:`UNASSIGNED`. If the ``condition`` itself is unassigned, the
    whole expression evaluates to :data:`UNASSIGNED`.

    Construct with the module-level :func:`~qprogram.where` helper.
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
    """Dispatch :class:`MathFunc` evaluation.

    Imports :mod:`numpy` lazily so that constructing :class:`MathFunc`
    instances at AST-construction time is free; we only pay the import when
    a tree is actually evaluated. Returns Python scalars (``float`` for
    transcendental functions; ``abs`` preserves the integer-vs-float
    distinction).
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


def eq(left: Expression | float, right: Expression | float) -> Comparison:
    """Build an equality comparison ``left == right``.

    Used instead of overloading ``==`` so that :class:`Variable`'s
    identity-based equality (needed for sets / dict keys / AST traversal)
    keeps working. The right-hand side is coerced from int/float via
    :class:`Constant`.
    """
    return Comparison("==", _wrap(left), _wrap(right))


def ne(left: Expression | float, right: Expression | float) -> Comparison:
    """Build an inequality comparison ``left != right`` (counterpart of :func:`eq`)."""
    return Comparison("!=", _wrap(left), _wrap(right))


def and_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left and right`` — function form of the ``&`` operator.

    Useful when chaining or when callers find ``and_(p, q)`` clearer than
    ``p & q``. Precedence reminder: ``&`` binds tighter than the comparison
    operators in Python, so ``a < b & c < d`` parses as ``a < (b & c) < d``;
    parenthesise comparisons explicitly: ``(a < b) & (c < d)``.
    """
    return LogicalBinaryOp("and", left, right)


def or_(left: Expression, right: Expression) -> LogicalBinaryOp:
    """Build ``left or right`` — function form of the ``|`` operator."""
    return LogicalBinaryOp("or", left, right)


def not_(operand: Expression) -> LogicalNot:
    """Build ``not operand`` — function form of the ``~`` operator."""
    return LogicalNot(operand)


def sin(x: Expression | float) -> MathFunc:
    """Build ``sin(x)``."""
    return MathFunc("sin", (_wrap(x),))


def cos(x: Expression | float) -> MathFunc:
    """Build ``cos(x)``."""
    return MathFunc("cos", (_wrap(x),))


def tan(x: Expression | float) -> MathFunc:
    """Build ``tan(x)``."""
    return MathFunc("tan", (_wrap(x),))


def exp(x: Expression | float) -> MathFunc:
    """Build ``exp(x)``."""
    return MathFunc("exp", (_wrap(x),))


def log(x: Expression | float) -> MathFunc:
    """Build ``log(x)`` (natural logarithm)."""
    return MathFunc("log", (_wrap(x),))


def sqrt(x: Expression | float) -> MathFunc:
    """Build ``sqrt(x)``."""
    return MathFunc("sqrt", (_wrap(x),))


def minimum(*args: Expression | float) -> MathFunc:
    """Build ``minimum(a, b, ...)`` — the symbolic counterpart of Python's :func:`min`.

    A separate name is required because the built-in :func:`min` uses ``<``
    for comparison, which on an :class:`Expression` builds a
    :class:`Comparison` node (truthy), causing ``min(var_a, var_b)`` to
    silently return ``var_a`` rather than build the symbolic minimum.
    """
    if len(args) < 2:
        msg = "minimum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("minimum", tuple(_wrap(a) for a in args))


def maximum(*args: Expression | float) -> MathFunc:
    """Build ``maximum(a, b, ...)``; see :func:`minimum` for the rationale."""
    if len(args) < 2:
        msg = "maximum() requires at least two arguments"
        raise TypeError(msg)
    return MathFunc("maximum", tuple(_wrap(a) for a in args))


def where(condition: Expression, then: Expression | float, else_: Expression | float) -> Where:
    """Build a ternary ``where(condition, then, else_)`` expression.

    The condition must be an :class:`Expression` (typically a
    :class:`Comparison` or :class:`LogicalBinaryOp`); the branches may be
    expressions or numeric literals, which are wrapped as :class:`Constant`.
    Only the chosen branch is evaluated, so the unchosen branch may
    reference variables that happen to be unassigned at evaluation time.
    """
    return Where(condition, _wrap(then), _wrap(else_))


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


def _require_expression(value: object, *, where: str) -> None:
    """Raise :class:`TypeError` if ``value`` is not an :class:`Expression`.

    Used by node constructors that take an :class:`Expression`-typed operand
    *without* numeric coercion (logical operators, ``Where`` condition). The
    most common pitfall is writing ``var == literal`` and expecting a
    :class:`Comparison` — but ``Variable.__eq__`` is identity-based, so it
    returns ``bool``. The error message points users to the right helper.
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
