"""Serializer for the ``.qp`` file format.

Implemented as a small ``_Writer`` class so per-program state — most importantly
the variable identifier map — can be carried as instance state without threading
it through every helper.

Why a variable identifier map: variables in the AST are identity-based, but the
``.qp`` file references them by name. Two ``Variable`` instances created with
the same label (e.g. ``program.variable("freq")`` twice) must be distinguished
in the file or load-time identity is lost. The writer assigns each variable a
unique identifier — derived from its label, disambiguated by suffix when
needed — and emits the human-readable label only when it differs from the
identifier (or contains characters not allowed in identifiers).
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusRef
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
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
from qprogram.serialization.registry import get_operation_vendor_name, get_vendor_version
from qprogram.variable import BinaryOp, Constant, UnaryOp, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from qprogram.qprogram import QProgram

FORMAT_VERSION = "1.0"


def dumps(program: QProgram) -> str:
    """Serialize a QProgram to ``.qp`` format string."""
    return _Writer(program).dump()


def save(program: QProgram, path: str) -> None:
    """Save a QProgram to a ``.qp`` file."""
    with Path(path).open("w") as f:
        f.write(dumps(program))


# ---------------------------------------------------------------------------
# Writer class
# ---------------------------------------------------------------------------


class _Writer:
    """Serializes a single QProgram to ``.qp`` text."""

    def __init__(self, program: QProgram) -> None:
        self._program = program
        self._out = StringIO()
        # Map Variable.id -> chosen identifier in the .qp file. Built lazily
        # by allocate_var_idents() before any body emission.
        self._var_idents: dict[str, str] = {}

    # -- public ---------------------------------------------------------------

    def dump(self) -> str:
        self._allocate_var_idents()
        self._write_header()
        self._write_requires()
        self._write_metadata()
        self._write_schema()
        self._write_body()
        return self._out.getvalue()

    # -- header / requires / metadata -----------------------------------------

    def _write_header(self) -> None:
        self._out.write(f"#!QProgram {FORMAT_VERSION}\n")

    def _write_requires(self) -> None:
        # Collect required vendors and emit `require <vendor> <major.minor>` for each.
        # The version comes from whichever extension is currently registered with
        # register_vendor_version(); patch is truncated since compatibility
        # semantics are defined at major.minor.
        vendors = self._collect_vendors(self._program.body)
        for vendor in sorted(vendors):
            version = get_vendor_version(vendor)
            if version is None:
                msg = (
                    f"Cannot serialize: vendor '{vendor}' is used in the program but no "
                    f"version is registered. The vendor extension package must call "
                    f"register_vendor_version('{vendor}', '<x.y.z>') on import."
                )
                raise RuntimeError(msg)
            self._out.write(f"\nrequire {vendor} {_major_minor(version)}")
        if vendors:
            self._out.write("\n")

    def _write_metadata(self) -> None:
        if self._program.label or self._program.description:
            self._out.write("\nmetadata:\n")
            if self._program.label:
                self._out.write(f'  label: "{_escape_str(self._program.label)}"\n')
            if self._program.description:
                self._out.write(f'  description: "{_escape_str(self._program.description)}"\n')

    # -- schema (BusRef declarations) ----------------------------------------

    def _write_schema(self) -> None:
        """Emit a ``schema:`` section declaring every BusRef used in the body.

        Plain ``str`` buses are not declared. The declarations preserve
        ``channel``, ``acquires``, ``element``, ``index``, and ``bus_type`` so
        the parser can rebuild the BusRef instances on load and downstream
        validation (waveform channel type, acquisition support) keeps working.
        """
        busrefs = self._collect_busrefs(self._program.body)
        if not busrefs:
            return
        self._out.write("\nschema:\n")
        for ref in sorted(busrefs, key=str):
            self._out.write(f"  {self._serialize_bus_decl(ref)}\n")

    @staticmethod
    def _serialize_bus_decl(ref: BusRef) -> str:
        parts = [f'bus "{_escape_str(str(ref))}"']
        # Emit type/element/index as a triplet only when any is non-default —
        # bare BusRefs (no schema metadata) get just `info`.
        if ref.type or ref.element or ref.index != 0:
            parts.append(f'type="{_escape_str(ref.type)}"')
            parts.append(f'element="{_escape_str(ref.element)}"')
            if isinstance(ref.index, tuple):
                parts.append(f"index={','.join(str(i) for i in ref.index)}")
            else:
                parts.append(f"index={ref.index}")
        info_value = ref.channel
        if ref.acquires:
            info_value += "+acquires"
        parts.append(f"info={info_value}")
        return " ".join(parts)

    @staticmethod
    def _collect_busrefs(block: Block) -> set[BusRef]:
        """Walk the block tree and collect every BusRef instance used."""
        acc: dict[str, BusRef] = {}
        _Writer._walk_busrefs(block, acc)
        return set(acc.values())

    @staticmethod
    def _walk_busrefs(block: Block, acc: dict[str, BusRef]) -> None:
        for el in block.elements:
            if isinstance(el, Block):
                _Writer._walk_busrefs(el, acc)
                continue
            for value in vars(el).values():
                if isinstance(value, BusRef):
                    acc[str(value)] = value
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, BusRef):
                            acc[str(item)] = item

    # -- body & variable declarations -----------------------------------------

    def _write_body(self) -> None:
        self._out.write("\nbody:\n")
        # Variable ids are valid identifiers and unique within a program
        # (validated by Variable.__init__ and QProgram.variable), so they are
        # used verbatim as identifiers here. Optional metadata (label,
        # units, description) is emitted as `key="value"` kwargs.
        for var in self._program.variables:
            self._out.write(f"  {self._serialize_var_decl(var)}\n")
        if self._program.variables:
            self._out.write("\n")
        self._write_block_contents(self._program.body, indent=2)

    def _serialize_var_decl(self, var: Variable) -> str:
        parts = [f"var {var.id}"]
        if var.label is not None:
            parts.append(f'label="{_escape_str(var.label)}"')
        if var.units is not None:
            parts.append(f'units="{_escape_str(var.units)}"')
        if var.description is not None:
            parts.append(f'description="{_escape_str(var.description)}"')
        return " ".join(parts)

    def _write_block_contents(self, block: Block, indent: int) -> None:
        prefix = " " * indent
        for element in block.elements:
            if isinstance(element, Parallel):
                headers = [self._serialize_loop_header(lp) for lp in element.loops]
                self._out.write(f"{prefix}{' | '.join(headers)}:\n")
                self._write_block_contents(element, indent + 2)
            elif isinstance(element, (ForLoop, Loop)):
                self._out.write(f"{prefix}{self._serialize_loop_header(element)}:\n")
                self._write_block_contents(element, indent + 2)
            elif isinstance(element, Average):
                self._out.write(f"{prefix}average {element.shots}:\n")
                self._write_block_contents(element, indent + 2)
            elif isinstance(element, Block):
                self._out.write(f"{prefix}block:\n")
                self._write_block_contents(element, indent + 2)
            else:
                self._out.write(f"{prefix}{self._serialize_operation(element)}\n")

    def _serialize_loop_header(self, loop: ForLoop | Loop) -> str:
        var_ident = self._var_idents[loop.variable.id]
        if isinstance(loop, ForLoop):
            return (
                f"for {var_ident} in range("
                f"{self._serialize_value(loop.start)}, "
                f"{self._serialize_value(loop.stop)}, "
                f"{self._serialize_value(loop.step)})"
            )
        # Loop
        items = ", ".join(self._serialize_value(v) for v in loop.values[:50])
        if len(loop.values) > 50:
            items += ", ..."
        return f"for {var_ident} in [{items}]"

    # -- value / waveform / operation serialization ---------------------------

    def _serialize_value(self, val: object) -> str:
        if isinstance(val, str):
            return f'"{_escape_str(val)}"'
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, Variable):
            return self._var_idents[val.id]
        if isinstance(val, Constant):
            return self._serialize_value(val.value)
        if isinstance(val, BinaryOp):
            return f"({self._serialize_value(val.left)} {val.op} {self._serialize_value(val.right)})"
        if isinstance(val, UnaryOp):
            return f"({val.op}{self._serialize_value(val.operand)})"
        if isinstance(val, (Waveform, IQWaveform)):
            return self._serialize_waveform(val)
        if isinstance(val, np.integer):
            return str(int(val))
        if isinstance(val, (int, float)):
            return str(val)
        return str(val)

    def _serialize_waveform(self, wf: object) -> str:
        cls_name = type(wf).__name__
        if cls_name == "Arbitrary" and hasattr(wf, "samples"):
            samples = wf.samples
            items = ", ".join(str(v) for v in samples[:20])
            if len(samples) > 20:
                items += ", ..."
            return f"Arbitrary(samples=[{items}])"

        params: list[str] = []
        for key, val in vars(wf).items():
            if key.startswith("_"):
                continue
            params.append(f"{key}={self._serialize_value(val)}")
        return f"{cls_name}({', '.join(params)})"

    def _serialize_operation(self, op: object) -> str:
        vn = get_operation_vendor_name(type(op))

        if isinstance(op, Play):
            wf = f'"{op.waveform}"' if isinstance(op.waveform, str) else self._serialize_waveform(op.waveform)
            prefix = f"{vn[0]}." if vn else ""
            return f'{prefix}play "{op.bus}" {wf}'

        if isinstance(op, Measure):
            wf = f'"{op.waveform}"' if isinstance(op.waveform, str) else self._serialize_waveform(op.waveform)
            wt = f'"{op.weights}"' if isinstance(op.weights, str) else self._serialize_waveform(op.weights)
            extras = " save_adc=true" if op.save_adc else ""
            prefix = f"{vn[0]}." if vn else ""
            return f'{prefix}measure "{op.bus}" {wf} {wt}{extras}'

        if isinstance(op, Wait):
            return f'wait "{op.bus}" {self._serialize_value(op.duration)}'

        if isinstance(op, Sync):
            if op.buses:
                return "sync " + " ".join(f'"{b}"' for b in op.buses)
            return "sync"

        if isinstance(op, SetFrequency):
            return f'set_frequency "{op.bus}" {self._serialize_value(op.frequency)}'

        if isinstance(op, SetPhase):
            return f'set_phase "{op.bus}" {self._serialize_value(op.phase)}'

        if isinstance(op, ResetPhase):
            return f'reset_phase "{op.bus}"'

        if isinstance(op, SetGain):
            return f'set_gain "{op.bus}" {self._serialize_value(op.gain)}'

        if isinstance(op, SetOffset):
            parts = f'set_offset "{op.bus}" {self._serialize_value(op.offset_path0)}'
            if op.offset_path1 is not None:
                parts += f" {self._serialize_value(op.offset_path1)}"
            return parts

        if isinstance(op, SetParameter):
            extras = f" channel_id={op.channel_id}" if op.channel_id is not None else ""
            return f'set_parameter "{op.alias}" "{op.parameter}" {self._serialize_value(op.value)}{extras}'

        if isinstance(op, GetParameter):
            extras = f" channel_id={op.channel_id}" if op.channel_id is not None else ""
            ident = self._var_idents[op.variable.id]
            return f'get_parameter "{op.alias}" "{op.parameter}"{extras} -> {ident}'

        if isinstance(op, SetCrosstalk):
            return "set_crosstalk crosstalk"

        # Generic vendor operation fallback
        if vn:
            parts = [f"{vn[0]}.{vn[1]}"]
            for key, val in vars(op).items():
                if not key.startswith("_"):
                    parts.append(self._serialize_value(val))
            return " ".join(parts)

        return f"# unknown operation: {type(op).__name__}"

    # -- variable identifier allocation ---------------------------------------

    def _allocate_var_idents(self) -> None:
        """Map each Variable to its identifier in the .qp file.

        Variable ids are validated to be Python-style identifiers
        (``[A-Za-z_][A-Za-z0-9_]*``) and ``QProgram.variable`` rejects
        duplicates, so the id is used verbatim as the identifier.
        """
        for var in self._program.variables:
            self._var_idents[var.id] = var.id

    # -- vendor collection ----------------------------------------------------

    @staticmethod
    def _collect_vendors(block: Block) -> set[str]:
        vendors: set[str] = set()
        for element in block.elements:
            if isinstance(element, Block):
                vendors.update(_Writer._collect_vendors(element))
            else:
                vn = get_operation_vendor_name(type(element))
                if vn:
                    vendors.add(vn[0])
        return vendors


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _major_minor(version: str) -> str:
    """Truncate a semver string to ``major.minor`` (e.g. ``"0.1.3" -> "0.1"``)."""
    parts = version.split(".")
    if len(parts) < 2:
        return f"{version}.0"
    return f"{parts[0]}.{parts[1]}"


def _escape_str(s: str) -> str:
    """Escape a string for embedding in double quotes inside a ``.qp`` file."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
