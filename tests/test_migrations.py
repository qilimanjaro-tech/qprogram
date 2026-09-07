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
"""Tests for loading a file written against an earlier format version.

The migrations a release registers are what let today's parser read yesterday's syntax. The
fixtures here register throwaway migrations, since the released format has needed none yet, and
the tests cover which of them a given header pulls in, the order they run, and the line-count
invariant that keeps error lines pointing at the file the author wrote.
"""

from __future__ import annotations

import re

import pytest
from _header import HEADER, WFL_HEADER

import qprogram as qp
from qprogram.serialization import migrations
from qprogram.serialization._format import FORMAT_VERSION
from qprogram.serialization.migrations import (
    known_migrations,
    known_vendor_migrations,
    migrate_lines,
    register_migration,
    register_vendor_migration,
)
from qprogram.serialization.registry import get_vendor_version, register_vendor_version


@pytest.fixture(autouse=True)
def _empty_registry():
    """Register into empty tables and restore whatever the package shipped afterwards."""
    saved = {file_format: list(table) for file_format, table in migrations._migrations.items()}
    saved_vendors = {vendor: list(table) for vendor, table in migrations._vendor_migrations.items()}
    for table in migrations._migrations.values():
        table.clear()
    migrations._vendor_migrations.clear()
    yield
    for file_format, table in saved.items():
        migrations._migrations[file_format][:] = table
    migrations._vendor_migrations.clear()
    migrations._vendor_migrations.update(saved_vendors)


def _older(offset: int = 1) -> str:
    """Return a version one or more minors below the running one, as an earlier release wrote it."""
    major, minor = FORMAT_VERSION.split(".")[:2]
    return f"{major}.{int(minor) - offset}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_migration_returns_the_function_unchanged():
    def rewrite(lines):
        return lines

    assert register_migration(FORMAT_VERSION)(rewrite) is rewrite


def test_known_migrations_is_ordered_oldest_first():
    register_migration(FORMAT_VERSION)(lambda lines: lines)
    register_migration(_older())(lambda lines: lines)
    assert [m.version for m in known_migrations()] == sorted(m.version for m in known_migrations())


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("", r"at least major\.minor"),
        ("1", r"at least major\.minor"),
        ("banana", r"at least major\.minor"),
        ("0.x", "non-integer major/minor"),
    ],
)
def test_register_migration_rejects_a_malformed_version(version, message):
    with pytest.raises(ValueError, match=message):
        register_migration(version)


# ---------------------------------------------------------------------------
# Which migrations a header pulls in
# ---------------------------------------------------------------------------


def test_an_older_file_is_migrated_on_the_way_in():
    """The released syntax has no `sweep` keyword; a migration is what makes the old file readable."""

    @register_migration(FORMAT_VERSION)
    def _sweep_became_for(lines):
        return [line.replace("  sweep ", "  for ") for line in lines]

    p = qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sweep x in Range(start=0, stop=3, step=1):\n    sync\n")
    assert isinstance(p.body.elements[0], qp.blocks.Sweep)


def test_a_current_file_is_left_alone():
    register_migration(FORMAT_VERSION)(lambda lines: [line.replace("sync", "wait_trigger") for line in lines])
    assert qp.loads(HEADER + "\n\nbody:\n  sync\n").body.elements


@pytest.mark.parametrize("offset", [1, 99])
def test_a_newer_file_is_refused(offset):
    """Migrations only run forward, so a file from a later release has nothing to bring it back."""
    major, minor = FORMAT_VERSION.split(".")[:2]
    with pytest.raises(qp.ParseError, match="Unsupported format version"):
        qp.loads(f"#!QProgram {major}.{int(minor) + offset}\n\nbody:\n  sync\n")
    with pytest.raises(qp.ParseError, match="Unsupported WaveformLibrary format version"):
        qp.WaveformLibrary.loads(f"#!WaveformLibrary {major}.{int(minor) + offset}\n")


def test_a_migration_ahead_of_its_release_is_left_out():
    """Registering against an unreleased version does nothing until that version ships."""
    major, minor = FORMAT_VERSION.split(".")[:2]
    register_migration(f"{major}.{int(minor) + 1}")(lambda lines: [line.replace("sync", "bogus_op") for line in lines])
    assert qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n").body.elements


