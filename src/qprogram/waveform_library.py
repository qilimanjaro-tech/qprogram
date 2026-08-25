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
"""Per-bus waveform resolution for QProgram.

A [`WaveformLibrary`][qprogram.WaveformLibrary] maps a *waveform name* (the string a program plays as an alias, e.g.
``play q[0].drive "pi_pulse"``) to a concrete [`Waveform`][qprogram.waveforms.Waveform] /
[`IQWaveform`][qprogram.waveforms.IQWaveform]. Lookup is **scoped to the bus**: the same name can resolve
to a *different* pulse on ``q[0].drive`` than on ``q[1].drive``, which a flat
``dict[str, Waveform]`` cannot express.

This is the mechanism behind [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms]. A program stays portable by
referencing waveforms by name; the concrete pulses live in a library, applied as a pre-execution step
(``program.with_waveforms(library)`` or [`WaveformLibrary.apply`][qprogram.WaveformLibrary.apply]). The library is a
standalone artifact — it is independent of any platform.

The library is **not** part of a ``.qp`` program file — like the platform parameter store, it is calibration/snapshot
state that travels alongside the (stable, name-bearing) program, not inside it. It has its own portable text format with
the ``.wfl`` extension (see [`WaveformLibrary.dumps`][qprogram.WaveformLibrary.dumps] /
[`WaveformLibrary.save`][qprogram.WaveformLibrary.save]), so a calibration set can be saved, shared, and reloaded
independently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qprogram.buses import BusRef
from qprogram.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from qprogram.qprogram import QProgram
    from qprogram.waveforms.waveform import IQWaveform, Waveform

# (element | None, idx | None, kind | None, name) — None in a slot marks a less-specific tier.
_LibraryKey = tuple["str | None", "int | tuple[int, ...] | None", "str | None", str]

# Version of the ``.wfl`` text format (independent of the ``.qp`` FORMAT_VERSION).
WAVEFORM_LIBRARY_FORMAT_VERSION = "1.0"

# Entry coordinate: ``element[idx].kind`` (exact) or ``element[*].kind`` (family). idx may be a tuple
# (couplers): ``c[0,1].flux``.
_COORD_RE = re.compile(r"^(\w+)\[(\*|\d+(?:,\d+)*)\]\.(\w+)$")


class WaveformLibrary:
    """A per-bus store mapping waveform names to concrete waveforms.

    Entries are keyed at one of three tiers; `get` tries them most-specific first:

    1. **exact** — ``set(name, wf, element="q", idx=0, kind="drive")`` — only ``q[0].drive``.
    2. **family** — ``set(name, wf, element="q", kind="drive")`` — any ``q[*].drive`` (idx unspecified).
    3. **global** — ``set(name, wf)`` — any bus. This tier is the only one a raw-string bus can reach,
       and a bare ``dict`` passed to [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms] lands here.

    More specific entries shadow less specific ones for a given bus.
    """

    def __init__(self) -> None:
        self._entries: dict[_LibraryKey, Waveform | IQWaveform] = {}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Waveform | IQWaveform]) -> WaveformLibrary:
        """Build a global-tier-only library from a plain ``{name: waveform}`` mapping.

        This is what makes a bare ``dict`` usable with [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms]:
        every name in the mapping resolves on every bus, whatever its ``(element, idx, kind)`` coordinate.

        Args:
            mapping (Mapping[str, Waveform | IQWaveform]): Waveform names mapped to concrete
                waveforms.

        Returns:
            A library holding one global-tier entry per mapping key.

        Raises:
            ValidationError: If a key is not a non-empty string.
        """
        library = cls()
        for name, waveform in mapping.items():
            library.set(name, waveform)
        return library

    def set(
        self,
        name: str,
        waveform: Waveform | IQWaveform,
        *,
        element: str | None = None,
        idx: int | tuple[int, ...] | None = None,
        kind: str | None = None,
    ) -> None:
        """Register ``waveform`` under ``name`` at the tier implied by the keyword arguments.

        Args:
            name (str): The waveform name as referenced in the program (the string alias).
            waveform (Waveform | IQWaveform): The concrete waveform to resolve to.
            element (str | None): Element kind (e.g. ``"q"``). Required for the exact and family
                tiers.
            idx (int | tuple[int, ...] | None): Element index — a tuple for a multi-index element
                such as a coupler. Given (with ``element`` and ``kind``) → exact tier.
            kind (str | None): Bus kind (e.g. ``"drive"``). Required for the exact and family tiers.

        Raises:
            ValidationError: If ``name`` is not a non-empty string, or the ``element``/``idx``/``kind``
                combination does not match one of the three tiers (exact = all three; family =
                element + kind; global = none).
        """
        if not isinstance(name, str) or not name:
            msg = f"waveform name must be a non-empty string, got {name!r}"
            raise ValidationError(msg)
        if element is not None and kind is not None and idx is not None:
            key: _LibraryKey = (element, idx, kind, name)
        elif element is not None and kind is not None and idx is None:
            key = (element, None, kind, name)
        elif element is None and idx is None and kind is None:
            key = (None, None, None, name)
        else:
            msg = (
                "WaveformLibrary.set: specify (element, idx, kind) for an exact entry, "
                "(element, kind) for a family default, or none of them for a global entry; "
                f"got element={element!r}, idx={idx!r}, kind={kind!r}"
            )
            raise ValidationError(msg)
        self._entries[key] = waveform

    def get(self, bus: str, name: str) -> Waveform | IQWaveform | None:
        """Resolve ``name`` for ``bus``, trying exact → family → global; ``None`` if no entry matches.

        A schema-backed [`BusRef`][qprogram.BusRef] can match all three tiers; a raw-string bus carries no
        ``(element, idx, kind)`` metadata, so only the global tier is reachable.

        Args:
            bus (str): The bus the name is played on — a [`BusRef`][qprogram.BusRef] to reach the
                exact and family tiers, any string for the global tier alone.
            name (str): The waveform name to resolve.

        Returns:
            The most specific waveform registered for ``(bus, name)``, or ``None`` when no tier
            matches.
        """
        if isinstance(bus, BusRef) and bus.element and bus.kind:
            for key in (
                (bus.element, bus.idx, bus.kind, name),
                (bus.element, None, bus.kind, name),
                (None, None, None, name),
            ):
                if key in self._entries:
                    return self._entries[key]
            return None
        return self._entries.get((None, None, None, name))

    def apply(self, program: QProgram) -> QProgram:
        """Return a copy of ``program`` with string waveform names resolved against this library.

        Platform-free convenience identical to ``program.with_waveforms(self)`` — useful for tooling and
        tests that resolve without going through a platform.

        Args:
            program (QProgram): The program whose string waveform aliases are resolved. Never
                mutated.

        Returns:
            A copy of ``program`` with each resolvable alias replaced by its concrete waveform.

        Raises:
            ValidationError: If a resolved waveform does not match the channel kind of the bus it
                lands on.
        """
        return program.with_waveforms(self)

    # ------------------------------------------------------------------
    # Portable ``.wfl`` text serialization
    # ------------------------------------------------------------------

    def dumps(self) -> str:
        """Serialize the library to the portable ``.wfl`` text format.

        Each entry is one line — ``"<name>" [<coord>] = <waveform>`` — where ``<coord>`` is
        ``element[idx].kind`` (exact tier), ``element[*].kind`` (family tier), or absent (global tier),
        and ``<waveform>`` reuses the same constructor syntax as ``.qp`` (e.g.
        ``IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)``). Entries are emitted in insertion
        order, so ``loads(dumps(lib))`` reproduces the library exactly.

        Returns:
            The complete ``.wfl`` document, header line included, ending in a newline.

        Raises:
            SerializationError: If a stored waveform is not concrete (e.g. carries a ``Variable``) — a
                calibration library must hold uploadable pulses, not symbolic ones.
        """
        from qprogram.errors import SerializationError  # ruff: ignore[import-outside-top-level]
        from qprogram.qprogram import QProgram  # ruff: ignore[import-outside-top-level]
        from qprogram.serialization.writer import _escape_str, _Writer  # ruff: ignore[import-outside-top-level]

        writer = _Writer(QProgram())
        lines = [f"#!WaveformLibrary {WAVEFORM_LIBRARY_FORMAT_VERSION}"]
        for (element, idx, kind, name), waveform in self._entries.items():
            try:
                wf_str = writer.serialize_waveform(waveform)
            except (KeyError, SerializationError) as e:
                msg = (
                    f"cannot serialize the waveform stored under name {name!r}: a WaveformLibrary must "
                    f"hold concrete waveforms (no Variables / symbolic parameters). Underlying error: {e}"
                )
                raise SerializationError(msg) from e
            coord = _format_coord(element, idx, kind)
            prefix = f'"{_escape_str(name)}"'
            lines.append(f"{prefix} {coord} = {wf_str}" if coord else f"{prefix} = {wf_str}")
        return "\n".join(lines) + "\n"

    def save(self, path: str) -> None:
        """Write the library to ``path`` as UTF-8 text.

        The ``.wfl`` extension is the convention.

        Args:
            path (str): Filesystem path to write. An existing file is overwritten.

        Raises:
            SerializationError: If a stored waveform is not concrete (see `dumps`).
            OSError: If the path cannot be written.
        """
        Path(path).write_text(self.dumps(), encoding="utf-8")

    @classmethod
    def loads(cls, text: str) -> WaveformLibrary:
        """Parse a ``.wfl`` text document into a [`WaveformLibrary`][qprogram.WaveformLibrary].

        The ``#!WaveformLibrary`` header must come first, preceded by blank lines at most — a
        comment ahead of it is an error. After the header, blank lines and ``#`` comment lines are
        skipped and every other line must be an entry.

        Args:
            text (str): The ``.wfl`` document to parse.

        Returns:
            The reconstructed library, entries in file order.

        Raises:
            ParseError: On a missing or incompatible header, a malformed entry, or an unknown
                waveform type. Waveform types are looked up in the global serialization registry, so
                every built-in is always available while a vendor waveform needs its package
                imported first.
            ValidationError: If an entry names an empty waveform name.
        """
        from qprogram.serialization.parser import (  # ruff: ignore[import-outside-top-level]
            ParseError,
            _parse_waveform_expr,
            _tokenize,
            _unescape_str,
        )

        library = cls()
        lines = text.splitlines()
        pos = 0
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        if pos >= len(lines) or not lines[pos].strip().startswith("#!WaveformLibrary"):
            msg = "Missing #!WaveformLibrary header"
            raise ParseError(msg, pos + 1)
        header = lines[pos].split()
        version = header[-1] if len(header) > 1 else "unknown"
        if version.split(".", maxsplit=1)[0] != WAVEFORM_LIBRARY_FORMAT_VERSION.split(".", maxsplit=1)[0]:
            msg = f"Unsupported WaveformLibrary format version {version}"
            raise ParseError(msg, pos + 1)
        pos += 1

        for offset, raw in enumerate(lines[pos:], start=pos):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            line_num = offset + 1
            tokens = _tokenize(stripped)
            if "=" not in tokens:
                msg = f"WaveformLibrary entry must contain '=': {stripped!r}"
                raise ParseError(msg, line_num)
            eq = tokens.index("=")
            left, right = tokens[:eq], tokens[eq + 1 :]
            if len(right) != 1:
                msg = "expected exactly one waveform after '='"
                raise ParseError(msg, line_num)
            if not left or not (left[0].startswith('"') and left[0].endswith('"') and len(left[0]) >= 2):
                msg = "entry must start with a quoted waveform name"
                raise ParseError(msg, line_num)
            name = _unescape_str(left[0][1:-1])
            if len(left) == 1:
                element, idx, kind = None, None, None
            elif len(left) == 2:
                element, idx, kind = _parse_coord(left[1], line_num)
            else:
                msg = f"unexpected tokens before '=': {left[1:-1]!r}"
                raise ParseError(msg, line_num)
            try:
                waveform = cast("Waveform | IQWaveform", _parse_waveform_expr(right[0]))
            except (ParseError, ValueError) as e:
                msg = f"invalid waveform: {e}"
                raise ParseError(msg, line_num) from e
            library.set(name, waveform, element=element, idx=idx, kind=kind)
        return library

    @classmethod
    def load(cls, path: str) -> WaveformLibrary:
        """Read and parse a ``.wfl`` file encoded as UTF-8.

        Args:
            path (str): Filesystem path of the document to read.

        Returns:
            The reconstructed library.

        Raises:
            ParseError: If the file's contents are not a valid ``.wfl`` document (see
                `loads`).
            OSError: If the path cannot be read.
        """
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    def __bool__(self) -> bool:
        """Report whether the library holds any entries, so callers can skip an empty pass.

        Returns:
            ``True`` when at least one entry is registered at any tier.
        """
        return bool(self._entries)

    def __repr__(self) -> str:
        return f"WaveformLibrary({len(self._entries)} entries)"


def _format_coord(element: str | None, idx: int | tuple[int, ...] | None, kind: str | None) -> str:
    """Render an entry coordinate for ``.wfl``; ``""`` for the global tier.

    Args:
        element (str | None): Element kind, or ``None`` for a global entry.
        idx (int | tuple[int, ...] | None): Element index, or ``None`` for the family wildcard.
        kind (str | None): Bus kind, or ``None`` for a global entry.

    Returns:
        ``element[idx].kind`` for an exact entry, ``element[*].kind`` for a family entry, or the
        empty string for a global one.
    """
    if element is None and kind is None:
        return ""
    if idx is None:  # family tier: element + kind, any index
        return f"{element}[*].{kind}"
    idx_str = ",".join(str(i) for i in idx) if isinstance(idx, tuple) else str(idx)
    return f"{element}[{idx_str}].{kind}"


def _parse_coord(token: str, line_num: int) -> tuple[str, int | tuple[int, ...] | None, str]:
    """Parse a ``.wfl`` entry coordinate ``element[idx].kind`` / ``element[*].kind``.

    Args:
        token (str): The coordinate token, taken from between the name and the ``=``.
        line_num (int): 1-based line number, reported with a parse failure.

    Returns:
        An ``(element, idx, kind)`` triple, where ``idx`` is an int for a single index, a tuple for
        a comma-separated one (a coupler, say), and ``None`` for the family wildcard ``[*]``.

    Raises:
        ParseError: If ``token`` does not have the shape ``element[idx].kind`` or
            ``element[*].kind``.
    """
    from qprogram.serialization.parser import ParseError  # ruff: ignore[import-outside-top-level]

    match = _COORD_RE.match(token)
    if match is None:
        msg = f"invalid entry coordinate {token!r}; expected element[idx].kind or element[*].kind"
        raise ParseError(msg, line_num)
    element, idx_str, kind = match.groups()
    if idx_str == "*":
        idx: int | tuple[int, ...] | None = None
    elif "," in idx_str:
        idx = tuple(int(part) for part in idx_str.split(","))
    else:
        idx = int(idx_str)
    return element, idx, kind
