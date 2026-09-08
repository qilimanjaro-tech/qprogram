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
"""The header lines the suite writes into its ``.qp`` and ``.wfl`` fixtures.

Both are built from the running format version, which follows the library version, so the
fixtures stay loadable across a release rather than pinning the version a test was written
under. The tests that are about the version itself spell one out.
"""

from __future__ import annotations

from qprogram.serialization._format import FORMAT_VERSION
from qprogram.waveform_library import WAVEFORM_LIBRARY_FORMAT_VERSION

HEADER = f"#!QProgram {FORMAT_VERSION}"
WFL_HEADER = f"#!WaveformLibrary {WAVEFORM_LIBRARY_FORMAT_VERSION}"
