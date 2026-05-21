"""Built-in waveform shapes for pulse-level programs.

Single-channel waveforms subclass :class:`Waveform`; complex (I/Q) waveforms subclass :class:`IQWaveform`.
Both bases expose ``envelope(resolution)`` / ``get_I()`` / ``get_Q()`` and ``get_duration()`` for
platforms that need to render samples.
"""

from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.chained import Chained
from qprogram.waveforms.cosine import Cosine
from qprogram.waveforms.flat_top import FlatTop
from qprogram.waveforms.gaussian import Gaussian
from qprogram.waveforms.gaussian_drag_correction import GaussianDragCorrection
from qprogram.waveforms.iq_drag import IQDrag
from qprogram.waveforms.iq_pair import IQPair
from qprogram.waveforms.iq_rotation import IQRotation
from qprogram.waveforms.iq_zero import IQZero
from qprogram.waveforms.modulated import Modulated
from qprogram.waveforms.ramp import Ramp
from qprogram.waveforms.sech import Sech
from qprogram.waveforms.sine import Sine
from qprogram.waveforms.snz import SuddenNetZero
from qprogram.waveforms.square import Square
from qprogram.waveforms.tukey import Tukey
from qprogram.waveforms.waveform import IQWaveform, Waveform

__all__ = [
    "Arbitrary",
    "Chained",
    "Cosine",
    "FlatTop",
    "Gaussian",
    "GaussianDragCorrection",
    "IQDrag",
    "IQPair",
    "IQRotation",
    "IQWaveform",
    "IQZero",
    "Modulated",
    "Ramp",
    "Sech",
    "Sine",
    "Square",
    "SuddenNetZero",
    "Tukey",
    "Waveform",
]
