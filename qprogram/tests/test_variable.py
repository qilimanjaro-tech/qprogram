"""Tests for the Variable / Expression AST in qprogram.variable."""

from __future__ import annotations

import math

import pytest

from qprogram import (
    UNASSIGNED,
    BinaryOp,
    Comparison,
    Constant,
    Expression,
    InvalidVariableIdError,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    UnaryOp,
    UnassignedVariableError,
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
from qprogram.variable import _UnassignedType, _wrap

# ---------------------------------------------------------------------------
# UNASSIGNED sentinel
# ---------------------------------------------------------------------------


def test_unassigned_is_singleton():
    second = _UnassignedType()
    assert second is UNASSIGNED


def test_unassigned_repr():
    assert repr(UNASSIGNED) == "UNASSIGNED"


def test_unassigned_is_falsy():
    assert not UNASSIGNED
    assert bool(UNASSIGNED) is False


# ---------------------------------------------------------------------------
# Variable
# ---------------------------------------------------------------------------


def test_variable_id_pattern_letters_digits_underscore():
    v = Variable("freq_1")
    assert v.id == "freq_1"


@pytest.mark.parametrize(
    "bad_id",
    ["1freq", "freq bar", "freq-bar", "freq.bar", "freq!", "", " freq", "freq "],
)
def test_variable_id_invalid_pattern(bad_id):
    with pytest.raises(InvalidVariableIdError) as exc_info:
        Variable(bad_id)
    assert exc_info.value.reserved is False
    assert exc_info.value.id == bad_id


@pytest.mark.parametrize("reserved", ["if", "while", "where", "true", "false", "null"])
def test_variable_id_reserved(reserved):
    with pytest.raises(InvalidVariableIdError) as exc_info:
        Variable(reserved)
    assert exc_info.value.reserved is True
    assert exc_info.value.id == reserved


def test_variable_metadata():
    v = Variable("freq", label="Drive frequency", units="Hz", description="NCO carrier")
    assert v.id == "freq"
    assert v.label == "Drive frequency"
    assert v.units == "Hz"
    assert v.description == "NCO carrier"


def test_variable_default_metadata_is_none():
    v = Variable("freq")
    assert v.label is None
    assert v.units is None
    assert v.description is None


def test_variable_value_starts_unassigned():
    v = Variable("freq")
    assert v.value is UNASSIGNED


def test_variable_set_value_and_reset():
    v = Variable("freq")
    v.set_value(5e9)
    assert math.isclose(v.evaluate_or_raise(), 5e9, rel_tol=1e-09, abs_tol=1e-09)
    v.reset()
    assert v.value is UNASSIGNED


def test_variable_evaluate_unassigned():
    v = Variable("freq")
    assert v.evaluate() is UNASSIGNED


def test_variable_evaluate_assigned():
    v = Variable("freq")
    v.set_value(42)
    assert v.evaluate() == 42


def test_variable_variables_returns_self():
    v = Variable("freq")
    assert v.variables() == {v}


def test_variable_repr():
    v = Variable("freq")
    assert repr(v) == "Variable('freq')"


def test_variable_structural_equality_by_id():
    v1 = Variable("x")
    v2 = Variable("x")
    assert v1 == v2
    assert v1 is not v2


def test_variable_inequality_different_ids():
    v1 = Variable("x")
    v2 = Variable("y")
    assert v1 != v2


def test_variable_hash_consistent_with_eq():
    v1 = Variable("x")
    v2 = Variable("x")
    assert hash(v1) == hash(v2)


def test_variable_in_set_collapses_same_id():
    s = {Variable("x"), Variable("x"), Variable("y")}
    assert len(s) == 2


def test_variable_not_equal_to_other_types():
    v = Variable("x")
    assert v != "x"
    assert v != 42
    assert v != Constant(0)


# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------


def test_constant_int():
    c = Constant(5)
    assert c.value == 5
    assert c.evaluate() == 5
    assert c.variables() == set()


def test_constant_float():
    c = Constant(3.14)
    assert math.isclose(c.value, 3.14, rel_tol=1e-09, abs_tol=1e-09)


@pytest.mark.parametrize("bad", [True, False, "5", None, [1]])
def test_constant_rejects_non_numeric(bad):
    with pytest.raises(TypeError):
        Constant(bad)


def test_constant_repr():
    assert repr(Constant(5)) == "Constant(5)"


def test_constant_equality():
    assert Constant(5) == Constant(5)
    assert Constant(5) != Constant(6)


def test_constant_hash_consistent():
    assert hash(Constant(5)) == hash(Constant(5))


def test_constant_unequal_other_types():
    assert Constant(5) != 5  # constants only equal other Constants


# ---------------------------------------------------------------------------
# Arithmetic operators (BinaryOp, UnaryOp)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_val", "op_fn", "right_val", "expected"),
    [
        (3, lambda a, b: a + b, 5, 8),
        (3, lambda a, b: a - b, 5, -2),
        (3, lambda a, b: a * b, 5, 15),
        (10, lambda a, b: a / b, 5, 2),
    ],
)
def test_binary_arithmetic_evaluate(left_val, op_fn, right_val, expected):
    v = Variable("x")
    v.set_value(left_val)
    expr = op_fn(v, right_val)
    assert isinstance(expr, BinaryOp)
    assert expr.evaluate() == expected


