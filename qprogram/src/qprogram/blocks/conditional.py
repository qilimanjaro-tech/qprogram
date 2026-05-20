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
        arms: List of ``(condition, body)`` pairs, in source order.
        else_body: Optional terminal ``else`` body.
    """

    def __init__(self) -> None:
        super().__init__()
        self.arms: list[tuple[Expression, Block]] = []
        self.else_body: Block | None = None

    def append(self, element: Block | Operation) -> None:  # noqa: ARG002
        """Disabled — populate via :meth:`QProgram.if_` / :meth:`elif_` / :meth:`else_` instead.

        Raises:
            ValidationError: Always.
        """
        msg = (
            "Cannot append directly to a Conditional. "
            "Use the arm body returned by program.if_() / elif_() / else_() instead."
        )
        raise ValidationError(msg)

    def walk(self) -> Iterator[Block | Operation]:
        yield self
        for _, body in self.arms:
            yield from body.walk()
        if self.else_body is not None:
            yield from self.else_body.walk()

    def variables(self) -> set[Variable]:
        out: set[Variable] = set()
        for cond, body in self.arms:
            out |= cond.variables() | body.variables()
        if self.else_body is not None:
            out |= self.else_body.variables()
        return out

    def buses(self) -> set[str]:
        out: set[str] = set()
        for _, body in self.arms:
            out |= body.buses()
        if self.else_body is not None:
            out |= self.else_body.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        out: set[Waveform | IQWaveform | str] = set()
        for _, body in self.arms:
            out |= body.waveforms()
        if self.else_body is not None:
            out |= self.else_body.waveforms()
        return out

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        caps = {"block.conditional"}
        for cond, _ in self.arms:
            caps |= expression_tokens(cond)
        return caps
