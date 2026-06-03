from __future__ import annotations

from qprogram.blocks.block import Block
from qprogram.errors import ValidationError


class Average(Block):
    """Repeat the contained block ``shots`` times and average measurement results across iterations.

    Args:
        shots: Number of times to execute the block body. Must be a positive integer.

    Raises:
        ValidationError: If ``shots`` is not an integer >= 1.
    """

    def __init__(self, shots: int) -> None:
        super().__init__()
        if not isinstance(shots, int) or isinstance(shots, bool) or shots < 1:
            msg = f"Average shots must be an integer >= 1, got {shots!r}"
            raise ValidationError(msg)
        self.shots = shots

    def required_capabilities(self) -> set[str]:
        return {"block.average"}
