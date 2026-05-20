"""Parser for the ``.qp`` file format.

Like the writer, the parser is intentionally thin. Header/require/metadata
parsing is fixed grammar, but everything inside ``body:`` — operations,
control-flow blocks, sweep generators — is dispatched through the registries
in :mod:`qprogram.serialization.registry`. New operations, blocks, or sweep
sources can be added by registration alone; no parser change required.

The parser exposes a small *parse context* API used by spec callbacks:

- :meth:`parse_value` — token → typed value (string / number / bool / list /
  inline waveform / declared variable).
- :meth:`parse_error` — produce a :class:`ParseError` tagged with the current
  line number, for use by callbacks that detect bad input.
- :meth:`get_or_declare_variable` — used by callbacks (e.g. ``get_parameter``)
  that need a target variable identifier; auto-declares one if not seen
  already, which matches the runtime behaviour of the Python API.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

import numpy as np

from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusNaming, BusRef, BusSchema
from qprogram.errors import QProgramError
from qprogram.operations.operation import MeasurementOperation
from qprogram.qprogram import QProgram
from qprogram.result import MeasurementHandle
from qprogram.serialization import _specs as _core_specs
from qprogram.serialization.registry import (
    get_block_spec,
    get_operation_spec,
    get_sweep_generator_spec,
    get_vendor_version,
    get_waveform_class,
)
from qprogram.variable import _ID_RE, MeasurementRef

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.variable import (
        BinaryOperator,
        ComparisonOperator,
        Expression,
        LogicalBinaryOperator,
        UnaryOperator,
        Variable,
    )

FORMAT_VERSION = "1.0"


class ParseError(QProgramError):
    """Error during ``.qp`` file parsing.

    A direct child of :class:`~qprogram.QProgramError`, separate from
    :class:`~qprogram.ValidationError`. Validation runs on in-memory
    programs; parsing fails on malformed input text. Both are user-facing
    errors but the failure surfaces are distinct enough that they live on
    different branches of the hierarchy.
    """

    def __init__(self, message: str, line_num: int = 0) -> None:
        self.line_num = line_num
        super().__init__(f"Line {line_num}: {message}" if line_num else message)


def loads(text: str) -> QProgram:
    """Parse a .qp format string into a QProgram."""
    return _Parser(text).parse()


def load(path: str) -> QProgram:
    """Load a .qp file into a QProgram."""
    with Path(path).open("r") as f:
        return loads(f.read())


# ---------------------------------------------------------------------------
# Module-level regexes
# ---------------------------------------------------------------------------


_BUS_PATH_RE = re.compile(r"^(\w+)\[(\d+(?:,\d+)*)\]\.(\w+)$")
_ELEMENT_HEADER_RE = re.compile(r"^element\s+(\w+)\s*:\s*$")
_BUS_LINE_RE = re.compile(r"^(\w+)\s+info=(\S+)\s*$")
_FOR_HEADER_RE = re.compile(r"^for\s+(\w+)\s+in\s+(.*)$")

# Operator alphabets — frozen sets so membership checks act as the
# accept/reject gates for the ``cast`` calls in :meth:`_parse_paren_expression`.
# Kept in lockstep with the ``Literal`` operator types in :mod:`qprogram.variable`.
_UNARY_OPS: frozenset[str] = frozenset({"+", "-"})
_BINARY_OPS: frozenset[str] = frozenset({"+", "-", "*", "/"})
_COMPARISON_OPS: frozenset[str] = frozenset({"==", "!=", "<", "<=", ">", ">="})
_LOGICAL_BINARY_OPS: frozenset[str] = frozenset({"and", "or"})


# ---------------------------------------------------------------------------
# Recursive-descent parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()
        self._pos = 0
        self._program = QProgram()
        self._variables: dict[str, Variable] = {}
        self._handles: dict[str, MeasurementHandle] = {}
        self._required_vendors: set[str] = set()

    # -- public entry point --------------------------------------------------

    def parse(self) -> QProgram:
        self._parse_header()
        self._parse_requires()
        while self._pos < len(self._lines):
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if line == "metadata:":
                self._pos += 1
                self._parse_metadata()
            elif line.startswith("schema:"):
                self._parse_schema_decl()
            elif line == "body:":
                self._pos += 1
                self._parse_body()
            else:
                self._pos += 1
        return self._program

    # -- line helpers --------------------------------------------------------

    @property
    def line_num(self) -> int:
        """Current 1-indexed line number, for error messages from callbacks."""
        return self._pos + 1

    def _stripped(self) -> str:
        if self._pos >= len(self._lines):
            return ""
        line = self._lines[self._pos]
        ci = _find_comment(line)
        if ci >= 0:
            line = line[:ci]
        return line.strip()

    def _indent(self) -> int:
        if self._pos >= len(self._lines):
            return 0
        raw = self._lines[self._pos]
        return len(raw) - len(raw.lstrip())

    # -- header & require ----------------------------------------------------

    def _parse_header(self) -> None:
        while self._pos < len(self._lines) and not self._stripped():
            self._pos += 1
        line = self._stripped()
        if not line.startswith("#!QProgram"):
            msg = "Missing #!QProgram header"
            raise ParseError(msg, self._pos + 1)
        version = line.split()[-1] if len(line.split()) > 1 else "unknown"
        if version.split(".")[0] != FORMAT_VERSION.split(".", maxsplit=1)[0]:
            msg = f"Unsupported format version {version}"
            raise ParseError(msg, self._pos + 1)
        self._pos += 1

    def _parse_requires(self) -> None:
        while self._pos < len(self._lines):
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if line.startswith("require "):
                tokens = line.split()
                if len(tokens) != 3:
                    msg = (
                        f"`require` declaration must specify a version: `require <vendor> <major.minor>`. Got: {line!r}"
                    )
                    raise ParseError(msg, self._pos + 1)
                _, vendor, file_version = tokens
                self._check_vendor_compat(vendor, file_version)
                self._required_vendors.add(vendor)
                self._pos += 1
            else:
                break

    def _check_vendor_compat(self, vendor: str, file_version: str) -> None:
        installed = get_vendor_version(vendor)
        if installed is None:
            msg = (
                f"file requires vendor '{vendor}' {file_version} but no "
                f"matching extension is registered in this environment"
            )
            raise ParseError(msg, self._pos + 1)
        try:
            file_major, file_minor = _parse_major_minor(file_version)
            inst_major, inst_minor = _parse_major_minor(installed)
        except ValueError as e:
            raise ParseError(str(e), self._pos + 1) from e
        if file_major != inst_major:
            msg = (
                f"file requires {vendor} {file_version} (major {file_major}); "
                f"installed {vendor} is {installed} (major {inst_major}) — "
                f"major versions must match"
            )
            raise ParseError(msg, self._pos + 1)
        if file_minor > inst_minor:
            msg = (
                f"file requires {vendor} {file_version} or compatible; "
                f"installed {vendor} is {installed} — minor version too old"
            )
            raise ParseError(msg, self._pos + 1)

    # -- metadata ------------------------------------------------------------

    def _parse_metadata(self) -> None:
        while self._pos < len(self._lines):
            if self._indent() < 2 and self._stripped():
                break
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip().strip('"')
                if key.strip() == "label":
                    self._program.label = val
                elif key.strip() == "description":
                    self._program.description = val
            self._pos += 1

    # -- schema declaration (inline element/bus declarations) ---------------

    _INFO_CHANNELS: ClassVar[frozenset[str]] = frozenset({"single", "IQ"})
    _INFO_FLAGS: ClassVar[frozenset[str]] = frozenset({"acquires"})

    def _parse_schema_decl(self) -> None:
        """Parse the single ``schema:`` block at the current position.

        Always inline form — the file format has no preset keyword. The
        Python side still has :func:`BusSchema.transmon` etc. as
        construction-time conveniences, but those compile to the same
        ``element/bus`` data the inline form records, and the writer
        always emits that structural form.
        """
        if self._program.schema is not None:
            msg = "duplicate schema declaration — a program may have at most one schema"
            raise ParseError(msg, self._pos + 1)
        line = self._stripped()
        if line != "schema:":
            msg = (
                f"invalid schema declaration: expected `schema:` followed by "
                f"indented `element <name>:` / bus declarations; got {line!r}"
            )
            raise ParseError(msg, self._pos + 1)
        self._pos += 1
        self._build_inline_schema()

    def _build_inline_schema(self) -> None:
        naming_pattern: str | None = None
        elements: dict[str, dict[str, tuple]] = {}
        while self._pos < len(self._lines):
            indent = self._indent()
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if indent < 2:
                break
            if line.startswith("naming:"):
                _, _, val = line.partition(":")
                val = val.strip()
                if len(val) < 2 or not (val.startswith('"') and val.endswith('"')):
                    msg = f"schema `naming` must be a quoted string: {line!r}"
                    raise ParseError(msg, self._pos + 1)
                naming_pattern = _unescape_str(val[1:-1])
                self._pos += 1
                continue
            elem_match = _ELEMENT_HEADER_RE.match(line)
            if not elem_match:
                msg = f"unexpected line in schema body: {line!r}"
                raise ParseError(msg, self._pos + 1)
            elem_name = elem_match.group(1)
            self._pos += 1
            elements[elem_name] = self._parse_inline_element_buses()
        if not elements:
            msg = "inline schema has no element declarations"
            raise ParseError(msg, self._pos)
        naming = BusNaming(naming_pattern) if naming_pattern is not None else None
        schema = BusSchema(naming=naming)
        for elem, buses in elements.items():
            schema.add_element(elem, buses)
        self._program._schema = schema  # noqa: SLF001

    def _parse_inline_element_buses(self) -> dict[str, tuple[str, bool]]:
        buses: dict[str, tuple[str, bool]] = {}
        while self._pos < len(self._lines):
            indent = self._indent()
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if indent < 4:
                break
            m = _BUS_LINE_RE.match(line)
            if not m:
                msg = f"invalid bus declaration in schema body: {line!r}; expected `<kind> info=<channel>[+acquires]`"
                raise ParseError(msg, self._pos + 1)
            kind, info_value = m.group(1), m.group(2)
            if kind in buses:
                msg = f"duplicate bus {kind!r} in element"
                raise ParseError(msg, self._pos + 1)
            channel, acquires = self._parse_bus_info(info_value)
            buses[kind] = (channel, acquires)
            self._pos += 1
        return buses

    def _parse_bus_info(self, value: str) -> tuple[str, bool]:
        if not value:
            msg = "bus `info` must specify a channel (single|IQ), got empty value"
            raise ParseError(msg, self._pos + 1)
        channel: str | None = None
        acquires = False
        seen_flags: set[str] = set()
        for raw in value.split("+"):
            part = raw.strip()
            if part in self._INFO_CHANNELS:
                if channel is not None:
                    msg = f"bus `info` has multiple channel tokens (single|IQ): {value!r}"
                    raise ParseError(msg, self._pos + 1)
                channel = part
            elif part in self._INFO_FLAGS:
                if part in seen_flags:
                    msg = f"bus `info` has duplicate flag {part!r}: {value!r}"
                    raise ParseError(msg, self._pos + 1)
                seen_flags.add(part)
                if part == "acquires":
                    acquires = True
            else:
                allowed = ", ".join(sorted(self._INFO_CHANNELS | self._INFO_FLAGS))
                msg = f"bus `info` has unknown token {part!r}; allowed: {allowed}"
                raise ParseError(msg, self._pos + 1)
        if channel is None:
            msg = f"bus `info` must specify a channel (single|IQ): {value!r}"
            raise ParseError(msg, self._pos + 1)
        return channel, acquires

    def _upgrade_busrefs(self, op: object) -> None:
        """Replace bus-path strings on an operation with resolved BusRef instances.

        Walks instance attributes for string values that look like
        ``element[index].kind`` and resolves each against the program's
        schema. Plain strings that don't match the path syntax are left
        alone (case 1: raw string buses). ``list`` attributes (e.g.
        ``Sync.buses``) are handled too.
        """
        if self._program.schema is None:
            return
        for key, value in vars(op).items():
            if isinstance(value, str) and not isinstance(value, BusRef):
                ref = self._resolve_bus_path(value)
                if ref is not None:
                    setattr(op, key, ref)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str) and not isinstance(item, BusRef):
                        ref = self._resolve_bus_path(item)
                        if ref is not None:
                            value[i] = ref

    def _resolve_bus_path(self, token: str) -> BusRef | None:
        schema = self._program.schema
        if schema is None:
            return None
        m = _BUS_PATH_RE.match(token)
        if not m:
            return None
        element, idx_str, kind = m.groups()
        index: int | tuple = tuple(int(p) for p in idx_str.split(",")) if "," in idx_str else int(idx_str)
        try:
            factory = getattr(schema, element)
            accessor = factory[index]
            ref = getattr(accessor, kind)
        except (AttributeError, KeyError) as e:
            msg = f"bus path {token!r} does not resolve against the program schema: {e}"
            raise ParseError(msg, self._pos + 1) from e
        if not isinstance(ref, BusRef):  # pragma: no cover — defensive
            msg = f"bus path {token!r} did not yield a BusRef"
            raise ParseError(msg, self._pos + 1)
        return ref

    # -- body ----------------------------------------------------------------

    def _parse_body(self) -> None:
        self._parse_statements(self._program._body, min_indent=2)  # noqa: SLF001

    def _parse_statements(self, parent: Block, min_indent: int) -> None:
        """Walk a block's children, dispatching by the first significant token.

        Order matters: ``var`` declarations come first because they share no
        prefix with block keywords. ``for`` introduces loops and parallel
        loop compositions, dispatched via the sweep-generator registry. All
        other ``<keyword>:`` lines hit the block registry. Anything left is
        an operation: vendor-prefixed lookup followed by core lookup.
        """
        while self._pos < len(self._lines):
            line = self._stripped()
            indent = self._indent()
            if not line:
                self._pos += 1
                continue
            if indent < min_indent:
                break
            if line.startswith("var "):
                var_id, attrs = self._parse_var_decl(line)
                if var_id in self._variables:
                    msg = f"duplicate variable id {var_id!r}"
                    raise ParseError(msg, self._pos + 1)
                var = self._program.variable(var_id, **attrs)
                self._variables[var_id] = var
                self._pos += 1
                continue
            if self._try_parse_block_header(parent, line, min_indent):
                continue
            op = self._parse_operation(line)
            if op is not None:
                self._upgrade_busrefs(op)
                parent.append(op)
                # Track measurement handles so subsequent conditions can
                # resolve ``<name>.<field>`` references.
                if isinstance(op, MeasurementOperation):
                    # The op's canonical handle was already obtained via
                    # ctx.get_or_create_handle(name) inside the custom
                    # parse callback for the measurement op (see
                    # _specs.py:measurement_op_parse). Re-publishing here
                    # is idempotent (same instance) and provides a safety
                    # net if a future vendor op skips the custom callback.
                    self._handles[op.name] = op.handle
            self._pos += 1

    def _try_parse_block_header(self, parent: Block, line: str, min_indent: int) -> bool:
        """Return True iff ``line`` was a block header (and the block was parsed)."""
        if not line.endswith(":"):
            return False
        header = line[:-1].rstrip()
        # Loop family: ``for`` heads either a single loop or a parallel of loops.
        if header.startswith("for ") or ("|" in header and "for " in header):
            self._parse_loop_or_parallel(parent, header, min_indent)
            return True
        # Conditional family: ``if`` opens a chain; ``elif``/``else`` here
        # without a preceding ``if`` at the same level is a parse error
        # (the well-formed case is handled inside _parse_conditional).
        if header.startswith("if ") or header == "if":
            self._parse_conditional(parent, header, min_indent)
            return True
        if header.startswith("elif ") or header in {"elif", "else"}:
            msg = f"{header.split()[0]!r} without a preceding `if:` at the same indent level"
            raise ParseError(msg, self._pos + 1)
        # Keyword-led block: leading word maps to a registered BlockSpec.
        first_token, _, rest = header.partition(" ")
        spec = get_block_spec(first_token)
        if spec is None:
            return False
        tokens = _tokenize(rest) if rest.strip() else []
        block: Block = spec.parse_header(tokens, self) if spec.parse_header is not None else spec.cls()
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2)
        return True

    def _parse_conditional(self, parent: Block, header: str, min_indent: int) -> None:
        """Parse an ``if`` block plus any ``elif`` / ``else`` continuation arms.

        Each arm is ``<keyword> [<condition>]:`` followed by an indented
        body. The chain stops at the first non-``elif``/``else`` line at
        the same indent level (or any line that outdents).
        """
        if header == "if":
            msg = "`if` requires a condition: `if <expr>:`"
            raise ParseError(msg, self._pos + 1)
        cond = Conditional()
        parent.append(cond)
        self._parse_conditional_arm(cond, header, min_indent, keyword="if")
        while self._pos < len(self._lines):
            line = self._stripped()
            indent = self._indent()
            if not line:
                self._pos += 1
                continue
            if indent != min_indent:
                break
            if not line.endswith(":"):
                break
            arm_header = line[:-1].rstrip()
            if arm_header.startswith("elif "):
                if cond.else_body is not None:
                    msg = "`elif` cannot follow `else` in the same chain"
                    raise ParseError(msg, self._pos + 1)
                self._parse_conditional_arm(cond, arm_header, min_indent, keyword="elif")
                continue
            if arm_header == "else":
                if cond.else_body is not None:
                    msg = "multiple `else` arms in the same conditional chain"
                    raise ParseError(msg, self._pos + 1)
                self._parse_conditional_else(cond, min_indent)
                continue
            break

    def _parse_conditional_arm(
        self,
        cond: Conditional,
        header: str,
        min_indent: int,
        *,
        keyword: str,
    ) -> None:
        """Parse one ``if``/``elif`` arm: condition + indented body."""
        expr_text = header[len(keyword) + 1 :].strip()
        if not expr_text:
            msg = f"`{keyword}` requires a condition"
            raise ParseError(msg, self._pos + 1)
        # Reuse the parenthesised-expression parser for the Comparison
        # shape; writer omits the outer parens for top-level conditions,
        # so synthesize them back.
        wrapped = expr_text if expr_text.startswith("(") and expr_text.endswith(")") else f"({expr_text})"
        condition = self._parse_paren_expression(wrapped)
        arm_body = self._program._body.__class__()  # noqa: SLF001  # build a bare Block instance
        cond.arms.append((condition, arm_body))
        self._pos += 1
        self._parse_statements(arm_body, min_indent + 2)

    def _parse_conditional_else(self, cond: Conditional, min_indent: int) -> None:
        """Parse the terminal ``else:`` arm."""
        else_body = self._program._body.__class__()  # noqa: SLF001
        cond.else_body = else_body
        self._pos += 1
        self._parse_statements(else_body, min_indent + 2)

    def _parse_loop_or_parallel(self, parent: Block, header: str, min_indent: int) -> None:
        loop_parts = [p.strip() for p in header.split("|")]
        # Every built-in sweep generator produces a ForLoop or Loop, but the
        # registry is open — defensively narrow before constructing Parallel,
        # which is the only block whose AST shape requires that constraint.
        loops: list[ForLoop | Loop] = []
        for part in loop_parts:
            lp = self._parse_for_header(part)
            if not isinstance(lp, (ForLoop, Loop)):
                msg = (
                    f"sweep generator produced a {type(lp).__name__}; only "
                    f"ForLoop and Loop can compose under `|` (parallel)"
                )
                raise ParseError(msg, self._pos + 1)
            loops.append(lp)
        block: Block = loops[0] if len(loops) == 1 else Parallel(loops=loops)
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2)

    def _parse_for_header(self, header: str) -> Block:
        """Parse a single ``for <var> in <generator>`` header into a loop block."""
        m = _FOR_HEADER_RE.match(header.strip())
        if not m:
            msg = f"invalid for loop header: {header!r}"
            raise ParseError(msg, self._pos + 1)
        var_name = m.group(1)
        var = self.get_or_declare_variable(var_name)
        rest = m.group(2).strip()
        # List literal -> the registered ``values`` generator.
        if rest.startswith("["):
            spec = get_sweep_generator_spec("values")
            if spec is None:
                msg = "sweep generator 'values' is not registered"
                raise ParseError(msg, self._pos + 1)
            return spec.parse(var, rest, self)
        # Otherwise: ``name(...)`` — look up the name in the registry.
        if "(" not in rest:
            msg = f"unknown sweep source {rest!r}; expected `name(args)` or `[values]`"
            raise ParseError(msg, self._pos + 1)
        paren = rest.index("(")
        gen_name = rest[:paren].strip()
        args_text = rest[paren + 1 : rest.rindex(")")]
        spec = get_sweep_generator_spec(gen_name)
        if spec is None:
            msg = f"unknown sweep generator {gen_name!r}"
            raise ParseError(msg, self._pos + 1)
        return spec.parse(var, args_text, self)

    # -- variable declarations ----------------------------------------------

    _VAR_ATTRS: ClassVar[frozenset[str]] = frozenset({"label", "units", "description"})

    def _parse_var_decl(self, line: str) -> tuple[str, dict[str, str]]:
        tokens = _tokenize(line)
        if len(tokens) < 2 or tokens[0] != "var":
            msg = (
                f"`var` declaration must have the form "
                f'`var <id> [label="..."] [units="..."] [description="..."]`. '
                f"Got: {line!r}"
            )
            raise ParseError(msg, self._pos + 1)
        var_id = tokens[1]
        if not _ID_RE.match(var_id):
            msg = (
                f"variable id {var_id!r} is invalid: must match "
                f"[A-Za-z_][A-Za-z0-9_]* (no spaces or special characters)"
            )
            raise ParseError(msg, self._pos + 1)

        attrs: dict[str, str] = {}
        for tok in tokens[2:]:
            if "=" not in tok:
                msg = f'unexpected token {tok!r} in `var` declaration; expected key="value"'
                raise ParseError(msg, self._pos + 1)
            key, _, value = tok.partition("=")
            key = key.strip()
            value = value.strip()
            if key not in self._VAR_ATTRS:
                allowed = ", ".join(sorted(self._VAR_ATTRS))
                msg = f"unknown variable attribute {key!r}; allowed: {allowed}"
                raise ParseError(msg, self._pos + 1)
            if key in attrs:
                msg = f"duplicate variable attribute {key!r}"
                raise ParseError(msg, self._pos + 1)
            if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
                msg = f"variable attribute {key!r} must be a quoted string, got: {value!r}"
                raise ParseError(msg, self._pos + 1)
            attrs[key] = _unescape_str(value[1:-1])
        return var_id, attrs

    # -- operations ----------------------------------------------------------

    def _parse_operation(self, line: str) -> Operation | None:
        """Dispatch an operation line to its registered spec.

        Returns ``None`` for unknown operations (the caller silently skips
        the line). Vendor operations carry a dotted prefix; core operations
        do not.
        """
        tokens = _tokenize(line)
        if not tokens:
            return None
        op_name = tokens[0]
        vendor: str | None = None
        if "." in op_name:
            vendor, op_name = op_name.split(".", 1)
        spec = get_operation_spec(vendor, op_name)
        if spec is None:
            return None
        if spec.parse is not None:
            return spec.parse(tokens[1:], self)
        return _core_specs.default_parse_operation(spec, tokens[1:], self)

    # -- callbacks exposed to spec functions (the "parse context") -----------

    def parse_value(self, token: str) -> object:
        """Decode a single ``.qp`` token into a typed value.

        Recognises quoted strings, booleans, list literals, inline waveform
        constructors, numeric literals, and references to previously
        declared variables. Anything that doesn't match any of those is
        returned as a plain string — this is how bus-path tokens
        (``q[0].drive``) flow through unchanged until ``_upgrade_busrefs``
        promotes them to typed :class:`BusRef` instances.
        """
        tok = token.strip()
        if not tok:
            msg = "empty argument token"
            raise ParseError(msg, self._pos + 1)
        if tok.startswith('"') and tok.endswith('"'):
            return _unescape_str(tok[1:-1])
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1]
            return np.array([_parse_number(v.strip()) for v in inner.split(",") if v.strip()])
        # Parenthesised symbolic expression — emitted by the writer for any
        # BinaryOp or UnaryOp (e.g. ``(freq + 1000000.0)``). The writer always
        # parenthesises and never leaves nested operations unwrapped, so this
        # recursive form is sufficient.
        if tok.startswith("(") and tok.endswith(")"):
            return self._parse_paren_expression(tok)
        # Function-call shape: ``name(args)``. Dispatched by name —
        # math functions and ``where`` are checked first because they
        # share the function-call form with waveform constructors.
        if "(" in tok and not tok[0].isdigit() and not tok.startswith("-"):
            return self._parse_function_call(tok)
        # Measurement-handle field reference: ``<handle_name>.<field>``.
        # Checked before the variable lookup because a dotted token can't
        # be a Variable id (Variable.id is restricted to [A-Za-z_][\w]*).
        if "." in tok and not tok.startswith("."):
            head, _, tail = tok.partition(".")
            if head in self._handles and tail:
                return MeasurementRef(self._handles[head], tail)
        if tok in self._variables:
            return self._variables[tok]
        try:
            return _parse_number(tok)
        except ValueError:
            return tok

    def parse_error(self, message: str) -> ParseError:
        """Build a :class:`ParseError` tagged with the current line number."""
        return ParseError(message, self._pos + 1)

    def _parse_paren_expression(self, tok: str) -> Expression:
        """Parse a parenthesised symbolic expression token.

        Round-trips the parenthesised forms emitted by the writer:

        - Arithmetic binary:  ``(<left> + <right>)``, ``-``, ``*``, ``/``.
        - Arithmetic unary:   ``(-<operand>)`` or ``(+<operand>)`` — no
          space between the sign and the operand.
        - Comparison:         ``(<left> == <right>)`` and ``!=``, ``<``,
          ``<=``, ``>``, ``>=``.
        - Binary logical:     ``(<left> and <right>)``, ``(<left> or <right>)``.
        - Unary logical:      ``(not <operand>)``.

        The strategy: tokenize the inner with parenthesis-aware whitespace
        splitting and dispatch on the count and shape.

        - 1 token starting with ``+``/``-`` → arithmetic unary.
        - 2 tokens, first is ``not`` → logical unary.
        - 3 tokens, middle is a known operator → the appropriate binary node.

        Math functions and ``where`` use the function-call shape and are
        handled by :meth:`_parse_function_call`, not here.
        """
        from qprogram.variable import (  # noqa: PLC0415
            BinaryOp,
            Comparison,
            LogicalBinaryOp,
            LogicalNot,
            UnaryOp,
        )

        inner = tok[1:-1].strip()
        if not inner:
            msg = f"empty expression: {tok!r}"
            raise ParseError(msg, self._pos + 1)

        # Logical unary (``not <operand>``) — recognised by the leading
        # keyword followed by whitespace.
        if inner.startswith("not "):
            operand_tok = inner[4:].strip()
            operand = _to_expression(self.parse_value(operand_tok))
            return LogicalNot(operand)

        tokens = _tokenize(inner)

        # Arithmetic unary — writer emits ``(-x)`` / ``(+x)`` with no space.
        if len(tokens) == 1:
            single = tokens[0]
            if len(single) > 1 and single[0] in _UNARY_OPS:
                operand = _to_expression(self.parse_value(single[1:].strip()))
                return UnaryOp(cast("UnaryOperator", single[0]), operand)
            msg = f"could not parse expression: {tok!r}"
            raise ParseError(msg, self._pos + 1)

        if len(tokens) == 3:
            left_tok, op_tok, right_tok = tokens
            left = _to_expression(self.parse_value(left_tok))
            right = _to_expression(self.parse_value(right_tok))
            if op_tok in _BINARY_OPS:
                return BinaryOp(cast("BinaryOperator", op_tok), left, right)
            if op_tok in _COMPARISON_OPS:
                return Comparison(cast("ComparisonOperator", op_tok), left, right)
            if op_tok in _LOGICAL_BINARY_OPS:
                return LogicalBinaryOp(cast("LogicalBinaryOperator", op_tok), left, right)
            msg = f"unknown operator {op_tok!r} in expression {tok!r}"
            raise ParseError(msg, self._pos + 1)

        msg = f"could not parse expression: {tok!r}"
        raise ParseError(msg, self._pos + 1)

    def _parse_function_call(self, tok: str) -> object:
        """Dispatch a ``name(args)`` token to math, ``where``, or waveform.

        Resolution order: math-function names (``sin``, ``cos``, ``minimum``,
        …) → ``where`` → waveform registry. Math and ``where`` are
        first-class :class:`~qprogram.Expression` nodes; anything else is
        treated as a waveform constructor invocation.
        """
        from qprogram.variable import _MATH_FUNCTIONS, MathFunc, Where  # noqa: PLC0415

        paren_idx = tok.index("(")
        name = tok[:paren_idx]
        args_text = tok[paren_idx + 1 : tok.rindex(")")]
        if name in _MATH_FUNCTIONS:
            args = tuple(_to_expression(self.parse_value(part.strip())) for part in _split_args(args_text))
            return MathFunc(name, args)
        if name == "where":
            parts = [_to_expression(self.parse_value(part.strip())) for part in _split_args(args_text)]
            if len(parts) != 3:
                msg = f"where(...) requires 3 arguments (condition, then, else); got {len(parts)}"
                raise ParseError(msg, self._pos + 1)
            return Where(parts[0], parts[1], parts[2])
        # Threading the variable table makes ``Gaussian(amplitude=amp, ...)``
        # parse back into a Variable rather than the bare string ``"amp"``.
        return _parse_waveform_expr(tok, self._variables)

    def get_or_declare_variable(self, name: str) -> Variable:
        """Return the declared :class:`Variable` named ``name``, declaring it on demand."""
        if name in self._variables:
            return self._variables[name]
        var = self._program.variable(name)
        self._variables[name] = var
        return var

    def declared_variable(self, name: str) -> Variable | None:
        """Return the declared :class:`Variable` named ``name``, or ``None``."""
        return self._variables.get(name)

    def get_or_create_handle(self, name: str) -> MeasurementHandle:
        """Return the canonical :class:`MeasurementHandle` for ``name``, creating it on demand.

        The same instance is returned for every call with the same name
        during a single parse, so every measurement op, every
        :class:`MeasurementRef`, and every other consumer of the handle
        end up sharing one Python object.
        """
        if name not in self._handles:
            self._handles[name] = MeasurementHandle(name)
        return self._handles[name]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _find_comment(line: str) -> int:
    in_str = False
    for i, c in enumerate(line):
        if c == '"':
            in_str = not in_str
        elif c == "#" and not in_str and line[:2] != "#!":
            return i
    return -1


def _unescape_str(s: str) -> str:
    r"""Inverse of the writer's escape: ``\\"`` -> ``"``, ``\\\\`` -> ``\\``."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
            if nxt == '"':
                out.append('"')
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_major_minor(version: str) -> tuple[int, int]:
    parts = version.split(".")
    if len(parts) < 2:
        msg = f"version {version!r} must have at least major.minor"
        raise ValueError(msg)
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as e:
        msg = f"version {version!r} has non-integer major/minor components"
        raise ValueError(msg) from e


