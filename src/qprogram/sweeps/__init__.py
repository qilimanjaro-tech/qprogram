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
"""Sweep sources — the pluggable value descriptions a :class:`~qprogram.blocks.Sweep` binds.

One block, many sources. ``Sweep(variable, source)`` is the DSL's only sweep construct; *how* the
values come to be is the source's job::

    with program.sweep(freq, Range(4e9, 6e9, 1e6)):
        ...  # a hardware ramp
    with program.sweep(amp, Linspace(0.0, 1.0, num=101)):
        ...  # ditto, by point count
    with program.sweep(det, Logspace(1e6, 1e9, num=50)):
        ...  # log spacing
    with program.sweep(phi, Values(calibrated)):
        ...  # explicit table
    with program.sweep(phi, File("phases.npy")):
        ...  # loaded from disk
    with program.sweep(phi, Concat(Rotate(base, by=i) for i in range(4))):
        ...  # composed

Every one of those has a fluent equivalent that needs none of these names in scope — omit the source
and :meth:`~qprogram.QProgram.sweep` returns a builder whose ``from_*`` methods construct exactly the
sources above (``from_range``, ``from_linspace``, ``from_logspace``, ``from_values``, ``from_file``,
plus one per registered source, vendor sources included). Same AST, same ``.qp``::

    with program.sweep(freq).from_range(4e9, 6e9, 1e6):
        ...
    with program.sweep(phi).from_values(calibrated).rotate(by=1).repeat(3):
        ...  # Repeat(Rotate(Values(calibrated), by=1), times=3)

The object form stays the right one for a *computed* source: one held in a variable, built in a
comprehension, or nested more deeply than the ``rotate`` / ``repeat`` shortcuts reach.

Adding a source is the extension point: subclass :class:`SweepSource`, declare ``KIND`` and ``TOKEN``,
implement ``length()`` and ``values()``, register with
:func:`~qprogram.serialization.register_sweep_source`. Serialization, capability reporting and
round-tripping then come for free — the same deal :class:`~qprogram.waveforms.Waveform` subclasses get.
"""

from qprogram.sweeps.builtin import File, Linspace, Logspace, Range, Values
from qprogram.sweeps.combinators import Concat, Repeat, Rotate
from qprogram.sweeps.source import SweepSource, validate_source

__all__ = [
    "Concat",
    "File",
    "Linspace",
    "Logspace",
    "Range",
    "Repeat",
    "Rotate",
    "SweepSource",
    "Values",
    "validate_source",
]
