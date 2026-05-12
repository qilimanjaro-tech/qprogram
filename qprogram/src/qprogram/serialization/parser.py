from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusNaming, BusRef, BusSchema, get_preset_class
from qprogram.crosstalk_matrix import CrosstalkMatrix
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
from qprogram.qprogram import QProgram
from qprogram.serialization.registry import get_operation_class, get_vendor_version, get_waveform_class
from qprogram.variable import _ID_RE

if TYPE_CHECKING:
    from qprogram.variable import Expression, Variable

FORMAT_VERSION = "1.0"


class ParseError(Exception):
    """Error during .qp file parsing."""

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
# Recursive-descent parser
# ---------------------------------------------------------------------------


_BUS_PATH_RE = re.compile(r"^(\w+)\[(\d+(?:,\d+)*)\]\.(\w+)$")
_SCHEMA_HEADER_RE = re.compile(r"^schema\s*:\s*(.*)$")
_SCHEMA_KIND_RE = re.compile(r'^(\w+)(?:\(\s*"(.+?)"\s*\))?$')
_ELEMENT_HEADER_RE = re.compile(r"^element\s+(\w+)\s*:\s*$")
_BUS_LINE_RE = re.compile(r"^(\w+)\s+info=(\S+)\s*$")


class _Parser:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()
        self._pos = 0
        self._program = QProgram()
        self._variables: dict[str, Variable] = {}
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
                        f"`require` declaration must specify a version: "
                        f"`require <vendor> <major.minor>`. Got: {line!r}"
                    )
                    raise ParseError(msg, self._pos + 1)
                _, vendor, file_version = tokens
                self._check_vendor_compat(vendor, file_version)
                self._required_vendors.add(vendor)
                self._pos += 1
            else:
                break

    def _check_vendor_compat(self, vendor: str, file_version: str) -> None:
        """Validate that the installed vendor extension is compatible.

        File versions use ``major.minor`` semantics: the installed extension
        must have the same major version, and a minor version >= the file's.
        """
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

    # -- schema declarations (preset one-liner or inline custom/dynamic) -----

    _INFO_CHANNELS: ClassVar[frozenset[str]] = frozenset({"single", "IQ"})
    _INFO_FLAGS: ClassVar[frozenset[str]] = frozenset({"acquires"})

    def _parse_schema_decl(self) -> None:
        """Parse the single ``schema`` declaration starting at the current line.

        One-liner forms (preset):
          ``schema: <kind>``                       — default naming
          ``schema: <kind>("<pattern>")``          — custom naming pattern

        Inline form (custom / dynamic):
          ``schema:``                              — body follows on indented lines
              ``naming: "<pattern>"``              (optional)
              ``element <elem>:``
                  ``<kind> info=<channel>[+acquires]``

        At most one schema per program — a second ``schema`` declaration in
        the same file is rejected.
        """
        if self._program.schema is not None:
            msg = "duplicate schema declaration — a program may have at most one schema"
            raise ParseError(msg, self._pos + 1)
        line = self._stripped()
        header = _SCHEMA_HEADER_RE.match(line)
        if not header:
            msg = f"invalid schema declaration: {line!r}"
            raise ParseError(msg, self._pos + 1)
        rest = header.group(1).strip()
        self._pos += 1
        if rest:
            self._build_preset_schema(rest)
        else:
            self._build_inline_schema()

    def _build_preset_schema(self, kind_expr: str) -> None:
        m = _SCHEMA_KIND_RE.match(kind_expr)
        if not m:
            msg = f"invalid schema kind expression: {kind_expr!r}"
            raise ParseError(msg, self._pos)
        kind, pattern = m.group(1), m.group(2)
        preset_cls = get_preset_class(kind)
        if preset_cls is None:
            msg = (
                f"unknown preset schema kind {kind!r}; use a built-in preset "
                f"or an inline `schema:` body for custom schemas"
            )
            raise ParseError(msg, self._pos)
        naming = BusNaming(pattern) if pattern is not None else None
        self._program._schema = preset_cls(naming=naming)  # noqa: SLF001

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
                msg = (
                    f"invalid bus declaration in schema body: {line!r}; "
                    f"expected `<kind> info=<channel>[+acquires]`"
                )
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
        """Parse ``info=channel[+flag[+flag...]]``.

        Channel must be ``single`` or ``IQ`` and appear exactly once. Currently
        the only supported flag is ``acquires``.
        """
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
        """Resolve ``element[index].kind`` against the program's schema.

        Returns ``None`` if the token doesn't match the path syntax (caller
        leaves the value as a plain string — the right behaviour for
        raw-string buses). Raises ``ParseError`` if the path looks valid but
        the schema rejects the element or bus kind, since that indicates a
        malformed file.
        """
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
                    msg = f"duplicate variable id '{var_id}'"
                    raise ParseError(msg, self._pos + 1)
                var = self._program.variable(var_id, **attrs)
                self._variables[var_id] = var
                self._pos += 1
            elif line.startswith("for ") or ("|" in line and "for " in line):
                self._parse_loop_or_parallel(parent, min_indent)
            elif line.startswith("average "):
                self._parse_average(parent, min_indent)
            elif line == "block:":
                self._parse_block_scope(parent, min_indent)
            else:
                op = self._parse_operation(line)
                if op is not None:
                    self._upgrade_busrefs(op)
                    parent.append(op)
                self._pos += 1

    # -- variable declarations ----------------------------------------------

    _VAR_ATTRS: ClassVar[frozenset[str]] = frozenset({"label", "units", "description"})

    def _parse_var_decl(self, line: str) -> tuple[str, dict[str, str]]:
        """Parse a ``var <id> [key="value"]...`` line.

        The id must be a Python-style identifier. Optional ``label``,
        ``units``, and ``description`` may appear in any order as quoted
        ``key="value"`` pairs. Returns ``(id, attrs)``.
        """
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

    # -- loops ---------------------------------------------------------------

    def _parse_loop_or_parallel(self, parent: Block, min_indent: int) -> None:
        line = self._stripped()
        line = line.removesuffix(":")
        loop_parts = [p.strip() for p in line.split("|")]
        loops = [self._parse_for_header(p) for p in loop_parts]
        self._pos += 1
        block: Block = loops[0] if len(loops) == 1 else Parallel(loops=loops)
        parent.append(block)
        self._parse_statements(block, min_indent + 2)

    def _parse_for_header(self, header: str) -> ForLoop | Loop:
        m = re.match(r"for\s+(\w+)\s+in\s+(.*)", header.strip())
        if not m:
            msg = f"Invalid for loop: {header}"
            raise ParseError(msg, self._pos + 1)
        var_name = m.group(1)
        if var_name not in self._variables:
            var = self._program.variable(var_name)
            self._variables[var_name] = var
        var = self._variables[var_name]
        rest = m.group(2).strip()
        if rest.startswith("range("):
            args = [_parse_number(a.strip()) for a in rest[6:].rstrip(")").split(",")]
            if len(args) == 2:
                return ForLoop(var, args[0], args[1], 1)
            if len(args) == 3:
                return ForLoop(var, args[0], args[1], args[2])
            msg = "range() expects 2 or 3 arguments"
            raise ParseError(msg, self._pos + 1)
        if rest.startswith("["):
            values = np.array([_parse_number(v.strip()) for v in rest.strip("[]").split(",")])
            return Loop(var, values)
        if rest.startswith("file("):
            path = rest[5:].rstrip(")").strip().strip('"')
            values = np.load(path)
            return Loop(var, values)
        msg = f"Unknown loop source: {rest}"
        raise ParseError(msg, self._pos + 1)

    def _parse_average(self, parent: Block, min_indent: int) -> None:
        line = self._stripped()
        m = re.match(r"average\s+(\d+)\s*:", line)
        if not m:
            msg = f"Invalid average: {line}"
            raise ParseError(msg, self._pos + 1)
        block = Average(shots=int(m.group(1)))
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2)

    def _parse_block_scope(self, parent: Block, min_indent: int) -> None:
        block = Block()
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2)

    # -- operations ----------------------------------------------------------

    def _parse_operation(self, line: str) -> object | None:
        tokens = _tokenize(line)
        if not tokens:
            return None
        op_name = tokens[0]
        vendor = None
        if "." in op_name:
            vendor, op_name = op_name.split(".", 1)

        # Vendor operation
        if vendor:
            op_cls = get_operation_class(vendor, op_name)
            if op_cls is None:
                return None
            return _construct_generic(op_cls, tokens[1:], self._variables)

        args = tokens[1:]

        if op_name == "play":
            return Play(bus=_uq(args[0]), waveform=self._resolve_wf(args[1]))
        if op_name == "measure":
            kw = _parse_kwargs(args[3:])
            return Measure(
                bus=_uq(args[0]),
                waveform=self._resolve_wf(args[1]),
                weights=self._resolve_wf(args[2]),
                save_adc=kw.get("save_adc", False),
            )
        if op_name == "wait":
            return Wait(bus=_uq(args[0]), duration=self._var_or_num(args[1]))
        if op_name == "sync":
            return Sync(buses=[_uq(a) for a in args] if args else None)
        if op_name == "set_frequency":
            return SetFrequency(bus=_uq(args[0]), frequency=self._var_or_num(args[1]))
        if op_name == "set_phase":
            return SetPhase(bus=_uq(args[0]), phase=self._var_or_num(args[1]))
        if op_name == "reset_phase":
            return ResetPhase(bus=_uq(args[0]))
        if op_name == "set_gain":
            return SetGain(bus=_uq(args[0]), gain=self._var_or_num(args[1]))
        if op_name == "set_offset":
            p1 = self._var_or_num(args[2]) if len(args) > 2 and "=" not in args[2] else None
            return SetOffset(bus=_uq(args[0]), offset_path0=self._var_or_num(args[1]), offset_path1=p1)
        if op_name == "set_parameter":
            kw = _parse_kwargs(args[3:])
            return SetParameter(
                alias=_uq(args[0]),
                parameter=_uq(args[1]),
                value=self._var_or_num(args[2]),
                channel_id=kw.get("channel_id"),
            )
        if op_name == "get_parameter":
            arrow = next((i for i, a in enumerate(args) if a == "->"), None)
            kw = _parse_kwargs(args[2:arrow] if arrow else args[2:])
            var_name = args[arrow + 1] if arrow is not None and arrow + 1 < len(args) else "result"
            if var_name not in self._variables:
                v = self._program.variable(var_name)
                self._variables[var_name] = v
            return GetParameter(
                variable=self._variables[var_name],
                alias=_uq(args[0]),
                parameter=_uq(args[1]),
                channel_id=kw.get("channel_id"),
            )
        if op_name == "set_crosstalk":
            return SetCrosstalk(crosstalk=CrosstalkMatrix())
        return None

    # -- resolution helpers --------------------------------------------------

    def _resolve_wf(self, token: str) -> object:
        token = token.strip()
        if token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        if "(" in token:
            return _parse_waveform_expr(token)
        return token

    def _var_or_num(self, token: str) -> Expression | int | float:
        token = token.strip()
        if token in self._variables:
            return self._variables[token]
        return _parse_number(token)


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