def _parse_number(s: str) -> int | float:
    s = s.strip()
    val = float(s)
    if val == int(val) and "." not in s and "e" not in s.lower():
        return int(val)
    return val


def _to_expression(value: object) -> Expression:
    """Promote a parsed value into an :class:`Expression`-compatible operand.

    Numbers become :class:`Constant`; variables and expression nodes pass
    through. Anything else (a bus string, say) reaches here only as a result
    of malformed input — raise so the caller surfaces a clean error instead
    of a downstream ``TypeError`` from the AST constructor.
    """
    from qprogram.variable import Constant, Expression  # noqa: PLC0415

    if isinstance(value, Expression):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Constant(value)
    msg = f"cannot use {value!r} ({type(value).__name__}) as an expression operand"
    raise ParseError(msg)


def _tokenize(line: str) -> list[str]:
    """Whitespace-split, respecting double quotes and parentheses.

    Tokens inside ``"..."`` or ``(...)`` are kept whole. ``[...]`` is NOT a
    nesting context — list literals must appear within a parenthesised group
    (typically a waveform constructor) or be the entire ``for ... in``
    right-hand side (which is handled before tokenization).
    """
    tokens: list[str] = []
    current = ""
    in_q = False
    depth = 0
    for c in line:
        if c == '"' and depth == 0:
            in_q = not in_q
            current += c
        elif c == "(" and not in_q:
            depth += 1
            current += c
        elif c == ")" and not in_q:
            depth -= 1
            current += c
        elif c == " " and not in_q and depth == 0:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += c
    if current:
        tokens.append(current)
    return tokens