def test_binary_op_unassigned_left():
    v = Variable("x")
    expr = v + 5
    assert expr.evaluate() is UNASSIGNED


def test_binary_op_unassigned_right():
    v = Variable("x")
    w = Variable("y")
    v.set_value(5)
    expr = v + w
    assert expr.evaluate() is UNASSIGNED


def test_reverse_arithmetic_ops():
    v = Variable("x")
    v.set_value(10)
    assert (5 + v).evaluate() == 15
    assert (5 - v).evaluate() == -5
    assert (5 * v).evaluate() == 50
    assert (50 / v).evaluate() == 5


def test_unary_neg():
    v = Variable("x")
    v.set_value(5)
    expr = -v
    assert isinstance(expr, UnaryOp)
    assert expr.evaluate() == -5


def test_unary_pos():
    v = Variable("x")
    v.set_value(-5)
    expr = +v
    assert isinstance(expr, UnaryOp)
    assert expr.evaluate() == -5


def test_unary_op_unassigned():
    v = Variable("x")
    assert (-v).evaluate() is UNASSIGNED


def test_binary_op_variables_set():
    v = Variable("x")
    w = Variable("y")
    expr = v + w
    assert expr.variables() == {v, w}


def test_unary_op_variables_set():
    v = Variable("x")
    assert (-v).variables() == {v}


def test_binary_op_structural_equality():
    v1 = Variable("x")
    v2 = Variable("x")
    assert (v1 + 5) == (v2 + 5)
    assert (v1 + 5) != (v1 + 6)


def test_binary_op_hash():
    v = Variable("x")
    assert hash(v + 5) == hash(v + 5)


def test_unary_op_structural_equality():
    v = Variable("x")
    assert -v == -v
    assert -v != +v


def test_unary_op_hash():
    v = Variable("x")
    assert hash(-v) == hash(-v)


def test_binary_op_repr():
    assert repr(Constant(1) + Constant(2)) == "(Constant(1) + Constant(2))"


def test_unary_op_repr():
    assert repr(-Constant(3)) == "(-Constant(3))"


def test_binary_op_unequal_other_type():
    v = Variable("x")
    assert (v + 5) != "anything"


def test_unary_op_unequal_other_type():
    v = Variable("x")
    assert (-v) != "anything"


def test_nested_arithmetic():
    v = Variable("x")
    v.set_value(2)
    expr = (v + 3) * 2 - 1
    assert expr.evaluate() == 9


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op_fn", "left_val", "right_val", "expected"),
    [
        (lambda a, b: a < b, 1, 2, True),
        (lambda a, b: a < b, 2, 1, False),
        (lambda a, b: a <= b, 2, 2, True),
        (lambda a, b: a > b, 3, 2, True),
        (lambda a, b: a >= b, 2, 2, True),
        (eq, 2, 2, True),
        (eq, 2, 3, False),
        (ne, 2, 3, True),
        (ne, 2, 2, False),
    ],
)
def test_comparison_evaluate(op_fn, left_val, right_val, expected):
    v = Variable("x")
    v.set_value(left_val)
    expr = op_fn(v, right_val)
    assert isinstance(expr, Comparison)
    assert expr.evaluate() is expected


