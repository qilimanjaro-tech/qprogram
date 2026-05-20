"""Built-in operations — the AST leaf nodes a :class:`~qprogram.QProgram` is composed of.

Operations are typed nodes appended to the program's active block by the builder methods on
:class:`~qprogram.QProgram`. Each subclass declares which constructor params hold buses (``BUS_ATTRS``)
and waveforms (``WAVEFORM_ATTRS``) so the shared introspection contract works without per-class overrides.
"""

from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.operation import Operation
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
from qprogram.operations.set_crosstalk import SetCrosstalk
from qprogram.operations.set_frequency import SetFrequency
from qprogram.operations.set_gain import SetGain
from qprogram.operations.set_offset import SetOffset
from qprogram.operations.set_parameter import SetParameter
from qprogram.operations.set_phase import SetPhase
from qprogram.operations.sync import Sync
from qprogram.operations.wait import Wait

__all__ = [
    "GetParameter",
    "Measure",
    "Operation",
    "Play",
    "ResetPhase",
    "SetCrosstalk",
    "SetFrequency",
    "SetGain",
    "SetOffset",
    "SetParameter",
    "SetPhase",
    "Sync",
    "Wait",
]
