"""Capability profile bundles for the Qblox vendor extension.

Defines :data:`QBLOX_DEFAULT_V1`: a bus-level profile covering every qblox-driven bus's real-time
feature set — pulse / timing / parameter operations, every waveform class qblox can render, both
sweep shapes, and the ``vendor.qblox.*`` operations. Platforms typically use the same profile for
both the hw and sw slots of a qblox-driven bus; the hw/sw distinction emerges from predicates that
emit :class:`~qprogram.DomainConstraint` rather than from two parallel profiles.

The bus-level :data:`QBLOX_DEFAULT_V1` is paired with the core-shipped ``qprogram-base-v1`` at the
platform slot (block-structure tokens, expression tokens, measurement return-tokens, bus-less ops),
giving a complete :class:`~qprogram.PlatformCapabilities` shape.

This module declares two predicates:

- :func:`_reject_arbitrary_sweep_at_wait_duration` — a hard :class:`~qprogram.Diagnostic`. The
  qblox wait instruction's operand is a fixed-step register, so an arbitrary numpy-array sweep
  doesn't fit any execution model qblox knows how to compile, hw or sw.
- :func:`_drag_sigma_in_loop_is_software_only` — a soft :class:`~qprogram.DomainConstraint`. The
  qblox sequencer can't real-time-update an IQDrag's ``sigma`` field, but the platform can still
  dispatch one shot per iteration. The constraint excludes ``"hw"`` only, so the enclosing
  for-loop is classified as ``{sw}`` while everything outside the offending Play stays unaffected.

Registered as a side effect of importing :mod:`qprogram_qblox`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.play import Play
from qprogram.operations.wait import Wait
from qprogram.protocol import (
    Diagnostic,
    DomainConstraint,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)
from qprogram.variable import Variable
from qprogram.waveforms.iq_drag import IQDrag

# Register vendor-specific capability tokens *before* constructing the profile that names them —
# Profile.__post_init__ validates that every listed token is in CAPABILITY_REGISTRY, so registration
# must come first.
register_capability_tokens(
    "vendor.qblox.acquire",
    "vendor.qblox.set_markers",
    "vendor.qblox.set_trigger",
    "vendor.qblox.wait_trigger",
    "vendor.qblox.active_reset",
    "vendor.qblox.set_acquisition_threshold",
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation


def _reject_arbitrary_sweep_at_wait_duration(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic | DomainConstraint]:
    """Flag ``Wait(duration=var)`` where ``var`` is swept by an arbitrary numpy array.

    Qblox's wait instruction takes a single integer cycle count; the instruction register is
    incremented by a fixed step. Sweeping with a :class:`~qprogram.blocks.Loop` (numpy-array-driven)
    violates this constraint, while a :class:`~qprogram.blocks.ForLoop` (linear sweep) or a constant
    work fine. The combination cannot be salvaged by software dispatch either — qblox still has to
    emit the wait instruction per shot — so this is a hard :class:`Diagnostic`, not a
    :class:`DomainConstraint`.
    """
    if not isinstance(node, Wait):
        return
    if not isinstance(node.duration, Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(
            severity="error",
            code="qblox.arbitrary-wait-sweep",
            message=(
                f"Variable {node.duration.id!r} is swept with arbitrary "
                f"values and used at Wait.duration, which qblox does not "
                f"support (the wait instruction needs a linear step). Use "
                f"a for_loop instead, or a constant duration."
            ),
            node=node,
        )


def _drag_sigma_in_loop_is_software_only(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic | DomainConstraint]:
    """Flag the enclosing loop of a ``Play(IQDrag(sigma=var))`` as software-only.

    The qblox sequencer can rapidly re-arm a real-time loop with a new amplitude or duration but
    cannot recompute a Drag envelope's ``sigma`` between iterations — the gaussian + derivative
    samples are precomputed at upload. So sweeping ``sigma`` requires re-uploading the waveform
    per iteration, which means the **enclosing loop** must dispatch from software (one qblox
    shot per iteration). The ``Play`` operation itself remains HW (qblox is a HW vendor by
    design); what changes is the loop's iteration mechanism.

    Per the spec, this is a :class:`DomainConstraint` targeting the **binding loop**, not the
    Play op. The classifier subtracts ``"hw"`` from the loop's domain; the Play stays HW and is
    dispatched as a single hardware shot per software iteration (per nesting rule (e1)).

    Uses ``ctx.binding_loop_of`` to find the loop that binds ``sigma``; bare variables that
    aren't loop-bound are unaffected (they're treated as constants at upload time).
    """
    if not isinstance(node, Play) or not isinstance(node.waveform, IQDrag):
        return
    sigma = node.waveform.sigma
    if not isinstance(sigma, Variable):
        return
    binding_loop = ctx.binding_loop_of(sigma)
    if binding_loop is None:
        return  # not loop-bound — constant at upload time
    yield DomainConstraint(
        node=binding_loop,
        exclude=frozenset({"hw"}),
        reason=(
            f"Variable {sigma.id!r} sweeps IQDrag.sigma in a contained Play, which qblox "
            f"cannot real-time-update; the loop dispatches per shot from software instead."
        ),
    )


_BUS_OPS: frozenset[str] = frozenset(
    {
        "op.play",
        "op.measure",
        "op.wait",
        "op.sync",
        "op.set_frequency",
        "op.set_phase",
        "op.set_gain",
        "op.reset_phase",
        "op.set_offset",
    },
)

_WAVEFORMS: frozenset[str] = frozenset(
    {
        "waveform.single",
        "waveform.iq",
        "waveform.alias",
        "waveform.arbitrary",
        "waveform.chained",
        "waveform.flat_top",
        "waveform.gaussian",
        "waveform.gaussian_drag_correction",
        "waveform.ramp",
        "waveform.snz",
        "waveform.square",
        "waveform.iq_drag",
        "waveform.iq_pair",
    },
)

_RETURNS: frozenset[str] = frozenset(
    {
        "measure.returns.iq",
        "measure.returns.raw",
        "measure.returns.state",
    },
)

_VENDOR: frozenset[str] = frozenset(
    {
        "vendor.qblox.acquire",
        "vendor.qblox.set_markers",
        "vendor.qblox.set_trigger",
        "vendor.qblox.wait_trigger",
        "vendor.qblox.active_reset",
        "vendor.qblox.set_acquisition_threshold",
    },
)


QBLOX_DEFAULT_V1 = Profile(
    name="qblox-default-v1",
    version=(0, 1, 0),
    extends=None,
    capabilities=_BUS_OPS | _WAVEFORMS | _RETURNS | _VENDOR,
    limits={"min_wait_duration_ns": 4},
    predicates=(
        _reject_arbitrary_sweep_at_wait_duration,
        _drag_sigma_in_loop_is_software_only,
    ),
    vendor_versions={"qblox": (0, 1, 0)},
)


def _register() -> None:
    """Idempotently register :data:`QBLOX_DEFAULT_V1` on the global profile registry."""
    register_profile(QBLOX_DEFAULT_V1)


__all__ = ["QBLOX_DEFAULT_V1"]
