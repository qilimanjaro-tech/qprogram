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
"""The library version, truncated for the headers the file formats carry.

Both text formats the package writes — ``.qp`` and ``.wfl`` — stamp their header with the
library version cut to ``major.minor``, so the one derivation lives here. It reads the installed
distribution metadata rather than ``qprogram.__version__``, and imports nothing from the package,
so a module may take the version without pulling the package's import graph in with it.
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
