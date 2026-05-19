"""Capability profile bundles for the Qblox vendor extension.

Defines :data:`QBLOX_DEFAULT_V1`: a single profile bundle covering everything
the qblox backend supports today. Registered as a side effect of importing
:mod:`qprogram_qblox`.

The profile composes three orthogonal axes (see
:mod:`qprogram.protocol`):

- A capability set — every dotted token a program may legally need.
- A limits dict — numeric thresholds the validator checks against AST
  measurements (loop depth, parallel arity, total measurements, ...).
- A tuple of predicates — callable hooks invoked on each AST node with a
  :class:`~qprogram.ValidationContext`. The included
  :func:`_reject_arbitrary_sweep_at_wait_duration` flags a known qblox
  hardware constraint: ``Wait.duration`` cannot be swept by an arbitrary
  numpy array (the wait-instruction operand is a fixed-step register).

A concrete platform constructs its
:class:`~qprogram.CompilerCapabilities` from this profile via
:meth:`CompilerCapabilities.from_profile`, optionally tightening any
``limits`` element for a specific device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.wait import Wait
from qprogram.protocol import (
    Diagnostic,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)
from qprogram.variable import Variable

# Register vendor-specific capability tokens *before* constructing the
# profile that names them — Profile.__post_init__ validates that every
# listed token is in CAPABILITY_REGISTRY, so registration must come first.
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
) -> Iterable[Diagnostic]:
    """Flag ``Wait(duration=var)`` where ``var`` is swept by an arbitrary
    numpy array.

    Qblox's wait instruction takes a single integer cycle count; the
    instruction register is incremented by a fixed step. Sweeping with a
    :class:`~qprogram.blocks.Loop` (numpy-array-driven) violates this
    constraint, while a :class:`~qprogram.blocks.ForLoop` (linear sweep)
    or a constant work fine.

    This is the data-flow case the capability protocol was designed to
    express: the requirement is not a property of ``Wait`` in isolation,
    nor of the loop in isolation, but of how the variable bound by the
    loop is *used* downstream.
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


_CORE_OPS: frozenset[str] = frozenset(
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
        "op.set_parameter",
        "op.get_parameter",
        "op.set_crosstalk",
    },
)

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

_SWEEPS: frozenset[str] = frozenset({"sweep.linear", "sweep.arbitrary"})

_EXPRS: frozenset[str] = frozenset(
    {
        "expr.constant",
        "expr.variable",
        "expr.measurement_ref",
        "expr.binary_op",
        "expr.unary_op",
        "expr.comparison",
    },
)

_RETURNS: frozenset[str] = frozenset(
    {"measure.returns.iq", "measure.returns.raw", "measure.returns.state"},
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
    capabilities=_CORE_OPS | _BLOCKS | _WAVEFORMS | _SWEEPS | _EXPRS | _RETURNS | _VENDOR,
    limits={
        "max_loop_nesting": 8,
        "max_parallel_loops": 4,
        "min_wait_duration_ns": 4,
        "max_measurements": 1024,
    },
    predicates=(_reject_arbitrary_sweep_at_wait_duration,),
    vendor_versions={"qblox": (0, 1, 0)},
)


def _register() -> None:
    """Idempotently register :data:`QBLOX_DEFAULT_V1` on the global profile registry."""
    register_profile(QBLOX_DEFAULT_V1)


__all__ = ["QBLOX_DEFAULT_V1"]
