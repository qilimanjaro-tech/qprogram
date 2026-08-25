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
"""The ``set_frequency`` operation — retuning a bus oscillator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetFrequency(Operation):
    """A new NCO / oscillator frequency for a bus.

    Args:
        bus (str): Bus whose oscillator to retune.
        frequency (float | Expression): New frequency in Hz. Accepts an
            [`Expression`][qprogram.Expression] for sweeps.
    """

    def __init__(self, bus: str, frequency: float | Expression) -> None:
        self.bus = bus
        self.frequency = frequency

    def required_capabilities(self) -> set[str]:
        """Return ``op.set_frequency`` plus the tokens contributed by the ``frequency`` expression."""
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        return {"op.set_frequency"} | expression_tokens(self.frequency)