def _parse_waveform_expr(expr: str, variables: dict[str, Variable] | None = None) -> object:
    """Parse a ``Name(arg, ...)`` waveform constructor call.

    When ``variables`` is provided (always the case when called from a live
    parser, via :meth:`_Parser._parse_function_call`), bare identifiers in
    the argument list are resolved against the variable table — so
    ``Gaussian(amplitude=amp, ...)`` round-trips with ``amp`` as a
    :class:`Variable` rather than the string ``"amp"``. Without ``variables``
    (defensive default), identifiers fall through to the legacy string-fallback
    behaviour.
    """
    expr = expr.strip()
    pi = expr.index("(")
    cls_name = expr[:pi]
    cls = get_waveform_class(cls_name)
    if cls is None:
        msg = f"Unknown waveform type: {cls_name}"
        raise ParseError(msg)
    args_str = expr[pi + 1 : expr.rindex(")")]
    pos, kw = _parse_constructor_args(args_str, variables)
    return cls(**kw) if kw else cls(*pos)


def _parse_constructor_args(
    args_str: str,
    variables: dict[str, Variable] | None = None,
) -> tuple[list, dict]:
    pos: list = []
    kw: dict = {}
    for arg in _split_args(args_str):
        arg_stripped = arg.strip()
        if not arg_stripped:
            continue
        if "=" in arg_stripped and not arg_stripped.startswith('"') and "(" not in arg_stripped.split("=")[0]:
            k, _, v = arg_stripped.partition("=")
            kw[k.strip()] = _parse_arg(v.strip(), variables)
        else:
            pos.append(_parse_arg(arg_stripped, variables))
    return pos, kw


