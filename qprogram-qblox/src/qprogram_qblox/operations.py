"""Qblox-specific Operation classes.

Each class is a concrete Operation subclass that lives in the QProgram AST.
These are the data nodes — they hold typed attributes and get serialized to `.qp` files.

Each class declares its ``BUS_ATTRS`` and ``WAVEFORM_ATTRS`` class attributes
where its data shape differs from the core ``Operation`` defaults. The base
``Operation``'s introspection methods (``variables``, ``buses``,
``waveforms``, ``walk``) then work automatically without per-class overrides.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from qprogram.operations.operation import MeasurementOperation, Operation, normalize_returns
from qprogram.variable import Expression
from qprogram.waveforms.waveform import IQWaveform


class Acquire(MeasurementOperation):
    """Qblox-specific acquisition without play.

    Unlike core ``measure()`` which plays a readout pulse and acquires, this
    operation only acquires — useful when the readout pulse is managed
    separately. Returns a :class:`~qprogram.MeasurementHandle` like
    ``measure``; the measurement participates in the program's shared
    per-qubit name counter (so an ``acquire`` after a ``measure`` on the
    same qubit picks up the next free name on that qubit).

    ``returns`` controls what the platform returns for this acquisition
    (default ``("iq",)``); see :class:`~qprogram.operations.Measure` for
    the full description.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("weights",)

    def __init__(
        self,
        bus: str,
        weights: IQWaveform | str,
        name: str,
        returns: str | Iterable[str] = ("iq",),
    ) -> None:
        self.bus = bus
        self.weights = weights
        self.name = name
        self.returns: tuple[str, ...] = normalize_returns(returns)

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token  # noqa: PLC0415

        caps = super().required_capabilities() | {"vendor.qblox.acquire", "waveform.iq"}
        if isinstance(self.weights, str):
            caps.add("waveform.alias")
        else:
            tok = waveform_token(self.weights)
            if tok is not None:
                caps.add(tok)
        return caps


class SetMarkers(Operation):
    """Set the 4-bit marker output mask on a Qblox sequencer.

    The mask is a 4-character string of 0s and 1s, e.g. "0001" enables marker 1.
    """

    def __init__(self, bus: str, mask: str) -> None:
        self.bus = bus
        self.mask = mask

    def required_capabilities(self) -> set[str]:
        return {"vendor.qblox.set_markers"}


class SetTrigger(Operation):
    """Configure a trigger output on a Qblox sequencer."""

    def __init__(
        self,
        bus: str,
        duration: int,
        outputs: list[int] | int | None = None,
        position: str = "start",
    ) -> None:
        self.bus = bus
        self.duration = duration
        self.outputs = outputs
        self.position = position

    def required_capabilities(self) -> set[str]:
        return {"vendor.qblox.set_trigger"}


class WaitTrigger(Operation):
    """Wait for an external trigger on a Qblox sequencer."""

    def __init__(self, bus: str, duration: int, port: int | None = None) -> None:
        self.bus = bus
        self.duration = duration
        self.port = port

    def required_capabilities(self) -> set[str]:
        return {"vendor.qblox.wait_trigger"}


class ActiveReset(Operation):
    """Active qubit reset: measure, then conditionally apply a reset pulse.

    A **complex** vendor operation — it doesn't map 1-1 to a single sequencer
    instruction. The qblox compiler expands it into the underlying choreography
    (readout pulse + integration + conditional reset pulse driven by the
    trigger network), but the user expresses intent at the experiment level.

    Has two bus references (``bus`` for the readout, ``control_bus`` for the
    reset pulse) and three waveforms (the readout ``waveform``, integration
    ``weights``, and the conditional ``reset_pulse``). The ``save_adc``
    boolean that previously gated the raw-ADC output has been folded into
    the new core convention: a future revision can grow this op a
    :attr:`returns` field of the same shape as :class:`Acquire`. Today it
    is left out — re-add as soon as a real use-case lands.
    """

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus", "control_bus")
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights", "reset_pulse")

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.control_bus = control_bus
        self.reset_pulse = reset_pulse
        self.trigger_address = trigger_address

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token  # noqa: PLC0415

        caps = {"vendor.qblox.active_reset", "waveform.iq"}
        for attr in (self.waveform, self.weights, self.reset_pulse):
            if isinstance(attr, str):
                caps.add("waveform.alias")
            else:
                tok = waveform_token(attr)
                if tok is not None:
                    caps.add(tok)
        return caps


class SetAcquisitionThreshold(Operation):
    """Set the qubit-state discrimination threshold on a readout bus.

    A **software-only** vendor operation — the qblox platform translates it to
    a QCoDeS parameter set at execution time rather than emitting any
    sequencer instruction. It illustrates that vendor ops don't have to map to
    hardware sequencer instructions at all: a vendor extension can expose any
    operation whose execution the platform knows how to interpret, whether
    that's a sequencer command, a slow-control parameter, or a multi-step
    orchestration.
    """

    def __init__(self, bus: str, value: float | Expression) -> None:
        self.bus = bus
        self.value = value

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # noqa: PLC0415

        return {"vendor.qblox.set_acquisition_threshold"} | expression_tokens(self.value)
