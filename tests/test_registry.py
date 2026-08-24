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
"""Tests for the serialization registry (operation / block / sweep-source specs)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from qprogram.blocks import Average, Block
from qprogram.operations import Operation, Play
from qprogram.protocol import CAPABILITY_REGISTRY
from qprogram.serialization import registry
from qprogram.serialization.registry import (
    BlockSpec,
    OperationSpec,
    get_block_spec,
    get_block_spec_by_class,
    get_operation_class,
    get_operation_spec,
    get_operation_spec_by_class,
    get_operation_vendor_name,
    get_sweep_source_class,
    get_vendor_version,
    get_waveform_class,
    register_block,
    register_operation,
    register_sweep_source,
    register_vendor_block,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
)
from qprogram.sweeps import (
    Concat,
    File,
    Linspace,
    Logspace,
    Range,
    Repeat,
    Rotate,
    SweepSource,
    Values,
)
from qprogram.waveforms import Square
from qprogram.waveforms.waveform import Waveform

# ---------------------------------------------------------------------------
# Operation registration & lookups
# ---------------------------------------------------------------------------


def test_core_operations_registered():
    spec = get_operation_spec(None, "play")
    assert spec is not None
    assert spec.cls is Play


def test_operation_qualified_name_no_vendor():
    spec = get_operation_spec(None, "play")
    assert spec is not None
    assert spec.qualified_name == "play"


def test_vendor_operation_qualified_name():
    spec = OperationSpec(name="acquire", vendor="dummy", cls=Play)
    assert spec.qualified_name == "dummy.acquire"


def test_get_operation_by_class():
    spec = get_operation_spec_by_class(Play)
    assert spec is not None
    assert spec.name == "play"


def test_get_operation_class_returns_none_for_unknown():
    assert get_operation_class("nonexistent", "x") is None


def test_get_operation_vendor_name_for_core_returns_none():
    assert get_operation_vendor_name(Play) is None


def test_register_operation_rejects_core_vendor():
    class _Op(Operation):
        def __init__(self, x: int) -> None:
            self.x = x

    with pytest.raises(ValueError, match="reserved"):
        register_operation("x", _Op, vendor="core")


def test_register_operation_rejects_reserved_keyword_vendor():
    class _Op(Operation):
        def __init__(self, x: int) -> None:
            self.x = x

    with pytest.raises(ValueError, match="reserved"):
        register_operation("x", _Op, vendor="if")


def test_register_vendor_operation_alias():
    class _Op(Operation):
        def __init__(self, x: int) -> None:
            self.x = x

    register_vendor_operation("temp_vendor", "temp_op", _Op)
    spec = get_operation_spec("temp_vendor", "temp_op")
    try:
        assert spec is not None
        assert spec.cls is _Op
    finally:
        # Cleanup: remove the temp registration (registry is module-state).
        registry._operation_specs_by_qualified.pop(("temp_vendor", "temp_op"), None)
        registry._operation_specs_by_class.pop(_Op, None)


def test_register_operation_returns_class():
    class _Op(Operation):
        def __init__(self, x: int) -> None:
            self.x = x

    result = register_operation("_test_op", _Op, vendor="temp_vendor_2")
    try:
        assert result is _Op
    finally:
        registry._operation_specs_by_qualified.pop(("temp_vendor_2", "_test_op"), None)
        registry._operation_specs_by_class.pop(_Op, None)


def test_register_operation_rejects_different_class_under_taken_name():
    """Re-registering 'play' with a foreign class must fail loudly, not silently hijack it."""

    class _FakePlay(Operation):
        def __init__(self, bus: str) -> None:
            self.bus = bus

    with pytest.raises(ValueError, match="already registered"):
        register_operation("play", _FakePlay)


def test_register_operation_same_class_is_idempotent():
    """The owner may re-register its own class (refreshing callbacks)."""

    class _Op(Operation):
        def __init__(self, x: int) -> None:
            self.x = x

    register_operation("_idem_op", _Op, vendor="temp_vendor_3")
    try:
        register_operation("_idem_op", _Op, vendor="temp_vendor_3")  # no raise
        spec = get_operation_spec("temp_vendor_3", "_idem_op")
        assert spec is not None
        assert spec.cls is _Op
    finally:
        registry._operation_specs_by_qualified.pop(("temp_vendor_3", "_idem_op"), None)
        registry._operation_specs_by_class.pop(_Op, None)


def test_register_block_rejects_different_class_under_taken_name():
    from qprogram.blocks import Block as _Block  # ruff: ignore[import-outside-top-level]

    class _FakeAverage(_Block):
        pass

    with pytest.raises(ValueError, match="already registered"):
        register_block("average", _FakeAverage)


def test_register_waveform_rejects_different_class_under_taken_name():
    from qprogram.waveforms import Waveform as _Waveform  # ruff: ignore[import-outside-top-level]

    class Gaussian(_Waveform):
        def envelope(self, resolution: int = 1):
            raise NotImplementedError

        def get_duration(self) -> int:
            raise NotImplementedError

    with pytest.raises(ValueError, match="already registered"):
        register_waveform(Gaussian)


def test_register_vendor_version_rejects_reserved_names():
    with pytest.raises(ValueError, match="reserved"):
        register_vendor_version("core", "1.0.0")
    with pytest.raises(ValueError, match="reserved"):
        register_vendor_version("if", "1.0.0")


def test_register_vendor_version_rejects_malformed_versions():
    with pytest.raises(ValueError, match=r"major\.minor"):
        register_vendor_version("okvendor", "1")
    with pytest.raises(ValueError, match="non-integer"):
        register_vendor_version("okvendor", "a.b")
    assert registry.get_vendor_version("okvendor") is None  # nothing leaked into the registry


# ---------------------------------------------------------------------------
# Block registration & lookups
# ---------------------------------------------------------------------------


def test_block_average_registered():
    spec = get_block_spec("average")
    assert spec is not None


def test_block_block_registered():
    spec = get_block_spec("block")
    assert spec is not None
    assert spec.cls is Block


def test_get_block_spec_unknown_returns_none():
    assert get_block_spec("nonexistent_block") is None


def test_get_block_spec_by_class():

    spec = get_block_spec_by_class(Average)
    assert spec is not None
    assert spec.name == "average"


def test_register_block_returns_class():
    class _Block(Block):
        pass

    result = register_block("_test_block", _Block)
    try:
        assert result is _Block
        assert get_block_spec("_test_block") is not None
    finally:
        registry._block_specs_by_name.pop("_test_block", None)
        registry._block_specs_by_class.pop(_Block, None)


# ---------------------------------------------------------------------------
# Vendor versions
# ---------------------------------------------------------------------------


def test_get_vendor_version_unknown_returns_none():
    assert get_vendor_version("nonexistent_vendor") is None


def test_register_vendor_version():
    register_vendor_version("temp_test_vendor", "1.2.3")
    try:
        assert get_vendor_version("temp_test_vendor") == "1.2.3"
    finally:
        registry._vendor_versions.pop("temp_test_vendor", None)


# ---------------------------------------------------------------------------
# Waveform registration
# ---------------------------------------------------------------------------


def test_waveform_builtins_registered():
    assert get_waveform_class("Square") is Square


def test_get_waveform_class_unknown_returns_none():
    assert get_waveform_class("NoSuchWaveform") is None


def test_register_waveform_decorator():
    class _TestWaveform(Waveform):
        def __init__(self, x: float) -> None:
            self.x = x

        def envelope(self, resolution: int = 1):  # ruff: ignore[unused-method-argument]
            return np.array([self.x])

        def get_duration(self) -> int:
            return 1

    cls = register_waveform(_TestWaveform)
    try:
        assert cls is _TestWaveform  # decorator returns the class
        assert get_waveform_class("_TestWaveform") is _TestWaveform
    finally:
        registry._waveform_registry.pop("_TestWaveform", None)


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


def test_block_spec_frozen():
    spec = BlockSpec(name="test", cls=Block)
    # ``setattr`` is the type-system-friendly way to exercise the freeze: the static
    # checkers see a read-only property and reject ``spec.name = "x"``.
    with pytest.raises(FrozenInstanceError):
        setattr(spec, "name", "x")  # ruff: ignore[set-attr-with-constant]


def test_operation_spec_frozen():
    spec = OperationSpec(name="play", vendor=None, cls=Play)
    with pytest.raises(FrozenInstanceError):
        setattr(spec, "name", "x")  # ruff: ignore[set-attr-with-constant]


# ---------------------------------------------------------------------------
# Vendor blocks
# ---------------------------------------------------------------------------


def test_register_vendor_block_keys_by_qualified_keyword():
    """The registry key is the dotted token the parser reads off the line."""

    class _VBlock(Block):
        pass

    register_vendor_block("_testvendor", "spin", _VBlock)
    try:
        spec = get_block_spec("_testvendor.spin")
        assert spec is not None
        assert spec.cls is _VBlock
        assert spec.vendor == "_testvendor"
        assert spec.name == "spin"
        assert spec.qualified_name == "_testvendor.spin"
        # The bare name must NOT be claimed — that namespace belongs to core.
        assert get_block_spec("spin") is None
    finally:
        registry._block_specs_by_name.pop("_testvendor.spin", None)
        registry._block_specs_by_class.pop(_VBlock, None)


def test_core_block_spec_has_no_vendor():
    spec = get_block_spec("average")
    assert spec is not None
    assert spec.vendor is None
    assert spec.qualified_name == "average"


def test_register_block_rejects_reserved_vendor_name():
    class _VBlock(Block):
        pass

    with pytest.raises(ValueError, match="reserved"):
        register_vendor_block("core", "spin", _VBlock)


def test_vendor_block_keyword_can_coexist_with_a_same_named_core_block():
    """``myvendor.block:`` and core ``block:`` are distinct keywords."""

    class _VBlock(Block):
        pass

    register_vendor_block("_testvendor2", "block", _VBlock)
    try:
        assert get_block_spec("_testvendor2.block").cls is _VBlock  # type: ignore[union-attr]
        assert get_block_spec("block").cls is Block  # type: ignore[union-attr]
    finally:
        registry._block_specs_by_name.pop("_testvendor2.block", None)
        registry._block_specs_by_class.pop(_VBlock, None)


# ---------------------------------------------------------------------------
# Sweep-source registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("Range", Range),
        ("Values", Values),
        ("Linspace", Linspace),
        ("Logspace", Logspace),
        ("File", File),
        ("Repeat", Repeat),
        ("Rotate", Rotate),
        ("Concat", Concat),
    ],
)
def test_builtin_sweep_sources_registered_by_class_name(name, cls):
    assert get_sweep_source_class(name) is cls


def test_get_sweep_source_class_unknown_returns_none():
    assert get_sweep_source_class("NoSuchSource") is None


def test_register_sweep_source_returns_the_class_so_it_can_decorate():
    class _TestSource(SweepSource):
        KIND = "arbitrary"
        TOKEN = "sweep._testsource"

        def length(self) -> int:
            return 1

        def values(self):
            return np.array([0.0])

    try:
        assert register_sweep_source(_TestSource) is _TestSource
        assert get_sweep_source_class("_TestSource") is _TestSource
    finally:
        registry._sweep_source_registry.pop("_TestSource", None)
        CAPABILITY_REGISTRY.discard("sweep._testsource")


def test_register_sweep_source_also_registers_its_capability_token():
    """One call, both registries — mirroring register_waveform_token."""

    class _TokenSource(SweepSource):
        KIND = "linear"
        TOKEN = "sweep._tokensource"

        def length(self) -> int:
            return 1

        def values(self):
            return np.array([0.0])

    try:
        register_sweep_source(_TokenSource)
        assert "sweep._tokensource" in CAPABILITY_REGISTRY
    finally:
        registry._sweep_source_registry.pop("_TokenSource", None)
        CAPABILITY_REGISTRY.discard("sweep._tokensource")


def test_register_sweep_source_rejects_a_different_class_under_a_taken_name():
    class Range(SweepSource):
        KIND = "linear"
        TOKEN = "sweep.range"

        def length(self) -> int:
            return 1

        def values(self):
            return np.array([0.0])

    with pytest.raises(ValueError, match="already registered"):
        register_sweep_source(Range)


def test_register_sweep_source_same_class_twice_is_a_noop():
    """Import-time side-effect modules may run twice; re-registering the same class must be fine."""
    assert register_sweep_source(Range) is Range
