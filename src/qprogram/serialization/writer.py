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
"""Writer for the ``.qp`` file format.

Walks the QProgram AST and dispatches each node to the registry's spec callbacks; no hard-coded
``isinstance`` ladder over operation or block keywords. The instance's ``serialize_*`` and
``var_ident`` methods form the *write context* — the duck-typed surface every callback in
`_specs` and every vendor extension's ``serialize`` consumes.

The ``_var_idents`` table maps each variable id to the identifier written in the file. Ids are
unique within a program, so the mapping is an identity; routing every emission through it keeps
emit-time renaming a single-point change, and is what lets a fragment body carry its own scope.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
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
    get_vendor_version,
)
from qprogram.sweeps.builtin import Values
from qprogram.sweeps.source import SweepSource
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
    """Serialize a [`QProgram`][qprogram.QProgram] to a ``.qp``-format string.

    Args:
        program (QProgram): Program to serialize.

    Returns:
        The full ``.qp`` text: header, ``require`` lines, metadata, schema, fragments, body.

    Raises:
        SerializationError: If the program contains a node or value the format cannot represent
            faithfully (unregistered operation/block class, vendor without a registered version,
            attribute value of an unsupported type), or if ``program`` is itself a
            [`Fragment`][qprogram.Fragment] — fragments serialize as sections of the host program
            that calls them. The writer never emits lossy output.
    """
    from qprogram.fragments import Fragment  # ruff: ignore[import-outside-top-level]

    if isinstance(program, Fragment):
        msg = (
            f"cannot serialize Fragment {program.name!r} directly; fragments are emitted as "
            f"`fragment ...:` sections of the host QProgram that calls them — serialize that program"
        )
        raise SerializationError(msg)
    return _Writer(program).dump()


def save(program: QProgram, path: str) -> None:
    """Serialize ``program`` and write the result to ``path``.

    ``.qp`` files are always UTF-8, independent of the platform's locale.

    Args:
        program (QProgram): Program to serialize.
        path (str): Destination file path.

    Raises:
        SerializationError: See `dumps`.
    """
    Path(path).write_text(dumps(program), encoding="utf-8")


# ---------------------------------------------------------------------------
# Writer class
# ---------------------------------------------------------------------------


class _Writer:
    """A single program's ``.qp`` rendering, section by section.

    The instance doubles as the *write context* handed to every spec callback: `serialize_value`,
    `serialize_bus`, `serialize_waveform`, `serialize_sweep_source`, and
    `var_ident` are the whole surface a callback may rely on.

    Args:
        program (QProgram): Program to render. It is read, never mutated.
    """

    def __init__(self, program: QProgram) -> None:
        self._program = program
        self._out = StringIO()
        self._var_idents: dict[str, str] = {}

    # -- public ---------------------------------------------------------------

    def dump(self) -> str:
        """Render the program in file order.

        Returns:
            The complete ``.qp`` text.
        """
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
        """Emit the ``#!QProgram <major.minor>`` line that opens every file."""
        self._out.write(f"#!QProgram {FORMAT_VERSION}\n")

    def _write_requires(self) -> None:
        """Emit ``require <vendor> <major.minor>`` for each vendor referenced in the body.

        The version comes from whichever extension is currently registered via
        `register_vendor_version`; patch is truncated since compatibility semantics are
        defined at major.minor. Fragment bodies are scanned alongside the program body — a vendor
        operation reachable only through a `Call` still needs its ``require`` line.

        Raises:
            SerializationError: If a vendor is referenced but no extension has registered a version
                for it, which would emit a file the parser cannot check for compatibility.
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
        """Emit the ``metadata:`` section, skipping fields the parser would default anyway.

        ``label`` defaults to ``""`` and is omitted when empty (the parser's default matches);
        ``description`` defaults to ``None``, so an explicit empty string is a distinct value and
        must still be emitted to round-trip faithfully.
        """
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
        side; the file format records the structural contents directly, so
        a change to a preset — one more bus, a differently spelled kind —
        can never silently flip the meaning of an existing ``.qp`` file.
        Both the custom-typed schemas and the dynamic schemas serialize
        through this same code path.
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

        Ordering is computed here (depth-first over nested `Call` nodes) rather than
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

        Returns:
            Every fragment reachable from this program, each listed after the fragments it calls.

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
        """Emit the ``body:`` section: the program's variable declarations, then its block tree."""
        self._out.write("\nbody:\n")
        for var in self._program.variables:
            self._out.write(f"  {self._serialize_var_decl(var)}\n")
        if self._program.variables:
            self._out.write("\n")
        self._write_block_contents(self._program.body, indent=2)

    def _serialize_var_decl(self, var: Variable) -> str:
        """Render one variable declaration.

        Only the annotations the variable actually carries are emitted, so a bare variable declares
        as ``var <id>`` and reloads identically.

        Args:
            var (Variable): Variable to declare.

        Returns:
            The ``var <id> [label=...] [units=...] [description=...]`` line, without indentation.
        """
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

        Five cases, in the priority order the code tests them:

        1. A `Call` — a bare ``name(args)`` statement.
        2. Any other [`Operation`][qprogram.operations.Operation] — look up its `OperationSpec` and call either the
           registered custom callback or the default signature-driven serializer.
        3. A [`Parallel`][qprogram.blocks.Parallel] — emit a pipe-joined sequence of loop headers from its child loops,
           then recurse into the body.
        4. A [`Conditional`][qprogram.blocks.Conditional] — emit one header per arm (via `_write_conditional`).
        5. Any other [`Block`][qprogram.blocks.Block] — emit ``<header>:`` (via `_serialize_block_header`) and
           recurse.

        Parallel and Conditional are special-cased rather than registered because neither is
        keyword-led: ``a | b | c:`` composes other blocks' headers, and a conditional has one header
        per arm instead of one for the node.

        Args:
            block (Block): Block whose children are emitted.
            indent (int): Column the child statements start at, in spaces.
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
                headers = [self._serialize_sweep_header(lp) for lp in element.loops]
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

        Args:
            cond (Conditional): The conditional to emit.
            indent (int): Column the arm headers start at, in spaces.
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

        `serialize_value` wraps [`Comparison`][qprogram.Comparison] in
        ``(<left> <op> <right>)`` to keep operator precedence
        unambiguous when comparisons appear nested inside arithmetic.
        For an ``if``/``elif`` header the wrap is noise, so a top-level
        Comparison is rendered without its outer parentheses.

        Args:
            condition (object): The arm's condition — a [`Comparison`][qprogram.Comparison], or any other value
                `serialize_value` accepts.

        Returns:
            The condition text to place between the keyword and the colon.
        """
        if isinstance(condition, Comparison):
            return f"{self.serialize_value(condition.left)} {condition.op} {self.serialize_value(condition.right)}"
        return self.serialize_value(condition)

    def _serialize_block_header(self, block: Block) -> str:
        """Render a block's header line without the trailing colon.

        A [`Sweep`][qprogram.blocks.Sweep] renders its own ``for <var> in <Source>(...)``
        header from the source object and never reaches the block registry.
        Every other block dispatches through that registry; an unregistered
        one raises — emitting a placeholder would silently drop the block
        (and its children's semantics) on reload.

        Args:
            block (Block): Block whose header is rendered.

        Returns:
            The header text, ready for the caller to append ``:`` to.

        Raises:
            SerializationError: If the block is not a [`Sweep`][qprogram.blocks.Sweep] and its class
                has no registered spec.
        """
        if isinstance(block, Sweep):
            return self._serialize_sweep_header(block)
        block_spec = get_block_spec_by_class(type(block))
        if block_spec is None:
            msg = (
                f"Cannot serialize block class {type(block).__name__!r}: it is not registered "
                f"with the .qp serializer. Register it via register_block(...) (or "
                f"register_sweep_source(...) plus a Sweep for loop-shaped blocks)."
            )
            raise SerializationError(msg)
        if block_spec.serialize_header is not None:
            return block_spec.serialize_header(block, self)
        return block_spec.qualified_name

    def _serialize_sweep_header(self, sweep: Sweep) -> str:
        """Render a sweep header as ``for <var> in <Source>(...)``.

        The source renders as a constructor call — same shape as a waveform, and for the same reason:
        the source's public attributes *are* its parameters, so the text is derived from the object
        rather than from a per-class callback.

        Args:
            sweep (Sweep): The loop block whose header is rendered.

        Returns:
            The header text, ready for the caller to append ``:`` to (or to join with ``|`` inside a
            [`Parallel`][qprogram.blocks.Parallel]).
        """
        var_ident = self._var_idents[sweep.variable.id]
        return f"for {var_ident} in {self.serialize_sweep_source(sweep.source)}"

    def serialize_sweep_source(self, source: SweepSource) -> str:
        """Render a sweep source as ``Name(param=value, ...)``.

        [`Values`][qprogram.Values] is special-cased to the bracket literal ``[v0, v1, ...]`` —
        it is the one source whose sole parameter reads better as the sweep itself. Never truncated:
        the literal must reload to exactly the same sweep.

        Args:
            source (SweepSource): Source to render. A combinator's nested sources recurse through
                `serialize_value`.

        Returns:
            The constructor call, or the bracket literal for [`Values`][qprogram.Values].
        """
        if isinstance(source, Values):
            return self.serialize_value(source.points)
        params = ", ".join(
            f"{key}={self.serialize_value(val)}" for key, val in vars(source).items() if not key.startswith("_")
        )
        return f"{type(source).__name__}({params})"

    def _serialize_call(self, call: Call) -> str:
        """Render a fragment call statement as ``<name>(<args>)``.

        Arguments are emitted positionally in the fragment's parameter order, which is how the parser
        reads them back — the keyword spelling a caller may have used at build time is not part of
        the wire form.

        Args:
            call (Call): The call node to render.

        Returns:
            The call statement text.

        Raises:
            SerializationError: If a parameter of the called fragment has no bound argument.
        """
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

        Args:
            op (Operation): Operation to render.

        Returns:
            The full statement line, keyword included, without indentation.

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

        Recognizes the full [`Expression`][qprogram.Expression] AST (variables,
        constants, arithmetic, comparison, logical, math functions,
        conditional ``where``), waveform instances, bus references,
        measurement handles (rendered as their quoted name), sweep sources
        (the nested ``source`` / ``sources`` a combinator carries), plain
        strings (quoted), booleans, ``None`` (as ``null``), numeric
        literals (including numpy scalars), lists/tuples/1-D arrays
        (rendered as ``[v0, v1, ...]`` — this is the form ``Measure.fields``
        / ``Acquire.fields`` take: ``fields=["state", "iq"]``), and
        string-keyed dicts (rendered as ``{"k": v, ...}``).

        Symbolic operators (arithmetic, comparison, binary logical) all
        emit the canonical parenthesized ``(<left> <op> <right>)`` shape;
        the parser recovers them through the same form. Math and ``where``
        use the function-call shape ``name(arg, ...)``.

        Args:
            val (object): Value taken from an operation attribute, a block header, or a nested
                position inside one of those.

        Returns:
            A single ``.qp`` token — no whitespace outside quotes, brackets, braces, or parentheses.

        Raises:
            SerializationError: For any value type the format has no
                representation for; for a [`MeasurementRef`][qprogram.MeasurementRef]
                whose handle name carries a character the unquoted
                ``<name>.<field>`` wire form cannot hold (whitespace, a
                quote, ``#``, a comma, a dot, or a bracket, brace, or
                parenthesis); for an array of
                any rank other than 1; or for a dict with a non-string key.
                Emitting ``str(val)`` instead would produce a token the
                parser either rejects or silently mis-types on reload.
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
            # Emit as ``<handle_name>.<field>``. The parser recognizes this
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
        if isinstance(val, (Waveform, IQWaveform)):
            return self.serialize_waveform(val)
        if isinstance(val, SweepSource):
            # Nested sources: a combinator's ``source`` / ``sources`` attributes.
            return self.serialize_sweep_source(val)
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
        """Render a bus argument as a path or as a quoted string.

        A [`BusRef`][qprogram.BusRef] carrying an element and a kind, in a program that declares a
        schema, emits as the unquoted ``element[idx].kind`` path the parser promotes back to a bus
        reference. Everything else emits as a quoted string, which is what keeps a raw-string bus
        that happens to look like a path from being promoted on reload.

        Args:
            bus (object): The operation's bus attribute.

        Returns:
            The bus token.
        """
        if self._program.schema is not None and isinstance(bus, BusRef) and bus.element and bus.kind:
            idx = bus.idx
            idx_str = ",".join(str(i) for i in idx) if isinstance(idx, tuple) else str(idx)
            return f"{bus.element}[{idx_str}].{bus.kind}"
        return f'"{_escape_str(str(bus))}"'

    def serialize_waveform(self, wf: object) -> str:
        """Emit a waveform constructor call, mirroring the class name and public attrs.

        Sample arrays (e.g. ``Arbitrary.samples``) are emitted in full — truncating them would
        break the round-trip guarantee, and the parser has no way to recover dropped samples.

        Args:
            wf (object): Waveform instance. Its attributes are read in the order ``__init__``
                assigned them; underscore-prefixed ones are internal and skipped.

        Returns:
            The ``Name(param=value, ...)`` constructor call.
        """
        cls_name = type(wf).__name__
        params: list[str] = []
        for key, val in vars(wf).items():
            if key.startswith("_"):
                continue
            params.append(f"{key}={self.serialize_value(val)}")
        return f"{cls_name}({', '.join(params)})"

    def var_ident(self, var: Variable) -> str:
        """Return the identifier chosen for ``var`` in the emitted file.

        The lookup a spec callback uses when it places a variable itself, as ``get_parameter`` does
        for its target after the ``->`` arrow.

        Args:
            var (Variable): Variable to name. It must belong to the program (or, inside a fragment
                section, to that fragment) being written.

        Returns:
            The identifier written in the file.
        """
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
        """Walk a block tree and gather the vendor names its nodes reference.

        Covers both vendor operations and vendor **blocks** — either is enough to make a file depend
        on an extension, and the ``require`` line is what lets `loads` auto-activate
        it.

        Uses `Block.walk` rather than recursing over ``.elements`` — Conditional keeps its
        arm bodies on ``.arms`` / ``.else_body`` (not in ``_elements``), so an elements-only
        recursion would miss vendor ops inside ``if_``/``elif_``/``else_`` arms and emit a file
        with no ``require`` line for them.

        Args:
            block (Block): Root of the tree to scan — a program body or a fragment body.

        Returns:
            The vendor names referenced anywhere in the tree.
        """
        vendors: set[str] = set()
        for node in block.walk():
            if isinstance(node, Block):
                # Vendor *blocks* count too: a program whose only vendor content is e.g. a
                # ``myplatform.infinite_loop:`` still needs its ``require`` line, or the file
                # won't auto-activate the extension and won't reload anywhere the package
                # happens not to be imported already.
                block_spec = get_block_spec_by_class(type(node))
                if block_spec is not None and block_spec.vendor is not None:
                    vendors.add(block_spec.vendor)
                continue
            spec = get_operation_spec_by_class(type(node))
            if spec is not None and spec.vendor is not None:
                vendors.add(spec.vendor)
        return vendors


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _major_minor(version: str) -> str:
    """Truncate a semver string to ``major.minor`` (e.g. ``"0.1.3" -> "0.1"``).

    A string carrying a major component only is padded to ``major.0``. The writer never reaches
    that branch: its argument comes from
    `get_vendor_version`, and
    [`register_vendor_version`][qprogram.serialization.registry.register_vendor_version] refuses a version without
    integer major and minor components.

    Args:
        version (str): Version a vendor extension registered.

    Returns:
        The ``major.minor`` prefix.
    """
    parts = version.split(".")
    if len(parts) < 2:
        return f"{version}.0"
    return f"{parts[0]}.{parts[1]}"


def _escape_str(s: str) -> str:
    """Escape a string for embedding in double quotes inside a ``.qp`` file.

    Backslash and double-quote get the usual escapes; newline, carriage return, and tab are
    escaped too — the format is line-based, so a raw newline inside a quoted string would split
    the statement across two lines and break the parse.

    Args:
        s (str): Raw text to escape. The surrounding quotes are the caller's to add.

    Returns:
        The escaped text.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
