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
"""Built-in capability profiles for QProgram core.

Ships :data:`QPROGRAM_BASE_V1` — a domain-agnostic, platform-level base profile listing every
non-bus capability the DSL exposes: block-structure tokens, sweep kind and sweep source tokens, and
expression tokens. ``set_parameter`` / ``get_parameter`` are **bus-scoped** ops, so
``op.set_parameter`` / ``op.get_parameter`` live on bus profiles rather than here.

Vendor platforms fill their platform-level slot from it with
``CompilerCapabilities.from_profile("qprogram-base-v1", limit_overrides=...)``, or compose by
declaring their own profile with ``extends="qprogram-base-v1"`` and adding only what differs.

Registered as a side effect of ``import qprogram``.
"""

from __future__ import annotations

from qprogram.protocol import Profile, register_profile

# Every token listed below is registered in protocol._BASE_TOKENS, so Profile.__post_init__ token
# validation passes without any further call to register_capability_tokens.

_BLOCKS: frozenset[str] = frozenset(
    {
        "block.block",
        "block.average",
        "block.sweep",
        "block.parallel",
        "block.conditional",
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

# Sweep tokens — declared on the platform profile because the ``Sweep`` block attaches them to its
# own ``required_capabilities`` (alongside ``block.sweep``), and blocks route to the platform slot.
#
# Two levels, mirroring waveforms: the ``sweep.<kind>`` pair says how compilable the values are, and
# one ``sweep.<source>`` token per built-in source class says which generators the platform can
# produce. Core declares every built-in here, so a platform inheriting this profile accepts them all;
# a platform that wants to *refuse* one (no native log sweep, say) omits it by declaring its own
# platform profile rather than extending this one.
_SWEEP_KINDS: frozenset[str] = frozenset({"sweep.linear", "sweep.arbitrary"})

_SWEEP_SOURCES: frozenset[str] = frozenset(
    {
        "sweep.range",
        "sweep.values",
        "sweep.linspace",
        "sweep.logspace",
        "sweep.file",
        "sweep.repeat",
        "sweep.rotate",
        "sweep.concat",
    },
)

QPROGRAM_BASE_V1 = Profile(
    name="qprogram-base-v1",
    version=(0, 1, 0),
    extends=None,
    capabilities=_BLOCKS | _EXPRS | _SWEEP_KINDS | _SWEEP_SOURCES,
    limits={},
    predicates=(),
    vendor_versions={},
)


def _register() -> None:
    """Idempotently register :data:`QPROGRAM_BASE_V1` on the global profile registry."""
    register_profile(QPROGRAM_BASE_V1)


_register()


__all__ = ["QPROGRAM_BASE_V1"]
