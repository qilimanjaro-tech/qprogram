from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementOperation, normalize_returns

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.result import MeasurementHandle
    from qprogram.waveforms.waveform import IQWaveform


class Measure(MeasurementOperation):
    """Play a readout pulse and acquire the result.

    ``returns`` is the canonical tuple of return-type tokens for this
    measurement. The default ``("iq",)`` matches the historical behaviour
    of returning in-phase / quadrature data. ``"raw"`` requests the raw
    ADC trace alongside; ``"state"`` requests a classified outcome
    (required when the program references ``handle.state`` in a
    conditional). Platforms decide which tokens they recognise.

    ``handle`` is the *canonical*
    :class:`~qprogram.MeasurementHandle` — the same Python instance the
    user got back from :meth:`QProgram.measure`, the same instance every
    :class:`~qprogram.MeasurementRef` inside a conditional points at,
    and the same instance returned by
    :meth:`~qprogram.QProgram.measurement_handles`. The runtime writes
    per-measurement values onto this handle once and every reader sees
    them.
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
