"""Tests for the capability-protocol type layer.

Covers :class:`Diagnostic`, :class:`Profile`, :class:`CompilerCapabilities`,
the global :data:`PROFILE_REGISTRY` (register/resolve/cycle detection), the
capability-token registry, and the waveform-class → token mapping.

Behaviour around how operations *declare* their required capabilities is
in :mod:`test_required_capabilities`; the validator itself is in
:mod:`test_validation`.
"""

from __future__ import annotations

import dataclasses

import pytest

from qprogram.protocol import (
    CAPABILITY_REGISTRY,
    CompilerCapabilities,
    Diagnostic,
    Profile,
    expression_tokens,
    register_capability_tokens,
    register_profile,
    register_waveform_token,
    resolve_profile,
    validate_tokens,
    waveform_token,
)
from qprogram.variable import Constant, Variable, sin
from qprogram.waveforms import IQDrag, IQPair, Square

# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


def test_diagnostic_is_frozen_dataclass() -> None:
    d = Diagnostic(severity="error", code="x", message="m")
    assert dataclasses.is_dataclass(d)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.code = "y"


def test_diagnostic_str_includes_severity_code_message() -> None:
    d = Diagnostic(severity="error", code="missing-capability", message="needs op.play")
    rendered = str(d)
    assert "error" in rendered
    assert "missing-capability" in rendered
    assert "needs op.play" in rendered


# ---------------------------------------------------------------------------
# Token registry
# ---------------------------------------------------------------------------


def test_base_capability_registry_contains_core_tokens() -> None:
    for tok in ("op.play", "op.measure", "block.for_loop", "sweep.linear", "waveform.iq_drag"):
        assert tok in CAPABILITY_REGISTRY


def test_register_capability_tokens_is_idempotent() -> None:
    register_capability_tokens("test.protocol.idempotent")
    register_capability_tokens("test.protocol.idempotent")
    assert "test.protocol.idempotent" in CAPABILITY_REGISTRY


def test_register_capability_tokens_rejects_malformed_tokens() -> None:
    for bad in ("", ".leading", "trailing.", "double..dot"):
        with pytest.raises(ValueError, match="Invalid capability token"):
            register_capability_tokens(bad)


def test_validate_tokens_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown capability token"):
        validate_tokens(["op.play", "totally.bogus.token"])


def test_validate_tokens_passes_for_registered_tokens() -> None:
    validate_tokens(["op.play", "op.measure", "block.for_loop"])  # no raise


# ---------------------------------------------------------------------------
# Waveform tokens
# ---------------------------------------------------------------------------


def test_waveform_token_returns_canonical_token_for_known_classes() -> None:
    assert waveform_token(Square(amplitude=0.5, duration=100)) == "waveform.square"
    assert (
        waveform_token(IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1)) == "waveform.iq_drag"
    )
    assert waveform_token(IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))) == "waveform.iq_pair"


def test_waveform_token_returns_none_for_string_alias() -> None:
    assert waveform_token("readout_pulse") is None


def test_register_waveform_token_extends_registry_and_dispatch() -> None:
    class _Fake:
        pass

    register_waveform_token(_Fake, "waveform.test_fake_class")
    assert "waveform.test_fake_class" in CAPABILITY_REGISTRY
    # We deliberately pass a non-Waveform instance to verify the dispatch
    # table — that's how vendor packages register their own classes.
    assert waveform_token(_Fake()) == "waveform.test_fake_class"


# ---------------------------------------------------------------------------
# expression_tokens
# ---------------------------------------------------------------------------


def test_expression_tokens_constant_and_variable() -> None:
    assert expression_tokens(Constant(5)) == {"expr.constant"}
    assert expression_tokens(Variable("v")) == {"expr.variable"}


def test_expression_tokens_recurses_through_binary_op() -> None:
    expr = Variable("v") * 2 + Constant(10)  # BinaryOp(BinaryOp(var, *, const), +, const)
    assert expression_tokens(expr) == {"expr.binary_op", "expr.variable", "expr.constant"}


def test_expression_tokens_handles_math_func() -> None:
    expr = sin(Variable("x")) + Constant(0)
    toks = expression_tokens(expr)
    assert "expr.math.sin" in toks
    assert "expr.binary_op" in toks
    assert "expr.variable" in toks
    assert "expr.constant" in toks


