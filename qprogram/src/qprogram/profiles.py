"""Built-in capability profiles for QProgram core.

Ships :data:`QPROGRAM_BASE_V1` — a domain-agnostic, platform-level base profile listing every
non-bus capability the DSL exposes: block-structure tokens, expression tokens, measurement
return-tokens, and the bus-less operations (``set_parameter``, ``get_parameter``). Vendor
platforms set their platform-level slot via
``CompilerCapabilities.from_profile("qprogram-base-v1", limit_overrides=...)``, or compose by
declaring their own profile with ``extends="qprogram-base-v1"`` and adding only what differs.

Registered as a side effect of ``import qprogram``.
"""

from __future__ import annotations

from qprogram.protocol import Profile, register_profile

# All tokens listed below are already registered in protocol._BASE_TOKENS, so Profile.__post_init__
# token validation passes without further calls to register_capability_tokens.

_BLOCKS: frozenset[str] = frozenset(
    {
        "block.block",
        "block.average",
        "block.for_loop",
        "block.loop",
        "block.parallel",
        "block.conditional",
    },
)

_BUS_LESS_OPS: frozenset[str] = frozenset(
    {
        "op.set_parameter",
        "op.get_parameter",
    },
)

_EXPRS: frozenset[str] = frozenset(
    {
        "expr.constant",
        "expr.variable",
        "expr.measurement_ref",
        "expr.binary_op",
        "expr.unary_op",
        "expr.comparison",
        "expr.logical_and_or",
        "expr.logical_not",
        "expr.where",
        "expr.math.sin",
        "expr.math.cos",
        "expr.math.tan",
        "expr.math.exp",
        "expr.math.log",
        "expr.math.sqrt",
        "expr.math.abs",
        "expr.math.minimum",
        "expr.math.maximum",
    },
)

# Sweep tokens — declared on the platform profile because loop blocks (ForLoop / Loop)
# attach them to their own ``required_capabilities`` (alongside the ``block.*`` tokens), and
# blocks route to the platform slot.
_SWEEPS: frozenset[str] = frozenset({"sweep.linear", "sweep.arbitrary"})

QPROGRAM_BASE_V1 = Profile(
    name="qprogram-base-v1",
    version=(0, 1, 0),
    extends=None,
    capabilities=_BLOCKS | _BUS_LESS_OPS | _EXPRS | _SWEEPS,
    limits={},
    predicates=(),
    vendor_versions={},
)


def _register() -> None:
    """Idempotently register :data:`QPROGRAM_BASE_V1` on the global profile registry."""
    register_profile(QPROGRAM_BASE_V1)


_register()


__all__ = ["QPROGRAM_BASE_V1"]