def test_every_step_between_the_file_and_the_running_version_runs():
    """A file two releases behind collects both migrations, oldest first."""
    order: list[str] = []

    @register_migration(_older())
    def _first(lines):
        order.append("first")
        return [line.replace("aaa", "bbb") for line in lines]

    @register_migration(FORMAT_VERSION)
    def _second(lines):
        order.append("second")
        return [line.replace("bbb", "sync") for line in lines]

    p = qp.loads(f"#!QProgram {_older(2)}\n\nbody:\n  aaa\n")
    assert order == ["first", "second"]
    assert p.body.elements


def test_two_migrations_on_one_version_run_in_registration_order():
    order: list[str] = []

    @register_migration(FORMAT_VERSION)
    def _first(lines):
        order.append("first")
        return lines

    @register_migration(FORMAT_VERSION)
    def _second(lines):
        order.append("second")
        return lines

    qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n")
    assert order == ["first", "second"]


def test_an_older_major_loads_too(monkeypatch):
    """The gate is a migration path, not a matching major: an 0.9 file loads on a 1.3 runtime."""
    monkeypatch.setattr("qprogram.serialization.parser.FORMAT_VERSION", "1.3")
    monkeypatch.setattr("qprogram.serialization.migrations._RUNNING_VERSION", "1.3")

    @register_migration("1.0")
    def _barrier_became_sync(lines):
        return [line.replace("  barrier", "  sync") for line in lines]

    assert qp.loads("#!QProgram 0.9\n\nbody:\n  barrier\n").body.elements


def test_an_older_file_loads_when_no_migration_applies():
    """The common case: a release that broke nothing leaves an older file readable as it is."""
    assert qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n").body.elements


def test_a_newer_major_is_still_refused():
    with pytest.raises(qp.ParseError, match=r"Unsupported format version 99\.0"):
        qp.loads("#!QProgram 99.0\n\nbody:\n  sync\n")


@pytest.mark.parametrize("version", ["unknown", "banana", "0", "0.2.3"])
def test_a_version_that_is_not_major_minor_is_refused(version):
    """A header carries two integer components: no bare major, no patch, nothing else."""
    with pytest.raises(qp.ParseError, match=f"Unsupported format version {re.escape(version)}"):
        qp.loads(f"#!QProgram {version}\n\nbody:\n  sync\n")


# ---------------------------------------------------------------------------
# The line-count invariant
# ---------------------------------------------------------------------------


def test_a_migration_that_drops_a_line_is_refused():
    @register_migration(FORMAT_VERSION)
    def _drops_a_line(lines):
        return lines[:-1]

    with pytest.raises(ValueError, match=r"_drops_a_line.*preserve the line count"):
        qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n")


def test_a_migration_that_adds_a_line_is_refused():
    @register_migration(FORMAT_VERSION)
    def _adds_a_line(lines):
        return [*lines, "  sync"]

    with pytest.raises(ValueError, match=r"_adds_a_line.*preserve the line count"):
        qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n")


def test_an_error_after_a_migration_still_names_the_line_of_the_file():
    """The point of the invariant: line 5 of the diagnostic is line 5 of what the author wrote."""

    @register_migration(FORMAT_VERSION)
    def _sweep_became_for(lines):
        return [line.replace("  sweep ", "  for ") for line in lines]

    text = f"#!QProgram {_older()}\n\nbody:\n  sweep x in Range(start=0, stop=3, step=1):\n    bogus_op 1\n"
    with pytest.raises(qp.ParseError) as excinfo:
        qp.loads(text)
    assert excinfo.value.line_num == 5
    assert text.splitlines()[4].strip() == "bogus_op 1"


def test_the_source_map_of_a_migrated_file_points_at_the_original_lines():
    @register_migration(FORMAT_VERSION)
    def _sweep_became_for(lines):
        return [line.replace("  sweep ", "  for ") for line in lines]

    p = qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sweep x in Range(start=0, stop=3, step=1):\n    sync\n")
    assert sorted(p.source_map.values()) == [4, 5]


# ---------------------------------------------------------------------------
# The waveform library format
# ---------------------------------------------------------------------------


def test_an_older_library_is_migrated_on_the_way_in():
    @register_migration(FORMAT_VERSION, file_format="wfl")
    def _sq_became_square(lines):
        return [line.replace("= Sq(", "= Square(") for line in lines]

    library = qp.WaveformLibrary.loads(f'#!WaveformLibrary {_older()}\n"pi" = Sq(amplitude=0.5, duration=40)\n')
    assert library.get("drive", "pi").amplitude == pytest.approx(0.5)


