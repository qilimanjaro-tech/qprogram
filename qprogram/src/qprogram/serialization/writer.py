"""Serializer for the ``.qp`` file format.

The writer is intentionally thin: it walks the QProgram AST and dispatches
each node to the appropriate spec callback from
:mod:`qprogram.serialization.registry`. The hard-coded ``isinstance`` ladders
that used to enumerate every core operation and block keyword are gone — the
writer is now agnostic to the concrete set of operations, blocks, and sweep
generators in scope.

State carried on the instance:

- ``_var_idents`` — per-variable identifier table. Variable ids are already
  unique within a program (the API rejects duplicates), so the map is
  effectively an identity function today, but the indirection is kept in
  place: future tooling that wants to rename variables on emit (e.g. to
  shorten them) has a single hook to do so without touching the rest of
  the writer.

The instance methods called ``serialize_*`` and ``var_ident`` are the public
interface used by registered callbacks. They form the *write context* — a
duck-typed protocol consumed by every callback in :mod:`_specs` and by every
vendor extension that registers a custom ``serialize`` function.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusNaming, BusRef
from qprogram.operations.operation import Operation
from qprogram.serialization import _specs
from qprogram.serialization.registry import (
    get_block_spec_by_class,
    get_operation_spec_by_class,
    get_sweep_generator_spec_by_class,
    get_vendor_version,
)
from qprogram.variable import (
    BinaryOp,
    Comparison,
    Constant,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    UnaryOp,
    Variable,
    Where,
)
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
        """Emit ``require <vendor> <major.minor>`` for each vendor referenced in the body.

        The version comes from whichever extension is currently registered
        via :func:`register_vendor_version`; patch is truncated since
        compatibility semantics are defined at major.minor.
        """
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

    # -- schema declaration --------------------------------------------------

    def _write_schema(self) -> None:
        """Emit the optional single ``schema`` block declared on the program.

        Always emits the expanded inline form (element/bus declarations) —
        even for the built-in presets like ``BusSchema.transmon()``. The
        preset classes are construction-time conveniences on the Python
        side; the file format records the structural contents directly so
        future changes to a preset (a new bus, a renamed kind) can never
        silently flip the meaning of an existing ``.qp`` file. Both the
        custom-typed schemas and the dynamic schemas serialize through
        this same code path.
        """
        schema = self._program.schema
        if schema is None:
            return
        self._out.write("\nschema:\n")
        if schema.naming.pattern != BusNaming.DEFAULT_PATTERN:
            self._out.write(f'  naming: "{_escape_str(schema.naming.pattern)}"\n')
        for element_name, element_schema in schema.elements.items():
            self._out.write(f"  element {element_name}:\n")
            for kind, (channel, acquires) in element_schema.buses.items():
                info = channel + "+acquires" if acquires else channel
                self._out.write(f"    {kind} info={info}\n")

    # -- body & variable declarations -----------------------------------------

    def _write_body(self) -> None:
        self._out.write("\nbody:\n")
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

    # -- dispatch over block tree --------------------------------------------

    def _write_block_contents(self, block: Block, indent: int) -> None:
        """Walk a block's children, dispatching each to the right serializer.

        Three cases, in priority order:

        1. The child is an :class:`Operation` — look up its
           :class:`OperationSpec` and call either the registered custom
           callback or the default signature-driven serializer.
        2. The child is a :class:`Parallel` — emit a pipe-joined sequence of
           loop headers from its child loops, then recurse into the body.
        3. The child is any other :class:`Block` — emit
           ``<header>:`` (via :meth:`_serialize_block_header`) and recurse.

        Parallel is special-cased rather than registered because its surface
        syntax (``a | b | c:``) composes other blocks' headers; it isn't
        keyword-led on its own.
        """
        prefix = " " * indent
        for element in block.elements:
            if isinstance(element, Operation):
                self._out.write(f"{prefix}{self._serialize_operation(element)}\n")
                continue
            if isinstance(element, Parallel):
                headers = [self._serialize_loop_header(lp) for lp in element.loops]
                self._out.write(f"{prefix}{' | '.join(headers)}:\n")
                self._write_block_contents(element, indent + 2)
                continue
            if isinstance(element, Block):
                header = self._serialize_block_header(element)
                self._out.write(f"{prefix}{header}:\n")
                self._write_block_contents(element, indent + 2)

    def _serialize_block_header(self, block: Block) -> str:
        """Render a block's header line without the trailing colon.

        Loop-shaped blocks dispatch through the sweep generator registry; all
        other blocks dispatch through the block registry. An unregistered
        block emits a comment so the output is still parseable as a whole
        but the offending block is visible.
        """
        gen_spec = get_sweep_generator_spec_by_class(type(block))
        if gen_spec is not None:
            return self._serialize_loop_header(block)
        block_spec = get_block_spec_by_class(type(block))
        if block_spec is None:
            return f"# unknown block: {type(block).__name__}"
        if block_spec.serialize_header is not None:
            return block_spec.serialize_header(block, self)
        return block_spec.name

    def _serialize_loop_header(self, loop: Block) -> str:
        """``for <var> in <generator>`` — generator text comes from the sweep registry."""
        gen_spec = get_sweep_generator_spec_by_class(type(loop))
        if gen_spec is None or gen_spec.write is None:
            return f"# unknown sweep block: {type(loop).__name__}"
        # Every registered loop block has a ``variable`` attribute; this is
        # the contract for participating in the ``for var in ...`` grammar.
        var = loop.variable  # type: ignore[attr-defined]
        var_ident = self._var_idents[var.id]
        gen_text = gen_spec.write(loop, self)
        return f"for {var_ident} in {gen_text}"

    def _serialize_operation(self, op: Operation) -> str:
        """Look up the operation in the registry and dispatch to its serializer."""
        spec = get_operation_spec_by_class(type(op))
        if spec is None:
            return f"# unknown operation: {type(op).__name__}"
        if spec.serialize is not None:
            return spec.serialize(op, self)
        return _specs.default_serialize_operation(op, spec, self)

    # -- callbacks exposed to spec functions (the "write context") -----------

    def serialize_value(self, val: object) -> str:
        """Render any AST value as a ``.qp`` token.

        Recognises the full :class:`~qprogram.Expression` AST (variables,
        constants, arithmetic, comparison, logical, math functions,
        conditional ``where``), waveform instances, bus references, plain
        strings (quoted), booleans, numeric literals, numpy integers, and
        tuples of strings (rendered as a single quoted comma-joined
        token — the canonical form used by ``Measure.returns`` /
        ``Acquire.returns``). Falls back to ``str(val)`` so the writer
        never raises mid-emit.

        Symbolic operators (arithmetic, comparison, binary logical) all
        emit the canonical parenthesised ``(<left> <op> <right>)`` shape;
        the parser recovers them through the same form. Math and ``where``
        use the function-call shape ``name(arg, ...)``.
        """
        if isinstance(val, BusRef):
            return self.serialize_bus(val)
        if isinstance(val, str):
            return f'"{_escape_str(val)}"'
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, Variable):
            return self._var_idents[val.id]
        if isinstance(val, Constant):
            return self.serialize_value(val.value)
        if isinstance(val, BinaryOp):
            return f"({self.serialize_value(val.left)} {val.op} {self.serialize_value(val.right)})"
        if isinstance(val, UnaryOp):
            return f"({val.op}{self.serialize_value(val.operand)})"
        if isinstance(val, Comparison):
            return f"({self.serialize_value(val.left)} {val.op} {self.serialize_value(val.right)})"
        if isinstance(val, LogicalBinaryOp):
            return f"({self.serialize_value(val.left)} {val.op} {self.serialize_value(val.right)})"
        if isinstance(val, LogicalNot):
            return f"(not {self.serialize_value(val.operand)})"
        if isinstance(val, MathFunc):
            args = ", ".join(self.serialize_value(op) for op in val.operands)
            return f"{val.name}({args})"
        if isinstance(val, Where):
            cond = self.serialize_value(val.condition)
            then = self.serialize_value(val.then)
            else_ = self.serialize_value(val.else_)
            return f"where({cond}, {then}, {else_})"
        # Tuple of strings → single quoted, comma-joined token. The only
        # current producer is ``Measure.returns`` / ``Acquire.returns``;
        # any other tuple shape (e.g. ``BusRef.index = (0, 1)``) carries
        # non-string elements and falls through to the generic path. The
        # explicit list comprehension narrows the element type so
        # ``str.join`` is statically resolvable.
        if isinstance(val, tuple) and all(isinstance(v, str) for v in val):
            return f'"{_escape_str(",".join([v for v in val if isinstance(v, str)]))}"'
        if isinstance(val, (Waveform, IQWaveform)):
            return self.serialize_waveform(val)
        if isinstance(val, np.integer):
            return str(int(val))
        if isinstance(val, (int, float)):
            return str(val)
        return str(val)

    def serialize_bus(self, bus: object) -> str:
        """Render a bus argument: path form if schema-backed, quoted string otherwise."""
        if (
            self._program.schema is not None
            and isinstance(bus, BusRef)
            and bus.element
            and bus.kind
        ):
            idx = bus.index
            idx_str = ",".join(str(i) for i in idx) if isinstance(idx, tuple) else str(idx)
            return f"{bus.element}[{idx_str}].{bus.kind}"
        return f'"{_escape_str(str(bus))}"'

    def serialize_waveform(self, wf: object) -> str:
        """Emit a waveform constructor call, mirroring the class name and public attrs."""
        cls_name = type(wf).__name__
        if cls_name == "Arbitrary" and hasattr(wf, "samples"):
            samples = wf.samples  # type: ignore[attr-defined]
            items = ", ".join(str(v) for v in samples[:20])
            if len(samples) > 20:
                items += ", ..."
            return f"Arbitrary(samples=[{items}])"
        params: list[str] = []
        for key, val in vars(wf).items():
            if key.startswith("_"):
                continue
            params.append(f"{key}={self.serialize_value(val)}")
        return f"{cls_name}({', '.join(params)})"

    def var_ident(self, var: Variable) -> str:
        """Return the identifier chosen for ``var`` in the emitted file."""
        return self._var_idents[var.id]

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
        """Walk a block tree and gather the vendor names referenced by operations."""
        vendors: set[str] = set()
        for element in block.elements:
            if isinstance(element, Block):
                vendors.update(_Writer._collect_vendors(element))
                continue
            spec = get_operation_spec_by_class(type(element))
            if spec is not None and spec.vendor is not None:
                vendors.add(spec.vendor)
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
