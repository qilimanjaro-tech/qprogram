"""Capability profile bundles for the QDAC vendor extension.

Defines :data:`QDAC_DEFAULT_V1`: the bus-level profile listing what the QDAC waveform engine
supports on a single bus (channel). Real qdac platforms typically attach this profile to flux
buses on a transmon schema, leaving the drive and readout buses to a different vendor (e.g.
qblox); the platform slot is filled by the core ``qprogram-base-v1`` profile, the same as for
any other vendor.

The profile carries two predicates:

- :func:`_qdac_op_with_swept_var_is_software_only` — soft :class:`~qprogram.DomainConstraint`
  excluding ``"hw"`` whenever any qdac operation references a loop-bound
  :class:`~qprogram.Variable`. **QDAC has no FPGA**: every parameter sweep — DC offset,
  waveform-engine parameters, embedded waveform parameters — has to be re-uploaded from the
  host between iterations. The classifier therefore lifts the enclosing loop to ``{sw}``;
  everything outside the qdac op is unaffected.
- :func:`_set_trigger_outputs_required` — hard :class:`~qprogram.Diagnostic` when
  :class:`~qprogram_qdac.operations.SetTrigger` is configured with an empty ``outputs`` set
  (almost certainly a user mistake; arming zero outputs means the trigger fires onto nothing).

Registered as a side effect of importing :mod:`qprogram_qdac`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.protocol import (
    Diagnostic,
    DomainConstraint,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)

from qprogram_qdac.operations import Play, SetOffset, SetTrigger, WaitTrigger

# Register vendor-specific capability tokens *before* constructing the profile that names them —
# Profile.__post_init__ validates that every listed token is in CAPABILITY_REGISTRY, so
# registration must come first.
register_capability_tokens(
    "vendor.qdac.wait_trigger",
    "vendor.qdac.set_trigger",
    "vendor.qdac.set_offset",
    "vendor.qdac.play",
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation


_QDAC_OP_CLASSES: tuple[type, ...] = (WaitTrigger, SetTrigger, SetOffset, Play)
"""Tuple of every qdac :class:`~qprogram.operations.Operation` subclass.

