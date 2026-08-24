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
"""Tests for the reserved keyword and vendor-name sets (qprogram._reserved)."""

from __future__ import annotations

import pytest

from qprogram import RESERVED_KEYWORDS
from qprogram._reserved import (
    RESERVED_VENDOR_NAMES,
    is_reserved_keyword,
    is_reserved_vendor,
)


def test_reserved_keywords_is_frozen_set():
    assert isinstance(RESERVED_KEYWORDS, frozenset)


def test_reserved_keywords_nonempty():
    assert len(RESERVED_KEYWORDS) > 0


@pytest.mark.parametrize(
    "keyword",
    [
        "if",
        "else",
        "elif",
        "while",
        "until",
        "break",
        "continue",
        "return",
        "fragment",
        "def",
        "gate",
        "case",
        "match",
        "repeat",
        "where",
        "let",
        "const",
        "import",
        "from",
        "as",
        "true",
        "false",
        "null",
    ],
)
def test_every_proposed_keyword_is_reserved(keyword):
    assert keyword in RESERVED_KEYWORDS
    assert is_reserved_keyword(keyword) is True


@pytest.mark.parametrize(
    "name",
    [
        "freq",
        "amp",
        "duration",
        "qubit_freq",
        "if_active",
        "returns_value",
        "true_value",
        "_private",
        "_",
        "x",
        "X",
        "If",
        "WHILE",
        # Alignment / scheduling words stay free as identifiers — ``sync`` covers
        # that ground in the QProgram model:
        "barrier",
        "align",
        "align_left",
        "align_right",
        "parallel",
    ],
)
def test_non_reserved_names(name):
    assert name not in RESERVED_KEYWORDS
    assert is_reserved_keyword(name) is False


def test_reserved_keywords_are_lowercase_only():
    # The set is case-sensitive: only the lowercase forms are reserved.
    for kw in RESERVED_KEYWORDS:
        assert kw == kw.lower()


def test_reserved_keywords_frozen():
    # frozenset doesn't have .add(); confirm immutability by attribute absence.
    assert not hasattr(RESERVED_KEYWORDS, "add")
    assert not hasattr(RESERVED_KEYWORDS, "remove")


def test_reserved_vendor_names_superset_of_keywords():
    assert RESERVED_KEYWORDS <= RESERVED_VENDOR_NAMES


def test_core_is_reserved_vendor():
    assert "core" in RESERVED_VENDOR_NAMES
    assert is_reserved_vendor("core") is True


def test_core_not_in_keyword_set():
    # "core" is a vendor sentinel, not a future-syntax keyword.
    assert "core" not in RESERVED_KEYWORDS


@pytest.mark.parametrize("name", ["dummy", "qmm", "quantum_machines", "qdac", "my_vendor"])
def test_non_reserved_vendor_names(name):
    assert is_reserved_vendor(name) is False


@pytest.mark.parametrize("keyword", ["if", "while", "where", "return", "true"])
def test_reserved_keyword_is_reserved_vendor(keyword):
    # Every reserved identifier keyword is also a reserved vendor name.
    assert is_reserved_vendor(keyword) is True


def test_empty_string_not_reserved():
    assert is_reserved_keyword("") is False
    assert is_reserved_vendor("") is False


def test_format_keywords_in_use_are_reserved():
    """Keywords the ``.qp`` grammar uses are rejected as identifiers.

    A variable named ``for`` would collide with the loop header grammar, ``var var`` with
    declarations, and ``and``/``or``/``not`` with expression keywords.
    """
    import pytest  # ruff: ignore[import-outside-top-level]

    from qprogram import QProgram  # ruff: ignore[import-outside-top-level]
    from qprogram.errors import InvalidVariableIdError  # ruff: ignore[import-outside-top-level]

    p = QProgram()
    for keyword in ("var", "for", "in", "and", "or", "not"):
        with pytest.raises(InvalidVariableIdError):
            p.variable(keyword)