def test_a_current_library_is_left_alone():
    register_migration(FORMAT_VERSION, file_format="wfl")(
        lambda lines: [line.replace("Square", "Bogus") for line in lines]
    )
    text = f'{WFL_HEADER}\n"pi" = Square(amplitude=0.5, duration=40)\n'
    assert qp.WaveformLibrary.loads(text).get("drive", "pi") is not None


def test_a_later_library_major_is_still_refused():
    with pytest.raises(qp.ParseError, match=r"Unsupported WaveformLibrary format version 9\.0"):
        qp.WaveformLibrary.loads("#!WaveformLibrary 9.0\n")


def test_a_library_version_carrying_a_patch_is_refused():
    """A file has no patch to carry: the format changes at major.minor and nowhere else."""
    with pytest.raises(qp.ParseError, match="Unsupported WaveformLibrary format version"):
        qp.WaveformLibrary.loads(f'#!WaveformLibrary {FORMAT_VERSION}.7\n"pi" = Square(0.5, 40)\n')


@pytest.mark.parametrize("version", ["unknown", "banana", "0", "0.2.3"])
def test_a_library_version_that_is_not_major_minor_is_refused(version):
    """The same rule as a program header: two integer components, no more and no fewer."""
    with pytest.raises(qp.ParseError, match=f"Unsupported WaveformLibrary format version {re.escape(version)}"):
        qp.WaveformLibrary.loads(f"#!WaveformLibrary {version}\n")


def test_the_two_formats_do_not_share_their_rewrites():
    """The same line means different things in a program and in a library, so tables are separate."""
    seen: list[str] = []

    @register_migration(FORMAT_VERSION)
    def _program_only(lines):
        seen.append("qp")
        return lines

    @register_migration(FORMAT_VERSION, file_format="wfl")
    def _library_only(lines):
        seen.append("wfl")
        return lines

    qp.loads(f"#!QProgram {_older()}\n\nbody:\n  sync\n")
    assert seen == ["qp"]
    qp.WaveformLibrary.loads(f'#!WaveformLibrary {_older()}\n"pi" = Square(amplitude=0.5, duration=40)\n')
    assert seen == ["qp", "wfl"]


def test_one_rewrite_can_be_registered_for_both_formats():
    """Vocabulary the two files share — a waveform constructor — is one rewrite, registered twice."""
    formats: list[str] = []

    @register_migration(FORMAT_VERSION)
    @register_migration(FORMAT_VERSION, file_format="wfl")
    def _square_became_rectangular(lines):
        formats.append("called")
        return [line.replace("Rectangular(", "Square(") for line in lines]

    qp.loads(f'#!QProgram {_older()}\n\nbody:\n  play "drive" Rectangular(amplitude=0.5, duration=40)\n')
    library = qp.WaveformLibrary.loads(
        f'#!WaveformLibrary {_older()}\n"pi" = Rectangular(amplitude=0.5, duration=40)\n'
    )
    assert library.get("drive", "pi").amplitude == pytest.approx(0.5)
    assert formats == ["called", "called"]


@pytest.mark.parametrize("file_format", ["", "qp ", "QP", "nope"])
def test_an_unknown_file_format_is_refused(file_format):
    with pytest.raises(ValueError, match="unknown file format"):
        register_migration(FORMAT_VERSION, file_format=file_format)


def test_known_migrations_reports_one_format_at_a_time():
    register_migration(FORMAT_VERSION)(lambda lines: lines)
    assert len(known_migrations()) == 1
    assert known_migrations("wfl") == ()


# ---------------------------------------------------------------------------
# Vendor extensions
# ---------------------------------------------------------------------------


@pytest.fixture
def _dummy_at(request, dummy_vendor):  # ruff: ignore[unused-function-argument]
    """Register the dummy vendor at the version the test asks for, restoring the real one after."""
    saved = get_vendor_version("dummy")
    register_vendor_version("dummy", request.param)
    yield request.param
    register_vendor_version("dummy", saved)


@pytest.mark.parametrize("_dummy_at", ["0.5.0"], indirect=True)
def test_an_older_require_line_migrates_the_body(_dummy_at):
    """A file written against dummy 0.3 loads on dummy 0.5, through the steps in between."""
    steps: list[str] = []

    @register_vendor_migration("dummy", "0.4")
    def _markers_became_set_markers(lines):
        steps.append("0.4")
        return [line.replace("dummy.markers", 'dummy.set_markers "bus" "0001"') for line in lines]

    @register_vendor_migration("dummy", "0.5")
    def _later_still(lines):
        steps.append("0.5")
        return lines

    p = qp.loads(f"{HEADER}\n\nrequire dummy 0.3\n\nbody:\n  dummy.markers\n")
    assert steps == ["0.4", "0.5"]
    assert p.body.elements


