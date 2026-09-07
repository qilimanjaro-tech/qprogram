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
"""Rewrites that carry an older ``.qp`` or ``.wfl`` file up to the running format version.

A breaking change to either syntax gets one migration, registered under the version that
introduced it. Reading a file whose header declares an earlier version applies every migration
newer than that version, oldest first, to the lines in memory; the file on disk is never touched.
A release that breaks nothing registers nothing, so the chain has an entry per breaking change
rather than per release.

The two formats share one version scale, since ``FORMAT_VERSION`` and
``WAVEFORM_LIBRARY_FORMAT_VERSION`` are both the library version cut to ``major.minor``, which is
why one running version bounds both chains. They do not share their rewrites: the same line of
text means different things in a program body and in a library entry, so a migration is
registered for one ``file_format`` and only ever sees files of that kind. A change to vocabulary
the two do share — a renamed waveform constructor, say — is one rewrite registered twice:

```python
@register_migration("0.3")
@register_migration("0.3", file_format="wfl")
def _square_became_rectangular(lines: list[str]) -> list[str]: ...
```

A migration is handed the file's lines and must return as many as it was given. That invariant is
what keeps a `ParseError`'s line number and every source-map entry true of the file its author
opened, so `migrate_lines` refuses a migration that changes the count.

Lives in a leaf module — only `qprogram._version` — so both readers can import it without
touching the writer↔parser import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, NamedTuple

from qprogram._version import library_major_minor, parse_file_version, parse_major_minor

MigrationFn = Callable[[list[str]], list[str]]
"""Rewrite applied to the lines of a file older than the version its migration is registered under."""

FILE_FORMATS: Final = ("qp", "wfl")
"""The two text formats the package reads, as the names `register_migration` keys its table by."""

# Both format versions are the library version cut to major.minor, so one ceiling bounds both
# chains: a migration applies up to here and a migration registered ahead of its release waits.
_RUNNING_VERSION: Final[str] = library_major_minor()


class Migration(NamedTuple):
    """One registered rewrite, with the version whose breaking change it repairs.

    Attributes:
        version: ``(major, minor)`` of the release that introduced the change. Files declaring an
            earlier version get this migration; files at this version or later do not.
        name: The rewrite's function name, or its repr when the callable has none, for the error
            message that reports a broken line count.
        migrate: The rewrite itself.
    """

    version: tuple[int, int]
    name: str
    migrate: MigrationFn


_migrations: dict[str, list[Migration]] = {file_format: [] for file_format in FILE_FORMATS}


def _table(file_format: str) -> list[Migration]:
    """Return the migration table for one file format.

    Args:
        file_format (str): One of `FILE_FORMATS`.

    Returns:
        The list the format's migrations live in, in application order.

    Raises:
        ValueError: If ``file_format`` names no format this package reads.
    """
    try:
        return _migrations[file_format]
    except KeyError as e:
        known = ", ".join(repr(name) for name in FILE_FORMATS)
        msg = f"unknown file format {file_format!r}; the formats that carry migrations are {known}"
        raise ValueError(msg) from e


def register_migration(version: str, *, file_format: str = "qp") -> Callable[[MigrationFn], MigrationFn]:
    """Register a line rewrite that carries files older than ``version`` up to it.

    Write one when a release changes a syntax in a way the previous spelling cannot survive, and
    key it to that release. The rewrite receives every line of the file, header included, and
    returns the same number of lines; it is expected to leave the header alone, since the reader
    has already taken the version off it.

    Two migrations may share a version — a release is free to break two things — and run in
    registration order.

    Args:
        version (str): The releasing version, as ``major.minor``. A patch component is ignored.
        file_format (str, optional): Which format the rewrite reads, ``"qp"`` for a program or
            ``"wfl"`` for a waveform library. A rewrite needed by both is registered twice.

    Returns:
        The decorator that registers the rewrite and hands it back unchanged.

    Raises:
        ValueError: If ``version`` does not parse as ``major.minor``, or ``file_format`` names no
            format this package reads.

    Example:
        ```python
        @register_migration("0.3")
        def _sweep_became_loop(lines: list[str]) -> list[str]:
            return [_SWEEP_KEYWORD.sub("loop", line) for line in lines]
        ```
    """
    table = _table(file_format)
    target = parse_major_minor(version)

    def decorate(migrate: MigrationFn) -> MigrationFn:
        table.append(Migration(target, getattr(migrate, "__name__", repr(migrate)), migrate))
        table.sort(key=lambda m: m.version)
        return migrate

    return decorate


def known_migrations(file_format: str = "qp") -> tuple[Migration, ...]:
    """Return every migration registered for one format, the oldest version first.

    Args:
        file_format (str, optional): Which format to report, ``"qp"`` or ``"wfl"``.

    Returns:
        The registered migrations, in the order `migrate_lines` applies them.

    Raises:
        ValueError: If ``file_format`` names no format this package reads.
    """
    return tuple(_table(file_format))


def migrate_lines(lines: list[str], file_version: str, *, file_format: str = "qp") -> list[str]:
    """Bring lines written against ``file_version`` up to the running format version.

    A migration applies when its version is newer than the file's and no newer than the running
    one: a file two releases behind collects both steps, and a migration registered ahead of its
    release is left out until the version it names ships.

    Args:
        lines (list[str]): The file's lines, header included.
        file_version (str): The version the file's header declares.
        file_format (str, optional): Which format's migrations to run, ``"qp"`` or ``"wfl"``.

    Returns:
        The migrated lines, or the argument itself when nothing applies.

    Raises:
        ValueError: If ``file_version`` is not exactly ``major.minor``, ``file_format`` names no
            format this package reads, or a migration returns a different number of lines than it
            was given.
    """
    table = _table(file_format)
    declared = parse_file_version(file_version)
    current = parse_file_version(_RUNNING_VERSION)
    for migration in table:
        if not declared < migration.version <= current:
            continue
        migrated = migration.migrate(list(lines))
        if len(migrated) != len(lines):
            version = ".".join(str(part) for part in migration.version)
            msg = (
                f"migration {migration.name!r} to {version} returned {len(migrated)} lines for "
                f"{len(lines)}: a migration must preserve the line count, so that a diagnostic "
                f"still points at the line of the file it came from"
            )
            raise ValueError(msg)
        lines = migrated
    return lines
