from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.crosstalk_matrix import CrosstalkMatrix


class SetCrosstalk(Operation):
    """Apply a crosstalk correction matrix."""

    def __init__(self, crosstalk: CrosstalkMatrix) -> None:
        self.crosstalk = crosstalk
