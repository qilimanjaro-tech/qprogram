"""Qblox-specific Operation classes.

Each class is a concrete Operation subclass that lives in the QProgram AST.
These are the data nodes — they hold typed attributes and get serialized to `.qp` files.
"""

from __future__ import annotations

from qprogram.operations.operation import Operation
from qprogram.variable import Variable
from qprogram.waveforms.waveform import IQWaveform


class Acquire(Operation):
    """Qblox-specific acquisition without play.

    Unlike ``measure()`` which plays a readout pulse and acquires, this operation
    only acquires — useful when the readout pulse is managed separately.
    """

    def __init__(self, bus: str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        self.bus = bus
        self.weights = weights
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


class MeasureReset(Operation):
    """Active reset: measure, then conditionally apply a reset pulse.

    This is a Qblox-specific operation that uses the sequencer's conditional
    execution capability to perform active qubit reset based on the measurement
    outcome.
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
        self.save_adc = save_adc