def test_expression_tokens_returns_empty_for_plain_numeric() -> None:
    assert expression_tokens(42) == set()
    assert expression_tokens(3.14) == set()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_profile_post_init_rejects_unknown_capability() -> None:
    with pytest.raises(ValueError, match="Unknown capability token"):
        Profile(
            name="test-bad",
            version=(1, 0, 0),
            extends=None,
            capabilities=frozenset({"this.does.not.exist"}),
        )


def test_profile_accepts_registered_capabilities() -> None:
    p = Profile(
        name="test-good",
        version=(1, 0, 0),
        extends=None,
        capabilities=frozenset({"op.play", "op.sync"}),
    )
    assert p.capabilities == frozenset({"op.play", "op.sync"})


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run with a fresh PROFILE_REGISTRY so tests don't pollute the global one."""
    monkeypatch.setattr("qprogram.protocol.PROFILE_REGISTRY", {})


@pytest.mark.usefixtures("isolated_registry")
def test_register_profile_then_resolve() -> None:
    p = Profile(name="test-x", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.play"}))
    register_profile(p)
    assert resolve_profile("test-x") is p


@pytest.mark.usefixtures("isolated_registry")
def test_register_profile_idempotent_same_object() -> None:
    p = Profile(name="test-id", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.play"}))
    register_profile(p)
    register_profile(p)
    assert resolve_profile("test-id") is p


@pytest.mark.usefixtures("isolated_registry")
def test_register_profile_rejects_different_content_same_name() -> None:
    p1 = Profile(name="dup", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.play"}))
    p2 = Profile(name="dup", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.sync"}))
    register_profile(p1)
    with pytest.raises(ValueError, match="already registered"):
        register_profile(p2)


@pytest.mark.usefixtures("isolated_registry")
def test_resolve_profile_missing() -> None:
    with pytest.raises(KeyError, match="Unknown profile"):
        resolve_profile("not-there")


# ---------------------------------------------------------------------------
# CompilerCapabilities + extends chain
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_registry")
def test_from_profile_merges_extends_chain() -> None:
    base = Profile(
        name="base",
        version=(1, 0, 0),
        extends=None,
        capabilities=frozenset({"op.play"}),
        limits={"max_loop_nesting": 4, "min_wait_duration_ns": 4},
    )
    child = Profile(
        name="child",
        version=(1, 1, 0),
        extends="base",
        capabilities=frozenset({"op.sync"}),
        limits={"max_loop_nesting": 6},
    )
    register_profile(base)
    register_profile(child)

    caps = CompilerCapabilities.from_profile("child")
    assert "op.play" in caps.capabilities
    assert "op.sync" in caps.capabilities
    assert caps.limits["max_loop_nesting"] == 6
    assert caps.limits["min_wait_duration_ns"] == 4


@pytest.mark.usefixtures("isolated_registry")
def test_from_profile_limit_overrides_apply_last() -> None:
    base = Profile(
        name="base",
        version=(1, 0, 0),
        extends=None,
        capabilities=frozenset(),
        limits={"max_loop_nesting": 8},
    )
    register_profile(base)
    caps = CompilerCapabilities.from_profile("base", limit_overrides={"max_loop_nesting": 2})
    assert caps.limits["max_loop_nesting"] == 2


@pytest.mark.usefixtures("isolated_registry")
def test_from_profile_extra_predicates_are_appended() -> None:
    base = Profile(name="base", version=(1, 0, 0), extends=None, capabilities=frozenset())
    register_profile(base)

    def my_pred(node, ctx):  # noqa: ARG001
        return ()

    caps = CompilerCapabilities.from_profile("base", extra_predicates=(my_pred,))
    assert caps.predicates == (my_pred,)


@pytest.mark.usefixtures("isolated_registry")
def test_from_profile_detects_inheritance_cycle() -> None:
    register_profile(
        Profile(name="a", version=(1, 0, 0), extends="b", capabilities=frozenset()),
    )
    register_profile(
        Profile(name="b", version=(1, 0, 0), extends="a", capabilities=frozenset()),
    )
    with pytest.raises(ValueError, match="cycle"):
        CompilerCapabilities.from_profile("a")


def test_compiler_capabilities_supports() -> None:
    caps = CompilerCapabilities(
        profile="x",
        version=(1, 0, 0),
        capabilities=frozenset({"op.play"}),
        limits={},
        predicates=(),
        vendor_versions={},
    )
    assert caps.supports("op.play") is True
    assert caps.supports("op.sync") is False