def _uq(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return _unescape_str(s[1:-1])
    return s


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
    """Parse the leading ``major.minor`` of a version string.

    Accepts ``"0.1"``, ``"1.0.3"``, etc. Raises ``ValueError`` if the format
    is not recognised. Patch and trailing components are ignored.
    """
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


def _tokenize(line: str) -> list[str]:
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


def _parse_waveform_expr(expr: str) -> object:
    expr = expr.strip()
    pi = expr.index("(")
    cls_name = expr[:pi]
    cls = get_waveform_class(cls_name)
    if cls is None:
        msg = f"Unknown waveform type: {cls_name}"
        raise ParseError(msg)
    args_str = expr[pi + 1 : expr.rindex(")")]
    pos, kw = _parse_constructor_args(args_str)
    return cls(**kw) if kw else cls(*pos)


def _parse_constructor_args(args_str: str) -> tuple[list, dict]:
    pos: list = []
    kw: dict = {}
    for arg in _split_args(args_str):
        arg_stripped = arg.strip()
        if not arg_stripped:
            continue
        if "=" in arg_stripped and not arg_stripped.startswith('"') and "(" not in arg_stripped.split("=")[0]:
            k, _, v = arg_stripped.partition("=")
            kw[k.strip()] = _parse_arg(v.strip())
        else:
            pos.append(_parse_arg(arg_stripped))
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


def _parse_arg(val: str) -> object:
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val == "true":
        return True
    if val == "false":
        return False
    if val.startswith("[") and val.endswith("]"):
        return np.array([_parse_number(v.strip()) for v in val[1:-1].split(",")])
    if "(" in val:
        return _parse_waveform_expr(val)
    try:
        return _parse_number(val)
    except ValueError:
        return val


def _parse_kwargs(tokens: list[str]) -> dict:
    result: dict = {}
    for t in tokens:
        if "=" in t:
            k, _, v = t.partition("=")
            result[k.strip()] = _parse_arg(v.strip())
    return result


def _construct_generic(cls: type, tokens: list[str], variables: dict[str, Variable]) -> object:
    sig = inspect.signature(cls.__init__)
    params = list(sig.parameters.values())[1:]  # skip self
    positional: list = []
    kw: dict = {}
    for token in tokens:
        token_stripped: str = token.strip()
        if "=" in token_stripped and not token_stripped.startswith('"'):
            k, _, v = token_stripped.partition("=")
            kw[k.strip()] = _parse_arg(v.strip())
        elif token_stripped in variables:
            # Bare identifier that names a declared variable.
            positional.append(variables[token_stripped])
        else:
            # Anything else (string literal, number, true/false, waveform expr).
            positional.append(_parse_arg(token_stripped))
    final: dict = {}
    for i, arg in enumerate(positional):
        if i < len(params):
            final[params[i].name] = arg
    final.update(kw)
    return cls(**final)
