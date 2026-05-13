from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.crosstalk_matrix import CrosstalkMatrix


class SetCrosstalk(Operation):
    """Apply a crosstalk correction matrix.

    The matrix is a program-wide setting (not per-bus); declares
    :attr:`BUS_ATTRS` as empty.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, crosstalk: CrosstalkMatrix) -> None:
        self.crosstalk = crosstalk
