"""QDAC-specific Operation classes.

Each class is a concrete :class:`~qprogram.operations.Operation` subclass that lives in the
QProgram AST. They are the data nodes — typed attributes plus introspection, no behaviour.

QDAC is a slow high-precision DAC most often used for flux biasing on transmon platforms; its
waveform engine emits ramp-shaped envelopes from a programmable sequencer with explicit dwell /
delay / repetitions / stepped controls. The vendor operations here expose those primitives plus
the trigger-network plumbing the engine listens to.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar, Literal

from qprogram.operations.operation import Operation
from qprogram.variable import Expression

if TYPE_CHECKING:
    from qprogram.waveforms.waveform import Waveform


TriggerPosition = Literal["start", "step", "end", "end_step"]
"""The four trigger-fire positions the QDAC sequencer recognises.

- ``"start"``: trigger emits when the sequence begins.
- ``"step"``: trigger emits at the start of every step within a stepped sequence.
- ``"end"``: trigger emits when the sequence finishes.
- ``"end_step"``: trigger emits at the end of every step.
"""


def _normalize_outputs(value: Iterable[int] | str) -> tuple[int, ...]:
    """Coerce an outputs argument into a sorted ``tuple[int, ...]``.

    Accepts any iterable of integers (``set``, ``list``, ``tuple``, numpy array, generator),
    or a comma-separated string like ``"1,2,3"`` produced by the ``.qp`` writer. Duplicates are
    discarded and the result is sorted ascending so two semantically equivalent inputs hash and
    compare the same.

    Args:
        value: Iterable of integers or comma-separated string.

    Returns:
        Sorted tuple of unique integer output indices.
    """
    if isinstance(value, str):
        return tuple(sorted({int(p.strip()) for p in value.split(",") if p.strip()}))
    return tuple(sorted({int(x) for x in value}))


class WaitTrigger(Operation):
    """Block until an external trigger arrives on the configured input port of a QDAC channel.

    Used to synchronise QDAC sequences with hardware running on another instrument (e.g. a qblox
    sequencer's `set_trigger`). The QDAC sequencer halts until the trigger fires; no waveform is
    emitted during the wait.

    Attributes:
        bus: QDAC channel whose trigger-input the sequencer listens to.
        port: Trigger input port number on the QDAC chassis.
    """

    def __init__(self, bus: str, port: int) -> None:
        """Initialise a WaitTrigger node.

        Args:
            bus: QDAC channel whose trigger input we're waiting on.
            port: Trigger input port number (chassis-defined; typically 1-based).
        """
        self.bus = bus
        self.port = port

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.qdac.wait_trigger"}`` — the single identity token. WaitTrigger has no
            instance-state-dependent refinement tokens.
        """
        return {"vendor.qdac.wait_trigger"}


class SetTrigger(Operation):
    """Configure one or more QDAC trigger outputs to fire at a chosen sequence position.

    The QDAC chassis carries an internal trigger bus with multiple output lines. ``SetTrigger``
    arms a subset of those outputs to fire at a sequence event — sequence start, every step,
    sequence end, or every step's end — for ``duration`` nanoseconds.

    Attributes:
        bus: QDAC channel whose trigger outputs are being configured.
        duration: Trigger-active duration in nanoseconds.
        position: Sequence event at which the triggers fire. One of
            ``"start"``, ``"step"``, ``"end"``, ``"end_step"``.
        outputs: Sorted, deduplicated tuple of trigger output indices to arm.
    """

    def __init__(
        self,
        bus: str,
        duration: int,
        position: TriggerPosition = "start",
        outputs: Iterable[int] | str = (),
    ) -> None:
        """Initialise a SetTrigger node.

        Args:
            bus: QDAC channel whose trigger outputs are being configured.
            duration: Trigger-active duration in nanoseconds.
            position: When the triggers should fire. Defaults to ``"start"``. Must be one of
                ``"start"``, ``"step"``, ``"end"``, ``"end_step"``.
            outputs: Trigger output indices to arm. Accepts any iterable of ints (``set``,
                ``list``, ``tuple``, ...) or a comma-separated string (the round-trip form from
                the ``.qp`` writer). Duplicates are discarded; the stored value is sorted.
        """
        self.bus = bus
        self.duration = duration
        self.position: TriggerPosition = position
        self.outputs: tuple[int, ...] = _normalize_outputs(outputs)

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.qdac.set_trigger"}`` — the single identity token.
        """
        return {"vendor.qdac.set_trigger"}


class SetOffset(Operation):
    """Set a static DC offset on a QDAC channel.

    The QDAC channel holds a constant voltage of ``offset`` until another operation changes it.
    ``offset`` may be a swept :class:`~qprogram.Expression` — the qdac platform handles such
    sweeps by re-uploading the value per shot (a soft :class:`~qprogram.DomainConstraint` on
    real-time hardware execution; see :mod:`qprogram_qdac.profiles`).

    Attributes:
        bus: QDAC channel whose DC offset is being set.
        offset: Target offset value (volts). Accepts a literal ``float`` or any
            :class:`~qprogram.Expression` for sweeps.
    """

    def __init__(self, bus: str, offset: float | Expression) -> None:
        """Initialise a SetOffset node.

        Args:
            bus: QDAC channel whose DC offset is being set.
            offset: Target offset value in volts. May be a literal or an
                :class:`~qprogram.Expression` (e.g. a loop-bound :class:`~qprogram.Variable`).
        """
        self.bus = bus
        self.offset = offset

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.qdac.set_offset"}`` unioned with whatever expression-shape tokens the
            ``offset`` argument contributes (``expr.variable``, ``expr.binary_op``, ...).
        """
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"vendor.qdac.set_offset"} | expression_tokens(self.offset)


