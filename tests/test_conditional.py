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
"""Tests for the ``if_`` / ``elif_`` / ``else_`` conditional construct.

Covers:

- :class:`MeasurementRef` construction, structural equality, expression tokens.
- ``handle.state`` proxy: builds ``Comparison`` for ``== int`` / ``!= int``,
  rejects float / non-int operands.
- :class:`Conditional` AST shape: arms list, optional else_body, ``append``
  raises, ``walk`` / ``variables`` / ``buses`` / ``waveforms`` aggregate
  across arms, instance-aware ``required_capabilities``.
- Builder semantics: ``if_`` / ``elif_`` / ``else_`` chain detection, the
  loud-failure cases (orphan elif, chain break, elif after else).
- :func:`validate` classification gating: ``missing-classification`` and
  ``unknown-measurement``.
- Round-trip through ``dumps``/``loads`` is byte-identical.
"""

from __future__ import annotations

from typing import cast

import pytest

from qprogram import QProgram, ValidationError, dumps, loads, validate
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.protocol import (
    BusCapabilities,
    CompilerCapabilities,
    PlatformCapabilities,
    Profile,
    expression_tokens,
    register_profile,
)
from qprogram.result import MeasurementHandle
from qprogram.sweeps import Range
from qprogram.variable import (
    UNASSIGNED,
    Comparison,
    Constant,
    Expression,
    MeasurementRef,
    _HandleFieldAccess,
    eq,
    ne,
)
from qprogram.waveforms import IQPair, Square

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_comparison(expr: object) -> Comparison:
    assert isinstance(expr, Comparison)
    return expr


def _assert_constant(expr: object) -> Constant:
    assert isinstance(expr, Constant)
    return expr


def _readout_wf() -> IQPair:
    return IQPair(I=Square(0.5, 200), Q=Square(0.0, 200))


def _weights_wf() -> IQPair:
    return IQPair(I=Square(1.0, 200), Q=Square(1.0, 200))


def _full_caps() -> PlatformCapabilities:
    """A :class:`PlatformCapabilities` covering every token a conditional + measurement might emit.

    Bus-touching ops + waveforms go on the default-bus slot; block / expression /
    measurement-field tokens go on the platform slot. Both halves of each slot share the same
    profile so the validator sees identical rt/host capabilities (the conditional tests don't
    exercise the rt/host split).
    """
    bus_profile_name = "test-full-conditional-bus"
    platform_profile_name = "test-full-conditional-platform"
    registry = __import__("qprogram.protocol", fromlist=["PROFILE_REGISTRY"]).PROFILE_REGISTRY
    if bus_profile_name not in registry:
        register_profile(
            Profile(
                name=bus_profile_name,
                version=(0, 0, 1),
                extends=None,
                capabilities=frozenset(
                    {
                        "op.measure",
                        "op.play",
                        "op.sync",
                        "waveform.iq",
                        "waveform.alias",
                        "waveform.square",
                        "waveform.iq_pair",
                    },
                ),
            ),
        )
    if platform_profile_name not in registry:
        register_profile(
            Profile(
                name=platform_profile_name,
                version=(0, 0, 1),
                extends=None,
                capabilities=frozenset(
                    {
                        "block.block",
                        "block.conditional",
                        "expr.constant",
                        "expr.measurement_ref",
                        "expr.comparison",
                        "measure.fields.iq",
                        "measure.fields.state",
                    },
                ),
            ),
        )
    bus_cc = CompilerCapabilities.from_profile(bus_profile_name)
    platform_cc = CompilerCapabilities.from_profile(platform_profile_name)
    bus_slot = BusCapabilities(rt=bus_cc, host=bus_cc)
    platform_slot = BusCapabilities(rt=platform_cc, host=platform_cc)
    return PlatformCapabilities(
        bus={},
        platform=platform_slot,
        default_bus_profile=bus_slot,
    )


def _validate(p: QProgram, caps: PlatformCapabilities) -> list:
    """Run the validator and return just the diagnostics (drops the plan)."""
    diagnostics, _ = validate(p, caps)
    return diagnostics


