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
"""Tests for entry-point vendor discovery — ``loads()`` auto-activates an installed-but-unimported extension.

A ``.qp`` file declares its vendor dependencies via ``require <vendor> <major.minor>``. Discovery
frees the caller from importing ``qprogram_<vendor>`` first — the parser finds the extension
through its ``qprogram.vendors`` entry point and imports it on demand, so the file is a
self-contained contract. These tests drive the wiring with a fake entry point (no real installed
distribution needed) plus the in-tree dummy vendor for the success path.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import _dummy_vendor
import pytest

import qprogram as qp
from qprogram.serialization import registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class _FakeEntryPoint:
    """Minimal stand-in for :class:`importlib.metadata.EntryPoint` — only ``name``/``value``/``load``."""

    def __init__(self, name: str, value: str, loader: Callable[[], object]) -> None:
        self.name = name
        self.value = value
        self._loader = loader

    def load(self) -> object:
        return self._loader()


@pytest.fixture
def dummy_inactive() -> Iterator[None]:
    """Guarantee the in-tree dummy vendor is *not* registered (so discovery must do it)."""
    _dummy_vendor.deactivate()
    registry.clear_vendor_discovery_cache()
    try:
        yield
    finally:
        _dummy_vendor.deactivate()
        registry.clear_vendor_discovery_cache()


def _require_doc(vendor: str, version: str = "0.0", body: str = "") -> str:
    return f"#!QProgram 1.0\n\nrequire {vendor} {version}\n\nbody:\n{body}"


# ---------------------------------------------------------------------------
# try_activate_vendor — unit behavior
# ---------------------------------------------------------------------------


def test_try_activate_unknown_vendor_returns_false(monkeypatch):
    monkeypatch.setattr(registry, "_vendor_entry_points", dict)
    assert registry.try_activate_vendor("definitely_absent") is False


def test_try_activate_already_registered_skips_discovery(monkeypatch, dummy_vendor):  # ruff: ignore[unused-function-argument]
    """An already-imported vendor returns True without scanning entry points at all."""

    def boom() -> dict:
        msg = "discovery must not run for an already-registered vendor"
        raise AssertionError(msg)

    monkeypatch.setattr(registry, "_vendor_entry_points", boom)
    assert registry.try_activate_vendor(_dummy_vendor.VENDOR_NAME) is True


def test_try_activate_imports_via_entry_point(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    calls: list[str] = []

    def loader() -> object:
        _dummy_vendor.activate()
        calls.append("loaded")
        return sys.modules["_dummy_vendor"]

    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {_dummy_vendor.VENDOR_NAME: _FakeEntryPoint(_dummy_vendor.VENDOR_NAME, "_dummy_vendor", loader)},
    )
    assert registry.get_vendor_version(_dummy_vendor.VENDOR_NAME) is None
    assert registry.try_activate_vendor(_dummy_vendor.VENDOR_NAME) is True
    assert calls == ["loaded"]
    assert registry.get_vendor_version(_dummy_vendor.VENDOR_NAME) == _dummy_vendor.VENDOR_VERSION


def test_try_activate_broken_extension_raises(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    def loader() -> object:
        msg = "boom"
        raise ImportError(msg)

    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {"broken": _FakeEntryPoint("broken", "broken_pkg", loader)},
    )
    with pytest.raises(qp.VendorActivationError, match="failed to import"):
        registry.try_activate_vendor("broken")


def test_try_activate_extension_without_version_raises(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    """An entry point that imports but registers nothing is a packaging bug, surfaced clearly."""
    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {"silent": _FakeEntryPoint("silent", "silent_pkg", object)},
    )
    with pytest.raises(qp.VendorActivationError, match="did not register a protocol version"):
        registry.try_activate_vendor("silent")


# ---------------------------------------------------------------------------
# loads() / load() integration
# ---------------------------------------------------------------------------


def test_loads_auto_activates_and_parses_vendor_op(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    """A ``require`` line imports the extension before the body is parsed.

    A vendor operation in the body then resolves even though the extension was never imported by
    the caller.
    """

    def loader() -> object:
        _dummy_vendor.activate()
        return sys.modules["_dummy_vendor"]

    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {_dummy_vendor.VENDOR_NAME: _FakeEntryPoint(_dummy_vendor.VENDOR_NAME, "_dummy_vendor", loader)},
    )
    p = qp.loads(_require_doc("dummy", body='  dummy.set_markers "bus" "0001"\n'))
    from _dummy_vendor import DummySetMarkers  # ruff: ignore[import-outside-top-level]

    assert isinstance(p.body.elements[0], DummySetMarkers)


def test_load_from_file_auto_activates(monkeypatch, dummy_inactive, tmp_path):  # ruff: ignore[unused-function-argument]
    def loader() -> object:
        _dummy_vendor.activate()
        return sys.modules["_dummy_vendor"]

    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {_dummy_vendor.VENDOR_NAME: _FakeEntryPoint(_dummy_vendor.VENDOR_NAME, "_dummy_vendor", loader)},
    )
    path = tmp_path / "prog.qp"
    path.write_text(_require_doc("dummy"))
    p = qp.load(str(path))
    assert p is not None
    assert registry.get_vendor_version("dummy") == _dummy_vendor.VENDOR_VERSION


def test_loads_auto_activate_false_does_not_discover(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    """``auto_activate=False`` never imports anything; an unimported vendor is a hard error."""

    def boom() -> dict:
        msg = "discovery must not run when auto_activate=False"
        raise AssertionError(msg)

    monkeypatch.setattr(registry, "_vendor_entry_points", boom)
    doc = _require_doc("dummy")
    with pytest.raises(qp.ParseError, match="auto-activation is disabled"):
        qp.loads(doc, auto_activate=False)


def test_loads_missing_vendor_without_entry_point_raises(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    monkeypatch.setattr(registry, "_vendor_entry_points", dict)
    doc = _require_doc("ghostvendor", "0.1")
    with pytest.raises(qp.ParseError, match=r"qprogram\.vendors' entry point"):
        qp.loads(doc)


def test_loads_broken_extension_wrapped_as_parse_error(monkeypatch, dummy_inactive):  # ruff: ignore[unused-function-argument]
    def loader() -> object:
        msg = "kaboom"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        registry,
        "_vendor_entry_points",
        lambda: {"broken": _FakeEntryPoint("broken", "broken_pkg", loader)},
    )
    doc = _require_doc("broken", "0.1")
    with pytest.raises(qp.ParseError, match="failed to import"):
        qp.loads(doc)


def test_loads_already_imported_vendor_needs_no_discovery(monkeypatch, dummy_vendor):  # ruff: ignore[unused-function-argument]
    """When the vendor is already imported, loads() never touches entry points."""

    def boom() -> dict:
        msg = "should not scan entry points"
        raise AssertionError(msg)

    monkeypatch.setattr(registry, "_vendor_entry_points", boom)
    p = qp.loads(_require_doc("dummy", body='  dummy.set_markers "bus" "0001"\n'))
    assert p is not None


# ---------------------------------------------------------------------------
# The real entry-point scan (unpatched)
# ---------------------------------------------------------------------------


def test_real_entry_point_scan_returns_cached_dict():
    """The genuine scan returns a dict and is memoized (same object across calls)."""
    registry.clear_vendor_discovery_cache()
    first = registry._vendor_entry_points()
    assert isinstance(first, dict)
    assert registry._vendor_entry_points() is first  # Cached.
