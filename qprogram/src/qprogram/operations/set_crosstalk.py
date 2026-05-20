from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.crosstalk_matrix import CrosstalkMatrix


class SetCrosstalk(Operation):
    """Install a program-wide crosstalk correction matrix.

    The matrix is a global setting rather than a per-bus property, so :attr:`BUS_ATTRS` is empty.

    Args:
        crosstalk: The correction matrix to apply.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, crosstalk: CrosstalkMatrix) -> None:
        self.crosstalk = crosstalk

    def required_capabilities(self) -> set[str]:
        return {"op.set_crosstalk"}