def _measure_with_state(p: QProgram) -> MeasurementHandle:
    return p.measure("readout_q0", _readout_wf(), _weights_wf(), fields=("iq", "state"))


# ---------------------------------------------------------------------------
# MeasurementRef construction & equality
# ---------------------------------------------------------------------------


def test_measurement_ref_carries_handle_and_field() -> None:
    h = MeasurementHandle("q0_m0")
    ref = MeasurementRef(h, "state")
    assert ref.handle is h
    assert ref.field == "state"


def test_measurement_ref_rejects_unknown_field() -> None:
    h = MeasurementHandle("q0_m0")
    with pytest.raises(ValueError, match="MeasurementRef field must be one of"):
        MeasurementRef(h, "iq")
    with pytest.raises(ValueError):
        MeasurementRef(h, "")


def test_measurement_ref_structural_equality_by_name_and_field() -> None:
    h1 = MeasurementHandle("q0_m0")
    h2 = MeasurementHandle("q0_m0")
    h3 = MeasurementHandle("q1_m0")
    assert MeasurementRef(h1, "state") == MeasurementRef(h2, "state")
    assert MeasurementRef(h1, "state") != MeasurementRef(h3, "state")
    assert hash(MeasurementRef(h1, "state")) == hash(MeasurementRef(h2, "state"))


def test_measurement_ref_variables_is_empty() -> None:
    """A MeasurementRef is a distinct kind of binding from Variable."""
    h = MeasurementHandle("q0_m0")
    assert MeasurementRef(h, "state").variables() == set()


def test_measurement_ref_evaluate_propagates_through_handle() -> None:
    h = MeasurementHandle("q0_m0")
    ref = MeasurementRef(h, "state")
    assert ref.evaluate() is UNASSIGNED
    h._set_value("state", 1)
    assert ref.evaluate() == 1


# ---------------------------------------------------------------------------
# handle.state proxy
# ---------------------------------------------------------------------------


def test_handle_state_returns_proxy() -> None:
    h = MeasurementHandle("q0_m0")
    assert isinstance(h.state, _HandleFieldAccess)


def test_handle_state_eq_int_builds_comparison() -> None:
    h = MeasurementHandle("q0_m0")
    c = h.state == 0
    assert isinstance(c, Comparison)
    assert c.op == "=="
    assert isinstance(c.left, MeasurementRef)
    assert c.left.handle.name == "q0_m0"
    assert isinstance(c.right, Constant)
    assert c.right.value == 0


def test_handle_state_ne_int_builds_comparison() -> None:
    h = MeasurementHandle("q0_m0")
    c = h.state != 1
    assert isinstance(c, Comparison)
    assert c.op == "!="


def test_handle_state_rejects_float() -> None:
    h = MeasurementHandle("q0_m0")
    with pytest.raises(TypeError, match="can only be compared to int"):
        _ = h.state == 0.5


def test_handle_state_rejects_bool() -> None:
    h = MeasurementHandle("q0_m0")
    # bool is a subclass of int but disallowed (would be confusing).
    with pytest.raises(TypeError):
        _ = h.state == True  # ruff: ignore[true-false-comparison]


def test_handle_state_proxy_is_unhashable() -> None:
    h = MeasurementHandle("q0_m0")
    with pytest.raises(TypeError):
        hash(h.state)


# ---------------------------------------------------------------------------
# expression_tokens
# ---------------------------------------------------------------------------


def test_expression_tokens_measurement_ref() -> None:
    h = MeasurementHandle("q0_m0")
    assert expression_tokens(MeasurementRef(h, "state")) == {"expr.measurement_ref"}


def test_expression_tokens_comparison_with_measurement_ref() -> None:
    h = MeasurementHandle("q0_m0")
    c = h.state == 0
    assert expression_tokens(c) == {"expr.comparison", "expr.measurement_ref", "expr.constant"}


# ---------------------------------------------------------------------------
# Conditional AST
# ---------------------------------------------------------------------------


def test_conditional_append_raises() -> None:
    cond = Conditional()
    body = Block()
    with pytest.raises(ValidationError, match="Cannot append directly to a Conditional"):
        cond.append(body)