Used by :func:`_qdac_op_with_swept_var_is_software_only` to scope the variable-sweep check to
qdac operations only — the predicate may run on every visited AST node (since it's registered
on the qdac bus profile), so we filter explicitly rather than relying on routing alone.
"""


def _qdac_op_with_swept_var_is_software_only(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic | DomainConstraint]:
    """Flag the enclosing loop of any qdac op that references a loop-bound variable.

    QDAC has **no FPGA**: every parameter change goes through the host's slow-control plane
    (USB, ~ms latency). Any qdac operation referencing a variable bound by an enclosing
    :class:`~qprogram.ForLoop` or :class:`~qprogram.Loop` therefore cannot live inside a
    real-time hardware loop — the enclosing loop must dispatch from software, re-uploading per
    iteration.

    Per the spec, this is a :class:`~qprogram.DomainConstraint` targeting the **binding loop
    block** of the variable, not the qdac op itself. The qdac op's classification doesn't
    change (it's SW by design — see :data:`QDAC_DEFAULT_V1` wiring); what changes is the
    enclosing loop's classification, which goes from HW-or-SW down to SW-only.

    Walks every variable on the node via :meth:`Operation.variables`, which transitively
    descends into expression arguments and waveform parameters — so a
    ``play(bus, Square(amplitude=v))`` is caught just like a ``set_offset(bus, v)``. Emits one
    constraint per (distinct) binding loop encountered.

    Note: when qdac is correctly wired into an ``sw``-only :class:`~qprogram.BusCapabilities`
    slot, the qdac op is already SW-only and the enclosing loop is naturally SW (via op-children
    consensus) — so this predicate is largely redundant. It remains useful as belt-and-braces
    for platforms that mistakenly wire qdac into both halves of a bus slot, and as a place to
    surface a clear ``forced-software`` reason in the diagnostic output.

    Args:
        node: AST node currently being visited.
        ctx: Read-only validation context with cross-op data-flow facts.

    Yields:
        Zero or more :class:`~qprogram.DomainConstraint`, one per distinct binding loop.
    """
    if not isinstance(node, _QDAC_OP_CLASSES):
        return
    seen_loops: set[int] = set()
    for var in node.variables():
        binding_loop = ctx.binding_loop_of(var)
        if binding_loop is None or id(binding_loop) in seen_loops:
            continue
        seen_loops.add(id(binding_loop))
        yield DomainConstraint(
            node=binding_loop,
            exclude=frozenset({"hw"}),
            reason=(
                f"qdac.{type(node).__name__} references loop-bound variable {var.id!r}; "
                f"qdac has no FPGA, so the loop must dispatch from software."
            ),
        )


def _set_trigger_outputs_required(
    node: Operation | Block,
    ctx: ValidationContext,  # noqa: ARG001
) -> Iterable[Diagnostic | DomainConstraint]:
    """Flag a :class:`~qprogram_qdac.operations.SetTrigger` with an empty ``outputs`` set.

    Arming zero outputs configures a trigger that fires onto nothing — almost certainly a
    user mistake. Hard error in every domain; we yield a :class:`~qprogram.Diagnostic`.

    Args:
        node: AST node currently being visited.
        ctx: Validation context (unused — this is a structural check).

    Yields:
        Zero or one :class:`~qprogram.Diagnostic`.
    """
    if not isinstance(node, SetTrigger):
        return
    if not node.outputs:
        yield Diagnostic(
            severity="error",
            code="qdac.empty-trigger-outputs",
            message=(
                "SetTrigger has no outputs configured. Specify at least one output index, e.g. "
                "outputs={1} or outputs=[1, 2]."
            ),
            node=node,
        )


_BUS_OPS: frozenset[str] = frozenset(
    {
        "vendor.qdac.wait_trigger",
        "vendor.qdac.set_trigger",
        "vendor.qdac.set_offset",
        "vendor.qdac.play",
    },
)
"""QDAC vendor operations.

Every qdac op carries a ``bus`` attribute and therefore routes to the per-bus
:class:`BusCapabilities` slot the platform attaches qdac to.
"""

_WAVEFORMS: frozenset[str] = frozenset(
    {
        "waveform.single",
        # Per-class waveform tokens the qdac engine can render. QDAC is single-channel only —
        # IQ waveforms never appear here.
        "waveform.arbitrary",
        "waveform.chained",
        "waveform.cosine",
        "waveform.flat_top",
        "waveform.gaussian",
        "waveform.ramp",
        "waveform.sine",
        "waveform.square",
        "waveform.sech",
        "waveform.snz",
        "waveform.tukey",
    },
)
"""Single-channel waveform tokens the QDAC waveform engine can render.

QDAC is single-channel only, so :data:`waveform.iq` and IQ-specific tokens are deliberately
excluded — a program emitting :class:`~qprogram.waveforms.IQDrag` on a qdac bus will fail
validation with a ``missing-capability`` diagnostic.
"""


QDAC_DEFAULT_V1 = Profile(
    name="qdac-default-v1",
    version=(0, 1, 0),
    extends=None,
    capabilities=_BUS_OPS | _WAVEFORMS,
    limits={
        # QDAC's waveform engine has a hard floor on dwell time; below this the output
        # interpolation breaks down. The validator doesn't yet enforce a per-Play dwell limit, but
        # the value is here for platforms that wire in their own predicate.
        "min_dwell_ns": 100,
    },
    predicates=(
        _qdac_op_with_swept_var_is_software_only,
        _set_trigger_outputs_required,
    ),
    vendor_versions={"qdac": (0, 1, 0)},
)
"""The default QDAC bus-level capability profile.

Platforms attach this to the ``hw`` slot of every qdac-driven bus (typically flux buses) and
optionally also to the ``sw`` slot when per-iteration software dispatch is supported. The
:func:`_qdac_op_with_swept_var_is_software_only` predicate then takes care of the hw→sw
lifting whenever any qdac operation references a loop-bound variable.

Holds every qdac vendor token (``wait_trigger``, ``set_trigger``, ``set_offset``, ``play``) plus
every single-channel waveform the engine can render. The platform-wide slot of a qdac platform's
:class:`~qprogram.PlatformCapabilities` typically uses the core-shipped ``qprogram-base-v1``
profile directly — qdac has no bus-less operations and so contributes nothing at the platform
level.
"""


def _register() -> None:
    """Idempotently register :data:`QDAC_DEFAULT_V1` on the global profile registry."""
    register_profile(QDAC_DEFAULT_V1)


__all__ = ["QDAC_DEFAULT_V1"]
