"""Multi-armed conditional block.

A :class:`Conditional` represents a chain of ``if``/``elif``/``else`` arms
in source order. Each arm carries a condition :class:`Expression` (today
restricted by :meth:`QProgram.if_` to a :class:`Comparison` against an
``int`` literal — see the v1 scope on the spec) and a body :class:`Block`.
The optional terminal ``else`` body lives in :attr:`else_body`.

Unlike :class:`~qprogram.blocks.Block`/``Average``/``ForLoop``/``Loop``,
``Conditional`` does **not** use the inherited ``_elements`` list. The
generic ``_elements`` would conflate "arm body content" with "no-op other
children", which is meaningless for a conditional. :meth:`append` raises;
the only way to populate a conditional is through the
:meth:`QProgram.if_` / :meth:`elif_` / :meth:`else_` builder, which
appends into the appropriate arm body.

Introspection (:meth:`walk`, :meth:`variables`, :meth:`buses`,
:meth:`waveforms`) traverses the arms in order, then the else body.
Each arm's condition expression contributes its variables (and capability
tokens) to the parent program.
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
    """Multi-armed conditional with optional else.

    See module docstring for the design notes.
    """

    def __init__(self) -> None:
        super().__init__()
        self.arms: list[tuple[Expression, Block]] = []
        self.else_body: Block | None = None

    def append(self, element: Block | Operation) -> None:  # noqa: ARG002
        msg = (
            "Cannot append directly to a Conditional. "
            "Use the arm body returned by program.if_() / elif_() / else_() instead."
        )
        raise ValidationError(msg)

    def walk(self) -> Iterator[Block | Operation]:
        """Yield self, then each arm body in order, then else_body (if any)."""
        yield self
        for _, body in self.arms:
            yield from body.walk()
        if self.else_body is not None:
            yield from self.else_body.walk()

    def variables(self) -> set[Variable]:
        """Union of variables across each condition and each arm body's contents."""
        out: set[Variable] = set()
        for cond, body in self.arms:
            out |= cond.variables() | body.variables()
        if self.else_body is not None:
            out |= self.else_body.variables()
        return out

    def buses(self) -> set[str]:
        """Union of bus references across arm bodies and the else body."""
        out: set[str] = set()
        for _, body in self.arms:
            out |= body.buses()
        if self.else_body is not None:
            out |= self.else_body.buses()
        return out

    def waveforms(self) -> set[Waveform | IQWaveform | str]:
        """Union of waveform references across arm bodies and the else body."""
        out: set[Waveform | IQWaveform | str] = set()
        for _, body in self.arms:
            out |= body.waveforms()
        if self.else_body is not None:
            out |= self.else_body.waveforms()
        return out

    def required_capabilities(self) -> set[str]:
        """Tokens needed by *this* block alone.

        ``block.conditional`` for the construct itself, plus the
        expression tokens of every arm condition. Children's tokens are
        not unioned here — the validator walks the AST and visits each
        child separately.
        """
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        caps = {"block.conditional"}
        for cond, _ in self.arms:
            caps |= expression_tokens(cond)
        return caps
