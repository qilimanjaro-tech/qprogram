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
"""Program rewrites that improve how a [`QProgram`][qprogram.QProgram] executes on a platform.

Where [`qprogram.validation.validate`][qprogram.validate] only *reports* how a program would run (and emits the
``"reorderable-averaging"`` hint), [`optimize`][qprogram.optimize] *applies* the rewrites the validator suggests.
It is a free function — ``optimize(qprogram, capabilities) -> QProgram`` — mirroring ``validate``:
capability-aware, never mutating its input, returning a new program.

The one rewrite it applies lifts a host-side sweep out of an averaging block so the averaging
itself runs in real-time hardware (see [`optimize`][qprogram.optimize]). The match/partition decision is shared
with the validator's hint via `qprogram.validation.reorderable_average_split`, so the hint and
the rewrite can never disagree.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.validation import reorderable_average_split, validate

if TYPE_CHECKING:
    from qprogram.protocol import ExecutionPlan, PlatformCapabilities
    from qprogram.qprogram import QProgram


def optimize(qprogram: QProgram, capabilities: PlatformCapabilities) -> QProgram:
    """Return a copy of ``qprogram`` rewritten to run better under ``capabilities``.

    Applies the rewrite the validator only *suggests* (the ``"reorderable-averaging"`` info hint).
    For every ``average`` that is host-side-only **solely because it encloses a host-side sweep** —
    while its measurement sequence supports real-time hardware — the sweep is lifted to become the
    outer loop and the host-side-only setup ops are hoisted out of the average, so the averaging
    itself runs as a real-time hardware feature::

        average(N):                     for v in sweep:           # host-side (slow DAC step)
            for v in sweep:                 set_offset(flux, v)   # hoisted setup
                set_offset(flux, v)   -->     average(N):         # REAL-TIME
                play(drive, ...)                  play(drive, ...)
                measure(readout)                  measure(readout)

    Only the well-defined pattern above is rewritten — an ``average`` whose sole child is a single
    flat sweep loop carrying host-side-only setup plus a real-time-capable measurement sequence.
    Anything else is left untouched. (Whether a given op is host-side-only is decided by
    ``capabilities``: the example assumes a flux bus with no real-time engine; on a platform whose
    flux bus has a real-time half, ``set_offset`` would not be hoisted and the average would already
    be real-time.)

    This is **opt-in** because the reorder is not unconditionally semantics-preserving:

    - It **groups** all shots of one sweep point together rather than interleaving sweep passes; the
      averaged result is identical for a stationary system but differs under drift.
    - Each hoisted op runs **once per sweep point** instead of once per shot. That is the point of
      hoisting "setup" (DC-offset / parameter writes are idempotent, so the count change is
      harmless), but the rewrite hoists *every* host-side-only op in the leading run — so only apply
      it when those ops are genuinely idempotent setup. To stay safe, the rewrite refuses to
      reorder: it only hoists a *leading contiguous run* of host-side-only ops, never one that sits
      after a kept op (which would move it past that op and could change results).

    Args:
        qprogram (QProgram): The program to optimize. Never mutated.
        capabilities (PlatformCapabilities): The platform descriptor used to classify which ops are
            host-side-only.

    Returns:
        A new [`QProgram`][qprogram.QProgram] with the rewrite applied; the original is untouched. The
        search for an ``average`` scans the program's own body and does not look inside the
        fragments it calls, so a program whose body holds no ``average`` block comes back a plain
        deep copy with its [`Call`][qprogram.operations.Call] nodes intact — including a program
        whose only ``average`` sits in a fragment body. A program whose body *does* hold an average
        is validated against ``capabilities`` to classify its ops, which expands any ``Call`` nodes
        first, so such a program is returned in expanded form even if no average ends up rewritten.

    Raises:
        ValidationError: If the program's body holds an average and its fragment calls cannot be
            expanded (a call cycle, or a binding used in an incompatible position).
    """
    # No average in the program's own body → nothing to do; return a faithful copy without the
    # (lossy) fragment expansion that classification would otherwise force.
    if not any(isinstance(node, Average) for node in qprogram.body.walk()):
        return copy.deepcopy(qprogram)
    new_program = qprogram.expand() if qprogram.fragments else copy.deepcopy(qprogram)
    _, plan = validate(new_program, capabilities)
    _reorder_rt_averages(new_program.body, plan)
    return new_program


def _reorder_rt_averages(block: Block, plan: ExecutionPlan) -> None:
    """Rewrite optimizable ``Average`` blocks under ``block`` in place (see [`optimize`][qprogram.optimize]).

    Args:
        block (Block): The block to scan, rewritten in place along with every block beneath it —
            conditional arms and parallel loop headers included.
        plan (ExecutionPlan): Per-node domain support, used to tell host-side-only ops from
            real-time-capable ones.
    """
    for i, el in enumerate(block.elements):
        if isinstance(el, Average):
            replacement = _try_reorder_average(el, plan)
            if replacement is not None:
                block.elements[i] = replacement
    # Recurse into every sub-block (replacements are already optimal; re-scanning is harmless).
    for el in block.elements:
        if isinstance(el, Block):
            _reorder_rt_averages(el, plan)
    if isinstance(block, Conditional):
        for _, body in block.arms:
            _reorder_rt_averages(body, plan)
        if block.else_body is not None:
            _reorder_rt_averages(block.else_body, plan)
    if isinstance(block, Parallel):
        for loop in block.loops:
            _reorder_rt_averages(loop, plan)


def _try_reorder_average(average: Average, plan: ExecutionPlan) -> Sweep | None:
    """Return a sweep-outer / average-inner rewrite of ``average``, or ``None`` if it doesn't apply.

    The match/partition decision is the shared `qprogram.validation.reorderable_average_split`
    predicate — the same one the validator's ``reorderable-averaging`` hint uses, so hint and rewrite
    never disagree. All that happens here is building the rewritten AST from the ``(hoist, keep)``
    split.

    Args:
        average (Average): The averaging block to consider.
        plan (ExecutionPlan): Per-node domain support, used to tell host-side-only ops from
            real-time-capable ones.

    Returns:
        A [`Sweep`][qprogram.blocks.Sweep] carrying the hoisted setup ops followed by an inner
        ``average`` over the kept ops, or ``None`` when ``average`` does not match the pattern.
    """
    split = reorderable_average_split(average, plan)
    if split is None:
        return None
    hoist, keep = split
    loop = average.elements[0]
    assert isinstance(loop, Sweep)  # ruff: ignore[assert] — guaranteed by reorderable_average_split

    # One constructor covers every sweep source, so the rewrite needs no per-source knowledge.
    # Sources are immutable value objects, so the new block safely shares the original's.
    new_loop = Sweep(loop.variable, loop.source)
    for op in hoist:
        new_loop.append(op)
    inner = Average(average.shots)
    for op in keep:
        inner.append(op)
    new_loop.append(inner)
    return new_loop
