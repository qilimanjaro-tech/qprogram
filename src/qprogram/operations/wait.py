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
"""The ``wait`` operation — idling a bus for a duration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class Wait(Operation):
    """An idle period of ``duration`` nanoseconds on ``bus``.

    Args:
        bus (str): Bus to idle on.
        duration (int | Expression): Wait duration in nanoseconds. Accepts an
            [`Expression`][qprogram.Expression] for sweeps.
    """

    def __init__(self, bus: str, duration: int | Expression) -> None:
        self.bus = bus
        self.duration = duration

    def required_capabilities(self) -> set[str]:
        """Return ``op.wait`` plus the tokens contributed by the ``duration`` expression."""
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        return {"op.wait"} | expression_tokens(self.duration)
