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
"""The shot-averaging block.

[`Average`][qprogram.blocks.Average] repeats its body the way a sweep does, but the repetitions are collapsed instead of
reported: every measurement inside it yields the mean over the shots rather than an extra result
dimension.
"""

from __future__ import annotations

from typing import ClassVar

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError


class Average(Block):
    """A body run ``shots`` times, with measurement results averaged across the repetitions.

    Averaging *is* repetition, so the block occupies a repetition level on the sequencer
    (`Block.REPEATS`). Unlike a [`Sweep`][qprogram.blocks.Sweep] it contributes no dimension to
    the results: the shots are accumulated and divided out, so a ``state`` field arrives as the
    excited-state population over the shots.

    Args:
        shots (int): Number of times to execute the block body. Must be a positive integer; a ``bool``
            is rejected even though it is an ``int``.

    Raises:
        ValidationError: If ``shots`` is not an integer >= 1.
    """

    REPEATS: ClassVar[bool] = True
    """This block re-runs its body — it occupies a repetition level (see `Block.REPEATS`)."""

    def __init__(self, shots: int) -> None:
        super().__init__()
        if not isinstance(shots, int) or isinstance(shots, bool) or shots < 1:
            msg = f"Average shots must be an integer >= 1, got {shots!r}"
            raise ValidationError(msg)
        self.shots = shots

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this block needs, in isolation.

        Returns:
            The identity token ``block.average``.
        """
        return {"block.average"}