def test_comparison_unassigned_left():
    v = Variable("x")
    assert (v < 5).evaluate() is UNASSIGNED


def test_comparison_unassigned_right():
    v = Variable("x")
    w = Variable("y")
    v.set_value(5)
    assert (v < w).evaluate() is UNASSIGNED


def test_comparison_variables():
    v = Variable("x")
    w = Variable("y")
    assert (v < w).variables() == {v, w}


def test_comparison_invalid_op_raises():
    v = Variable("x")
    with pytest.raises(ValueError, match="Comparison op must be"):
        Comparison("??", v, Constant(1))  # ty:ignore[invalid-argument-type]


def test_comparison_structural_equality():
    v1 = Variable("x")
    v2 = Variable("x")
    assert (v1 < 5) == (v2 < 5)
    assert (v1 < 5) != (v1 > 5)
    assert (v1 < 5) != (v1 < 6)


def test_comparison_hash_consistent():
    v = Variable("x")
    assert hash(v < 5) == hash(v < 5)


def test_comparison_repr():
    assert repr(Constant(1) < Constant(2)) == "(Constant(1) < Constant(2))"


def test_comparison_unequal_other_type():
    v = Variable("x")
    assert (v < 5) != "anything"


# ---------------------------------------------------------------------------
# Logical operators
# ---------------------------------------------------------------------------


def test_logical_and_via_operator():
    v = Variable("x")
    v.set_value(3)
    expr = (v > 0) & (v < 10)
    assert isinstance(expr, LogicalBinaryOp)
    assert expr.op == "and"
    assert expr.evaluate() is True


def test_logical_or_via_operator():
    v = Variable("x")
    v.set_value(15)
    expr = (v > 10) | (v < 5)
    assert isinstance(expr, LogicalBinaryOp)
    assert expr.op == "or"
    assert expr.evaluate() is True


def test_logical_not_via_operator():
    v = Variable("x")
    v.set_value(3)
    expr = ~(v > 10)
    assert isinstance(expr, LogicalNot)
    assert expr.evaluate() is True


def test_logical_and_named():
    v = Variable("x")
    v.set_value(3)
    assert and_(v > 0, v < 10).evaluate() is True


def test_logical_or_named():
    v = Variable("x")
    v.set_value(15)
    assert or_(v > 10, v < 5).evaluate() is True


def test_logical_not_named():
    v = Variable("x")
    v.set_value(3)
    assert not_(v > 10).evaluate() is True


def test_logical_and_unassigned_left():
    v = Variable("x")
    w = Variable("y")
    w.set_value(5)
    assert ((v < 5) & (w < 10)).evaluate() is UNASSIGNED


def test_logical_and_unassigned_right():
    v = Variable("x")
    w = Variable("y")
    v.set_value(5)
    assert ((v < 5) & (w < 10)).evaluate() is UNASSIGNED


def test_logical_or_unassigned_either():
    v = Variable("x")
    w = Variable("y")
    assert ((v < 5) | (w < 10)).evaluate() is UNASSIGNED


def test_logical_not_unassigned():
    v = Variable("x")
    assert (~(v < 5)).evaluate() is UNASSIGNED


def test_logical_and_returns_notimplemented_for_non_expression():
    v = Variable("x")
    result = v.__and__("not an expression")  # ty:ignore[invalid-argument-type]
    assert result is NotImplemented


def test_logical_or_returns_notimplemented_for_non_expression():
    v = Variable("x")
    result = v.__or__("not an expression")  # ty:ignore[invalid-argument-type]
    assert result is NotImplemented


def test_logical_binary_op_invalid_op():
    v = Variable("x")
    w = Variable("y")
    with pytest.raises(ValueError, match="LogicalBinaryOp op must be"):
        LogicalBinaryOp("xor", v, w)  # ty:ignore[invalid-argument-type]


def test_logical_binary_op_rejects_non_expression():
    with pytest.raises(TypeError, match="must be an Expression"):
        LogicalBinaryOp("and", 1, Variable("x"))  # ty:ignore[invalid-argument-type]


def test_logical_binary_op_message_mentions_eq_helper_for_bool():
    # The error message specifically helps users who wrote `var == lit`.
    with pytest.raises(TypeError, match=r"qprogram\.eq"):
        LogicalBinaryOp("and", True, Variable("x"))  # ty:ignore[invalid-argument-type]  # noqa: FBT003


