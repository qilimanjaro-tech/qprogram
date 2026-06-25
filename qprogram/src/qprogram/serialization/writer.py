"""Writer for the ``.qp`` file format.

Walks the QProgram AST and dispatches each node to the registry's spec callbacks; no hard-coded
``isinstance`` ladder over operation or block keywords. The instance's ``serialize_*`` and
``var_ident`` methods form the *write context* — the duck-typed surface every callback in
:mod:`_specs` and every vendor extension's ``serialize`` consumes.

The ``_var_idents`` table is effectively an identity function today (Variable ids are unique within
a program), but the indirection gives future tooling a single hook for emit-time variable renaming.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusNaming, BusRef
from qprogram.errors import SerializationError
from qprogram.operations.call import Call
from qprogram.operations.operation import Operation
from qprogram.result import MeasurementHandle
from qprogram.serialization import _specs
from qprogram.serialization._format import FORMAT_VERSION
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
    MeasurementRef,
    UnaryOp,
    Variable,
    Where,
)
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from qprogram.fragments import Fragment
    from qprogram.qprogram import QProgram

# Characters that break the unquoted ``<name>.<field>`` wire form of a MeasurementRef: token
# delimiters (whitespace, comma), quoting/nesting characters, the comment marker, and the dot
# (the parser splits the token at its first dot to separate name from field).
_MEASUREMENT_REF_UNSAFE = re.compile(r'[\s"#,.()\[\]{}]')


def dumps(program: QProgram) -> str:
    """Serialise a :class:`QProgram` to a ``.qp``-format string.

    Args:
        program: Program to serialise.

    Returns:
        The full ``.qp`` text (header, metadata, schema, body).

    Raises:
        SerializationError: If the program contains a node or value the format cannot represent
            faithfully (unregistered operation/block class, vendor without a registered version,
            attribute value of an unsupported type), or if ``program`` is itself a
            :class:`~qprogram.Fragment` — fragments serialize as sections of the host program
            that calls them. The writer never emits lossy output.
    """
    from qprogram.fragments import Fragment  # noqa: PLC0415

    if isinstance(program, Fragment):
        msg = (
            f"cannot serialize Fragment {program.name!r} directly; fragments are emitted as "
            f"`fragment ...:` sections of the host QProgram that calls them — serialize that program"
        )
        raise SerializationError(msg)
    return _Writer(program).dump()


def save(program: QProgram, path: str) -> None:
    """Serialise ``program`` and write the result to ``path``.

    ``.qp`` files are always UTF-8, independent of the platform's locale.

    Args:
        program: Program to serialise.
        path: Destination file path.

    Raises:
        SerializationError: See :func:`dumps`.
    """
    with Path(path).open("w", encoding="utf-8") as f:
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
        self._write_fragments()
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
        for frag in self._program.fragments.values():
            vendors |= self._collect_vendors(frag.body)
        for vendor in sorted(vendors):
            version = get_vendor_version(vendor)
            if version is None:
                msg = (
                    f"Cannot serialize: vendor '{vendor}' is used in the program but no "
                    f"version is registered. The vendor extension package must call "
                    f"register_vendor_version('{vendor}', '<x.y.z>') on import."
                )
                raise SerializationError(msg)
            self._out.write(f"\nrequire {vendor} {_major_minor(version)}")
        if vendors:
            self._out.write("\n")

    def _write_metadata(self) -> None:
        # ``label`` defaults to ``""`` and is omitted when empty (the parser's default matches);
        # ``description`` defaults to ``None``, so an explicit empty string is a distinct value
        # and must still be emitted to round-trip faithfully.
        if self._program.label or self._program.description is not None:
            self._out.write("\nmetadata:\n")
            if self._program.label:
                self._out.write(f'  label: "{_escape_str(self._program.label)}"\n')
            if self._program.description is not None:
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

    # -- fragment definitions --------------------------------------------------

    def _write_fragments(self) -> None:
        """Emit a ``fragment <name>(<params>):`` section per fragment, dependencies first.

        Ordering is computed here (depth-first over nested :class:`Call` nodes) rather than
        trusted from registration order, so the emitted file always defines a fragment before any
        fragment that calls it — the define-before-use rule the parser enforces.
        """
        for frag in self._topo_fragments():
            params = ", ".join(p.id for p in frag.params)
            self._out.write(f"\nfragment {frag.name}({params}):\n")
            # Fragment params/locals form their own identifier scope; ids are unique within the
            # fragment by construction, so they map verbatim — shadowing host ids is fine because
            # a fragment body can only reference its own params/locals.
            saved_idents = self._var_idents
            self._var_idents = dict(saved_idents)
            for param in frag.params:
                self._var_idents[param.id] = param.id
            for var in frag.variables:
                self._var_idents[var.id] = var.id
            try:
                for var in frag.variables:
                    self._out.write(f"  {self._serialize_var_decl(var)}\n")
                if frag.variables:
                    self._out.write("\n")
                self._write_block_contents(frag.body, indent=2)
            finally:
                self._var_idents = saved_idents

    def _topo_fragments(self) -> list[Fragment]:
        """Return the program's fragments in dependency order (callees before callers).

        Raises:
            SerializationError: On a fragment call cycle, or when two different fragments under
                the same name are reachable from this program.
        """
        registered = self._program.fragments
        ordered: list[Fragment] = []
        emitted: dict[str, Fragment] = {}

        def visit(frag: Fragment, stack: tuple[str, ...]) -> None:
            if frag.name in stack:
                msg = f"cannot serialize: fragment call cycle: {' -> '.join((*stack, frag.name))}"
                raise SerializationError(msg)
            previous = emitted.get(frag.name) or registered.get(frag.name)
            if previous is not None and previous is not frag:
                msg = (
                    f"cannot serialize: two different fragments named {frag.name!r} are reachable "
                    f"from this program; fragment names must be unique"
                )
                raise SerializationError(msg)
            if frag.name in emitted:
                return
            for node in frag.body.walk():
                if isinstance(node, Call):
                    visit(node.fragment, (*stack, frag.name))
            emitted[frag.name] = frag
            ordered.append(frag)

        for frag in registered.values():
            visit(frag, ())
        return ordered

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
            # Call before the generic Operation branch — it is an Operation subclass but has
            # its own ``name(args)`` statement form rather than a registered OperationSpec.
            if isinstance(element, Call):
                self._out.write(f"{prefix}{self._serialize_call(element)}\n")
                continue
            if isinstance(element, Operation):
                self._out.write(f"{prefix}{self._serialize_operation(element)}\n")
                continue
            if isinstance(element, Parallel):
                headers = [self._serialize_loop_header(lp) for lp in element.loops]
                self._out.write(f"{prefix}{' | '.join(headers)}:\n")
                self._write_block_contents(element, indent + 2)
                continue
            if isinstance(element, Conditional):
                self._write_conditional(element, indent)
                continue
            if isinstance(element, Block):
                header = self._serialize_block_header(element)
                self._out.write(f"{prefix}{header}:\n")
                self._write_block_contents(element, indent + 2)

    def _write_conditional(self, cond: Conditional, indent: int) -> None:
        """Emit a Conditional as a sequence of ``if`` / ``elif`` / ``else`` arms.

        Conditional has multiple headers (one per arm), so the generic
        block-emitter cannot handle it. Each arm renders as
        ``if|elif <expr>:`` / ``else:`` followed by its body at
        ``indent + 2``.
        """
        prefix = " " * indent
        for i, (condition, body) in enumerate(cond.arms):
            keyword = "if" if i == 0 else "elif"
            cond_text = self._serialize_condition(condition)
            self._out.write(f"{prefix}{keyword} {cond_text}:\n")
            self._write_block_contents(body, indent + 2)
        if cond.else_body is not None:
            self._out.write(f"{prefix}else:\n")
            self._write_block_contents(cond.else_body, indent + 2)

    def _serialize_condition(self, condition: object) -> str:
        """Render a conditional's condition without the wrapping parens.

        :meth:`serialize_value` wraps :class:`Comparison` in
        ``(<left> <op> <right>)`` to keep operator precedence
        unambiguous when comparisons appear nested inside arithmetic.
        For an ``if``/``elif`` header the wrap is noise, so we strip
        the outer parens of a top-level Comparison.
        """
        if isinstance(condition, Comparison):
            return f"{self.serialize_value(condition.left)} {condition.op} {self.serialize_value(condition.right)}"
        return self.serialize_value(condition)

    def _serialize_block_header(self, block: Block) -> str:
        """Render a block's header line without the trailing colon.

        Loop-shaped blocks dispatch through the sweep generator registry; all
        other blocks dispatch through the block registry. An unregistered
        block raises — emitting a placeholder would silently drop the block
        (and its children's semantics) on reload.

        Raises:
            SerializationError: If the block class has no registered spec.
        """
        gen_spec = get_sweep_generator_spec_by_class(type(block))
        if gen_spec is not None:
            return self._serialize_loop_header(block)
        block_spec = get_block_spec_by_class(type(block))
        if block_spec is None:
            msg = (
                f"Cannot serialize block class {type(block).__name__!r}: it is not registered "
                f"with the .qp serializer. Register it via register_block(...) (or "
                f"register_sweep_generator(...) for loop-shaped blocks)."
            )
            raise SerializationError(msg)
        if block_spec.serialize_header is not None:
            return block_spec.serialize_header(block, self)
        return block_spec.name

    def _serialize_loop_header(self, loop: Block) -> str:
        """``for <var> in <generator>`` — generator text comes from the sweep registry.

        Raises:
            SerializationError: If the loop class has no write-side sweep generator, or doesn't
                carry the ``variable`` attribute the ``for var in ...`` grammar requires.
        """
        gen_spec = get_sweep_generator_spec_by_class(type(loop))
        # Every registered loop block has a ``variable`` attribute; this is
        # the contract for participating in the ``for var in ...`` grammar.
        # ``Block`` itself doesn't declare it, so narrow via the concrete
        # loop block subclasses that do.
        if gen_spec is None or gen_spec.write is None or not isinstance(loop, (ForLoop, Loop)):
            msg = (
                f"Cannot serialize loop block class {type(loop).__name__!r}: no write-side sweep "
                f"generator is registered for it. Register one via register_sweep_generator(...)."
            )
            raise SerializationError(msg)
        var = loop.variable
        var_ident = self._var_idents[var.id]
        gen_text = gen_spec.write(loop, self)
        return f"for {var_ident} in {gen_text}"

    def _serialize_call(self, call: Call) -> str:
        """Emit a fragment call statement: ``<name>(<args>)``, positional in parameter order."""
        frag = call.fragment
        args: list[str] = []
        for param in frag.params:
            if param.id not in call.arguments:  # pragma: no cover — bind_arguments guarantees coverage
                msg = f"cannot serialize call to fragment {frag.name!r}: parameter {param.id!r} is unbound"
                raise SerializationError(msg)
            args.append(self.serialize_value(call.arguments[param.id]))
        return f"{frag.name}({', '.join(args)})"

    def _serialize_operation(self, op: Operation) -> str:
        """Look up the operation in the registry and dispatch to its serializer.

        Raises:
            SerializationError: If the operation class has no registered spec — emitting a
                placeholder would silently drop the operation on reload.
        """
        spec = get_operation_spec_by_class(type(op))
        if spec is None:
            msg = (
                f"Cannot serialize operation class {type(op).__name__!r}: it is not registered "
                f"with the .qp serializer. Core ops register in qprogram.serialization._specs; "
                f"vendor ops must call register_vendor_operation(...) at import time."
            )
            raise SerializationError(msg)
        if spec.serialize is not None:
            return spec.serialize(op, self)
        return _specs.default_serialize_operation(op, spec, self)

    # -- callbacks exposed to spec functions (the "write context") -----------

    def serialize_value(self, val: object) -> str:
        """Render any AST value as a ``.qp`` token.

        Recognises the full :class:`~qprogram.Expression` AST (variables,
        constants, arithmetic, comparison, logical, math functions,
        conditional ``where``), waveform instances, bus references, plain
        strings (quoted), booleans, ``None`` (as ``null``), numeric
        literals (including numpy scalars), tuples of strings (rendered
        as a single quoted comma-joined token — the canonical form used
        by ``Measure.returns`` / ``Acquire.returns``), numeric
        lists/tuples/1-D arrays (rendered as ``[v0, v1, ...]``), and
        string-keyed dicts (rendered as ``{"k": v, ...}``).

        Symbolic operators (arithmetic, comparison, binary logical) all
        emit the canonical parenthesised ``(<left> <op> <right>)`` shape;
        the parser recovers them through the same form. Math and ``where``
        use the function-call shape ``name(arg, ...)``.

        Raises:
            SerializationError: For any value type the format has no
                representation for. Emitting ``str(val)`` instead would
                produce a token the parser either rejects or silently
                mis-types on reload.
        """
        if isinstance(val, BusRef):
            return self.serialize_bus(val)
        if isinstance(val, MeasurementHandle):
            # Emit the handle as its quoted name — the canonical form
            # readers know how to round-trip. The parser converts the
            # name back into the program's canonical handle instance.
            return f'"{_escape_str(val.name)}"'
        if isinstance(val, str):
            return f'"{_escape_str(val)}"'
        if val is None:
            return "null"
        if isinstance(val, (bool, np.bool_)):
            return "true" if val else "false"
        if isinstance(val, Variable):
            return self._var_idents[val.id]
        if isinstance(val, MeasurementRef):
            # Emit as ``<handle_name>.<field>``. The parser recognises this
            # form by looking up the identifier in its known-handles set; the
            # name is unquoted on the wire, so it must be a single clean token.
            if _MEASUREMENT_REF_UNSAFE.search(val.handle.name):
                msg = (
                    f"measurement name {val.handle.name!r} is referenced in a conditional but "
                    f"contains characters that don't survive the unquoted `<name>.<field>` wire "
                    f"form (whitespace, quotes, dots, brackets, '#', or ','). Pass a token-safe "
                    f"name= to measure() when you intend to branch on the result."
                )
                raise SerializationError(msg)
            return f"{val.handle.name}.{val.field}"
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
        # current producer is ``Measure.returns`` / ``Acquire.returns``.
        # Other tuples (e.g. qdac SetTrigger.outputs) fall through to the
        # bracket-literal sequence form below.
        if isinstance(val, tuple) and val and all(isinstance(v, str) for v in val):
            return f'"{_escape_str(",".join([v for v in val if isinstance(v, str)]))}"'
        if isinstance(val, (Waveform, IQWaveform)):
            return self.serialize_waveform(val)
        if isinstance(val, np.integer):
            return str(int(val))
        if isinstance(val, np.floating):
            return repr(float(val))
        if isinstance(val, (int, float)):
            return str(val)
        # Sequences → ``[v0, v1, ...]``. Never truncated: the bracket
        # literal must reload to exactly the same values. The tokenizer
        # treats ``[...]`` as a nesting context, so the spaces after the
        # commas are safe.
        if isinstance(val, np.ndarray):
            if val.ndim != 1:
                msg = f"Cannot serialize a {val.ndim}-D array; only 1-D arrays have a .qp form"
                raise SerializationError(msg)
            # ``tolist()`` converts numpy scalars to Python ints/floats up front. The
            # ``np.asarray`` round-trip gives the type checker a clean ndarray type.
            elements: list[object] = np.asarray(val).tolist()
            return f"[{', '.join(self.serialize_value(v) for v in elements)}]"
        if isinstance(val, (list, tuple)):
            return f"[{', '.join(self.serialize_value(v) for v in val)}]"
        # String-keyed dicts → ``{"k": v, ...}`` (the generic brace-literal form for dict kwargs).
        if isinstance(val, dict):
            if not all(isinstance(k, str) for k in val):
                msg = "Cannot serialize a dict with non-string keys"
                raise SerializationError(msg)
            items = ", ".join(f'"{_escape_str(str(k))}": {self.serialize_value(v)}' for k, v in val.items())
            return f"{{{items}}}"
        msg = (
            f"Cannot serialize value {val!r} of type {type(val).__name__!r}: the .qp format has "
            f"no representation for it. Register a custom serialize callback for the operation "
            f"that carries it, or use a supported value type."
        )
        raise SerializationError(msg)

    def serialize_bus(self, bus: object) -> str:
        """Render a bus argument: path form if schema-backed, quoted string otherwise."""
        if self._program.schema is not None and isinstance(bus, BusRef) and bus.element and bus.kind:
            idx = bus.idx
            idx_str = ",".join(str(i) for i in idx) if isinstance(idx, tuple) else str(idx)
            return f"{bus.element}[{idx_str}].{bus.kind}"
        return f'"{_escape_str(str(bus))}"'

    def serialize_waveform(self, wf: object) -> str:
        """Emit a waveform constructor call, mirroring the class name and public attrs.

        Sample arrays (e.g. ``Arbitrary.samples``) are emitted in full — truncating them would
        break the round-trip guarantee, and the parser has no way to recover dropped samples.
        """
        cls_name = type(wf).__name__
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
        """Walk a block tree and gather the vendor names referenced by operations.

        Uses :meth:`Block.walk` rather than recursing over ``.elements`` — Conditional keeps its
        arm bodies on ``.arms`` / ``.else_body`` (not in ``_elements``), so an elements-only
        recursion would miss vendor ops inside ``if_``/``elif_``/``else_`` arms and emit a file
        with no ``require`` line for them.
        """
        vendors: set[str] = set()
        for node in block.walk():
            if isinstance(node, Block):
                continue
            spec = get_operation_spec_by_class(type(node))
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
    """Escape a string for embedding in double quotes inside a ``.qp`` file.

    Backslash and double-quote get the usual escapes; newline, carriage return, and tab are
    escaped too — the format is line-based, so a raw newline inside a quoted string would split
    the statement across two lines and break the parse.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
