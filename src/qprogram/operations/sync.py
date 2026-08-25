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
"""The ``sync`` operation — aligning buses to a common time reference."""

from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation


class Sync(Operation):
    """An alignment of buses to a common time reference.

    The user-facing [`QProgram.sync`][qprogram.QProgram.sync] exposes a ``buses=`` keyword; the AST attribute is named
    ``targets`` to avoid shadowing `Operation.buses` (a list attribute named ``buses`` would
    silently hide the introspection method).

    Args:
        targets (list[str] | None): Bus names to sync, or ``None`` to sync every bus currently
            active in the program. With no explicit targets the op broadcasts
            (`Operation.BROADCASTS_WHEN_NO_BUS`), so the validator intersects the
            capabilities of every bus the program touches.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("targets",)
    BROADCASTS_WHEN_NO_BUS: ClassVar[bool] = True

    def __init__(self, targets: list[str] | None = None) -> None:
        self.targets = targets

    def required_capabilities(self) -> set[str]:
        """Return the single ``op.sync`` token."""
        return {"op.sync"}
