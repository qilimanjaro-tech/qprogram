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
"""Parser for the ``.qp`` file format.

Header / ``require`` / metadata parsing is fixed-grammar; everything inside ``body:`` is dispatched
through the registries in `qprogram.serialization.registry`. New operations, blocks, and sweep
sources can be added by registration alone — no parser change required.

The parser exposes a small *parse context* API used by spec callbacks: `parse_value` for token
to typed-value conversion, `parse_error` for line-tagged errors, and
`get_or_declare_variable` for callbacks that need a target variable identifier.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.buses import BusNaming, BusRef, BusSchema, resolve_ref
from qprogram.errors import QProgramError, ValidationError, VendorActivationError
from qprogram.fragments import Fragment, bind_arguments
from qprogram.operations.call import Call
from qprogram.operations.operation import MeasurementOperation
from qprogram.qprogram import QProgram
from qprogram.result import MeasurementHandle
from qprogram.serialization import _specs as _core_specs
from qprogram.serialization._format import FORMAT_VERSION
from qprogram.serialization._specs import _parse_number
from qprogram.serialization.registry import (
    get_block_spec,
    get_operation_spec,
    get_sweep_source_class,
    get_vendor_version,
    get_waveform_class,
    known_sweep_sources,
    try_activate_vendor,
)
from qprogram.sweeps.builtin import Values
from qprogram.variable import _ID_RE, MeasurementRef

if TYPE_CHECKING:
    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation
    from qprogram.sweeps.source import SweepSource
    from qprogram.variable import (
        BinaryOperator,
        ComparisonOperator,
        Expression,
        LogicalBinaryOperator,
        UnaryOperator,
        Variable,
    )


class ParseError(QProgramError):
    """Error during ``.qp`` file parsing.

    Direct child of [`QProgramError`][qprogram.QProgramError], distinct from
    [`ValidationError`][qprogram.ValidationError] — validation runs on in-memory programs, parsing fails on malformed
    input text.

    Args:
        message (str): Human-readable error description.
        line_num (int, optional): 1-based line number of the offending input, or ``0`` for
            whole-file errors.
    """

    def __init__(self, message: str, line_num: int = 0) -> None:
        self.line_num = line_num
        super().__init__(f"Line {line_num}: {message}" if line_num else message)


class _QuotedStr(str):
    """A string value that arrived inside double quotes on the wire.

    On the wire, the quoting *is* the type distinction: quoted strings are plain values; bare
    ``element[index].kind`` tokens are bus paths. Tokenization erases the quotes, so this marker
    subclass preserves "was quoted" long enough for `_Parser._upgrade_busrefs` to know it
    must NOT promote the value to a [`BusRef`][qprogram.BusRef] — a raw-string bus that merely looks
    like a path (``"q[0].drive"``) stays the string the author wrote. Behaves exactly like ``str``
    everywhere else (equality, hashing, serialization), so instances may live in the AST.
    """

    __slots__ = ()


def loads(text: str, *, auto_activate: bool = True) -> QProgram:
    """Parse a ``.qp``-format string into a [`QProgram`][qprogram.QProgram].

    Args:
        text (str): The ``.qp`` document to parse.
        auto_activate (bool, optional): Whether a ``require <vendor>`` line whose extension is not
            imported yet triggers entry-point discovery (the ``qprogram.vendors`` group) — the
            installed package is imported on demand so the file is self-contained. Set ``False``
            to require that vendors be imported explicitly beforehand (no implicit imports).

    Returns:
        The reconstructed [`QProgram`][qprogram.QProgram], with its source map populated.

    Raises:
        ParseError: On malformed input, unknown registry entries, or a required vendor that is
            neither registered nor discoverable (and, when its extension is installed but broken,
            the wrapped [`VendorActivationError`][qprogram.VendorActivationError]).
        ValidationError: If a declaration the grammar accepts is rejected by the program it builds,
            such as a variable id that is a reserved ``.qp`` keyword.
        TypeError: If a constructor call in the file does not fit its class's signature — an inline
            waveform, or a sweep source nested inside a combinator's argument list.
    """
    return _Parser(text, auto_activate=auto_activate).parse()


def load(path: str, *, auto_activate: bool = True) -> QProgram:
    """Read a ``.qp`` file and parse it into a [`QProgram`][qprogram.QProgram].

    ``.qp`` files are always UTF-8, independent of the platform's locale.

    Args:
        path (str): Path to the ``.qp`` file.
        auto_activate (bool, optional): See `loads`.

    Returns:
        The reconstructed [`QProgram`][qprogram.QProgram].

    Raises:
        ParseError: On malformed input or unknown registry entries.
        OSError: If the file cannot be read.
        ValidationError: If a declaration the grammar accepts is rejected by the program it builds,
            such as a variable id that is a reserved ``.qp`` keyword.
        TypeError: If a constructor call in the file does not fit its class's signature — an inline
            waveform, or a sweep source nested inside a combinator's argument list.
    """
    return loads(Path(path).read_text(encoding="utf-8"), auto_activate=auto_activate)


# ---------------------------------------------------------------------------
# Module-level regexes
# ---------------------------------------------------------------------------


# The format's only line terminator, matching the ``_NL`` terminal in ``qp.lark``. Deliberately
# narrower than what `str.splitlines` breaks on — see `_split_lines`.
_LINE_BREAK_RE = re.compile(r"\r?\n")

_BUS_PATH_RE = re.compile(r"^(\w+)\[(\d+(?:,\d+)*)\]\.(\w+)$")
_ELEMENT_HEADER_RE = re.compile(r"^element\s+(\w+)\s*:\s*$")
_BUS_LINE_RE = re.compile(r"^(\w+)\s+info=(\S+)\s*$")
_FOR_HEADER_RE = re.compile(r"^for\s++(\w++)\s++in\s++(.*)$")
_FRAGMENT_HEADER_RE = re.compile(r"^fragment\s+([A-Za-z_]\w*)\s*\((.*)\)\s*:$", re.ASCII)
# A whole statement of the shape ``name(args)`` — a fragment call. Operations never take this
# form (their name is followed by whitespace-separated tokens) and block headers end with ``:``,
# so the shape is unambiguous at statement position.
_CALL_STMT_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$", re.ASCII)

# Operator alphabets — frozen sets so membership checks act as the
# accept/reject gates for the ``cast`` calls in `_parse_paren_expression`.
# Kept in lockstep with the ``Literal`` operator types in `qprogram.variable`.
_UNARY_OPS: frozenset[str] = frozenset({"+", "-"})
_BINARY_OPS: frozenset[str] = frozenset({"+", "-", "*", "/"})
_COMPARISON_OPS: frozenset[str] = frozenset({"==", "!=", "<", "<=", ">", ">="})
_LOGICAL_BINARY_OPS: frozenset[str] = frozenset({"and", "or"})


# ---------------------------------------------------------------------------
# Recursive-descent parser
# ---------------------------------------------------------------------------


class _Parser:
    """Recursive-descent parser for one ``.qp`` document.

    Carries the whole parse state: the input lines, the cursor, the program under construction, and
    the tables that give identifiers meaning — declared variables, measurement handles, and
    fragment definitions. An instance is single-use; `parse` walks the input top to bottom and
    returns the finished program.

    The instance doubles as the *parse context* handed to spec callbacks, which reach it through
    `parse_value`, `parse_error`, `get_or_declare_variable`,
    `declared_variable`, `get_or_create_handle`,
    `allocate_measurement_handle`, and the `line_num` property a callback reads when it
    builds an error of its own.

    Args:
        text (str): The ``.qp`` document to parse.
        auto_activate (bool, optional): Whether a ``require`` line may import an unregistered vendor
            extension through its ``qprogram.vendors`` entry point.
    """

    def __init__(self, text: str, *, auto_activate: bool = True) -> None:
        self._lines = _split_lines(text)
        self._pos = 0
        self._program = QProgram()
        self._variables: dict[str, Variable] = {}
        self._handles: dict[str, MeasurementHandle] = {}
        self._required_vendors: set[str] = set()
        self._auto_activate = auto_activate
        # Fragment definitions parsed so far, by name. Calls resolve against this table, which
        # enforces define-before-use (and therefore topological definition order) for free.
        self._fragment_defs: dict[str, Fragment] = {}
        self._body_parsed = False

    # -- public entry point --------------------------------------------------

    def parse(self) -> QProgram:
        """Parse the whole document and return the program it describes.

        The header and its ``require`` lines come first; the sections that follow — ``metadata:``,
        ``schema:``, ``fragment <name>(...):``, ``body:`` — are each dispatched to their own parser.

        Returns:
            The reconstructed program.

        Raises:
            ParseError: On a missing or unsupported header, a ``require`` line placed after the
                first section, an unrecognized top-level line, or any error inside a section.
            ValidationError: If a declaration the grammar accepts is rejected by the program being
                built, such as a variable id that is a reserved ``.qp`` keyword.
            TypeError: If a constructor call in the file does not fit its class's signature.
        """
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
            elif line.startswith("fragment ") or line == "fragment":
                self._parse_fragment_def()
            elif line == "body:":
                self._pos += 1
                self._body_parsed = True
                self._parse_body()
            elif line.startswith("require "):
                msg = "`require` declarations must appear directly after the header, before any section"
                raise ParseError(msg, self._pos + 1)
            else:
                # Silently skipping an unrecognized top-level line would hide typos
                # (`bodyy:`) behind an empty-but-valid program.
                msg = (
                    f"unexpected top-level line {line!r}; expected `metadata:`, `schema:`, `fragment ...:`, or `body:`"
                )
                raise ParseError(msg, self._pos + 1)
        return self._program

    # -- line helpers --------------------------------------------------------

    @property
    def line_num(self) -> int:
        """Current 1-indexed line number, for error messages from callbacks."""
        return self._pos + 1

    def _stripped(self) -> str:
        """Return the current line with any trailing comment and surrounding whitespace removed.

        Returns:
            The significant text of the current line, or ``""`` past the end of the input.
        """
        if self._pos >= len(self._lines):
            return ""
        line = self._lines[self._pos]
        ci = _find_comment(line)
        if ci >= 0:
            line = line[:ci]
        return line.strip()

    def _indent(self) -> int:
        """Return the indentation width of the current line.

        Returns:
            The number of leading whitespace characters, or ``0`` past the end of the input.
        """
        if self._pos >= len(self._lines):
            return 0
        raw = self._lines[self._pos]
        return len(raw) - len(raw.lstrip())

    # -- header & require ----------------------------------------------------

    def _parse_header(self) -> None:
        """Consume the leading ``#!QProgram <version>`` header, skipping any blank lines before it.

        Only the major component of the version is binding: a file whose major matches the running
        format version loads whatever its minor is.

        Raises:
            ParseError: If the header is missing or declares a different major format version.
        """
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
        """Consume the ``require <vendor> <major.minor>`` lines that follow the header.

        Scanning stops at the first line that is not a ``require`` declaration, which is what lets
        `parse` reject a ``require`` line that appears later in the file.

        Raises:
            ParseError: If a ``require`` line omits the version, or names a vendor that is
                unavailable or version-incompatible.
        """
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
        """Check one ``require`` line against the vendor extension registered in this environment.

        Majors must match exactly and the file's minor must be no newer than the installed
        extension's; a patch component is informational and ignored. When auto-activation is on and
        the vendor is not registered yet, its ``qprogram.vendors`` entry point is imported first so
        the comparison runs against the extension the file expects.

        Args:
            vendor (str): Vendor name from the ``require`` line.
            file_version (str): Version the file requires, as ``major.minor``.

        Raises:
            ParseError: If the vendor cannot be resolved, either version is malformed, the majors
                differ, or the file needs a newer minor than the installed extension provides.
        """
        installed = get_vendor_version(vendor)
        if installed is None and self._auto_activate:
            # The extension isn't imported yet — try discovering and importing it via its
            # `qprogram.vendors` entry point so the file stays self-contained.
            try:
                try_activate_vendor(vendor)
            except VendorActivationError as e:
                raise ParseError(str(e), self._pos + 1) from e
            installed = get_vendor_version(vendor)
        if installed is None:
            hint = (
                f"install the package that declares the 'qprogram.vendors' entry point for "
                f"'{vendor}', or import the extension before loading"
                if self._auto_activate
                else f"auto-activation is disabled; import the extension before loading "
                f"(e.g. `import qprogram_{vendor}`)"
            )
            msg = (
                f"file requires vendor '{vendor}' {file_version} but no matching extension is "
                f"registered in this environment — {hint}"
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
        """Parse the ``metadata:`` section into the program's label and description.

        Both values must be quoted strings, and are unescaped with the exact inverse of the writer's
        escaping, so a label holding quotes or backslashes survives the round trip. Unknown keys are
        tolerated for forward compatibility but their values must still be well-formed.

        Raises:
            ParseError: On a line that is not shaped ``key: value``, or a ``label`` /
                ``description`` value that is not a quoted string.
        """
        while self._pos < len(self._lines):
            if self._indent() < 2 and self._stripped():
                break
            line = self._stripped()
            if not line:
                self._pos += 1
                continue
            if ":" not in line:
                msg = f"invalid metadata line {line!r}; expected `key: value`"
                raise ParseError(msg, self._pos + 1)
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key in {"label", "description"}:
                if len(val) < 2 or not (val.startswith('"') and val.endswith('"')):
                    msg = f"metadata {key!r} must be a quoted string, got: {val!r}"
                    raise ParseError(msg, self._pos + 1)
                text = _unescape_str(val[1:-1])
                if key == "label":
                    self._program.label = text
                else:
                    self._program.description = text
            self._pos += 1

    # -- schema declaration (inline element/bus declarations) ---------------

    _INFO_CHANNELS: ClassVar[frozenset[str]] = frozenset({"single", "IQ"})
    _INFO_FLAGS: ClassVar[frozenset[str]] = frozenset({"acquires"})

    def _parse_schema_decl(self) -> None:
        """Parse the single ``schema:`` block at the current position.

        The file format has one schema spelling: the inline ``element`` / bus declarations. The typed
        Python factories ([`BusSchema.transmon`][qprogram.BusSchema.transmon] and friends) are construction-time
        conveniences that record the same ``element`` / bus data, and the writer emits that
        structural form for every schema.

        Raises:
            ParseError: If the program already declared a schema, the header is not exactly
                ``schema:``, or the indented body is malformed.
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
        """Build the program's schema from the indented body of a ``schema:`` section.

        The body holds an optional quoted ``naming:`` pattern followed by one ``element <name>:``
        header per element, each with its own indented bus declarations. The result is a dynamic
        (untyped) [`BusSchema`][qprogram.BusSchema], which carries everything the wire format records.

        Raises:
            ParseError: On an unquoted ``naming`` value, a line that is neither ``naming:`` nor an
                element header, a malformed bus declaration, or a schema with no elements.
        """
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
        self._program._schema = schema  # ruff: ignore[private-member-access]

    def _parse_inline_element_buses(self) -> dict[str, tuple[str, bool]]:
        """Parse the ``<kind> info=<channel>[+acquires]`` lines under one ``element`` header.

        Returns:
            The element's bus kinds mapped to their ``(channel, acquires)`` pairs, in declaration
            order.

        Raises:
            ParseError: On a line that is not a bus declaration, or a bus kind declared twice for
                the same element.
        """
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
        """Decode a bus ``info=`` value into its channel and its acquisition flag.

        The value is a ``+``-joined token list carrying exactly one channel (``single`` or ``IQ``)
        and, for a bus with an ADC, the ``acquires`` flag.

        Args:
            value (str): The text after ``info=``, for example ``"IQ+acquires"``.

        Returns:
            The ``(channel, acquires)`` pair the schema records for this bus.

        Raises:
            ParseError: If the value is empty, names more than one channel, repeats a flag, holds an
                unknown token, or names no channel at all.
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

    def _upgrade_busrefs(self, op: Operation) -> None:
        """Replace bus-path strings on an operation with resolved [`BusRef`][qprogram.BusRef] values.

        Only the attributes the operation declares in `Operation.BUS_ATTRS` are considered —
        promoting *every* string attribute would mangle legitimate quoted strings that merely look
        like paths (e.g. a vendor ``myplatform.set_parameter`` alias of ``"cluster[0].module"``,
        whose op declares ``BUS_ATTRS = ()``). Within a bus attribute, a string that does not match
        the path syntax is left alone: it is a raw-string bus, which opts out of schema validation.
        ``list``-shaped attributes (``Sync.targets``) are handled element-wise. A program with no
        schema keeps every bus exactly as it was written.

        Args:
            op (Operation): Operation whose bus attributes are promoted in place.

        Raises:
            ParseError: If a path-shaped token does not resolve against the program's schema.
        """
        if self._program.schema is None:
            return
        for key in type(op).BUS_ATTRS:
            value = getattr(op, key, None)
            if isinstance(value, str) and not isinstance(value, (BusRef, _QuotedStr)):
                ref = self._resolve_bus_path(value)
                if ref is not None:
                    setattr(op, key, ref)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str) and not isinstance(item, (BusRef, _QuotedStr)):
                        ref = self._resolve_bus_path(item)
                        if ref is not None:
                            value[i] = ref

    def _resolve_bus_path(self, token: str) -> BusRef | None:
        """Resolve an ``element[index].kind`` token against the program's schema.

        Args:
            token (str): Candidate bus path, e.g. ``q[0].drive`` or ``coupler[0,1].flux``.

        Returns:
            The typed bus reference, or ``None`` when the program has no schema or the token is not
            path-shaped.

        Raises:
            ParseError: If the token is path-shaped but names an element, index, or bus kind the
                schema does not define.
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
            ref = resolve_ref(schema, element, index, kind)
        except (AttributeError, KeyError) as e:
            msg = f"bus path {token!r} does not resolve against the program schema: {e}"
            raise ParseError(msg, self._pos + 1) from e
        if not isinstance(ref, BusRef):  # pragma: no cover — defensive
            msg = f"bus path {token!r} did not yield a BusRef"
            raise ParseError(msg, self._pos + 1)
        return ref

    # -- fragment definitions --------------------------------------------------

    def _parse_fragment_def(self) -> None:
        """Parse one ``fragment <name>(<params>):`` section into a [`Fragment`][qprogram.Fragment].

        The fragment body is parsed with the same statement machinery as ``body:``, under a
        swapped scope: ``var`` declarations land on the fragment, identifiers resolve to the
        fragment's parameters and locals, and measurement auto-naming counts within the fragment.
        The host program's schema is shared so bus paths in the fragment body resolve.

        Raises:
            ParseError: If the section appears after ``body:``, the header is malformed, the name is
                already defined, a parameter id is invalid, or the body fails to parse.
        """
        line = self._stripped()
        if self._body_parsed:
            msg = "fragment definitions must appear before the `body:` section"
            raise ParseError(msg, self._pos + 1)
        m = _FRAGMENT_HEADER_RE.match(line)
        if not m:
            msg = f"invalid fragment header {line!r}; expected `fragment <name>(<param>, ...):`"
            raise ParseError(msg, self._pos + 1)
        name, params_text = m.groups()
        if name in self._fragment_defs:
            msg = f"duplicate fragment definition {name!r}"
            raise ParseError(msg, self._pos + 1)
        try:
            frag = Fragment(name)
            if params_text.strip():
                for raw in params_text.split(","):
                    param_id = raw.strip()
                    if not _ID_RE.match(param_id):
                        msg = f"invalid fragment parameter {param_id!r}: must match [A-Za-z_][A-Za-z0-9_]*"
                        raise ParseError(msg, self._pos + 1)
                    frag.parameter(param_id)
        except ValidationError as e:
            raise ParseError(str(e), self._pos + 1) from e
        # Share the host schema so `q[0].drive` paths inside the fragment body resolve. Fragments
        # appear after the (optional) `schema:` section in writer output; hand-written files that
        # use paths before declaring a schema keep them as raw strings.
        frag._schema = self._program.schema  # ruff: ignore[private-member-access]
        self._fragment_defs[name] = frag
        self._program._fragments[name] = frag  # ruff: ignore[private-member-access]
        self._pos += 1
        # Scope swap: the fragment is itself a QProgram, so every statement helper (var decls,
        # measurement allocation, block parsing) works unchanged against it.
        saved = (self._program, self._variables, self._handles)
        self._program = frag
        self._variables = {p.id: p for p in frag.params}
        self._handles = {}
        try:
            self._parse_statements(frag._body, min_indent=2)  # ruff: ignore[private-member-access]
        finally:
            self._program, self._variables, self._handles = saved

    # -- body ----------------------------------------------------------------

    def _parse_body(self) -> None:
        """Parse the ``body:`` section into the program's top-level block.

        This is the only scope whose nodes are recorded in the source map, so it starts the walk
        with the root path ``()``.

        Raises:
            ParseError: If any statement in the body is malformed.
            ValidationError: If a variable declared in the body is rejected by the program, such as
                an id that is a reserved ``.qp`` keyword.
            TypeError: If a constructor call in the body does not fit its class's signature.
        """
        self._parse_statements(self._program._body, min_indent=2, path=())  # ruff: ignore[private-member-access]

    def _record_source(self, path: tuple[int | str, ...] | None, line_num: int) -> None:
        """Record ``path → line`` in the program's source map (no-op outside the body scope).

        Args:
            path (tuple[int | str, ...] | None): Structural address of the node (see
                `qprogram.paths`); ``None`` marks a node outside the mapped ``body:`` scope.
            line_num (int): 1-based line the node was parsed from.
        """
        if path is not None:
            self._program._qp_source_map[path] = line_num  # ruff: ignore[private-member-access]

    def _child_path(self, path: tuple[int | str, ...] | None, parent: Block) -> tuple[int | str, ...] | None:
        """Return the path of the *next* element appended to ``parent`` (call before ``append``).

        Args:
            path (tuple[int | str, ...] | None): Structural address of ``parent``, or ``None``
                outside the mapped ``body:`` scope.
            parent (Block): Block the next element will be appended to.

        Returns:
            The child's structural path, or ``None`` when ``path`` is ``None``.
        """
        if path is None:
            return None
        return (*path, len(parent.elements))

    def _parse_statements(self, parent: Block, min_indent: int, path: tuple[int | str, ...] | None = None) -> None:
        """Walk a block's children, dispatching by the first significant token.

        Order matters: ``var`` declarations come first because they share no prefix with block
        keywords. ``for`` introduces loops and parallel loop compositions, whose sources resolve
        through the sweep-source registry. All other ``<keyword>:`` lines hit the block registry.
        A whole statement shaped ``name(args)`` is a fragment call — no operation takes that form.
        Anything left is an operation: vendor-prefixed lookup followed by core lookup.

        Args:
            parent (Block): Block the parsed statements are appended to.
            min_indent (int): Indentation width of this block's body; the first line indented less
                ends the block.
            path (tuple[int | str, ...] | None): Structural address of ``parent`` (see
                `qprogram.paths`); when given, every appended child's path → 1-based line is
                recorded in the program's source map. Fragment bodies pass ``None`` — only the
                ``body:`` section is mapped.

        Raises:
            ParseError: On a duplicate variable id, an unknown block keyword or operation, a
                malformed statement, or any error raised while parsing a nested block.
            ValidationError: If a ``var`` declaration names an id the program rejects, such as a
                reserved ``.qp`` keyword.
            TypeError: If a constructor call in a statement does not fit its class's signature.
        """
        while self._pos < len(self._lines):
            line = self._stripped()
            indent = self._indent()
            if not line:
                self._pos += 1
                continue
            if indent < min_indent:
                break
            if line == "var" or line.startswith("var "):
                var_id, attrs = self._parse_var_decl(line)
                if var_id in self._variables:
                    msg = f"duplicate variable id {var_id!r}"
                    raise ParseError(msg, self._pos + 1)
                var = self._program.variable(var_id, **attrs)
                self._variables[var_id] = var
                self._pos += 1
                continue
            if self._try_parse_block_header(parent, line, min_indent, path):
                continue
            call_match = _CALL_STMT_RE.match(line)
            if call_match:
                self._record_source(self._child_path(path, parent), self._pos + 1)
                parent.append(self._parse_call_statement(call_match))
                self._pos += 1
                continue
            op = self._parse_operation(line)
            self._upgrade_busrefs(op)
            self._record_source(self._child_path(path, parent), self._pos + 1)
            parent.append(op)
            # Track measurement handles so subsequent conditions can
            # resolve ``<name>.<field>`` references.
            if isinstance(op, MeasurementOperation):
                # The op's canonical handle was already obtained via
                # ctx.get_or_create_handle(name) inside the measurement
                # op's own parse callback (see
                # _specs.make_measurement_op_parse). Re-publishing here is
                # idempotent (the same instance) and covers a vendor op
                # that skips that callback.
                self._handles[op.name] = op.handle
            self._pos += 1

    def _parse_call_statement(self, match: re.Match[str]) -> Call:
        """Parse a bare ``<name>(<args>)`` statement into a `Call`.

        Arguments follow the Python calling convention (positional in parameter order, then
        ``key=value`` keywords) and accept the same token shapes as operation arguments: numbers,
        quoted strings, bus paths, identifiers (variables/parameters), parenthesized expressions,
        and inline waveform constructors. The called fragment must already be defined, which is what
        makes a ``.qp`` file list fragment definitions in dependency order.

        Args:
            match (re.Match[str]): Match of the call-statement pattern, carrying the fragment name
                and the raw argument text.

        Returns:
            The call node, with every fragment parameter bound to an argument.

        Raises:
            ParseError: If the name is not a defined fragment, a keyword argument is repeated, a
                positional argument follows a keyword one, or the arguments do not bind to the
                fragment's parameters.
        """
        name, args_text = match.groups()
        frag = self._fragment_defs.get(name)
        if frag is None:
            if get_waveform_class(name) is not None:
                msg = (
                    f"waveform constructor {name!r} cannot stand alone as a statement; waveforms "
                    f'appear as operation arguments (e.g. `play "bus" {name}(...)`)'
                )
            else:
                msg = (
                    f"unknown fragment {name!r}; fragments must be defined in a "
                    f"`fragment {name}(...):` section before use"
                )
            raise ParseError(msg, self._pos + 1)
        pos_args: list[object] = []
        kw_args: dict[str, object] = {}
        for raw in _split_args(args_text):
            arg = raw.strip()
            if not arg:
                continue
            # Same kwarg heuristic as waveform constructor args: a `key=` prefix that isn't
            # the start of a quoted string or a parenthesized/bracketed expression.
            if "=" in arg and not arg.startswith('"') and "(" not in arg.split("=")[0]:
                key, _, val = arg.partition("=")
                key = key.strip()
                if key in kw_args:
                    msg = f"fragment call {name!r}: duplicate keyword argument {key!r}"
                    raise ParseError(msg, self._pos + 1)
                kw_args[key] = self._parse_call_argument(val.strip())
            else:
                if kw_args:
                    msg = f"fragment call {name!r}: positional argument after keyword argument"
                    raise ParseError(msg, self._pos + 1)
                pos_args.append(self._parse_call_argument(arg))
        try:
            bound = bind_arguments(frag, tuple(pos_args), kw_args)
        except ValidationError as e:
            raise ParseError(str(e), self._pos + 1) from e
        # Mirror QProgram.call's registration on the current scope (host program or enclosing
        # fragment) — a no-op for top-level calls, whose fragment registered at definition.
        self._program._fragments.setdefault(frag.name, frag)  # ruff: ignore[private-member-access]
        return Call(fragment=frag, arguments=bound)

    def _parse_call_argument(self, token: str) -> object:
        """Parse one fragment-call argument.

        A bare path-shaped token promotes to a [`BusRef`][qprogram.BusRef] exactly as it does in an
        operation's bus attribute, so a fragment can take a bus as a parameter.

        Args:
            token (str): The argument text.

        Returns:
            The typed argument value: a bus reference, number, string, variable, expression, or
            waveform.

        Raises:
            ParseError: If the token cannot be decoded, or a path-shaped token does not resolve
                against the program's schema.
        """
        value = self.parse_value(token)
        if isinstance(value, str) and not isinstance(value, (BusRef, _QuotedStr)):
            ref = self._resolve_bus_path(value)
            if ref is not None:
                return ref
        return value

    def _try_parse_block_header(
        self,
        parent: Block,
        line: str,
        min_indent: int,
        path: tuple[int | str, ...] | None = None,
    ) -> bool:
        """Return ``True`` when ``line`` is a block header, having parsed the block and its body.

        The trailing colon marks a header; the leading keyword then selects the family: ``for`` for
        loops and parallel compositions, ``if`` for a conditional chain, and anything else is looked
        up in the block registry — which is how a vendor block becomes parseable by registration
        alone.

        Args:
            parent (Block): Block the new block is appended to.
            line (str): The statement text, comments already stripped.
            min_indent (int): Indentation width of ``parent``'s body; the new block's own body is
                indented two columns further.
            path (tuple[int | str, ...] | None): Structural address of ``parent``, or ``None``
                outside the mapped ``body:`` scope.

        Returns:
            ``True`` if the line was a block header and the block was parsed, ``False`` if the line
            is not a header at all.

        Raises:
            ParseError: On an ``elif`` / ``else`` with no preceding ``if`` at the same indent, an
                unregistered block keyword, or a malformed header.
        """
        if not line.endswith(":"):
            return False
        header = line[:-1].rstrip()
        # Loop family: ``for`` heads either a single loop or a parallel of loops.
        if header.startswith("for ") or ("|" in header and "for " in header):
            self._parse_loop_or_parallel(parent, header, min_indent, path)
            return True
        # Conditional family: ``if`` opens a chain; ``elif``/``else`` here
        # without a preceding ``if`` at the same level is a parse error
        # (the well-formed case is handled inside _parse_conditional).
        if header.startswith("if ") or header == "if":
            self._parse_conditional(parent, header, min_indent, path)
            return True
        if header.startswith("elif ") or header in {"elif", "else"}:
            msg = f"{header.split()[0]!r} without a preceding `if:` at the same indent level"
            raise ParseError(msg, self._pos + 1)
        # Keyword-led block: leading word maps to a registered BlockSpec.
        first_token, _, rest = header.partition(" ")
        spec = get_block_spec(first_token)
        if spec is None:
            # The line is shaped like a block header (trailing ``:``) but the keyword is
            # unregistered — silently skipping it would drop the whole indented body.
            msg = (
                f"unknown block keyword {first_token!r}; registered blocks, `for ... in ...` "
                f"loops, and `if`/`elif`/`else` are the only valid `<header>:` forms"
            )
            raise ParseError(msg, self._pos + 1)
        tokens = _tokenize(rest) if rest.strip() else []
        block: Block = spec.parse_header(tokens, self) if spec.parse_header is not None else spec.cls()
        block_path = self._child_path(path, parent)
        self._record_source(block_path, self._pos + 1)
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2, block_path)
        return True

    def _parse_conditional(
        self,
        parent: Block,
        header: str,
        min_indent: int,
        path: tuple[int | str, ...] | None = None,
    ) -> None:
        """Parse an ``if`` block plus any ``elif`` / ``else`` continuation arms.

        Each arm is ``<keyword> [<condition>]:`` followed by an indented
        body. The chain stops at the first non-``elif``/``else`` line at
        the same indent level (or any line that outdents).

        Args:
            parent (Block): Block the conditional is appended to.
            header (str): The ``if`` header without its trailing colon.
            min_indent (int): Indentation width of the chain's headers; arm bodies are indented two
                columns further.
            path (tuple[int | str, ...] | None): Structural address of ``parent``, or ``None``
                outside the mapped ``body:`` scope.

        Raises:
            ParseError: If ``if`` carries no condition, an ``elif`` follows the ``else``, a second
                ``else`` appears in the chain, or an arm fails to parse.
        """
        if header == "if":
            msg = "`if` requires a condition: `if <expr>:`"
            raise ParseError(msg, self._pos + 1)
        cond = Conditional()
        cond_path = self._child_path(path, parent)
        self._record_source(cond_path, self._pos + 1)
        parent.append(cond)
        self._parse_conditional_arm(cond, header, min_indent, keyword="if", cond_path=cond_path)
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
                self._parse_conditional_arm(cond, arm_header, min_indent, keyword="elif", cond_path=cond_path)
                continue
            if arm_header == "else":
                if cond.else_body is not None:
                    msg = "multiple `else` arms in the same conditional chain"
                    raise ParseError(msg, self._pos + 1)
                self._parse_conditional_else(cond, min_indent, cond_path=cond_path)
                continue
            break

    def _parse_conditional_arm(
        self,
        cond: Conditional,
        header: str,
        min_indent: int,
        *,
        keyword: str,
        cond_path: tuple[int | str, ...] | None = None,
    ) -> None:
        """Parse one ``if``/``elif`` arm: condition plus indented body.

        Args:
            cond (Conditional): Conditional the arm is appended to.
            header (str): Arm header without its trailing colon.
            min_indent (int): Indentation width of the arm header.
            keyword (str): ``"if"`` or ``"elif"`` — the keyword the condition follows.
            cond_path (tuple[int | str, ...] | None): Structural address of ``cond``, or ``None``
                outside the mapped ``body:`` scope.

        Raises:
            ParseError: If the condition is missing or is not a well-formed expression.
        """
        expr_text = header[len(keyword) + 1 :].strip()
        if not expr_text:
            msg = f"`{keyword}` requires a condition"
            raise ParseError(msg, self._pos + 1)
        # Reuse the parenthesized-expression parser for the Comparison
        # shape; the writer omits the outer parens for top-level conditions,
        # so synthesize them back.
        wrapped = expr_text if expr_text.startswith("(") and expr_text.endswith(")") else f"({expr_text})"
        condition = self._parse_paren_expression(wrapped)
        arm_body = self._program._body.__class__()  # ruff: ignore[private-member-access]  # build a bare Block instance
        arm_path = None if cond_path is None else (*cond_path, f"arm:{len(cond.arms)}")
        self._record_source(arm_path, self._pos + 1)
        cond.arms.append((condition, arm_body))
        self._pos += 1
        self._parse_statements(arm_body, min_indent + 2, arm_path)

    def _parse_conditional_else(
        self,
        cond: Conditional,
        min_indent: int,
        *,
        cond_path: tuple[int | str, ...] | None = None,
    ) -> None:
        """Parse the terminal ``else:`` arm.

        Args:
            cond (Conditional): Conditional the else body is attached to.
            min_indent (int): Indentation width of the ``else:`` header.
            cond_path (tuple[int | str, ...] | None): Structural address of ``cond``, or ``None``
                outside the mapped ``body:`` scope.

        Raises:
            ParseError: If a statement in the else body is malformed.
        """
        else_body = self._program._body.__class__()  # ruff: ignore[private-member-access]
        else_path = None if cond_path is None else (*cond_path, "else")
        self._record_source(else_path, self._pos + 1)
        cond.else_body = else_body
        self._pos += 1
        self._parse_statements(else_body, min_indent + 2, else_path)

    def _parse_loop_or_parallel(
        self,
        parent: Block,
        header: str,
        min_indent: int,
        path: tuple[int | str, ...] | None = None,
    ) -> None:
        """Parse a ``for`` header — one loop, or several composed with ``|`` — together with its body.

        A single header builds a [`Sweep`][qprogram.blocks.Sweep]; a ``|``-composed header builds a
        [`Parallel`][qprogram.blocks.Parallel] of sweeps that advance in lockstep. The composed loop headers
        share the parallel block's source line, so each maps to it.

        Args:
            parent (Block): Block the loop or parallel is appended to.
            header (str): The header text without its trailing colon.
            min_indent (int): Indentation width of the header; the body is indented two columns
                further.
            path (tuple[int | str, ...] | None): Structural address of ``parent``, or ``None``
                outside the mapped ``body:`` scope.

        Raises:
            ParseError: On a malformed ``for`` header, an unknown sweep source, or composed loops
                whose lengths differ.
            ValidationError: If a loop variable declared by the header is rejected by the program,
                such as an id that is a reserved ``.qp`` keyword.
            TypeError: If a sweep source nested inside a combinator's argument list is given
                arguments that do not fit its signature.
        """
        loop_parts = [p.strip() for p in header.split("|")]
        loops: list[Sweep] = [self._parse_for_header(part) for part in loop_parts]
        if len(loops) == 1:
            block: Block = loops[0]
        else:
            try:
                block = Parallel(loops=loops)
            except ValidationError as e:
                # Mismatched iteration counts etc. — surface with the line number.
                raise ParseError(str(e), self._pos + 1) from e
        block_path = self._child_path(path, parent)
        self._record_source(block_path, self._pos + 1)
        if isinstance(block, Parallel) and block_path is not None:
            # Composed loop headers live on the same source line as the parallel header.
            for i in range(len(block.loops)):
                self._record_source((*block_path, f"loop:{i}"), self._pos + 1)
        parent.append(block)
        self._pos += 1
        self._parse_statements(block, min_indent + 2, block_path)

    def _parse_for_header(self, header: str) -> Sweep:
        """Parse a single ``for <var> in <source>`` header into a [`Sweep`][qprogram.blocks.Sweep].

        The loop variable is declared on demand, so a file may drive a loop with a variable that has
        no ``var`` declaration of its own.

        Args:
            header (str): Header text without its trailing colon.

        Returns:
            The sweep block for this loop, with its variable bound to the parsed source.

        Raises:
            ParseError: If the header is not shaped ``for <var> in <source>``, the source is
                unknown, or the sweep rejects the variable/source pair.
            ValidationError: If the loop variable is declared on demand and the program rejects its
                id, such as a reserved ``.qp`` keyword.
            TypeError: If a sweep source nested inside a combinator's argument list is given
                arguments that do not fit its signature.
        """
        m = _FOR_HEADER_RE.match(header.strip())
        if not m:
            msg = f"invalid for loop header: {header!r}"
            raise ParseError(msg, self._pos + 1)
        var = self.get_or_declare_variable(m.group(1))
        source = self._parse_sweep_source(m.group(2).strip())
        try:
            return Sweep(variable=var, source=source)
        except ValidationError as e:
            raise ParseError(str(e), self._pos + 1) from e

    def _parse_sweep_source(self, text: str) -> SweepSource:
        """Parse a sweep source: a bracket literal, or a registered ``Name(args)`` constructor.

        The bracket form is sugar for [`Values`][qprogram.Values]; everything else resolves
        through the sweep-source registry and is constructed from its signature, exactly as a waveform
        constructor is. Nested sources inside a combinator come back through ``_parse_arg``, so
        ``Concat(sources=[Rotate(source=[...], by=1)])`` works at any depth.

        Args:
            text (str): The source text, e.g. ``Range(start=0, stop=10, step=1)`` or ``[1, 2, 3]``.

        Returns:
            The sweep source the loop iterates.

        Raises:
            ParseError: If the token is neither a bracket literal nor a ``Name(args)`` call, the
                name is not a registered sweep source, or the outermost constructor rejects the
                arguments.
            TypeError: If a source nested inside a combinator's argument list is given arguments
                that do not fit its signature — only the outermost constructor's failure is wrapped
                in a `ParseError`.
        """
        if text.startswith("["):
            try:
                return Values(_parse_list_literal(text, self._variables))
            except ValidationError as e:
                raise ParseError(str(e), self._pos + 1) from e
        if "(" not in text:
            msg = f"unknown sweep source {text!r}; expected `Name(args)` or `[values]`"
            raise ParseError(msg, self._pos + 1)
        name = text[: text.index("(")].strip()
        cls = get_sweep_source_class(name)
        if cls is None:
            known = sorted(known_sweep_sources())
            msg = f"unknown sweep source {name!r}; registered sources are {known}"
            raise ParseError(msg, self._pos + 1)
        args_text = text[text.index("(") + 1 : text.rindex(")")]
        pos, kw = _parse_constructor_args(args_text, self._variables)
        try:
            return cls(*pos, **kw)
        except (ValidationError, TypeError) as e:
            msg = f"cannot construct sweep source {name}: {e}"
            raise ParseError(msg, self._pos + 1) from e

    # -- variable declarations ----------------------------------------------

    _VAR_ATTRS: ClassVar[frozenset[str]] = frozenset({"label", "units", "description"})

    def _parse_var_decl(self, line: str) -> tuple[str, dict[str, str]]:
        """Parse a ``var <id> [label="..."] [units="..."] [description="..."]`` declaration.

        Args:
            line (str): The declaration line, comments already stripped.

        Returns:
            A ``(var_id, attrs)`` pair, where ``attrs`` holds the unescaped keyword values ready to
            pass to [`QProgram.variable`][qprogram.QProgram.variable].

        Raises:
            ParseError: If the identifier is missing or does not match ``[A-Za-z_][A-Za-z0-9_]*``,
                or an attribute is unknown, repeated, or not a quoted string.
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

    # -- operations ----------------------------------------------------------

    def _parse_operation(self, line: str) -> Operation:
        """Dispatch an operation line to its registered spec.

        Vendor operations carry a dotted prefix; core operations do not. A spec may bring its own
        parse callback; every other operation is reconstructed from its ``__init__`` signature.

        Args:
            line (str): The operation line, comments already stripped.

        Returns:
            The parsed operation, its bus attributes still plain strings — `_upgrade_busrefs`
            promotes them.

        Raises:
            ParseError: If no operation is registered under the line's leading keyword. Skipping
                such a line would load a *different* program, so a typo'd op — or a vendor op whose
                extension is not importable — must be loud, not absent.
        """
        tokens = _tokenize(line)
        if not tokens:
            msg = "empty operation line"
            raise ParseError(msg, self._pos + 1)
        op_name = tokens[0]
        vendor: str | None = None
        if "." in op_name:
            vendor, op_name = op_name.split(".", 1)
        spec = get_operation_spec(vendor, op_name)
        if spec is None:
            if vendor is not None:
                msg = (
                    f"unknown vendor operation {vendor}.{op_name!r}: no operation is registered "
                    f"under that name. Import the {vendor!r} extension package before loading, "
                    f"and check the file's `require {vendor} <x.y>` declaration."
                )
            elif get_block_spec(op_name) is not None:
                msg = f"{op_name!r} is a block keyword — block headers need a trailing colon: `{line}:`"
            else:
                msg = f"unknown operation {op_name!r}: no core operation is registered under that name"
            raise ParseError(msg, self._pos + 1)
        if spec.parse is not None:
            return spec.parse(tokens[1:], self)
        return _core_specs.default_parse_operation(spec, tokens[1:], self)

    # -- callbacks exposed to spec functions (the "parse context") -----------

    def parse_value(self, token: str) -> object:
        """Decode a single ``.qp`` token into a typed value.

        Recognizes quoted strings, booleans, ``null``, list literals (as plain Python lists —
        consumers that want arrays convert themselves), dict literals (string keys), inline
        waveform constructors, numeric literals, and references to variables already declared
        in the file. Anything that doesn't match any of those is returned as a plain string —
        this is how bus-path tokens (``q[0].drive``) flow through unchanged until
        ``_upgrade_busrefs`` promotes them to typed [`BusRef`][qprogram.BusRef] instances.

        Args:
            token (str): One ``.qp`` token, as produced by `_tokenize`.

        Returns:
            The decoded value: a bool, ``None``, a number, a list, a dict, a waveform, a sweep
            source (it shares the constructor shape), an [`Expression`][qprogram.Expression] node, a
            [`MeasurementRef`][qprogram.MeasurementRef], a declared [`Variable`][qprogram.Variable], or a string.

        Raises:
            ParseError: If the token is empty, or a compound token (expression, dict literal,
                function call) is malformed.
            TypeError: If a constructor call in the token does not fit its class's signature.
        """
        tok = token.strip()
        if not tok:
            msg = "empty argument token"
            raise ParseError(msg, self._pos + 1)
        if tok.startswith('"') and tok.endswith('"'):
            # _QuotedStr marks "this was quoted on the wire" so the bus-path
            # promotion pass leaves it alone. Plain str for every consumer.
            return _QuotedStr(_unescape_str(tok[1:-1]))
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok == "null":
            return None
        if tok.startswith("[") and tok.endswith("]"):
            return _parse_list_literal(tok, self._variables)
        if tok.startswith("{") and tok.endswith("}"):
            return self._parse_dict_literal(tok)
        # Parenthesized symbolic expression — emitted by the writer for any
        # BinaryOp or UnaryOp (e.g. ``(freq + 1000000.0)``). The writer always
        # parenthesizes and never leaves nested operations unwrapped, so this
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
        """Build a `ParseError` tagged with the current line number.

        Returned rather than raised, so a spec callback raises it from its own ``raise`` statement.

        Args:
            message (str): Description of what is wrong with the line.

        Returns:
            The error, ready to raise.
        """
        return ParseError(message, self._pos + 1)

    def _parse_dict_literal(self, tok: str) -> dict[str, object]:
        """Parse a ``{"key": value, ...}`` token into a dict.

        Keys must be quoted strings; values go through `parse_value` recursively, so nested
        dicts, numbers, ``null``, and quoted strings all work. The generic brace-literal form for
        dict-valued operation kwargs.

        Args:
            tok (str): The brace literal, braces included.

        Returns:
            The decoded mapping, empty for ``{}``.

        Raises:
            ParseError: On malformed entries or unquoted keys.
        """
        inner = tok[1:-1].strip()
        out: dict[str, object] = {}
        if not inner:
            return out
        for entry in _split_args(inner):
            entry_stripped = entry.strip()
            if not entry_stripped:
                continue
            key_tok, sep, val_tok = _partition_dict_entry(entry_stripped)
            if not sep:
                msg = f'invalid dict entry {entry_stripped!r}; expected `"key": value`'
                raise ParseError(msg, self._pos + 1)
            key_tok = key_tok.strip()
            if len(key_tok) < 2 or not (key_tok.startswith('"') and key_tok.endswith('"')):
                msg = f"dict keys must be quoted strings, got {key_tok!r}"
                raise ParseError(msg, self._pos + 1)
            out[_unescape_str(key_tok[1:-1])] = self.parse_value(val_tok.strip())
        return out

    def _parse_paren_expression(self, tok: str) -> Expression:
        """Parse a parenthesized symbolic expression token.

        Round-trips the parenthesized forms emitted by the writer:

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
        handled by `_parse_function_call`, not here.

        Args:
            tok (str): The expression token, outer parentheses included.

        Returns:
            The expression node the token denotes.

        Raises:
            ParseError: If the token is empty, names an unknown operator, or has a shape the writer
                never emits.
        """
        from qprogram.variable import (  # ruff: ignore[import-outside-top-level]
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

        # Logical unary (``not <operand>``) — recognized by the leading
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
        first-class [`Expression`][qprogram.Expression] nodes; anything else is
        treated as a waveform constructor invocation.

        Args:
            tok (str): The ``name(args)`` token.

        Returns:
            An [`Expression`][qprogram.Expression] node for a math function or ``where``, otherwise the
            constructed waveform (or sweep source, which shares the constructor shape).

        Raises:
            ParseError: If ``where`` is given other than three arguments, the name is neither a
                registered waveform nor a registered sweep source, or an argument is malformed.
            TypeError: If the constructor arguments do not fit the waveform's or sweep source's
                signature.
        """
        from qprogram.variable import _MATH_FUNCTIONS, MathFunc, Where  # ruff: ignore[import-outside-top-level]

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
        """Return the declared [`Variable`][qprogram.Variable] named ``name``, declaring it on demand.

        A loop header may name a variable no ``var`` line declared; the declaration is then created
        on the program being built, so the round trip through `dumps` writes it out.

        Args:
            name (str): Variable id as written in the file.

        Returns:
            The variable registered under ``name`` for this parse.

        Raises:
            ValidationError: If ``name`` is a reserved ``.qp`` keyword or already declared on the
                program under construction.
        """
        if name in self._variables:
            return self._variables[name]
        var = self._program.variable(name)
        self._variables[name] = var
        return var

    def declared_variable(self, name: str) -> Variable | None:
        """Return the declared [`Variable`][qprogram.Variable] named ``name``, or ``None``.

        The lookup-only counterpart of `get_or_declare_variable`, for callbacks that must
        distinguish a variable reference from a plain string without declaring anything.

        Args:
            name (str): Variable id as written in the file.

        Returns:
            The variable declared under ``name``, or ``None`` if there is none.
        """
        return self._variables.get(name)

    def get_or_create_handle(self, name: str) -> MeasurementHandle:
        """Return the canonical [`MeasurementHandle`][qprogram.MeasurementHandle] for ``name``, creating it on demand.

        The same instance is returned for every call with the same name
        during a single parse, so every measurement op, every
        [`MeasurementRef`][qprogram.MeasurementRef], and every other consumer of the handle
        end up sharing one Python object.

        Args:
            name (str): Measurement name as written in the file.

        Returns:
            The handle for that measurement name.
        """
        if name not in self._handles:
            self._handles[name] = MeasurementHandle(name)
        return self._handles[name]

    def allocate_measurement_handle(self, bus: object) -> MeasurementHandle:
        """Auto-allocate a handle for a measurement line that carries no ``name=``.

        Allocates through [`QProgram.measure`][qprogram.QProgram.measure]'s own naming code, against
        the program parsed so far, so a hand-written file without explicit names gets ``m0``,
        ``m1``, ... The per-bus prefix the builder uses for a schema-backed bus is never reached
        here: the bus is still the parsed token at this point, not a [`BusRef`][qprogram.BusRef],
        so every auto-allocated name takes the global form even on a ``q[0].readout`` line. Files
        the writer produced are unaffected, since it always emits an explicit ``name=``.

        Args:
            bus (object): The bus the measurement targets, used only to compute the name prefix.

        Returns:
            The handle for the freshly allocated name.
        """
        bus_str = bus if isinstance(bus, str) else ""
        allocated = self._program._allocate_measurement_name(bus_str, requested=None)  # ruff: ignore[private-member-access]
        return self.get_or_create_handle(allocated)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _find_comment(line: str) -> int:
    r"""Return the index of the first ``#`` that starts a comment, or ``-1``.

    Escape-aware: a ``\"`` inside a quoted string does not toggle the in-string state, so a
    ``#`` following an escaped quote is still recognized as string content rather than a comment.

    A line whose first two characters are ``#!`` is the format header, and no ``#`` on it starts a
    comment: the header is taken whole, so ``#!QProgram 1.0 # note`` keeps its trailing text and
    fails version parsing.

    Args:
        line (str): One raw input line.

    Returns:
        The 0-based index where the comment starts, or ``-1`` when the line holds no comment.
    """
    in_str = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str and c == "\\" and i + 1 < n:
            # escaped character inside a string — never a delimiter
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif c == "#" and not in_str and not line.startswith("#!"):
            return i
        i += 1
    return -1


