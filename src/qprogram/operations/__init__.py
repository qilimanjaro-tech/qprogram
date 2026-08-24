# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Built-in operations — the AST leaf nodes a :class:`~qprogram.QProgram` is composed of.

Operations are typed nodes appended to the program's active block by the builder methods on
:class:`~qprogram.QProgram`. Each subclass declares which constructor params hold buses (``BUS_ATTRS``)
and waveforms (``WAVEFORM_ATTRS``) so the shared introspection contract works without per-class overrides.
"""

from qprogram.operations.call import Call
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.operation import MeasurementField, Operation
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
from qprogram.operations.set_frequency import SetFrequency
from qprogram.operations.set_gain import SetGain
from qprogram.operations.set_offset import SetOffset
from qprogram.operations.set_parameter import SetParameter
from qprogram.operations.set_phase import SetPhase
from qprogram.operations.sync import Sync
from qprogram.operations.wait import Wait

__all__ = [
    "Call",
    "GetParameter",
    "Measure",
    "MeasurementField",
    "Operation",
    "Play",
    "ResetPhase",
    "SetFrequency",
    "SetGain",
    "SetOffset",
    "SetParameter",
    "SetPhase",
    "Sync",
    "Wait",
]
