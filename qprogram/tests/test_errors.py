"""Tests for the QProgram exception hierarchy (qprogram.errors)."""

from __future__ import annotations

import pytest

from qprogram import (
    BusNotAvailableError,
    CompilationError,
    HardwareError,
    InvalidVariableIdError,
    QProgramError,
    UnassignedVariableError,
    UnsupportedOperationError,
    ValidationError,
    Variable,
    WaveformResolutionError,
)
from qprogram.serialization.parser import ParseError


@pytest.mark.parametrize(
    "exc_cls",
    [
        ValidationError,
        UnsupportedOperationError,
        BusNotAvailableError,
        WaveformResolutionError,
        CompilationError,
        HardwareError,
        ParseError,
    ],
)
def test_subclass_of_qprogram_error(exc_cls):
    assert issubclass(exc_cls, QProgramError)


def test_qprogram_error_subclass_of_exception():
    assert issubclass(QProgramError, Exception)


def test_invalid_variable_id_error_hierarchy():
    assert issubclass(InvalidVariableIdError, ValidationError)
    assert issubclass(InvalidVariableIdError, ValueError)
    assert issubclass(InvalidVariableIdError, QProgramError)


def test_unassigned_variable_error_hierarchy():
    assert issubclass(UnassignedVariableError, ValidationError)
    assert issubclass(UnassignedVariableError, ValueError)
    assert issubclass(UnassignedVariableError, QProgramError)


def test_invalid_variable_id_error_pattern_message():
    err = InvalidVariableIdError("1bad")
    assert err.id == "1bad"
    assert err.reserved is False
    assert "1bad" in str(err)
    assert "must match" in str(err)


def test_invalid_variable_id_error_reserved_message():
    err = InvalidVariableIdError("if", reserved=True)
    assert err.id == "if"
    assert err.reserved is True
    assert "if" in str(err)
    assert "reserved" in str(err).lower()


def test_invalid_variable_id_error_default_reserved_is_false():
    err = InvalidVariableIdError("bad space")
    assert err.reserved is False


def test_unassigned_variable_error_carries_expression():
    v = Variable("x")
    expr = v + 5

    err = UnassignedVariableError(expr)
    assert err.expression is expr
    assert v in err.free_variables
    assert "x" in str(err)


def test_unassigned_variable_error_message_mentions_expression():
    v = Variable("freq")
    expr = v * 2

    err = UnassignedVariableError(expr)
    # The repr of the expression should appear in the message.
    assert repr(expr) in str(err)


def test_parse_error_with_line_number():
    err = ParseError("bad token", line_num=42)
    assert err.line_num == 42
    assert "Line 42" in str(err)
    assert "bad token" in str(err)


def test_parse_error_without_line_number():
    err = ParseError("bad token")
    assert err.line_num == 0
    assert "Line" not in str(err)  # No line prefix when line_num is 0
    assert "bad token" in str(err)


def test_validation_error_not_a_value_error_by_default():
    # The base ValidationError does NOT extend ValueError (only the two
    # specific subclasses do, for back-compat).
    err = ValidationError("test")
    assert isinstance(err, QProgramError)
    assert not isinstance(err, ValueError)


def test_can_catch_invalid_id_as_value_error():
    bad_id = "1bad"
    with pytest.raises(ValueError, match="must match"):
        raise InvalidVariableIdError(bad_id)


def test_can_catch_unassigned_as_value_error():
    v = Variable("x")
    with pytest.raises(ValueError):
        raise UnassignedVariableError(v + 5)


def test_can_catch_all_via_qprogram_error():
    """Single except QProgramError must catch every defined exception class."""
    classes = [
        QProgramError("base"),
        ValidationError("validation"),
        InvalidVariableIdError("123"),
        UnassignedVariableError(Variable("x") + 5),
        UnsupportedOperationError("unsupported"),
        BusNotAvailableError("bus"),
        WaveformResolutionError("alias"),
        CompilationError("compile"),
        HardwareError("hw"),
        ParseError("parse"),
    ]
    for exc in classes:
        with pytest.raises(QProgramError):
            raise exc


def test_platform_side_classes_instantiable():
    # Each should construct cleanly with a string message.
    for cls in [
        UnsupportedOperationError,
        BusNotAvailableError,
        WaveformResolutionError,
        CompilationError,
        HardwareError,
    ]:
        err = cls("test message")
        assert "test message" in str(err)
