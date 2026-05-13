from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementOperation

if TYPE_CHECKING:
    from qprogram.waveforms.waveform import IQWaveform


class Measure(MeasurementOperation):
    """Play a readout pulse and acquire the result.

    ``name`` is the measurement's :class:`~qprogram.MeasurementHandle` name,
    assigned at construction time by :meth:`QProgram.measure`. It is part
    of the AST node so that round-tripping through ``.qp`` preserves
    handle identity.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights")

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        name: str,
        save_adc: bool = False,
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.name = name
        self.save_adc = save_adc
