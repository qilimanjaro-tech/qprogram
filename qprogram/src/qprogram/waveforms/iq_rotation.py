from __future__ import annotations

from typing import ClassVar

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.waveform import IQWaveform, Waveform


class IQRotation(IQWaveform):
    """Rotate the I/Q channels of an existing :class:`IQWaveform` by ``phase`` radians.

    Applies the 2x2 rotation::

        I_out = I * cos(phase) - Q * sin(phase)
        Q_out = I * sin(phase) + Q * cos(phase)

    Useful for virtual-Z gates and for applying a software-side phase offset to a calibrated pulse
    without resampling the envelope. Materialises both channels as :class:`Arbitrary` waveforms; for
    purely-symbolic rotation, prefer carrying the phase through the underlying envelope's parameters.

    Args:
        base: The :class:`IQWaveform` to rotate.
        phase: Rotation angle in radians.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("base",)

    def __init__(self, base: IQWaveform, phase: float | Expression) -> None:
        if not isinstance(base, IQWaveform):
            msg = f"IQRotation base must be an IQWaveform, got {type(base).__name__}"
            raise TypeError(msg)
        self.base = base
        self.phase = phase

    def _resolved_phase(self) -> float:
        phase = self.phase.evaluate_or_raise() if isinstance(self.phase, Expression) else self.phase
        return float(phase)

    def get_I(self) -> Waveform:
        phase = self._resolved_phase()
        i_env = self.base.get_I().envelope()
        q_env = self.base.get_Q().envelope()
        return Arbitrary(i_env * np.cos(phase) - q_env * np.sin(phase))

    def get_Q(self) -> Waveform:
        phase = self._resolved_phase()
        i_env = self.base.get_I().envelope()
        q_env = self.base.get_Q().envelope()
        return Arbitrary(i_env * np.sin(phase) + q_env * np.cos(phase))

    def get_duration(self) -> int:
        return self.base.get_duration()
