"""Typed VendorNamespace for Qblox operations.

This is where strong typing lives — each method has explicit parameter types,
so IDE autocomplete and mypy work when users write ``program.qblox.acquire(...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.vendor import VendorNamespace
from qprogram.waveforms.waveform import IQWaveform

from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)

if TYPE_CHECKING:
    from qprogram.variable import Expression


class QbloxNamespace(VendorNamespace):
    """Qblox vendor namespace — provides typed methods for Qblox-specific operations.

    Accessed via ``program.qblox.<operation>()`` after registration.
    """

    def acquire(self, bus: str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        """Acquire measurement data without playing a readout pulse.

        Args:
            bus: Readout bus name.
            weights: Integration weights (IQWaveform or calibration alias).
            save_adc: Whether to save raw ADC data.
        """
        self._append(Acquire(bus=bus, weights=weights, save_adc=save_adc))

    def set_markers(self, bus: str, mask: str) -> None:
        """Set the 4-bit marker output mask.

        Args:
            bus: Bus name.
            mask: 4-character string of 0s and 1s, e.g. "0001".
        """
        self._append(SetMarkers(bus=bus, mask=mask))

    def set_trigger(
        self,
        bus: str,
        duration: int,
        outputs: list[int] | int | None = None,
        position: str = "start",
    ) -> None:
        """Configure a trigger output.

        Args:
            bus: Bus name.
            duration: Trigger duration in ns.
            outputs: Which trigger outputs to activate.
            position: "start" or "end" of the operation.
        """
        self._append(SetTrigger(bus=bus, duration=duration, outputs=outputs, position=position))

    def wait_trigger(self, bus: str, duration: int, port: int | None = None) -> None:
        """Wait for an external trigger.

        Args:
            bus: Bus name.
            duration: Timeout duration in ns.
            port: Trigger input port number.
        """
        self._append(WaitTrigger(bus=bus, duration=duration, port=port))

    def active_reset(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
    ) -> None:
        """Active qubit reset: measure and conditionally apply a reset pulse.

        A *complex* operation — the qblox compiler expands it into a readout
        pulse + integration + conditional reset pulse driven via the trigger
        network. There's no single sequencer instruction that does this; the
        vendor library owns the lowering from user intent to instructions.

        Args:
            bus: Readout bus name.
            waveform: Readout waveform (IQWaveform or calibration alias).
            weights: Integration weights.
            control_bus: Drive bus for the reset pulse.
            reset_pulse: Reset pulse waveform.
            trigger_address: Trigger network address for conditional execution.
        """
        self._append(
            ActiveReset(
                bus=bus,
                waveform=waveform,
                weights=weights,
                control_bus=control_bus,
                reset_pulse=reset_pulse,
                trigger_address=trigger_address,
            )
        )

    def set_acquisition_threshold(self, bus: str, value: float | Expression) -> None:
        """Set the qubit-state discrimination threshold on a readout bus.

        A *software-only* operation — the qblox platform translates this to a
        QCoDeS parameter set at execution time, not to any sequencer
        instruction. It demonstrates that a vendor namespace can expose
        operations whose effect is entirely off-sequencer; the platform
        decides at execution time how to realize each operation (sequencer
        instruction, slow-control set, multi-step orchestration, …).

        Args:
            bus: Readout bus whose discrimination threshold to set.
            value: Threshold value (volts after integration). Accepts an
                :class:`~qprogram.Expression` so it can be swept by an
                enclosing loop.
        """
        self._append(SetAcquisitionThreshold(bus=bus, value=value))