def _unescape_str(s: str) -> str:
    r"""Resolve the escape sequences the writer emits: ``\"``, ``\\``, ``\n``, ``\r``, ``\t``.

    Args:
        s (str): The escaped text, without its surrounding quotes.

    Returns:
        The text with every recognized escape sequence replaced by the character it denotes; an
        unrecognized sequence is left as written.
    """
    simple = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_major_minor(version: str) -> tuple[int, int]:
    """Split a version string into its major and minor components.

    A patch component is accepted and ignored: vendor compatibility is decided at major.minor.

    Args:
        version (str): Version text from a ``require`` line or a registered vendor.

    Returns:
        The ``(major, minor)`` pair.

    Raises:
        ValueError: If the string has no minor component, or either component is not an integer.
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


def _to_expression(value: object) -> Expression:
    """Promote a parsed value into an [`Expression`][qprogram.Expression]-compatible operand.

    Numbers become [`Constant`][qprogram.Constant]; variables and expression nodes pass
    through. Anything else (a bus string, say) reaches here only as a result
    of malformed input — raise so the caller surfaces a clean error instead
    of a downstream ``TypeError`` from the AST constructor.

    Args:
        value (object): A value returned by `_Parser.parse_value`.

    Returns:
        The value as an expression node.

    Raises:
        ParseError: If the value cannot appear as an operand. The error carries no line number:
            this helper has no view of the parser's cursor.
    """
    from qprogram.variable import Constant, Expression  # ruff: ignore[import-outside-top-level]

    if isinstance(value, Expression):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Constant(value)
    msg = f"cannot use {value!r} ({type(value).__name__}) as an expression operand"
    raise ParseError(msg)


def _split_lines(text: str) -> list[str]:
    r"""Split a document into lines on the format's own line terminator.

    ``str.splitlines`` cannot be used here. It breaks on ten characters — it adds ``\v``, ``\f``,
    ``\x1c``, ``\x1d``, ``\x1e``, ``\x85``, ``\u2028``, and ``\u2029`` to the three line endings —
    whereas the format's ``_NL`` terminal is ``\r?\n`` and its ``STRING`` terminal admits every one
    of those eight characters raw. A label or a units string holding one would therefore be split
    across two lines on reload, breaking a document the reference grammar accepts.

    Args:
        text (str): The whole document.

    Returns:
        The document's lines, without their terminators, and with no trailing empty entry for a
        document that ends in a newline. A lone ``\r`` is not a terminator, exactly as in the
        grammar, so it stays inside its line.
    """
    lines = _LINE_BREAK_RE.split(text)
    if lines and not lines[-1]:
        lines.pop()
    return lines


def _tokenize(line: str) -> list[str]:
    r"""Whitespace-split, respecting double quotes, parentheses, brackets, and braces.

    Tokens inside ``"..."``, ``(...)``, ``[...]``, or ``{...}`` are kept whole — so list-literal
    kwargs like ``outputs=[1, 2]`` and dict-literal kwargs like ``matrix={"a": 1.0}`` survive as
    single tokens. Quote state is tracked at every nesting depth and ``\"`` escapes inside
    strings are honored, so a parenthesis or bracket inside a quoted string never perturbs the
    nesting count.

    Args:
        line (str): One statement, comments already stripped.

    Returns:
        The statement's tokens in order, with quotes and bracket pairs preserved verbatim.
    """
    tokens: list[str] = []
    current = ""
    in_q = False
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_q:
            if c == "\\" and i + 1 < n:
                current += line[i : i + 2]
                i += 2
                continue
            if c == '"':
                in_q = False
            current += c
            i += 1
            continue
        if c == '"':
            in_q = True
            current += c
        elif c in "([{":
            depth += 1
            current += c
        elif c in ")]}":
            depth -= 1
            current += c
        elif c == " " and depth == 0:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += c
        i += 1
    if current:
        tokens.append(current)
    return tokens


def _parse_waveform_expr(expr: str, variables: dict[str, Variable] | None = None) -> object:
    """Parse a ``Name(arg, ...)`` waveform constructor call.

    When ``variables`` is provided (always the case when called from a live
    parser, via `_Parser._parse_function_call`), bare identifiers in
    the argument list are resolved against the variable table — so
    ``Gaussian(amplitude=amp, ...)`` round-trips with ``amp`` as a
    [`Variable`][qprogram.Variable] rather than the string ``"amp"``. Without a table, an
    identifier is returned as a plain string.

    Positional and keyword arguments are not combined: a call carrying any ``key=value`` argument
    is constructed from its keyword arguments alone and its positional arguments are dropped, while
    a call written without keywords is splatted positionally. The writer spells every constructor
    argument as a keyword, so a written file is unaffected; a hand-written mix silently loses its
    positional values.

    Args:
        expr (str): The constructor call, e.g. ``Gaussian(amplitude=0.5, duration=40, sigma=8)``.
        variables (dict[str, Variable] | None): Variable table identifiers resolve against.

    Returns:
        The constructed waveform, or the constructed sweep source when the name is registered as
        one — both share the ``Name(args)`` shape and the registry-by-class-name design.

    Raises:
        ParseError: If the name is registered as neither a waveform nor a sweep source.
        TypeError: If the arguments do not fit the class's signature; the constructor's own error
            travels out unwrapped, without a line number.
    """
    expr = expr.strip()
    pi = expr.index("(")
    cls_name = expr[:pi]
    cls: type | None = get_waveform_class(cls_name)
    if cls is None:
        # Sweep sources share the ``Name(args)`` shape and the same registry-by-class-name design, so
        # one lookup path serves both. This is what lets a source nest inside a combinator's argument
        # list (``Concat(sources=[Rotate(...), Range(...)])``).
        cls = get_sweep_source_class(cls_name)
    if cls is None:
        msg = f"Unknown waveform or sweep source type: {cls_name}"
        raise ParseError(msg)
    args_str = expr[pi + 1 : expr.rindex(")")]
    pos, kw = _parse_constructor_args(args_str, variables)
    return cls(**kw) if kw else cls(*pos)


def _parse_constructor_args(
    args_str: str,
    variables: dict[str, Variable] | None = None,
) -> tuple[list, dict]:
    """Split a constructor argument list into positional and keyword arguments.

    A ``key=`` prefix marks a keyword argument, unless the token opens with a quote or the text
    before the ``=`` contains a ``(`` — which is how an equals sign inside a string or a nested call
    stays part of a positional argument.

    Args:
        args_str (str): The raw text between the constructor's parentheses.
        variables (dict[str, Variable] | None): Variable table identifiers resolve against.

    Returns:
        A ``(positional, keyword)`` pair of decoded arguments, ready to splat into the constructor.
    """
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
    r"""Split a comma-separated argument list, respecting quotes and all bracket kinds.

    Escape-aware inside quoted strings — a ``\"`` does not end the string, so commas after an
    escaped quote stay inside their argument.

    Args:
        s (str): The argument list, without its enclosing brackets or parentheses.

    Returns:
        One entry per top-level comma-separated argument, whitespace and nesting preserved. A
        trailing comma adds no empty entry.
    """
    parts: list[str] = []
    cur = ""
    depth = 0
    in_q = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if in_q:
            if c == "\\" and i + 1 < n:
                cur += s[i : i + 2]
                i += 2
                continue
            if c == '"':
                in_q = False
            cur += c
            i += 1
            continue
        if c == '"':
            in_q = True
            cur += c
        elif c in "([{":
            depth += 1
            cur += c
        elif c in ")]}":
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if cur.strip():
        parts.append(cur)
    return parts


def _partition_dict_entry(entry: str) -> tuple[str, str, str]:
    """Split one ``"key": value`` dict entry at the first colon outside quotes.

    Args:
        entry (str): One entry of a brace literal.

    Returns:
        ``(key, ":", value)`` on success, ``(entry, "", "")`` when the entry holds no top-level
        colon — the same no-separator contract as `str.partition`.
    """
    in_q = False
    i = 0
    n = len(entry)
    while i < n:
        c = entry[i]
        if in_q:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_q = False
        elif c == '"':
            in_q = True
        elif c == ":":
            return entry[:i], ":", entry[i + 1 :]
        i += 1
    return entry, "", ""


def _parse_list_literal(tok: str, variables: dict[str, Variable] | None = None) -> list:
    """Parse a ``[v0, v1, ...]`` token into a plain Python list.

    Elements go through `_parse_arg`, so numbers, quoted strings, booleans, ``null``, and
    nested lists all work. The result is a *list*, not a numpy array — consumers that want arrays
    (``Arbitrary``, ``Values``) convert via ``np.asarray`` themselves, while consumers that store
    lists (e.g. vendor ops with ``list[int]`` attributes) round-trip type-faithfully.

    Args:
        tok (str): The bracket literal, brackets included.
        variables (dict[str, Variable] | None): Variable table identifiers resolve against.

    Returns:
        The decoded elements in order, empty for ``[]``.

    Raises:
        ParseError: If a nested ``Name(args)`` element names neither a registered waveform nor a
            registered sweep source.
        TypeError: If a nested constructor's arguments do not fit its class's signature.
    """
    inner = tok[1:-1].strip()
    if not inner:
        return []
    return [_parse_arg(v.strip(), variables) for v in _split_args(inner)]


def _parse_arg(val: str, variables: dict[str, Variable] | None = None) -> object:
    """Parse a single waveform-constructor argument.

    The [`Variable`][qprogram.Variable] resolution mirrors
    `_Parser.parse_value`: when the parser threads its variable table
    in (always, in live parsing), an identifier that matches a declared
    variable returns the [`Variable`][qprogram.Variable] instance, so waveforms with
    symbolic parameters (``Gaussian(amplitude=amp, ...)``) round-trip with
    their variable references intact. Without a table, the function falls
    back to returning the identifier as a string.

    Args:
        val (str): The argument text.
        variables (dict[str, Variable] | None): Variable table identifiers resolve against.

    Returns:
        The decoded argument: a bool, ``None``, a number, a list, a waveform or sweep source, a
        variable, or a string.

    Raises:
        ParseError: If a nested ``Name(args)`` argument names neither a registered waveform nor a
            registered sweep source.
        TypeError: If a nested constructor's arguments do not fit its class's signature.
    """
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return _unescape_str(val[1:-1])
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "null":
        return None
    if val.startswith("[") and val.endswith("]"):
        return _parse_list_literal(val, variables)
    if "(" in val:
        return _parse_waveform_expr(val, variables)
    if variables is not None and val in variables:
        return variables[val]
    try:
        return _parse_number(val)
    except ValueError:
        return val
