"""Typed VendorNamespace for Qblox operations.

This is where strong typing lives — each method has explicit parameter types,
so IDE autocomplete and mypy work when users write ``program.qblox.acquire(...)``.
"""

from __future__ import annotations

from qprogram.vendor import VendorNamespace
from qprogram.waveforms.waveform import IQWaveform

from qprogram_qblox.operations import Acquire, MeasureReset, SetMarkers, SetTrigger, WaitTrigger


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

    def measure_reset(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
        save_adc: bool = False,
    ) -> None:
        """Active reset: measure and conditionally apply a reset pulse.

        Args:
            bus: Readout bus name.
            waveform: Readout waveform (IQWaveform or calibration alias).
            weights: Integration weights.
            control_bus: Drive bus for the reset pulse.
            reset_pulse: Reset pulse waveform.
            trigger_address: Trigger network address for conditional execution.
            save_adc: Whether to save raw ADC data.
        """
        self._append(
            MeasureReset(
                bus=bus,
                waveform=waveform,
                weights=weights,
                control_bus=control_bus,
                reset_pulse=reset_pulse,
                trigger_address=trigger_address,
                save_adc=save_adc,
            )
        )