class Play(Operation):
    """Emit an arbitrary waveform from a QDAC channel's waveform engine.

    Routes through the bus's per-channel capability slot like every other QDAC operation; the
    target channel comes from ``bus``. The remaining parameters describe the waveform-engine
    program (envelope + timing).

    Attributes:
        bus: QDAC channel that emits the waveform.
        waveform: Single-channel waveform whose envelope drives the QDAC DAC.
        dwell: Per-sample dwell time in nanoseconds (sample emission rate).
        delay: Delay in nanoseconds between sequence start and the first sample.
        repetitions: How many times the waveform engine repeats the envelope (``1`` plays once).
        stepped: ``True`` to step through samples discretely (re-arming the DAC each sample);
            ``False`` for continuous interpolated output.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(
        self,
        bus: str,
        waveform: Waveform,
        dwell: int = 1,
        delay: int = 0,
        repetitions: int = 1,
        stepped: bool = False,
    ) -> None:
        """Initialise a Play node.

        Args:
            bus: QDAC channel that emits the waveform.
            waveform: Single-channel :class:`~qprogram.waveforms.Waveform` whose envelope is
                uploaded to the QDAC waveform engine.
            dwell: Per-sample dwell time in nanoseconds. Default ``1``.
            delay: Delay in nanoseconds before the first sample emits. Default ``0``.
            repetitions: How many times to repeat the envelope. Default ``1``.
            stepped: Discrete-step mode (``True``) vs continuous output (``False``). Default
                ``False``.
        """
        self.bus = bus
        self.waveform = waveform
        self.dwell = dwell
        self.delay = delay
        self.repetitions = repetitions
        self.stepped = stepped

    def required_capabilities(self) -> set[str]:
        """Return the capability tokens this operation needs.

        Returns:
            ``{"vendor.qdac.play"}`` plus the per-class waveform token (e.g. ``"waveform.ramp"``
            when the envelope is a :class:`~qprogram.waveforms.Ramp`). The single-channel kind
            token ``"waveform.single"`` is always added — QDAC only emits single-channel
            envelopes.
        """
        from qprogram.protocol import waveform_token  # noqa: PLC0415

        caps = {"vendor.qdac.play", "waveform.single"}
        tok = waveform_token(self.waveform)
        if tok is not None:
            caps.add(tok)
        return caps