def test_conditional_required_capabilities_includes_expression_tokens() -> None:
    h = MeasurementHandle("q0_m0")
    cond = Conditional()
    cond.arms.append((h.state == 0, Block()))
    caps = cond.required_capabilities()
    assert "block.conditional" in caps
    assert "expr.comparison" in caps
    assert "expr.measurement_ref" in caps
    assert "expr.constant" in caps


def test_conditional_walk_yields_self_then_arm_bodies() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    with p.elif_(m.state == 1):
        p.play("drive_q0", "b")
    cond = p.body.elements[-1]
    nodes = list(cond.walk())
    assert nodes[0] is cond
    type_names = [type(n).__name__ for n in nodes]
    # Each arm body, then the Play inside it
    assert type_names.count("Block") == 2
    assert type_names.count("Play") == 2


def test_conditional_walk_includes_else_body() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    with p.else_():
        p.sync()
    cond = p.body.elements[-1]
    types = [type(n).__name__ for n in cond.walk()]
    assert "Sync" in types  # else body's content surfaces


def test_conditional_aggregates_buses_and_variables() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    var = p.variable("d")
    with p.if_(m.state == 0):
        p.wait("drive_q0", var)
    with p.else_():
        p.play("readout_q0", "x")
    cond = p.body.elements[-1]
    assert "drive_q0" in cond.buses()
    assert "readout_q0" in cond.buses()
    assert var in cond.variables()


# ---------------------------------------------------------------------------
# Builder semantics
# ---------------------------------------------------------------------------


def test_if_alone_creates_conditional_with_one_arm_no_else() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    cond = p.body.elements[-1]
    assert isinstance(cond, Conditional)
    assert len(cond.arms) == 1
    assert cond.else_body is None


def test_if_elif_elif_else_chain() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    with p.elif_(m.state == 1):
        p.play("drive_q0", "b")
    with p.elif_(m.state == 2):
        p.play("drive_q0", "c")
    with p.else_():
        p.sync()
    cond = p.body.elements[-1]
    assert isinstance(cond, Conditional)
    assert len(cond.arms) == 3
    assert cond.else_body is not None
    arms = [(_assert_comparison(op), body) for op, body in cond.arms]
    assert [op.op for op, _ in arms] == ["==", "==", "=="]
    assert [_assert_constant(op.right).value for op, _ in arms] == [0, 1, 2]


def test_nested_conditionals() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0), p.if_(m.state == 1):
        p.play("drive_q0", "inner")
    outer = p.body.elements[-1]
    assert isinstance(outer, Conditional)
    inner = outer.arms[0][1].elements[0]
    assert isinstance(inner, Conditional)


def test_op_between_if_and_elif_breaks_chain() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    p.sync()  # breaks the chain
    with pytest.raises(ValidationError, match="no open conditional chain"), p.elif_(m.state == 1):
        pass


def test_block_between_if_and_elif_breaks_chain() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    with p.block():
        p.sync()
    with pytest.raises(ValidationError), p.elif_(m.state == 1):
        pass


def test_elif_without_if_raises() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with pytest.raises(ValidationError, match="no open conditional chain"), p.elif_(m.state == 0):
        pass


def test_else_without_if_raises() -> None:
    p = QProgram()
    with pytest.raises(ValidationError, match="no open conditional chain"), p.else_():
        pass


def test_elif_after_else_raises() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        pass
    with p.else_():
        pass
    with pytest.raises(ValidationError, match="no open conditional chain"), p.elif_(m.state == 1):
        pass


def test_multiple_else_raises() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        pass
    with p.else_():
        pass
    with pytest.raises(ValidationError, match="no open conditional chain"), p.else_():
        pass


def test_if_rejects_non_comparison_condition() -> None:
    p = QProgram()
    # ``42`` is intentionally the wrong type — the validator must reject it.
    # ``cast`` smuggles the literal past the static signature so the runtime
    # check is the thing under test rather than the type checker.
    with pytest.raises(ValidationError, match=r"if_\(\) expects"), p.if_(cast("Expression", 42)):
        pass


