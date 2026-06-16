"""Program rewrites that improve how a :class:`~qprogram.QProgram` executes on a platform.

Where :func:`qprogram.validation.validate` only *reports* how a program would run (and emits the
``"reorderable-averaging"`` hint), :func:`optimize` *applies* the rewrites the validator suggests.
It is a free function — ``optimize(qprogram, capabilities) -> QProgram`` — mirroring ``validate``:
capability-aware, never mutating its input, returning a new program.

Today there is a single rewrite: lifting a software sweep out of an averaging block so the
averaging itself runs in real-time hardware (see :func:`optimize`). The match/partition decision is
shared with the validator's hint via :func:`qprogram.validation.reorderable_average_split`, so the
hint and the rewrite can never disagree.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.validation import reorderable_average_split, validate

if TYPE_CHECKING:
    from qprogram.protocol import ExecutionPlan, PlatformCapabilities
    from qprogram.qprogram import QProgram


def optimize(qprogram: QProgram, capabilities: PlatformCapabilities) -> QProgram:
    """Return a copy of ``qprogram`` rewritten to run better under ``capabilities``.

    Applies the rewrite the validator only *suggests* (the ``"reorderable-averaging"`` info hint).
    For every ``average`` that is software-only **solely because it encloses a software sweep** —
    while its measurement sequence supports hardware — the sweep is lifted to become the outer loop
    and the software-only setup ops are hoisted out of the average, so the averaging itself runs as
    a real-time hardware feature::

        average(N):                     for v in sweep:           # software (slow DAC step)
            for v in sweep:                 set_offset(flux, v)   # hoisted setup
                set_offset(flux, v)   -->     average(N):         # now HARDWARE
                play(drive, ...)                  play(drive, ...)
                measure(readout)                  measure(readout)

    Only the well-defined pattern above is rewritten — an ``average`` whose sole child is a single
    flat sweep loop carrying software-only setup plus a hardware-capable measurement sequence.
    Anything else is left untouched. (Whether a given op is software-only is decided by
    ``capabilities``: the example assumes a flux bus with no hardware engine, as on the
    ``my-platform`` demo; on a platform whose flux bus has a hardware half, ``set_offset`` would not
    be hoisted and the average would already be hardware.)

    This is **opt-in** because the reorder is not unconditionally semantics-preserving:

    - It **groups** all shots of one sweep point together rather than interleaving sweep passes; the
      averaged result is identical for a stationary system but differs under drift.
    - Each hoisted op runs **once per sweep point** instead of once per shot. That is the point of
      hoisting "setup" (DC-offset / parameter writes are idempotent, so the count change is
      harmless), but the rewrite hoists *every* software-only op in the leading run — so only apply
      it when those ops are genuinely idempotent setup. To stay safe, the rewrite refuses to
      reorder: it only hoists a *leading contiguous run* of software-only ops, never one that sits
      after a kept op (which would move it past that op and could change results).

    Args:
        qprogram: The program to optimise (never mutated).
        capabilities: The platform descriptor used to classify which ops are software-only.

    Returns:
        A new :class:`~qprogram.QProgram` with the rewrite applied; the original is untouched. A
        program with no ``average`` block comes back a plain deep copy. A program that *does*
        contain an average is validated against ``capabilities`` to classify its ops, which expands
        any fragment :class:`~qprogram.operations.Call` nodes first — so a fragment-bearing program
        is returned in expanded form even if no average ends up rewritten.
    """
    # No average anywhere → nothing to do; return a faithful copy without the (lossy) fragment
    # expansion that classification would otherwise force.
    if not any(isinstance(node, Average) for node in qprogram.body.walk()):
        return copy.deepcopy(qprogram)
    new_program = qprogram.expand() if qprogram.fragments else copy.deepcopy(qprogram)
    _, plan = validate(new_program, capabilities)
    _reorder_hw_averages(new_program.body, plan)
    return new_program


def _reorder_hw_averages(block: Block, plan: ExecutionPlan) -> None:
    """Rewrite optimisable ``Average`` blocks under ``block`` in place (see :func:`optimize`)."""
    for i, el in enumerate(block.elements):
        if isinstance(el, Average):
            replacement = _try_reorder_average(el, plan)
            if replacement is not None:
                block.elements[i] = replacement
    # Recurse into every sub-block (replacements are already optimal; re-scanning is harmless).
    for el in block.elements:
        if isinstance(el, Block):
            _reorder_hw_averages(el, plan)
    if isinstance(block, Conditional):
        for _, body in block.arms:
            _reorder_hw_averages(body, plan)
        if block.else_body is not None:
            _reorder_hw_averages(block.else_body, plan)
    if isinstance(block, Parallel):
        for loop in block.loops:
            _reorder_hw_averages(loop, plan)


def _try_reorder_average(average: Average, plan: ExecutionPlan) -> ForLoop | Loop | None:
    """Return a sweep-outer / average-inner rewrite of ``average``, or ``None`` if it doesn't apply.

    The match/partition decision is the shared :func:`qprogram.validation.reorderable_average_split`
    predicate (the same one the validator's ``reorderable-averaging`` hint uses, so hint and rewrite
    never disagree); here we just build the rewritten AST from the ``(hoist, keep)`` split.
    """
    split = reorderable_average_split(average, plan)
    if split is None:
        return None
    hoist, keep = split
    loop = average.elements[0]
    assert isinstance(loop, (ForLoop, Loop))  # noqa: S101 — guaranteed by reorderable_average_split

    new_loop: ForLoop | Loop = (
        ForLoop(loop.variable, loop.start, loop.stop, loop.step)
        if isinstance(loop, ForLoop)
        else Loop(loop.variable, loop.values)
    )
    for op in hoist:
        new_loop.append(op)
    inner = Average(average.shots)
    for op in keep:
        inner.append(op)
    new_loop.append(inner)
    return new_loop
