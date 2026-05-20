from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementOperation, normalize_returns

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.result import MeasurementHandle
    from qprogram.waveforms.waveform import IQWaveform


class Measure(MeasurementOperation):
    """Play a readout pulse and acquire the result.

    Args:
        bus: Readout bus (must have ``acquires=True``).
        waveform: Readout pulse — concrete :class:`~qprogram.waveforms.IQWaveform` or a string alias.
        weights: Integration weights — concrete :class:`~qprogram.waveforms.IQWaveform` or a string alias.
        handle: The canonical :class:`~qprogram.MeasurementHandle` for this measurement. The same Python
            instance is returned to user code, referenced by any :class:`~qprogram.MeasurementRef` in
            conditionals, and listed by :meth:`QProgram.measurement_handles` — the runtime writes
            per-measurement values onto this single object and every reader sees them.
        returns: Tuple of return-type tokens. Default ``("iq",)``. ``"raw"`` requests the raw ADC trace,
            ``"state"`` requests a classified outcome (required when the program references
            ``handle.state`` in a conditional). Platforms decide which tokens they recognise.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights")

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        handle: MeasurementHandle,
        returns: str | Iterable[str] = ("iq",),
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.handle = handle
        self.returns: tuple[str, ...] = normalize_returns(returns)

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token  # noqa: PLC0415

        caps = super().required_capabilities() | {"op.measure", "waveform.iq"}
        for attr in (self.waveform, self.weights):
            if isinstance(attr, str):
                caps.add("waveform.alias")
            else:
                tok = waveform_token(attr)
                if tok is not None:
                    caps.add(tok)
        return caps