def test_logical_not_rejects_non_expression():
    with pytest.raises(TypeError, match="must be an Expression"):
        LogicalNot("not an expression")  # ty:ignore[invalid-argument-type]


def test_logical_binary_op_variables():
    v = Variable("x")
    w = Variable("y")
    assert and_(v < 5, w > 0).variables() == {v, w}


def test_logical_not_variables():
    v = Variable("x")
    assert not_(v < 5).variables() == {v}


def test_logical_binary_op_structural_equality():
    v = Variable("x")
    a = and_(v < 5, v > 0)
    b = and_(v < 5, v > 0)
    assert a == b
    assert hash(a) == hash(b)


def test_logical_not_structural_equality():
    v = Variable("x")
    assert not_(v < 5) == not_(v < 5)
    assert hash(not_(v < 5)) == hash(not_(v < 5))


def test_logical_binary_op_repr():
    v = Variable("x")
    assert "and" in repr(and_(v < 5, v > 0))


def test_logical_not_repr():
    v = Variable("x")
    assert "not" in repr(not_(v < 5))


def test_logical_unequal_other_type():
    v = Variable("x")
    assert and_(v < 5, v > 0) != "anything"
    assert not_(v < 5) != "anything"


# ---------------------------------------------------------------------------
# Math functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "name", "input_val", "expected"),
    [
        (sin, "sin", 0.0, 0.0),
        (cos, "cos", 0.0, 1.0),
        (tan, "tan", 0.0, 0.0),
        (exp, "exp", 0.0, 1.0),
        (log, "log", 1.0, 0.0),
        (sqrt, "sqrt", 4.0, 2.0),
    ],
)
def test_math_funcs(fn, name, input_val, expected):
    v = Variable("x")
    v.set_value(input_val)
    expr = fn(v)
    assert isinstance(expr, MathFunc)
    assert expr.name == name
    assert math.isclose(expr.evaluate(), expected, abs_tol=1e-10)


def test_math_func_unassigned():
    v = Variable("x")
    assert sin(v).evaluate() is UNASSIGNED


def test_math_func_with_literal():
    expr = sin(0.0)
    assert math.isclose(expr.evaluate_or_raise(), 0.0, abs_tol=1e-10)


def test_abs_via_builtin():
    v = Variable("x")
    v.set_value(-5)
    expr = abs(v)
    assert isinstance(expr, MathFunc)
    assert expr.name == "abs"
    assert expr.evaluate() == 5


def test_abs_preserves_integer():
    v = Variable("x")
    v.set_value(-3)
    assert abs(v).evaluate() == 3


def test_minimum_two_args():
    expr = minimum(Variable("x"), 5)
    v = expr.operands[0]
    assert isinstance(v, Variable)
    v.set_value(3)
    assert minimum(v, 5).evaluate() == 3
    v.set_value(10)
    assert minimum(v, 5).evaluate() == 5


def test_minimum_three_args():
    v = Variable("x")
    v.set_value(2)
    assert minimum(v, 5, 10).evaluate() == 2


def test_minimum_requires_at_least_two_args():
    with pytest.raises(TypeError, match="at least two"):
        minimum(Variable("x"))


def test_maximum_two_args():
    v = Variable("x")
    v.set_value(3)
    assert maximum(v, 5).evaluate() == 5


def test_maximum_requires_at_least_two_args():
    with pytest.raises(TypeError, match="at least two"):
        maximum(Variable("x"))


def test_math_func_unknown_name():
    with pytest.raises(ValueError, match="Unknown math function"):
        MathFunc("nonsense", (Constant(1),))


def test_math_func_requires_operands():
    with pytest.raises(ValueError, match="at least one operand"):
        MathFunc("sin", ())


def test_math_func_variables():
    v = Variable("x")
    w = Variable("y")
    assert minimum(v, w).variables() == {v, w}
    assert sin(v + w).variables() == {v, w}


def test_math_func_structural_equality():
    v1 = Variable("x")
    v2 = Variable("x")
    assert sin(v1) == sin(v2)
    assert sin(v1) != cos(v1)


def test_math_func_hash():
    v = Variable("x")
    assert hash(sin(v)) == hash(sin(v))