def _split_args(s: str) -> list[str]:
    parts: list[str] = []
    cur = ""
    depth = 0
    in_q = False
    for c in s:
        if c == '"':
            in_q = not in_q
            cur += c
        elif c in "([" and not in_q:
            depth += 1
            cur += c
        elif c in ")]" and not in_q:
            depth -= 1
            cur += c
        elif c == "," and depth == 0 and not in_q:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        parts.append(cur)
    return parts


def _parse_arg(val: str, variables: dict[str, Variable] | None = None) -> object:
    """Parse a single waveform-constructor argument.

    The :class:`~qprogram.Variable` resolution mirrors
    :meth:`_Parser.parse_value`: when the parser threads its variable table
    in (always, in live parsing), an identifier that matches a declared
    variable returns the :class:`Variable` instance, so waveforms with
    symbolic parameters (``Gaussian(amplitude=amp, ...)``) round-trip with
    their variable references intact. Without a table (legacy callers), the
    function falls back to returning the identifier as a string.
    """
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return _unescape_str(val[1:-1])
    if val == "true":
        return True
    if val == "false":
        return False
    if val.startswith("[") and val.endswith("]"):
        return np.array([_parse_number(v.strip()) for v in val[1:-1].split(",") if v.strip()])
    if "(" in val:
        return _parse_waveform_expr(val, variables)
    if variables is not None and val in variables:
        return variables[val]
    try:
        return _parse_number(val)
    except ValueError:
        return val
