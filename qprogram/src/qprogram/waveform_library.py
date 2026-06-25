"""Per-bus waveform resolution for QProgram.

A :class:`WaveformLibrary` maps a *waveform name* (the string a program plays as an alias, e.g.
``play q[0].drive "pi_pulse"``) to a concrete :class:`~qprogram.waveforms.Waveform` /
:class:`~qprogram.waveforms.IQWaveform`. Unlike a flat ``dict[str, Waveform]``, lookup is **scoped to
the bus**: the same name can resolve to a *different* pulse on ``q[0].drive`` than on ``q[1].drive``.

This is the mechanism behind :meth:`QProgram.with_waveforms`. A program stays portable by referencing
waveforms by name; the concrete pulses live in a library, applied as a pre-execution step
(``program.with_waveforms(library)`` or :meth:`WaveformLibrary.apply`). The library is a standalone
artifact — it is independent of any platform.

The library is **not** part of a ``.qp`` program file — like the platform parameter store, it is
calibration/snapshot state that travels alongside the (stable, name-bearing) program, not inside it. It
has its own portable text format with the ``.wfl`` extension (see :meth:`WaveformLibrary.dumps` /
:meth:`WaveformLibrary.save`), so a calibration set can be saved, shared, and reloaded independently.
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

    Entries are keyed at one of three tiers; :meth:`get` tries them most-specific first:

    1. **exact** — ``set(name, wf, element="q", idx=0, kind="drive")`` — only ``q[0].drive``.
    2. **family** — ``set(name, wf, element="q", kind="drive")`` — any ``q[*].drive`` (idx unspecified).
    3. **global** — ``set(name, wf)`` — any bus. This tier is the only one a raw-string bus can reach,
       and a bare ``dict`` passed to :meth:`QProgram.with_waveforms` lands here (== legacy behavior).

    More specific entries shadow less specific ones for a given bus.
    """

    def __init__(self) -> None:
        self._entries: dict[_LibraryKey, Waveform | IQWaveform] = {}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Waveform | IQWaveform]) -> WaveformLibrary:
        """Build a global-tier-only library from a plain ``{name: waveform}`` mapping.

        This is the bridge that keeps a bare ``dict`` usable with :meth:`QProgram.with_waveforms` — the
        result resolves every name on every bus, exactly like the legacy flat mapping.
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
            name: The waveform name as referenced in the program (the string alias).
            waveform: The concrete waveform to resolve to.
            element: Element kind (e.g. ``"q"``). Required for the exact and family tiers.
            idx: Element index. Given (with ``element`` and ``kind``) → exact tier.
            kind: Bus kind (e.g. ``"drive"``). Required for the exact and family tiers.

        Raises:
            ValidationError: If ``name`` is empty, or the ``element``/``idx``/``kind`` combination does
                not match one of the three tiers (exact = all three; family = element + kind; global =
                none).
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

        A schema-backed :class:`~qprogram.BusRef` can match all three tiers; a raw-string bus carries no
        ``(element, idx, kind)`` metadata, so only the global tier is reachable.
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

        Raises:
            SerializationError: If a stored waveform is not concrete (e.g. carries a ``Variable``) — a
                calibration library must hold uploadable pulses, not symbolic ones.
        """
        from qprogram.errors import SerializationError  # noqa: PLC0415
        from qprogram.qprogram import QProgram  # noqa: PLC0415
        from qprogram.serialization.writer import _escape_str, _Writer  # noqa: PLC0415

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
        """Write the library to ``path`` (UTF-8). The ``.wfl`` extension is the convention."""
        Path(path).write_text(self.dumps(), encoding="utf-8")

    @classmethod
    def loads(cls, text: str) -> WaveformLibrary:
        """Parse a ``.wfl`` text document into a :class:`WaveformLibrary`.

        Raises:
            ParseError: On a missing/incompatible header, a malformed entry, or an unknown waveform
                type. (Waveform types are looked up in the global registry — all built-ins are always
                available; a vendor waveform would require its package imported, but no current vendor
                ships waveforms.)
        """
        from qprogram.serialization.parser import (  # noqa: PLC0415
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
        """Read and parse a ``.wfl`` file (UTF-8)."""
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    def __bool__(self) -> bool:
        """Return ``True`` if the library holds any entries (lets callers skip an empty pass)."""
        return bool(self._entries)

    def __repr__(self) -> str:
        return f"WaveformLibrary({len(self._entries)} entries)"


def _format_coord(element: str | None, idx: int | tuple[int, ...] | None, kind: str | None) -> str:
    """Render an entry coordinate for ``.wfl``; ``""`` for the global tier."""
    if element is None and kind is None:
        return ""
    if idx is None:  # family tier: element + kind, any index
        return f"{element}[*].{kind}"
    idx_str = ",".join(str(i) for i in idx) if isinstance(idx, tuple) else str(idx)
    return f"{element}[{idx_str}].{kind}"


def _parse_coord(token: str, line_num: int) -> tuple[str, int | tuple[int, ...] | None, str]:
    """Parse a ``.wfl`` entry coordinate ``element[idx].kind`` / ``element[*].kind``.

    Returns ``(element, idx, kind)`` with ``idx=None`` for the family wildcard ``[*]``.
    """
    from qprogram.serialization.parser import ParseError  # noqa: PLC0415

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
