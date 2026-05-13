from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementOperation, normalize_returns

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.waveforms.waveform import IQWaveform


class Measure(MeasurementOperation):
    """Play a readout pulse and acquire the result.

    ``returns`` is the canonical tuple of return-type tokens for this
    measurement. The default ``("iq",)`` matches the historical behaviour
    of returning in-phase / quadrature data. ``"raw"`` requests the raw
    ADC trace alongside; ``"state"`` (forward-looking) will request a
    classified outcome once the platform-side classifier lands. Platforms
    decide which tokens they recognise.

    ``name`` is the measurement's :class:`~qprogram.MeasurementHandle`
    name, assigned at construction time by :meth:`QProgram.measure`. It
    is part of the AST node so that round-tripping through ``.qp``
    preserves handle identity.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights")

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        name: str,
        returns: str | Iterable[str] = ("iq",),
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.name = name
        self.returns: tuple[str, ...] = normalize_returns(returns)
