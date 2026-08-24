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
"""The ``get_parameter`` operation — reading a bus-scoped platform parameter into a variable."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Variable


class GetParameter(Operation):
    """A read of a bus-scoped parameter into a :class:`~qprogram.Variable` at runtime.

    Targets a **bus** (``BUS_ATTRS = ("bus",)``), so the op routes to that bus's capability slot.
    Like :class:`~qprogram.operations.SetParameter`, reading a parameter is a platform-configuration
    action — platforms expose ``op.get_parameter`` only in a bus slot's host half, making it
    host-side-only.

    Args:
        variable (Variable): Destination :class:`~qprogram.Variable` for the read value.
        bus (str): The bus whose parameter is read.
        parameter (str): Name of the parameter to read.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, variable: Variable, bus: str, parameter: str) -> None:
        self.variable = variable
        self.bus = bus
        self.parameter = parameter

    def required_capabilities(self) -> set[str]:
        """Return the single ``op.get_parameter`` token."""
        return {"op.get_parameter"}
