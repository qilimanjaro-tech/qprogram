"""Typed :class:`~qprogram.VendorNamespace` for QDAC operations.

Each method on :class:`QdacNamespace` is a strongly-typed wrapper that constructs the
corresponding :class:`~qprogram_qdac.operations.Operation` subclass and appends it to the
program's active block. This is where IDE autocomplete and type-checking happen — at runtime the
dynamic ``__getattr__`` on :class:`~qprogram.QProgram` would dispatch the same calls, but the
typed namespace gives users a discoverable surface.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from qprogram.vendor import VendorNamespace

from qprogram_qdac.operations import (
    Play,
    SetOffset,
    SetTrigger,
    TriggerPosition,
    WaitTrigger,
)

if TYPE_CHECKING:
    from qprogram.variable import Expression
    from qprogram.waveforms.waveform import Waveform


class QdacNamespace(VendorNamespace):
    """QDAC vendor namespace — typed methods accessible via ``program.qdac.<operation>()``.

    Attached to every :class:`~qprogram.QProgram` instance as ``.qdac`` once the
    :mod:`qprogram_qdac` package is imported. Each method validates its arguments via the typed
    signature, constructs the matching operation, and appends it to the program's currently
    active block.
    """

    def wait_trigger(self, bus: str, port: int) -> None:
        """Append a :class:`~qprogram_qdac.operations.WaitTrigger` operation.

        Args:
            bus: QDAC channel whose trigger input the sequencer listens on.
            port: Trigger input port number on the chassis.
        """
        self._append(WaitTrigger(bus=bus, port=port))

    def set_trigger(
        self,
        bus: str,
        duration: int,
        position: TriggerPosition = "start",
        outputs: Iterable[int] = (),
    ) -> None:
        """Append a :class:`~qprogram_qdac.operations.SetTrigger` operation.

        Args:
            bus: QDAC channel whose trigger outputs are being configured.
            duration: Trigger-active duration in nanoseconds.
            position: Sequence event at which the triggers fire. One of ``"start"``,
                ``"step"``, ``"end"``, ``"end_step"``. Default ``"start"``.
            outputs: Trigger output indices to arm. Any iterable of ints — ``set``, ``list``,
                ``tuple``, generator. Empty by default.
        """
        self._append(SetTrigger(bus=bus, duration=duration, position=position, outputs=outputs))

    def set_offset(self, bus: str, offset: float | Expression) -> None:
        """Append a :class:`~qprogram_qdac.operations.SetOffset` operation.

        Args:
            bus: QDAC channel whose DC offset is being set.
            offset: Target offset in volts. Accepts a literal ``float`` or any
                :class:`~qprogram.Expression` (e.g. a loop-bound :class:`~qprogram.Variable`).
        """
        self._append(SetOffset(bus=bus, offset=offset))

    def play(
        self,
        bus: str,
        waveform: Waveform,
        dwell: int = 1,
        delay: int = 0,
        repetitions: int = 1,
        stepped: bool = False,
    ) -> None:
        """Append a :class:`~qprogram_qdac.operations.Play` operation.

        Args:
            bus: QDAC channel that emits the waveform.
            waveform: Single-channel :class:`~qprogram.waveforms.Waveform` to emit.
            dwell: Per-sample dwell time in nanoseconds. Default ``1``.
            delay: Delay before the first sample, in nanoseconds. Default ``0``.
            repetitions: Number of times to repeat the envelope. Default ``1``.
            stepped: Discrete-step output (``True``) vs continuous interpolated (``False``).
                Default ``False``.
        """
        self._append(
            Play(
                bus=bus,
                waveform=waveform,
                dwell=dwell,
                delay=delay,
                repetitions=repetitions,
                stepped=stepped,
            ),
        )