def test_if_rejects_wrong_comparison_shape() -> None:
    """A condition must compare a measurement-state ref; a bare variable is rejected."""
    p = QProgram()
    var = p.variable("v")
    with pytest.raises(ValidationError, match="measurement-state ref"), p.if_(eq(var, 0)):
        pass


# ---------------------------------------------------------------------------
# Wider condition shapes — qp.eq, m1.state == m2.state, reverse forms
# ---------------------------------------------------------------------------


def test_qp_eq_accepts_handle_field_access() -> None:
    """``qp.eq(handle.state, 0)`` builds the same Comparison as the operator form."""
    h = MeasurementHandle("q0_m0")
    operator_form = h.state == 0
    helper_form = eq(h.state, 0)
    assert operator_form == helper_form
    assert isinstance(helper_form.left, MeasurementRef)
    assert isinstance(helper_form.right, Constant)
    assert helper_form.right.value == 0


def test_qp_ne_accepts_handle_field_access() -> None:
    h = MeasurementHandle("q0_m0")
    assert (h.state != 1) == ne(h.state, 1)


def test_qp_eq_accepts_handle_field_access_on_right() -> None:
    """``qp.eq(0, handle.state)`` is symmetric with the reversed form."""
    h = MeasurementHandle("q0_m0")
    c = eq(0, h.state)
    assert isinstance(c.left, Constant)
    assert isinstance(c.right, MeasurementRef)


def test_native_eq_reversed_int_first() -> None:
    """``0 == handle.state`` falls back to the proxy's ``__eq__`` and works."""
    h = MeasurementHandle("q0_m0")
    c = 0 == h.state  # ruff: ignore[yoda-conditions] — deliberately yoda to verify the reverse path
    assert isinstance(c, Comparison)
    # Python invoked the proxy as ``proxy.__eq__(0)``, so the MeasurementRef
    # is the left operand of the Comparison regardless of source order.
    assert isinstance(c.left, MeasurementRef)
    assert isinstance(c.right, Constant)


def test_proxy_eq_proxy_builds_ref_ref_comparison() -> None:
    h1 = MeasurementHandle("q0_m0")
    h2 = MeasurementHandle("q1_m0")
    c = h1.state == h2.state
    assert isinstance(c, Comparison)
    assert c.op == "=="
    assert isinstance(c.left, MeasurementRef)
    assert c.left.handle.name == "q0_m0"
    assert isinstance(c.right, MeasurementRef)
    assert c.right.handle.name == "q1_m0"


def test_proxy_ne_proxy_builds_ref_ref_comparison() -> None:
    h1 = MeasurementHandle("q0_m0")
    h2 = MeasurementHandle("q1_m0")
    c = h1.state != h2.state
    assert isinstance(c, Comparison)
    assert c.op == "!="


def test_proxy_eq_raw_measurement_ref() -> None:
    h1 = MeasurementHandle("q0_m0")
    h2 = MeasurementHandle("q1_m0")
    raw = MeasurementRef(h2, "state")
    c = h1.state == raw
    assert isinstance(c, Comparison)
    assert isinstance(c.left, MeasurementRef)
    assert isinstance(c.right, MeasurementRef)
    assert c.right.handle.name == "q1_m0"


def test_if_accepts_proxy_to_proxy() -> None:
    p = QProgram()
    m1 = _measure_with_state(p)
    m2 = p.measure("readout_q1", _readout_wf(), _weights_wf(), fields=("state",))
    with p.if_(m1.state == m2.state):
        p.play("drive_q0", "a")
    cond = p.body.elements[-1]
    assert isinstance(cond, Conditional)
    arm_cond = cond.arms[0][0]
    assert isinstance(arm_cond, Comparison)
    assert isinstance(arm_cond.left, MeasurementRef)
    assert isinstance(arm_cond.right, MeasurementRef)


def test_if_accepts_qp_eq_form() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(eq(m.state, 0)):
        p.play("drive_q0", "a")
    cond = p.body.elements[-1]
    assert isinstance(cond, Conditional)


