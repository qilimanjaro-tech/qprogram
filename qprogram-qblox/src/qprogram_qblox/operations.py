"""Qblox-specific Operation classes.

Each class is a concrete Operation subclass that lives in the QProgram AST.
These are the data nodes — they hold typed attributes and get serialized to `.qp` files.
"""

from __future__ import annotations

from qprogram.operations.operation import MeasurementOperation, Operation
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform


class Acquire(MeasurementOperation):
    """Qblox-specific acquisition without play.

    Unlike core ``measure()`` which plays a readout pulse and acquires, this
    operation only acquires — useful when the readout pulse is managed
    separately. Returns a :class:`~qprogram.MeasurementHandle` like
    ``measure``; the measurement participates in the program's shared
    per-qubit name counter (so an ``acquire`` after a ``measure`` on the
    same qubit picks up the next free name on that qubit).
    """

    def __init__(self, bus: str, weights: IQWaveform | str, name: str, save_adc: bool = False) -> None:
        self.bus = bus
        self.weights = weights
        self.name = name
        self.save_adc = save_adc

    def get_variables(self) -> set[Variable]:
        if isinstance(self.weights, Variable):
            return {self.weights}
        return set()


class SetMarkers(Operation):
    """Set the 4-bit marker output mask on a Qblox sequencer.

    The mask is a 4-character string of 0s and 1s, e.g. "0001" enables marker 1.
    """

    def __init__(self, bus: str, mask: str) -> None:
        self.bus = bus
        self.mask = mask


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


class WaitTrigger(Operation):
    """Wait for an external trigger on a Qblox sequencer."""

    def __init__(self, bus: str, duration: int, port: int | None = None) -> None:
        self.bus = bus
        self.duration = duration
        self.port = port


class ActiveReset(Operation):
    """Active qubit reset: measure, then conditionally apply a reset pulse.

    A **complex** vendor operation — it doesn't map 1-1 to a single sequencer
    instruction. The qblox compiler expands it into the underlying choreography
    (readout pulse + integration + conditional reset pulse driven by the
    trigger network), but the user expresses intent at the experiment level.
    """

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
        save_adc: bool = False,
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.control_bus = control_bus
        self.reset_pulse = reset_pulse
        self.trigger_address = trigger_address


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

    def get_variables(self) -> set[Variable]:
        if isinstance(self.value, Expression):
            return self.value.variables()
        return set()
