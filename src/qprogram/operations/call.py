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
"""The fragment call site — the AST leaf that instantiates a [`Fragment`][qprogram.Fragment]."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation, _collect_variables

if TYPE_CHECKING:
    from qprogram.fragments import Fragment
    from qprogram.variable import Variable


class Call(Operation):
    """Instantiation of a [`Fragment`][qprogram.Fragment] at a specific site with bound arguments.

    A first-class AST leaf: definitions and call sites survive serialization (``.qp`` emits a
    ``fragment <name>(...):`` section and a bare ``<name>(<args>)`` statement) and structural
    equality. [`QProgram.expand`][qprogram.QProgram.expand] replaces every ``Call`` with the substituted fragment body —
    compilers and validators consume the expanded, fragment-free program.

    Built by [`QProgram.call`][qprogram.QProgram.call]; direct construction is rarely needed.

    Args:
        fragment (Fragment): The called [`Fragment`][qprogram.Fragment].
        arguments (dict[str, object]): Fully-bound ``{param_id: value}`` mapping (one entry per
            fragment parameter), produced by `qprogram.fragments.bind_arguments`.
    """

    # Buses reach a Call only through ``arguments`` values; the validator never routes a Call
    # (validation expands first), so no BUS_ATTRS are declared.
    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, fragment: Fragment, arguments: dict[str, object]) -> None:
        self.fragment = fragment
        self.arguments = arguments

    def variables(self) -> set[Variable]:
        """Return host variables referenced by the bound arguments.

        The fragment body's own parameters/locals are *not* reported — they are placeholders that
        exist only until expansion.
        """
        out: set[Variable] = set()
        for value in self.arguments.values():
            out |= _collect_variables(value)
        return out

    def buses(self) -> set[str]:
        """Return every string-valued argument bound at this call site.

        A [`Parameter`][qprogram.Parameter] is untyped — only the position an argument lands in inside
        the fragment body decides whether it is a bus, a waveform alias, or a plain value — so a
        string argument of any kind is reported here and the result over-approximates the buses the
        call touches. Buses named inside the fragment body become visible once
        [`QProgram.expand`][qprogram.QProgram.expand] has substituted the arguments.
        """
        return {value for value in self.arguments.values() if isinstance(value, str)}

    def required_capabilities(self) -> set[str]:
        """Return the empty set — ``validate()`` expands calls before checking capabilities."""
        return set()

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items())
        return f"Call({self.fragment.name}({args}))"
