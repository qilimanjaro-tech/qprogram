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
"""The ``reset_phase`` operation — zeroing a bus oscillator's phase."""

from __future__ import annotations

from qprogram.operations.operation import Operation


class ResetPhase(Operation):
    """A reset of the NCO phase on a bus to zero.

    Args:
        bus (str): Bus whose oscillator phase to reset.
    """

    def __init__(self, bus: str) -> None:
        self.bus = bus

    def required_capabilities(self) -> set[str]:
        """Return the single ``op.reset_phase`` token."""
        return {"op.reset_phase"}
