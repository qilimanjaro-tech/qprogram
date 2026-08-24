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
"""The ``set_offset`` operation — DC offsets on a bus's signal paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetOffset(Operation):
    """A new DC offset on one or both signal paths of a bus.

    Args:
        bus (str): Bus whose DC offset to set.
        offset_path0 (float | Expression): Offset on path 0 (the only path for single-channel buses,
            I for IQ buses).
        offset_path1 (float | Expression | None): Offset on path 1 (Q for IQ buses). ``None`` leaves
            the path's offset unchanged.
    """

    def __init__(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self.bus = bus
        self.offset_path0 = offset_path0
        self.offset_path1 = offset_path1

    def required_capabilities(self) -> set[str]:
        """Return ``op.set_offset`` plus the tokens contributed by the offset expressions.

        An unset ``offset_path1`` contributes nothing.
        """
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        caps = {"op.set_offset"} | expression_tokens(self.offset_path0)
        if self.offset_path1 is not None:
            caps |= expression_tokens(self.offset_path1)
        return caps