@pytest.mark.parametrize("_dummy_at", ["2.1.0"], indirect=True)
def test_an_older_require_major_loads_now(_dummy_at):
    """An earlier major used to be refused outright; it migrates like any other older version."""

    @register_vendor_migration("dummy", "1.0")
    def _markers_became_set_markers(lines):
        return [line.replace("dummy.markers", 'dummy.set_markers "bus" "0001"') for line in lines]

    assert qp.loads(f"{HEADER}\n\nrequire dummy 0.9\n\nbody:\n  dummy.markers\n").body.elements


@pytest.mark.parametrize("_dummy_at", ["0.5.0"], indirect=True)
def test_a_require_line_at_the_installed_version_migrates_nothing(_dummy_at):
    register_vendor_migration("dummy", "0.5")(lambda lines: [line.replace("dummy.", "bogus.") for line in lines])
    assert qp.loads(f'{HEADER}\n\nrequire dummy 0.5\n\nbody:\n  dummy.set_markers "bus" "0001"\n').body.elements


@pytest.mark.parametrize("_dummy_at", ["0.5.0"], indirect=True)
def test_a_vendor_migration_ahead_of_the_installed_release_is_left_out(_dummy_at):
    """The ceiling is the installed extension, so a rewrite for its next release waits."""
    register_vendor_migration("dummy", "0.6")(lambda lines: [line.replace("dummy.", "bogus.") for line in lines])
    assert qp.loads(f'{HEADER}\n\nrequire dummy 0.3\n\nbody:\n  dummy.set_markers "bus" "0001"\n').body.elements


def test_a_vendor_with_no_migrations_is_no_obstacle(dummy_vendor):  # ruff: ignore[unused-function-argument]
    """The common case: nothing registered, so an older require line loads as it is."""
    assert qp.loads(f'{HEADER}\n\nrequire dummy 0.0\n\nbody:\n  dummy.set_markers "bus" "0001"\n').body.elements


@pytest.mark.parametrize("_dummy_at", ["0.5.0"], indirect=True)
def test_a_vendor_migration_must_preserve_the_line_count(_dummy_at):
    @register_vendor_migration("dummy", "0.5")
    def _drops_a_line(lines):
        return lines[:-1]

    with pytest.raises(ValueError, match=r"_drops_a_line.*preserve the line count"):
        qp.loads(f'{HEADER}\n\nrequire dummy 0.3\n\nbody:\n  dummy.set_markers "bus" "0001"\n')


def test_vendor_tables_are_separate_from_the_format_table(dummy_vendor):  # ruff: ignore[unused-function-argument]
    register_migration(FORMAT_VERSION)(lambda lines: lines)
    register_vendor_migration("dummy", "0.1")(lambda lines: lines)
    assert [m.name for m in known_vendor_migrations("dummy")] == ["<lambda>"]
    assert known_vendor_migrations("nonexistent_vendor") == ()
    assert len(known_migrations()) == 1


def test_known_vendor_migrations_is_ordered_oldest_first():
    register_vendor_migration("dummy", "0.9")(lambda lines: lines)
    register_vendor_migration("dummy", "0.2")(lambda lines: lines)
    assert [m.version for m in known_vendor_migrations("dummy")] == [(0, 2), (0, 9)]


# ---------------------------------------------------------------------------
# The runner on its own
# ---------------------------------------------------------------------------


def test_migrate_lines_returns_its_argument_when_nothing_applies():
    lines = ["#!QProgram " + FORMAT_VERSION, "body:", "  sync"]
    assert migrate_lines(lines, FORMAT_VERSION) is lines


def test_migrate_lines_rejects_a_malformed_file_version():
    with pytest.raises(ValueError, match=r"major\.minor"):
        migrate_lines(["#!QProgram banana"], "banana")


def test_a_migration_cannot_corrupt_the_caller_s_lines():
    """A migration that mutates what it is handed works on a copy."""

    @register_migration(FORMAT_VERSION)
    def _mutates_in_place(lines):
        lines[-1] = "  sync"
        return lines

    lines = [f"#!QProgram {_older()}", "body:", "  aaa"]
    assert migrate_lines(lines, _older())[-1] == "  sync"
    assert lines[-1] == "  aaa"
