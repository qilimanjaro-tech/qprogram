"""Tests for the serialization registry (operation/block/sweep-generator specs)."""

from __future__ import annotations

import pytest

import numpy as np

from qprogram.blocks import Block, ForLoop, Loop
from qprogram.operations import Operation, Play
from qprogram.serialization.registry import (
    BlockSpec,
    OperationSpec,
    SweepGeneratorSpec,
    get_block_spec,
    get_block_spec_by_class,
    get_operation_class,
    get_operation_spec,
    get_operation_spec_by_class,
    get_operation_vendor_name,
    get_sweep_generator_spec,
    get_sweep_generator_spec_by_class,
    get_vendor_version,
    get_waveform_class,
    register_block,
    register_operation,
    register_sweep_generator,
    register_vendor_operation,
    register_vendor_version,
    register_waveform,
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
    assert spec.qualified_name == "play"  # type: ignore[union-attr]


def test_vendor_operation_qualified_name():
    spec = OperationSpec(name="acquire", vendor="qblox", cls=Play)
    assert spec.qualified_name == "qblox.acquire"


def test_get_operation_by_class():
    spec = get_operation_spec_by_class(Play)
    assert spec is not None
    assert spec.name == "play"


def test_get_operation_class_legacy():
    # Legacy lookup function (kept for back-compat).
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
        from qprogram.serialization import registry
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
        from qprogram.serialization import registry
        registry._operation_specs_by_qualified.pop(("temp_vendor_2", "_test_op"), None)
        registry._operation_specs_by_class.pop(_Op, None)


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
    from qprogram.blocks import Average
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
        from qprogram.serialization import registry
        registry._block_specs_by_name.pop("_test_block", None)
        registry._block_specs_by_class.pop(_Block, None)


# ---------------------------------------------------------------------------
# Sweep-generator registration & lookups
# ---------------------------------------------------------------------------


def test_sweep_generator_range_registered():
    spec = get_sweep_generator_spec("range")
    assert spec is not None
    assert spec.block_cls is ForLoop
    assert spec.write is not None


def test_sweep_generator_values_registered():
    spec = get_sweep_generator_spec("values")
    assert spec is not None
    assert spec.block_cls is Loop


def test_sweep_generator_file_registered():
    spec = get_sweep_generator_spec("file")
    assert spec is not None
    assert spec.block_cls is Loop
    # ``file`` is parse-only — no write callback.
    assert spec.write is None


def test_get_sweep_generator_unknown_returns_none():
    assert get_sweep_generator_spec("nonexistent_gen") is None


def test_get_sweep_generator_by_class():
    spec = get_sweep_generator_spec_by_class(ForLoop)
    assert spec is not None
    assert spec.name == "range"


def test_get_sweep_generator_by_class_loop():
    # Loop has both 'values' (write) and 'file' (parse-only); write side
    # picks 'values' (registered second with a write callback).
    spec = get_sweep_generator_spec_by_class(Loop)
    assert spec is not None
    assert spec.name == "values"


def test_sweep_generator_spec_default_write_none():
    """A parse-only registration leaves write=None and skips by-class indexing."""
    class _ParseOnlyLoop(Block):
        pass

    def _parse(_var, _args, _ctx):
        return _ParseOnlyLoop()

    register_sweep_generator("_test_sweep", _ParseOnlyLoop, parse=_parse)
    try:
        spec = get_sweep_generator_spec("_test_sweep")
        assert spec is not None
        assert spec.write is None
        assert get_sweep_generator_spec_by_class(_ParseOnlyLoop) is None
    finally:
        from qprogram.serialization import registry
        registry._sweep_generator_specs_by_name.pop("_test_sweep", None)


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
        from qprogram.serialization import registry
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

        def envelope(self, resolution: int = 1):  # noqa: ARG002
            return np.array([self.x])

        def get_duration(self) -> int:
            return 1

    cls = register_waveform(_TestWaveform)
    try:
        assert cls is _TestWaveform  # decorator returns the class
        assert get_waveform_class("_TestWaveform") is _TestWaveform
    finally:
        from qprogram.serialization import registry
        registry._waveform_registry.pop("_TestWaveform", None)


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


def test_block_spec_frozen():
    spec = BlockSpec(name="test", cls=Block)
    with pytest.raises(Exception):
        spec.name = "x"  # type: ignore[misc]


def test_operation_spec_frozen():
    spec = OperationSpec(name="play", vendor=None, cls=Play)
    with pytest.raises(Exception):
        spec.name = "x"  # type: ignore[misc]


def test_sweep_generator_spec_frozen():
    spec = SweepGeneratorSpec(name="r", block_cls=ForLoop, parse=lambda *_a: None)
    with pytest.raises(Exception):
        spec.name = "x"  # type: ignore[misc]