def test_math_func_repr():
    v = Variable("x")
    assert "sin" in repr(sin(v))


def test_math_func_unequal_other_type():
    assert sin(Variable("x")) != "anything"


# ---------------------------------------------------------------------------
# Where (ternary)
# ---------------------------------------------------------------------------


def test_where_true_branch():
    v = Variable("x")
    v.set_value(3)
    assert where(v < 5, v, 100).evaluate() == 3


def test_where_false_branch():
    v = Variable("x")
    v.set_value(10)
    assert where(v < 5, v, 100).evaluate() == 100


def test_where_unassigned_condition():
    v = Variable("x")
    assert where(v < 5, 1, 2).evaluate() is UNASSIGNED


def test_where_chosen_branch_only_evaluated():
    """When the condition is True, the else branch isn't required to be assigned."""
    cond = Variable("cond")
    used = Variable("used")
    unused = Variable("unused")
    cond.set_value(1)
    used.set_value(42)
    # ``unused`` stays UNASSIGNED, but ``where`` doesn't touch it.
    expr = where(eq(cond, 1), used, unused)
    assert expr.evaluate() == 42


def test_where_rejects_non_expression_condition():
    with pytest.raises(TypeError, match="must be an Expression"):
        Where(True, Constant(1), Constant(2))  # ty:ignore[invalid-argument-type]  # noqa: FBT003


def test_where_helper_wraps_literal_branches():
    v = Variable("c")
    v.set_value(1)
    expr = where(eq(v, 1), 1, 2)
    assert isinstance(expr.then, Constant)
    assert isinstance(expr.else_, Constant)


def test_where_variables_union():
    cond = Variable("c")
    then = Variable("t")
    else_ = Variable("e")
    expr = where(eq(cond, 1), then, else_)
    assert expr.variables() == {cond, then, else_}


def test_where_structural_equality():
    v = Variable("c")
    a = where(eq(v, 1), 0, 1)
    b = where(eq(v, 1), 0, 1)
    assert a == b
    assert hash(a) == hash(b)


def test_where_repr():
    v = Variable("c")
    assert "where" in repr(where(eq(v, 1), 0, 1))


def test_where_unequal_other_type():
    v = Variable("c")
    assert where(eq(v, 1), 0, 1) != "anything"


# ---------------------------------------------------------------------------
# Expression.__bool__ guard
# ---------------------------------------------------------------------------


def test_expression_bool_raises():
    v = Variable("x")
    with pytest.raises(TypeError, match="no truth value"):
        _ = bool(v < 5)


def test_expression_bool_message_points_to_where():
    v = Variable("x")
    with pytest.raises(TypeError, match="where"):
        _ = bool(v < 5)


def test_expression_in_if_raises():
    v = Variable("x")
    v.set_value(3)

    def _bool_in_if() -> None:
        if v < 5:
            ...

    with pytest.raises(TypeError):
        _bool_in_if()


# ---------------------------------------------------------------------------
# evaluate_or_raise
# ---------------------------------------------------------------------------


def test_evaluate_or_raise_assigned():
    v = Variable("x")
    v.set_value(7)
    expr = v + 3
    assert expr.evaluate_or_raise() == 10


def test_evaluate_or_raise_unassigned():
    v = Variable("x")
    with pytest.raises(UnassignedVariableError) as exc_info:
        (v + 1).evaluate_or_raise()
    assert v in exc_info.value.free_variables


# ---------------------------------------------------------------------------
# _wrap helper
# ---------------------------------------------------------------------------


def test_wrap_passes_expressions_through():
    v = Variable("x")
    assert _wrap(v) is v


def test_wrap_int_creates_constant():
    wrapped = _wrap(5)
    assert isinstance(wrapped, Constant)
    assert wrapped.value == 5


def test_wrap_float_creates_constant():
    wrapped = _wrap(3.14)
    assert isinstance(wrapped, Constant)


@pytest.mark.parametrize("bad", ["str", None, [1], True])
def test_wrap_rejects_other_types(bad):
    with pytest.raises(TypeError):
        _wrap(bad)


# ---------------------------------------------------------------------------
# Variables on Expression base
# ---------------------------------------------------------------------------


def test_expression_is_abstract():
    # Can't instantiate the abstract base.
    with pytest.raises(TypeError):
        Expression()
