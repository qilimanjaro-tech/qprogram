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
"""Multi-armed conditional block.

A :class:`Conditional` represents a chain of ``if`` / ``elif`` / ``else`` arms in source order. Built via
:meth:`QProgram.if_` / :meth:`elif_` / :meth:`else_` — never instantiated directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.operations.operation import Operation
    from qprogram.variable import Expression, Variable
    from qprogram.waveforms.waveform import IQWaveform, Waveform


class Conditional(Block):
    """A chain of ``if`` / ``elif`` / ``else`` arms.

    Why this doesn't reuse the inherited ``_elements`` list: arms each carry an independent body and a
    condition expression, so a single ordered list would conflate "arm body" with "shared body" — there
    is no shared body in a conditional. :meth:`append` therefore raises; populate via the builder
    methods on :class:`~qprogram.QProgram`.

    Attributes:
        arms (list[tuple[Expression, Block]]): The ``(condition, body)`` pairs, in source order. The
            first condition that evaluates truthy selects its body.
        else_body (Block | None): The terminal ``else`` body, or ``None`` when the chain has none.
    """

    def __init__(self) -> None:
        super().__init__()
        self.arms: list[tuple[Expression, Block]] = []
        self.else_body: Block | None = None

    def append(self, element: Block | Operation) -> None:  # ruff: ignore[unused-method-argument]
        """Raise ``ValidationError`` — populate via :meth:`QProgram.if_` / :meth:`elif_` / :meth:`else_` instead.

        Args:
            element (Block | Operation): Ignored; the call never succeeds.

        Raises:
            ValidationError: Always.
        """
        msg = (
            "Cannot append directly to a Conditional. "
            "Use the arm body returned by program.if_() / elif_() / else_() instead."
        )
        raise ValidationError(msg)

    def walk(self) -> Iterator[Block | Operation]:
        """Yield this block, then every node of each arm body and of the ``else`` body.

        Arm conditions are expressions rather than AST nodes, so they are not yielded; a consumer that
        needs them reads :attr:`arms` directly.

        Yields:
            This conditional first, then each arm body's nodes in source order, then the ``else``
            body's.
        """
        yield self
        for _, body in self.arms:
            yield from body.walk()
        if self.else_body is not None:
            yield from self.else_body.walk()

    def variables(self) -> set[Variable]:
        """Return every :class:`~qprogram.Variable` referenced by an arm condition or an arm body.

        The conditions count: a branch taken on a swept threshold makes that variable part of the
        conditional even when no operation inside it reads the variable.

        Returns:
            The union over every arm's condition and body, plus the ``else`` body.
        """
        out: set[Variable] = set()
        for cond, body in self.arms:
            out |= cond.variables() | body.variables()
        if self.else_body is not None:
            out |= self.else_body.variables()
        return out

    def buses(self) -> set[str]:
        """Return every bus name referenced from any arm body or from the ``else`` body.

        Returns:
            The union of the bus names of every arm body and the ``else`` body.
        """
        out: set[str] = set()
        for _, body in self.arms:
            out |= body.buses()
        if self.else_body is not None:
            out |= self.else_body.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Return every waveform (concrete or string alias) referenced by any arm body.

        Returns:
            The union of the waveforms of every arm body and the ``else`` body.
        """
        out: set[Waveform | IQWaveform | str] = set()
        for _, body in self.arms:
            out |= body.waveforms()
        if self.else_body is not None:
            out |= self.else_body.waveforms()
        return out

    def required_capabilities(self) -> set[str]:
        """Return ``block.conditional`` plus the expression tokens of every arm condition.

        A platform that branches must also evaluate the conditions, so each arm contributes the
        ``expr.*`` tokens of its condition tree — the comparison, the measurement reference it reads,
        and any arithmetic around them. The ``else`` arm has no condition and contributes none. Like
        every other node's token set this is non-recursive: the arm bodies' own tokens are collected as
        the validator walks them.

        Returns:
            The identity token ``block.conditional`` together with the ``expr.*`` tokens of the arm
            conditions.
        """
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        caps = {"block.conditional"}
        for cond, _ in self.arms:
            caps |= expression_tokens(cond)
        return caps
