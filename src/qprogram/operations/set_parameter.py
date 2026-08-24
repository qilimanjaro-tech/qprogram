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
"""The ``set_parameter`` operation — writing a bus-scoped platform parameter by name."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetParameter(Operation):
    """A write to a bus-scoped parameter named by string.

    Targets a **bus** (like :class:`~qprogram.operations.SetFrequency` and the other ``set_*`` ops),
    so :attr:`BUS_ATTRS` is ``("bus",)`` and the op routes to that bus's capability slot. Unlike the
    real-time ``set_*`` ops, a parameter write is a platform-configuration action — not real-time — so
    platforms expose ``op.set_parameter`` only in a bus slot's host half, making it host-side-only (a
    swept value additionally forces its binding loop to host via a predicate).

    Args:
        bus (str): The bus whose parameter is written.
        parameter (str): Name of the parameter to set.
        value (float | Expression): New value. Accepts an :class:`~qprogram.Expression` for sweeps.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(
        self,
        bus: str,
        parameter: str,
        value: float | Expression,
    ) -> None:
        self.bus = bus
        self.parameter = parameter
        self.value = value

    def required_capabilities(self) -> set[str]:
        """Return ``op.set_parameter`` plus the tokens contributed by the ``value`` expression."""
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        return {"op.set_parameter"} | expression_tokens(self.value)
