"""AST block types — containers that group operations and other blocks together.

The base :class:`Block` is a simple ordered container; concrete subclasses (:class:`Average`,
:class:`Conditional`, :class:`ForLoop`, :class:`Loop`, :class:`Parallel`) carry extra structure that the
runtime, validator, and serializer understand.
"""

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel

__all__ = ["Average", "Block", "Conditional", "ForLoop", "Loop", "Parallel"]
