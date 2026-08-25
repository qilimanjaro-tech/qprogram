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
"""AST block types — containers that group operations and other blocks together.

The base [`Block`][qprogram.blocks.Block] is a simple ordered container; concrete subclasses
([`Average`][qprogram.blocks.Average], [`Conditional`][qprogram.blocks.Conditional], [`Sweep`][qprogram.blocks.Sweep],
[`Parallel`][qprogram.blocks.Parallel]) carry extra structure that the runtime, validator, and serializer understand.

[`Sweep`][qprogram.blocks.Sweep] is the only loop: it binds a variable to whatever a
[`SweepSource`][qprogram.SweepSource] produces, so a linear ramp, an explicit table, a log-spaced set
and any composition of those are all the same block with a different source.
"""

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep

__all__ = ["Average", "Block", "Conditional", "Parallel", "Sweep"]
