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
"""Base [`Block`][qprogram.blocks.Block] container.

Blocks satisfy the same introspection contract as [`Operation`][qprogram.operations.Operation] so the entire
AST walks through a single uniform API. See the architecture docs for the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram._structural import ast_eq, ast_hash

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable
    from qprogram.waveforms.waveform import IQWaveform, Waveform


class Block:
    """Generic sequential container for operations and nested blocks.

    The base ``Block`` is what [`QProgram.block`][qprogram.QProgram.block] produces — an unstructured grouping with no
    extra semantics. Repeating blocks ([`Sweep`][qprogram.blocks.Sweep], [`Average`][qprogram.blocks.Average],
    [`Parallel`][qprogram.blocks.Parallel]) and [`Conditional`][qprogram.blocks.Conditional] subclass it to add
    structure the runtime understands.

    Equality and hashing are structural (same class + same children); once a block has been used as a
    ``set`` / ``dict`` key, do not `append` to it.
    """

    REPEATS: ClassVar[bool] = False
    """Whether this block re-runs its body, i.e. occupies a **repetition level** on the sequencer.

    ``True`` on the three core repeating blocks ([`Sweep`][qprogram.blocks.Sweep],
    [`Parallel`][qprogram.blocks.Parallel], [`Average`][qprogram.blocks.Average] — the last because averaging *is*
    repetition), ``False`` on the plain grouping [`Block`][qprogram.blocks.Block] and on
    [`Conditional`][qprogram.blocks.Conditional] (branching selects a body, it doesn't iterate).

    [`qprogram.validation.validate`][qprogram.validate] reads this to compute ``max_loop_nesting`` — the only reason
    the attribute exists rather than the validator testing concrete classes. A vendor or platform
    package contributing a repeating block of its own (see the ``.qp`` block registry,
    `register_vendor_block`) sets it ``True`` on its subclass and is
    then counted correctly against a platform's loop-depth limit, with no core change.

    A ``Parallel`` counts as **one** level in total, not one per composed loop: its loop headers live
    on `loops` rather than among its children, and they advance in
    lockstep.
    """

    def __init__(self) -> None:
        self._elements: list[Block | Operation] = []

    @property
    def elements(self) -> list[Block | Operation]:
        """The contained operations and sub-blocks, in declaration order.

        The block's own list rather than a copy; `append` is the sanctioned way to extend it.
        """
        return self._elements

    def append(self, element: Block | Operation) -> None:
        """Append an operation or sub-block to the end of this block.

        Args:
            element (Block | Operation): The node to add as this block's next child.
        """
        self._elements.append(element)

    def variables(self) -> set[Variable]:
        """Return every [`Variable`][qprogram.Variable] referenced by any child.

        Loop subclasses override to also include the loop-counter variable they bind.

        Returns:
            The union of every child's variables.
        """
        out: set[Variable] = set()
        for el in self._elements:
            out |= el.variables()
        return out

    def buses(self) -> set[str]:
        """Return every bus name referenced by any child.

        Returns:
            The union of every child's bus names. A [`BusRef`][qprogram.BusRef] is a ``str``, so
            schema-backed references appear alongside raw string buses.
        """
        out: set[str] = set()
        for el in self._elements:
            out |= el.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Return every waveform (concrete or string alias) referenced by any child.

        Returns:
            The union of every child's waveforms.
        """
        out: set[Waveform | IQWaveform | str] = set()
        for el in self._elements:
            out |= el.waveforms()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        """Yield this block, then each descendant in pre-order.

        Pairs with `Operation.walk` (which yields just the leaf) so callers can write
        ``for node in program.body.walk():`` without recursion.

        Yields:
            This block first, then every descendant operation and sub-block, in declaration order.
        """
        yield self
        for el in self._elements:
            yield from el.walk()

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this block needs, in isolation.

        Non-recursive by design — [`qprogram.validation.validate`][qprogram.validate] visits every node and checks
        each one's own token set against the slot that node routes to. Recursing here would
        double-count. Subclasses override to add their own identity token (``block.<name>``) and
        refinement tokens (sweep shape, etc.).

        Returns:
            The identity token ``block.block`` of a plain grouping block.
        """
        return {"block.block"}

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))
