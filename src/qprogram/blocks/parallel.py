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
"""The lockstep loop-composition block.

[`Parallel`][qprogram.blocks.Parallel] advances two or more [`Sweep`][qprogram.blocks.Sweep] headers together over a
single shared body, which is how a program sweeps coupled parameters along one axis instead of over their cross product.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from qprogram.blocks.sweep import Sweep
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


class Parallel(Block):
    """Several loops advanced in lockstep, sharing one body.

    Created via the ``|`` operator on sweep contexts (``with sweep(a, src) | sweep(b, src) as p:``).
    Note the structural quirk: composed loop headers live on ``self.loops``, *not* in ``self._elements``.
    Body operations live in ``_elements`` as usual. The introspection overrides below thread loop
    variables back into the unioned views so analyzers see them.

    The composition occupies a single repetition level (see `Block.REPEATS`), not one per
    composed loop, because the headers advance together rather than nesting.

    Args:
        loops (Iterable[Sweep]): The [`Sweep`][qprogram.blocks.Sweep] instances to advance in lockstep.
            At least two, and all with the same number of iterations — which every source can report
            statically, so the check happens here at construction rather than at run time.

    Raises:
        ValidationError: If fewer than two loops are given, if their iteration counts differ, or if a
            bound source cannot describe its points — a [`File`][qprogram.File] holding an
            array that is not 1-D, or holding none.
        OSError: If a bound source reads a file whose path does not exist or cannot be read.
    """

    REPEATS: ClassVar[bool] = True
    """This block re-runs its body — it occupies a repetition level (see `Block.REPEATS`)."""

    def __init__(self, loops: Iterable[Sweep]) -> None:
        super().__init__()
        loop_list: list[Sweep] = list(loops)
        if len(loop_list) < 2:
            msg = f"Parallel requires at least two loops, got {len(loop_list)}"
            raise ValidationError(msg)
        iteration_counts = [lp.num_iterations() for lp in loop_list]
        if len(set(iteration_counts)) > 1:
            described = ", ".join(
                f"{type(lp).__name__}({lp.variable.id!r}): {n}"
                for lp, n in zip(loop_list, iteration_counts, strict=True)
            )
            msg = f"parallel loops must have the same number of iterations to advance in lockstep; got {described}"
            raise ValidationError(msg)
        self.loops: list[Sweep] = loop_list

    def variables(self) -> set[Variable]:
        """Return every [`Variable`][qprogram.Variable] in the shared body, plus the ones the loops bind.

        The loop headers sit outside ``_elements``, so the inherited walk over the body misses them and
        they are unioned in here.

        Returns:
            The body's variables together with each composed loop's own.
        """
        out = super().variables()
        for lp in self.loops:
            out |= lp.variables()
        return out

    def walk(self) -> Iterator[Block | Operation]:
        """Yield this block, then each composed loop header, then the shared body in pre-order.

        The headers come first so a consumer meets the loops that bind the variables before the
        operations that read them.

        Yields:
            This block, then each composed [`Sweep`][qprogram.blocks.Sweep] with its own descendants,
            then every node of the shared body.
        """
        yield self
        for lp in self.loops:
            yield from lp.walk()
        for el in self._elements:
            yield from el.walk()

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this block needs, in isolation.

        [`qprogram.validation.validate`][qprogram.validate] classifies each composed header as a child block in its
        own right and checks its tokens there, so the headers' ``sweep.*`` tokens are not repeated
        here.

        Returns:
            The identity token ``block.parallel``.
        """
        return {"block.parallel"}
