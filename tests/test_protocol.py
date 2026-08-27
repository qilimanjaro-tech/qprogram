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
"""Tests for the capability-protocol type layer.

Covers :class:`Diagnostic`, :class:`DomainConstraint`, :class:`Profile`,
:class:`CompilerCapabilities`, :class:`BusCapabilities`, :class:`PlatformCapabilities`,
the global :data:`PROFILE_REGISTRY` (register/resolve/cycle detection), the
capability-token registry, and the waveform-class → token mapping.

Behavior around how operations *declare* their required capabilities is
in :mod:`test_required_capabilities`; the validator itself is in
:mod:`test_validation`.
"""

from __future__ import annotations

import dataclasses

import pytest

from qprogram.buses import BusSchema
from qprogram.protocol import (
    CAPABILITY_REGISTRY,
    BusCapabilities,
    CompilerCapabilities,
    Diagnostic,
    DomainConstraint,
    PlatformCapabilities,
    Profile,
    expression_tokens,
    known_measurement_fields,
    measurement_field_token,
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
    # ``setattr`` is the type-system-friendly way to exercise the freeze: the
    # static checkers see read-only properties and reject ``d.code = "y"``.
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(d, "code", "y")  # ruff: ignore[set-attr-with-constant]


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
    for tok in ("op.play", "op.measure", "block.sweep", "sweep.linear", "waveform.iq_drag"):
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
    validate_tokens(["op.play", "op.measure", "block.sweep"])  # no raise


# ---------------------------------------------------------------------------
# Waveform tokens
# ---------------------------------------------------------------------------


def test_waveform_token_returns_canonical_token_for_known_classes() -> None:
    assert waveform_token(Square(amplitude=0.5, duration=100)) == "waveform.square"
    assert waveform_token(IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)) == "waveform.iq_drag"
    assert waveform_token(IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))) == "waveform.iq_pair"


def test_waveform_token_returns_none_for_string_alias() -> None:
    assert waveform_token("readout_pulse") is None


