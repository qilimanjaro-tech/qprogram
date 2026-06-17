"""Typed :class:`~qprogram.VendorNamespace` for MyPlatform operations.

Each method on :class:`MyPlatformNamespace` is a strongly-typed wrapper that constructs the
matching :class:`~my_platform.operations.Operation` subclass and appends it to the program's active
block. At runtime the dynamic ``__getattr__`` on :class:`~qprogram.QProgram` dispatches the same
calls once :mod:`my_platform` is imported; this typed surface is what gives IDE autocomplete and
type-checking for ``program.myplatform.*``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.vendor import VendorNamespace

from my_platform.operations import SetCrosstalk, SetRFSwitch

if TYPE_CHECKING:
    from numpy.typing import ArrayLike

    from qprogram.variable import Expression


class MyPlatformNamespace(VendorNamespace):
    """MyPlatform vendor namespace — typed methods accessible via ``program.myplatform.<operation>()``.

    Attached to every :class:`~qprogram.QProgram` instance as ``.myplatform`` once :mod:`my_platform`
    is imported. Each method validates its arguments via the typed signature, constructs the matching
    operation, and appends it to the program's currently active block.
    """

    def set_crosstalk(self, bus: str, matrix: ArrayLike) -> None:
        """Append a :class:`~my_platform.operations.SetCrosstalk` operation.

        Args:
            bus: Flux bus whose controller receives the matrix (``flux`` on a flux-tunable transmon,
                or ``flux_x`` / ``flux_z`` on a fluxonium).
            matrix: Dense ``N×N`` crosstalk matrix — an :class:`numpy.ndarray` or anything
                :func:`numpy.asarray` accepts (e.g. a nested list of floats).
        """
        self._append(SetCrosstalk(bus=bus, matrix=matrix))

    def set_rf_switch(self, bus: str, channel: int | Expression) -> None:
        """Append a :class:`~my_platform.operations.SetRFSwitch` operation.

        Args:
            bus: The RF-switch control line (an ``rf`` bus of the RF-switch schema).
            channel: Output port index to route to. A literal ``int`` or any
                :class:`~qprogram.Expression` (e.g. a loop-bound :class:`~qprogram.Variable`).
        """
        self._append(SetRFSwitch(bus=bus, channel=channel))
