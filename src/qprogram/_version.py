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
"""The library version, truncated for the headers the file formats carry, and how to read one.

Both text formats the package writes — ``.qp`` and ``.wfl`` — stamp their header with the
library version cut to ``major.minor``, so the one derivation lives here, next to the parsing
every version comparison goes through. It reads the installed distribution metadata rather than
``qprogram.__version__``, and imports nothing from the package, so a module may take the version
without pulling the package's import graph in with it.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def library_major_minor() -> str:
    """Truncate the installed library version to its ``major.minor`` components.

    Returns:
        The first two components of the distribution version, or ``"0.0"`` when the package has
        no installed metadata to read.
    """
    try:
        release = version("qprogram")
    except PackageNotFoundError:
        return "0.0"
    major, _, rest = release.partition(".")
    minor = rest.partition(".")[0]
    return f"{major}.{minor or '0'}"


def parse_major_minor(version: str) -> tuple[int, int]:
    """Split a version string into its major and minor components.

    A patch component is accepted and ignored: the format version and vendor compatibility are
    both decided at major.minor.

    Args:
        version (str): Version text from a ``#!QProgram`` header, a ``require`` line, or a
            registered vendor.

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


def parse_file_version(version: str) -> tuple[int, int]:
    """Read the version a file's header declares, which is exactly ``major.minor``.

    Stricter than `parse_major_minor`, which takes a version from a package and tolerates the
    patch component such a version carries. A file has no patch: the format changes at
    ``major.minor``, and a release that only moves the patch cannot have changed it.

    Args:
        version (str): The version token from a ``#!QProgram`` or ``#!WaveformLibrary`` header.

    Returns:
        The ``(major, minor)`` pair.

    Raises:
        ValueError: If the string is not exactly two integer components.
    """
    parts = version.split(".")
    expected_parts = 2
    if len(parts) != expected_parts:
        msg = f"file version {version!r} must be exactly major.minor"
        raise ValueError(msg)
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as e:
        msg = f"file version {version!r} has non-integer major/minor components"
        raise ValueError(msg) from e