def test_register_waveform_token_extends_registry_and_dispatch() -> None:
    class _Fake:
        pass

    register_waveform_token(_Fake, "waveform.test_fake_class")
    assert "waveform.test_fake_class" in CAPABILITY_REGISTRY
    # A non-Waveform instance is enough to exercise the dispatch table — that is how
    # vendor packages register their own classes.
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
    capabilities = frozenset({"this.does.not.exist"})
    with pytest.raises(ValueError, match="Unknown capability token"):
        Profile(
            name="test-bad",
            version=(1, 0, 0),
            extends=None,
            capabilities=capabilities,
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
def test_register_profile_idempotent_for_an_equal_profile() -> None:
    """A rebuilt-but-identical Profile is a no-op, not a conflict.

    The guard used to compare object identity, so an import-time side effect that ran twice, a
    reloaded module, or a re-executed notebook cell was told its own bundle had "different
    content". Of an equal pair the registry keeps the first object.
    """
    first = Profile(name="eq", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.play"}))
    second = Profile(name="eq", version=(1, 0, 0), extends=None, capabilities=frozenset({"op.play"}))
    assert first == second
    assert first is not second

    register_profile(first)
    register_profile(second)
    assert resolve_profile("eq") is first


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
def test_from_profile_merges_vendor_versions_through_chain() -> None:
    """A parent's vendor_versions survive resolution; the child may override per vendor."""
    base = Profile(
        name="vv-base",
        version=(1, 0, 0),
        extends=None,
        capabilities=frozenset(),
        vendor_versions={"qblox": (0, 1, 0), "qdac": (0, 1, 0)},
    )
    child = Profile(
        name="vv-child",
        version=(1, 1, 0),
        extends="vv-base",
        capabilities=frozenset(),
        vendor_versions={"qblox": (0, 2, 0)},
    )
    register_profile(base)
    register_profile(child)

    caps = CompilerCapabilities.from_profile("vv-child")
    assert caps.vendor_versions == {"qblox": (0, 2, 0), "qdac": (0, 1, 0)}


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

    def my_pred(node, ctx):  # ruff: ignore[unused-function-argument]
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


# ---------------------------------------------------------------------------
# Diagnostic — info severity and domain field
# ---------------------------------------------------------------------------


def test_diagnostic_accepts_info_severity() -> None:
    d = Diagnostic(severity="info", code="forced-host", message="m", domain="host")
    assert d.severity == "info"
    assert d.domain == "host"


# ---------------------------------------------------------------------------
# DomainConstraint
# ---------------------------------------------------------------------------


def test_domain_constraint_is_frozen_dataclass() -> None:
    dc = DomainConstraint(node=None, exclude=frozenset({"rt"}), reason="why")  # ty:ignore[invalid-argument-type]
    assert dataclasses.is_dataclass(dc)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(dc, "reason", "y")  # ruff: ignore[set-attr-with-constant]


# ---------------------------------------------------------------------------
# BusCapabilities
# ---------------------------------------------------------------------------


def _cc(name: str, *, tokens: frozenset[str] = frozenset()) -> CompilerCapabilities:
    return CompilerCapabilities(
        profile=name,
        version=(1, 0, 0),
        capabilities=tokens,
        limits={},
        predicates=(),
        vendor_versions={},
    )


def test_bus_capabilities_get_and_supported_domains() -> None:
    cc_rt = _cc("rt")
    bc = BusCapabilities(rt=cc_rt, host=None)
    assert bc.get("rt") is cc_rt
    assert bc.get("host") is None
    assert bc.supported_domains() == frozenset({"rt"})


def test_bus_capabilities_empty_supported_domains_when_both_none() -> None:
    bc = BusCapabilities(rt=None, host=None)
    assert bc.supported_domains() == frozenset()


# ---------------------------------------------------------------------------
# PlatformCapabilities.for_bus routing
# ---------------------------------------------------------------------------


def _default_caps() -> tuple[BusCapabilities, BusCapabilities, BusCapabilities]:
    """Return three distinct BusCapabilities slots so identity tests are unambiguous."""
    return (
        BusCapabilities(rt=_cc("drive-rt"), host=None),
        BusCapabilities(rt=_cc("platform-rt"), host=None),
        BusCapabilities(rt=_cc("default-rt"), host=None),
    )


def test_for_bus_routes_busref_to_per_element_slot() -> None:
    drive_slot, platform_slot, default_slot = _default_caps()
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive_slot},
        platform=platform_slot,
        default_bus_profile=default_slot,
    )
    schema = BusSchema.transmon()
    assert caps.for_bus(schema.q[0].drive) is drive_slot


def test_for_bus_falls_back_to_default_for_unmapped_busref() -> None:
    drive_slot, platform_slot, default_slot = _default_caps()
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive_slot},
        platform=platform_slot,
        default_bus_profile=default_slot,
    )
    schema = BusSchema.transmon()
    # The (q, readout) BusRef has no entry in caps.bus.
    assert caps.for_bus(schema.q[0].readout) is default_slot


def test_for_bus_routes_raw_string_to_default() -> None:
    drive_slot, platform_slot, default_slot = _default_caps()
    caps = PlatformCapabilities(
        bus={("q", "drive"): drive_slot},
        platform=platform_slot,
        default_bus_profile=default_slot,
    )
    assert caps.for_bus("anonymous_bus") is default_slot


# ---------------------------------------------------------------------------
# Measurement-field tokens
# ---------------------------------------------------------------------------


def test_measurement_field_token_builds_namespaced_token() -> None:
    assert measurement_field_token("iq") == "measure.fields.iq"


def test_known_measurement_fields_covers_the_core_set() -> None:
    assert known_measurement_fields() == {"state", "iq", "raw"}


def test_known_measurement_fields_grows_with_vendor_registration() -> None:
    """Registering the token is the whole extension step — no core change needed."""
    register_capability_tokens("measure.fields.counts")
    try:
        assert "counts" in known_measurement_fields()
    finally:
        CAPABILITY_REGISTRY.discard("measure.fields.counts")
    assert "counts" not in known_measurement_fields()
