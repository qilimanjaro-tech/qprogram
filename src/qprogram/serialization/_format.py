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
"""Shared ``.qp`` format constants.

The single source of truth for the format version emitted by the writer and accepted by the
parser. Lives in its own leaf module, importing only `qprogram._version`, which is itself
stdlib-only, so both sides can import it without touching the writer↔parser import cycle.
"""

from __future__ import annotations

from typing import Final

from qprogram._version import library_major_minor

FORMAT_VERSION: Final[str] = library_major_minor()
"""``major.minor`` version emitted in the ``#!QProgram`` header and accepted by the parser.

The format version follows the library version truncated to ``major.minor``, so ``qprogram``
0.2.1 writes ``#!QProgram 0.2``. Compatibility contract: a file at an earlier version is migrated
up to this one on load, and a file at a later version is refused, since a release cannot know what
a later one changed. A patch never appears in a file, having no way to change the format.
"""
