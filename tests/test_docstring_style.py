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
"""Guards on the notation docstrings are written in.

mkdocstrings renders a docstring as Markdown and has no reStructuredText reader, so a Sphinx
cross-reference role reaches the published page as literal text rather than as a link. Ruff has no
rule for it and the docs build does not fail on it, because an unrendered role is not a broken
link, just ugly prose. These tests are what notices.

Write a cross-reference the way mkdocstrings reads one: ``[`Variable`][qprogram.Variable]`` for a
target the API reference documents, and plain ``` `Variable` ``` for anything else, such as a
builtin, a stdlib name, or a helper no page renders.
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "qprogram"

# The roles a reStructuredText docstring would use for a cross-reference.
ROLE = re.compile(r":(?:attr|class|const|data|exc|func|meth|mod|obj|term|ref|doc):`")


def _source_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_source_tree_is_not_empty():
    """Guard the guards: a bad root would make every check below pass by finding nothing."""
    files = _source_files()
    assert len(files) > 50, f"expected the package under {SOURCE_ROOT}, found {len(files)} modules"


def test_no_sphinx_roles_in_source():
    """No module may carry a reStructuredText cross-reference role, in a docstring or a comment."""
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if ROLE.search(line):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "Sphinx roles do not render; use [`Name`][path] or `Name`:\n" + "\n".join(offenders)


def test_cross_references_are_balanced():
    """Every Markdown cross-reference must be a complete ``[`title`][target]`` or ``[`title`][]``."""
    opening = re.compile(r"\[`[^`\n]+`\]")
    complete = re.compile(r"\[`[^`\n]+`\]\[[A-Za-z_][A-Za-z0-9_.]*\]|\[`[^`\n]+`\]\[\]")
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if len(opening.findall(line)) != len(complete.findall(line)):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "a cross-reference lost its target, so it renders as literal brackets:\n" + "\n".join(
        offenders
    )