def test_if_accepts_constant_on_left() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    # Yoda is deliberate: Python falls back to the proxy's __eq__, which
    # builds a Comparison instead of a bool. Static type-checkers infer
    # ``bool`` for ``0 == m.state``; ``cast`` records that the runtime value
    # is the Comparison under test.
    condition = cast("Expression", 0 == m.state)  # ruff: ignore[yoda-conditions]
    with p.if_(condition):
        p.play("drive_q0", "a")


def test_validator_classification_fires_for_either_side_in_ref_ref() -> None:
    """When ``m1.state == m2.state`` and one measurement lacks state, gate fires."""
    p = QProgram()
    m_with = _measure_with_state(p)  # has state classification
    m_without = p.measure("readout_q1", _readout_wf(), _weights_wf())  # no state
    with p.if_(m_with.state == m_without.state):
        p.play("drive_q0", "a")
    diags = _validate(p, _full_caps())
    assert any(d.code == "missing-classification" for d in diags)


def test_if_rejects_float_constant() -> None:
    """The Constant value must be int; floats trip the validator."""
    p = QProgram()
    m = _measure_with_state(p)
    bad = Comparison("==", MeasurementRef(MeasurementHandle(m.name), "state"), Constant(0.5))
    with pytest.raises(ValidationError, match=r"int literal expected"), p.if_(bad):
        pass


def test_if_error_message_mentions_handle_state() -> None:
    """The error message guides the user to the right shape."""
    p = QProgram()
    with pytest.raises(ValidationError, match=r"handle\.state == 0"), p.if_(cast("Expression", 42)):
        pass


def test_chain_inside_loop_body() -> None:
    """A conditional inside a loop should be a standalone chain at its level."""
    p = QProgram()
    m = _measure_with_state(p)
    var = p.variable("v")
    with p.sweep(var, Range(0, 5, 1)):
        with p.if_(m.state == 0):
            p.play("drive_q0", "a")
        with p.elif_(m.state == 1):
            p.play("drive_q0", "b")
    loop_block = p.body.elements[-1]
    assert isinstance(loop_block, Block)
    cond = loop_block.elements[-1]
    assert isinstance(cond, Conditional)
    assert len(cond.arms) == 2


# ---------------------------------------------------------------------------
# Validation — classification gating
# ---------------------------------------------------------------------------


def test_validate_missing_classification_emits_diagnostic() -> None:
    p = QProgram()
    # measure with NO state classification
    m = p.measure("readout_q0", _readout_wf(), _weights_wf())  # default fields=("iq",)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    diags = _validate(p, _full_caps())
    assert any(d.code == "missing-classification" for d in diags)


def test_validate_with_state_field_is_clean() -> None:
    p = QProgram()
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    diags = _validate(p, _full_caps())
    assert not any(d.code in {"missing-classification", "unknown-measurement"} for d in diags)


def test_validate_unknown_measurement_emits_diagnostic() -> None:
    """Construct a Conditional referencing a handle that no measurement op produces."""
    p = QProgram()
    _measure_with_state(p)  # registers q0_m0 in the program
    fake = MeasurementHandle("q9_m99")
    cond = Conditional()
    cond.arms.append((Comparison("==", MeasurementRef(fake, "state"), Constant(0)), Block()))
    p.body.append(cond)
    diags = _validate(p, _full_caps())
    assert any(d.code == "unknown-measurement" for d in diags)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_if_only() -> None:
    p = QProgram(label="x")
    m = _measure_with_state(p)
    with p.if_(m.state == 0):
        p.play("drive_q0", "a")
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text
    assert p.body == reloaded.body


def test_round_trip_if_elif_else() -> None:
    p = QProgram(label="x")
    m = _measure_with_state(p)
    with p.if_(m.state == 1):
        p.play("drive_q0", "a")
    with p.elif_(m.state == 0):
        p.play("drive_q0", "b")
    with p.else_():
        p.sync()
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text


def test_round_trip_nested() -> None:
    p = QProgram(label="x")
    m = _measure_with_state(p)
    with p.if_(m.state == 0), p.if_(m.state == 1):
        p.play("drive_q0", "inner")
    text = dumps(p)
    reloaded = loads(text)
    assert dumps(reloaded) == text
