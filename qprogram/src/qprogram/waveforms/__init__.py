"""Built-in waveform shapes for pulse-level programs.

Single-channel waveforms subclass :class:`Waveform`; complex (I/Q) waveforms subclass :class:`IQWaveform`.
Both bases expose ``envelope(resolution)`` / ``get_I()`` / ``get_Q()`` and ``get_duration()`` for
platforms that need to render samples.
"""

from qprogram.waveforms.arbitrary import Arbitrary
from qprogram.waveforms.chained import Chained
from qprogram.waveforms.flat_top import FlatTop
from qprogram.waveforms.gaussian import Gaussian
from qprogram.waveforms.gaussian_drag_correction import GaussianDragCorrection
from qprogram.waveforms.iq_drag import IQDrag
from qprogram.waveforms.iq_pair import IQPair
from qprogram.waveforms.ramp import Ramp
from qprogram.waveforms.snz import SuddenNetZero
from qprogram.waveforms.square import Square
from qprogram.waveforms.waveform import IQWaveform, Waveform

__all__ = [
    "Arbitrary",
    "Chained",
    "FlatTop",
    "Gaussian",
    "GaussianDragCorrection",
    "IQDrag",
    "IQPair",
    "IQWaveform",
    "Ramp",
    "Square",
    "SuddenNetZero",
    "Waveform",
]
