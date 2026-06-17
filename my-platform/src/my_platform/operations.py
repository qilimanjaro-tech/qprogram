"""MyPlatform-specific :class:`~qprogram.operations.Operation` classes.

The MyPlatform package is unusual among this monorepo's examples: it is both a *platform*
(:class:`~my_platform.MyPlatform`) and a *vendor* extension. These are the vendor operations it
contributes — data nodes in the QProgram AST, with typed attributes and capability introspection,
no behaviour.

Two operations, each bound to a different bus family of the combined schema MyPlatform drives
(``BusSchema.flux_tunable_transmon() + RFSwitchSchema()``):

* :class:`SetCrosstalk` — installs a flux **crosstalk-compensation matrix** for the flux line it
  targets. Valid on any flux-like bus: ``flux`` of a flux-tunable transmon, or ``flux_x`` / ``flux_z``
  of a fluxonium. Capability routing enforces that (the ``vendor.myplatform.set_crosstalk`` token
  lives only on flux profiles), so the same op simply fails to validate on a drive/readout/switch bus.
* :class:`SetRFSwitch` — selects which output port an RF switch routes to. Valid on the ``rf`` buses
  of the RF-switch schema this package ships.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from qprogram.errors import ValidationError
from qprogram.operations.operation import Operation
from qprogram.variable import Expression

if TYPE_CHECKING:
    from numpy.typing import ArrayLike


class SetCrosstalk(Operation):
    """Install a flux crosstalk-compensation matrix on a flux bus.

    Flux lines on neighbouring qubits couple capacitively, so a bias applied to one line bleeds onto
    others. MyPlatform corrects this by pushing a small dense **crosstalk matrix** to the flux
    controller for the targeted line. The matrix is stored as a 2-D :class:`numpy.ndarray` (rows =
    sources, columns = targets) so structural equality and hashing work out of the box (see
    :mod:`qprogram._structural`).

    The op routes to the per-bus capability slot of its ``bus`` (the default ``BUS_ATTRS = ("bus",)``
    applies). The ``vendor.myplatform.set_crosstalk`` token is published only on flux profiles, so a
    program that targets a non-flux bus fails validation with a ``missing-capability`` diagnostic.

    Attributes:
        bus: Flux bus whose controller receives the matrix (``flux``, ``flux_x``, or ``flux_z``).
        matrix: Dense ``N×N`` crosstalk matrix as a ``float`` :class:`numpy.ndarray`.
    """

    def __init__(self, bus: str, matrix: ArrayLike) -> None:
        """Initialise a SetCrosstalk node.

        Args:
            bus: Flux bus the matrix applies to.
            matrix: Crosstalk coefficients — anything :func:`numpy.asarray` accepts (an ``ndarray``,
                a nested list of floats, ...). Coerced to a 2-D ``float`` :class:`numpy.ndarray` so
                the stored value compares structurally regardless of the input container.

        Raises:
            ValidationError: If ``matrix`` is not a non-empty 2-D array. A crosstalk matrix is a
                2-D grid of source→target coefficients; a 1-D input is almost always a missing
                layer of brackets, and an empty matrix can't survive the ``.qp`` round-trip (it
                serializes to ``[]`` and reloads as 1-D). Rejecting both at build time keeps the
                invariant that anything the builder accepts also reloads.
        """
        self.bus = bus
        self.matrix = np.asarray(matrix, dtype=float)
        if self.matrix.ndim != 2 or self.matrix.size == 0:
            msg = (
                f"set_crosstalk matrix must be a non-empty 2-D array (an N×N grid of coefficients), "
                f"got shape {self.matrix.shape}. Pass e.g. [[1.0, 0.1], [0.1, 1.0]]."
            )
            raise ValidationError(msg)

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.myplatform.set_crosstalk"}`` — the single identity token. The matrix is plain
            data, contributing no refinement tokens.
        """
        return {"vendor.myplatform.set_crosstalk"}


class SetRFSwitch(Operation):
    """Route an RF switch to one of its output ports.

    The (imaginary) RF switch is a microwave routing matrix; ``channel`` selects which output port
    the input is connected to. ``channel`` may be a swept :class:`~qprogram.variable.Expression`, so
    an experiment can step a signal across output ports inside a loop. The switch is a fast
    (real-time-capable) device, so MyPlatform wires its bus into both the ``hw`` and ``sw`` domains —
    a swept-channel loop can stay real-time hardware.

    Routes to the per-bus capability slot of its ``bus`` (default ``BUS_ATTRS = ("bus",)``). The
    ``vendor.myplatform.set_rf_switch`` token lives only on the RF-switch bus profile.

    Attributes:
        bus: The RF-switch control line (an ``rf`` bus of the RF-switch schema).
        channel: Output port index to route to. A literal ``int`` or any
            :class:`~qprogram.variable.Expression` (e.g. a loop-bound :class:`~qprogram.Variable`).
    """

    def __init__(self, bus: str, channel: int | Expression) -> None:
        """Initialise a SetRFSwitch node.

        Args:
            bus: The RF-switch control line.
            channel: Output port index — a literal ``int`` or an :class:`~qprogram.variable.Expression`.
        """
        self.bus = bus
        self.channel = channel

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.myplatform.set_rf_switch"}`` unioned with whatever expression-shape tokens the
            ``channel`` argument contributes (``expr.variable``, ``expr.binary_op``, ...) when it is
            an :class:`~qprogram.variable.Expression`.
        """
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"vendor.myplatform.set_rf_switch"} | expression_tokens(self.channel)


def set_crosstalk_serialize(op: SetCrosstalk, ctx: Any) -> str:
    """Serialize :class:`SetCrosstalk` to its ``.qp`` line.

    A custom callback is required because the writer's generic value serializer only handles 1-D
    arrays, while a crosstalk matrix is 2-D. We emit the bus (path form when schema-backed) and the
    matrix as a nested bracket literal:

        ``myplatform.set_crosstalk q[0].flux matrix=[[1.0, 0.1], [0.1, 1.0]]``

    The default parser reverses this with no custom callback: it reads ``matrix=[[...]]`` as a nested
    Python list and :class:`SetCrosstalk`'s constructor coerces it back to an ``ndarray``.
    """
    bus = ctx.serialize_bus(op.bus)
    matrix = ctx.serialize_value(op.matrix.tolist())
    return f"myplatform.set_crosstalk {bus} matrix={matrix}"


__all__ = ["SetCrosstalk", "SetRFSwitch", "set_crosstalk_serialize"]
