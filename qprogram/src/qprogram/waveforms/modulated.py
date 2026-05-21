from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class Modulated(IQWaveform):
    """IQ pulse formed by intermediate-frequency modulation of a real envelope.

    Produces ``I = envelope · cos(2π·frequency·t + phase)`` and
    ``Q = envelope · sin(2π·frequency·t + phase)``, evaluated at 1-ns resolution. Use this to lift any
    single-channel envelope onto an IQ bus for sideband-modulated drive without writing an
    :class:`IQPair` by hand.

    The materialised I and Q channels are :class:`Arbitrary` waveforms — modulation collapses the
    enclosing envelope's parametric structure to concrete samples.

    Args:
        envelope: Underlying single-channel :class:`Waveform` shaping the pulse.
        frequency: Modulation frequency in Hz.
        phase: Phase offset in radians. Defaults to zero.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("envelope",)

    def __init__(
        self,
        envelope: Waveform,
        frequency: float | Expression,
        phase: float | Expression = 0.0,
    ) -> None:
        if not isinstance(envelope, Waveform):
            msg = f"Modulated envelope must be a Waveform, got {type(envelope).__name__}"
            raise TypeError(msg)
        self.envelope = envelope
        self.frequency = frequency
        self.phase = phase

    def _resolved_params(self) -> tuple[float, float]:
        frequency = self.frequency.evaluate_or_raise() if isinstance(self.frequency, Expression) else self.frequency
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        return float(frequency), float(phase)

    def get_I(self) -> Waveform:
        env = self.envelope.envelope()
        frequency, phase = self._resolved_params()
        t = np.arange(len(env)) * 1e-9
        return Arbitrary(env * np.cos(2 * np.pi * frequency * t + phase))

    def get_Q(self) -> Waveform:
        env = self.envelope.envelope()
        frequency, phase = self._resolved_params()
        t = np.arange(len(env)) * 1e-9
        return Arbitrary(env * np.sin(2 * np.pi * frequency * t + phase))

    def get_duration(self) -> int:
        return self.envelope.get_duration()
