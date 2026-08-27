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
"""A minimal in-tree dummy vendor extension for the test suite.

Mirrors the structure that a real vendor package (e.g. one that ships outside
``qprogram``) would follow, but without pulling in any external dependency:

- a :class:`DummyNamespace` exposing typed methods,
- a :class:`DummyMixin` providing a typed ``.dummy`` property,
- a pre-combined :class:`DummyQProgram` (``DummyMixin`` + base ``QProgram``),
- a profile bundle registered under :data:`DUMMY_DEFAULT_V1_NAME`,
- an :func:`activate` / :func:`deactivate` pair tests can call from fixtures
  to install / remove the vendor at well-defined points.

Tests should never depend on activation as a module import side-effect;
prefer the :func:`dummy_vendor` pytest fixture in :mod:`conftest`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementField, MeasurementOperation, Operation, normalize_fields
from qprogram.protocol import (
    PROFILE_REGISTRY,
    Diagnostic,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)
from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization._specs import make_measurement_op_parse, measurement_op_serialize
from qprogram.serialization.registry import (
    register_vendor_operation,
    register_vendor_version,
)
from qprogram.vendor import VendorNamespace

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.blocks.block import Block
    from qprogram.result import MeasurementHandle
    from qprogram.variable import Expression
    from qprogram.waveforms.waveform import IQWaveform


VENDOR_NAME = "dummy"
VENDOR_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class DummyAcquire(MeasurementOperation):
    """Measurement-style op without an emitted readout pulse.

    Mirrors the shape of a typical vendor acquisition op.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("weights",)

    def __init__(
        self,
        bus: str,
        weights: IQWaveform | str,
        handle: MeasurementHandle,
        fields: Iterable[MeasurementField] = (MeasurementField.IQ,),
    ) -> None:
        self.bus = bus
        self.weights = weights
        self.handle = handle
        self.fields: tuple[str, ...] = normalize_fields(fields)

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token  # ruff: ignore[import-outside-top-level]

        caps = super().required_capabilities() | {"vendor.dummy.acquire", "waveform.iq"}
        if isinstance(self.weights, str):
            caps.add("waveform.alias")
        else:
            tok = waveform_token(self.weights)
            if tok is not None:
                caps.add(tok)
        return caps


class DummySetMarkers(Operation):
    """Simple 1-1 vendor op with two scalar parameters."""

    def __init__(self, bus: str, mask: str) -> None:
        self.bus = bus
        self.mask = mask

    def required_capabilities(self) -> set[str]:
        return {"vendor.dummy.set_markers"}


class DummySetTrigger(Operation):
    """Vendor op whose optional parameters carry defaults."""

    def __init__(
        self,
        bus: str,
        duration: int,
        outputs: list[int] | int | None = None,
        position: str = "start",
    ) -> None:
        self.bus = bus
        self.duration = duration
        self.outputs = outputs
        self.position = position

    def required_capabilities(self) -> set[str]:
        return {"vendor.dummy.set_trigger"}


class DummyWaitTrigger(Operation):
    """Wait-for-trigger op."""

    def __init__(self, bus: str, duration: int, port: int | None = None) -> None:
        self.bus = bus
        self.duration = duration
        self.port = port

    def required_capabilities(self) -> set[str]:
        return {"vendor.dummy.wait_trigger"}


class DummyComposite(Operation):
    """A complex vendor op with multiple buses and waveforms."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus", "control_bus")
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights", "reset_pulse")

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.control_bus = control_bus
        self.reset_pulse = reset_pulse
        self.trigger_address = trigger_address

    def required_capabilities(self) -> set[str]:
        return {"vendor.dummy.composite", "waveform.iq"}


class DummySetThreshold(Operation):
    """Software-only vendor op: holds a scalar Expression."""

    def __init__(self, bus: str, value: float | Expression) -> None:
        self.bus = bus
        self.value = value

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        return {"vendor.dummy.set_threshold"} | expression_tokens(self.value)


# ---------------------------------------------------------------------------
# Namespace + mixin + pre-combined QProgram
# ---------------------------------------------------------------------------


class DummyNamespace(VendorNamespace):
    """Typed namespace exposing the dummy vendor ops."""

    def acquire(
        self,
        bus: str,
        weights: IQWaveform | str,
        fields: Iterable[MeasurementField] = (MeasurementField.IQ,),
        *,
        name: str | None = None,
    ) -> MeasurementHandle:
        return self._append_measurement(
            DummyAcquire,
            bus=bus,
            weights=weights,
            fields=fields,
            name=name,
        )

    def set_markers(self, bus: str, mask: str) -> None:
        self._append(DummySetMarkers(bus=bus, mask=mask))

    def set_trigger(
        self,
        bus: str,
        duration: int,
        outputs: list[int] | int | None = None,
        position: str = "start",
    ) -> None:
        self._append(DummySetTrigger(bus=bus, duration=duration, outputs=outputs, position=position))

    def wait_trigger(self, bus: str, duration: int, port: int | None = None) -> None:
        self._append(DummyWaitTrigger(bus=bus, duration=duration, port=port))

    def composite(  # ruff: ignore[too-many-arguments]
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        control_bus: str,
        reset_pulse: IQWaveform | str,
        trigger_address: int = 1,
    ) -> None:
        self._append(
            DummyComposite(
                bus=bus,
                waveform=waveform,
                weights=weights,
                control_bus=control_bus,
                reset_pulse=reset_pulse,
                trigger_address=trigger_address,
            )
        )

    def set_threshold(self, bus: str, value: float | Expression) -> None:
        self._append(DummySetThreshold(bus=bus, value=value))


class DummyMixin:
    """Mixin that adds a typed ``.dummy`` property to QProgram."""

    @property
    def dummy(self: _BaseQProgram) -> DummyNamespace:
        try:
            return object.__getattribute__(self, "_dummy_ns")
        except AttributeError:
            pass
        ns = DummyNamespace(self)
        object.__setattr__(self, "_dummy_ns", ns)
        return ns


class DummyQProgram(DummyMixin, _BaseQProgram):
    """QProgram pre-combined with :class:`DummyMixin` for typed access."""


# ---------------------------------------------------------------------------
# Profile bundle
# ---------------------------------------------------------------------------


def _reject_arbitrary_sweep_at_wait_duration(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic]:
    """Sample data-flow predicate: rejects arbitrary sweeps at ``Wait.duration``.

    Demonstrates how a vendor profile can encode a constraint that depends on
    how a variable is *used* downstream, not on the op or loop in isolation.
    """
    from qprogram.operations.wait import Wait  # ruff: ignore[import-outside-top-level]
    from qprogram.variable import Variable  # ruff: ignore[import-outside-top-level]

    if not isinstance(node, Wait):
        return
    if not isinstance(node.duration, Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(
            severity="error",
            code="dummy.arbitrary-wait-sweep",
            message=(
                f"Variable {node.duration.id!r} is swept with arbitrary "
                f"values and used at Wait.duration, which the dummy backend "
                f"does not support. Use a linear sweep source (Range / Linspace) or a constant duration."
            ),
            node=node,
        )


_CORE_OPS: frozenset[str] = frozenset(
    {
        "op.play",
        "op.measure",
        "op.wait",
        "op.sync",
        "op.set_frequency",
        "op.set_phase",
        "op.set_gain",
        "op.reset_phase",
        "op.set_offset",
        "op.set_parameter",
        "op.get_parameter",
    },
)
_BLOCKS: frozenset[str] = frozenset(
    {
        "block.block",
        "block.average",
        "block.sweep",
        "block.parallel",
        "block.conditional",
    },
)
_WAVEFORMS: frozenset[str] = frozenset(
    {
        "waveform.single",
        "waveform.iq",
        "waveform.alias",
        "waveform.arbitrary",
        "waveform.chained",
        "waveform.flat_top",
        "waveform.gaussian",
        "waveform.gaussian_drag_correction",
        "waveform.ramp",
        "waveform.snz",
        "waveform.square",
        "waveform.iq_drag",
        "waveform.iq_pair",
    },
)
_SWEEPS: frozenset[str] = frozenset(
    {
        "sweep.linear",
        "sweep.arbitrary",
        "sweep.range",
        "sweep.values",
        "sweep.linspace",
        "sweep.logspace",
        "sweep.file",
        "sweep.repeat",
        "sweep.rotate",
        "sweep.concat",
    },
)
_EXPRS: frozenset[str] = frozenset(
    {
        "expr.constant",
        "expr.variable",
        "expr.measurement_ref",
        "expr.binary_op",
        "expr.unary_op",
        "expr.comparison",
    },
)
_FIELDS: frozenset[str] = frozenset(
    {"measure.fields.iq", "measure.fields.raw", "measure.fields.state"},
)
_VENDOR_TOKENS: tuple[str, ...] = (
    "vendor.dummy.acquire",
    "vendor.dummy.set_markers",
    "vendor.dummy.set_trigger",
    "vendor.dummy.wait_trigger",
    "vendor.dummy.composite",
    "vendor.dummy.set_threshold",
)


def _build_profile() -> Profile:
    register_capability_tokens(*_VENDOR_TOKENS)
    profile = Profile(
        name="dummy-default-v1",
        version=(0, 1, 0),
        extends=None,
        capabilities=_CORE_OPS | _BLOCKS | _WAVEFORMS | _SWEEPS | _EXPRS | _FIELDS | frozenset(_VENDOR_TOKENS),
        limits={
            "max_loop_nesting": 8,
            "max_parallel_loops": 4,
            "min_wait_duration_ns": 4,
            "max_measurements": 1024,
        },
        predicates=(_reject_arbitrary_sweep_at_wait_duration,),
        vendor_versions={VENDOR_NAME: (0, 1, 0)},
    )
    register_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


_OPERATIONS: tuple[tuple[str, type[Operation], bool], ...] = (
    ("acquire", DummyAcquire, True),
    ("set_markers", DummySetMarkers, False),
    ("set_trigger", DummySetTrigger, False),
    ("wait_trigger", DummyWaitTrigger, False),
    ("composite", DummyComposite, False),
    ("set_threshold", DummySetThreshold, False),
)


def activate() -> Profile:
    """Register the dummy vendor namespace, version, operations, and profile.

    Returns the freshly built :class:`Profile`. On a repeat activation the
    registry keeps the first equal profile, so this is not necessarily the object
    :func:`resolve_profile` returns; call :func:`deactivate` first to override it.
    """
    _BaseQProgram.register_vendor(VENDOR_NAME, DummyNamespace)
    register_vendor_version(VENDOR_NAME, VENDOR_VERSION)
    for op_name, op_cls, is_measurement in _OPERATIONS:
        parse = make_measurement_op_parse(op_cls) if is_measurement else None
        serialize = measurement_op_serialize if is_measurement else None
        register_vendor_operation(VENDOR_NAME, op_name, op_cls, serialize=serialize, parse=parse)
    return _build_profile()


def deactivate() -> None:
    """Undo :func:`activate`, leaving the global registries clean."""
    from qprogram.serialization import registry  # ruff: ignore[import-outside-top-level]

    _BaseQProgram._vendor_registry.pop(VENDOR_NAME, None)
    registry._vendor_versions.pop(VENDOR_NAME, None)
    for op_name, op_cls, _ in _OPERATIONS:
        registry._operation_specs_by_qualified.pop((VENDOR_NAME, op_name), None)
        registry._operation_specs_by_class.pop(op_cls, None)
    PROFILE_REGISTRY.pop(DUMMY_DEFAULT_V1_NAME, None)


__all__ = [
    "DUMMY_DEFAULT_V1_NAME",
    "VENDOR_NAME",
    "VENDOR_VERSION",
    "DummyAcquire",
    "DummyComposite",
    "DummyMixin",
    "DummyNamespace",
    "DummyQProgram",
    "DummySetMarkers",
    "DummySetThreshold",
    "DummySetTrigger",
    "DummyWaitTrigger",
    "activate",
    "deactivate",
]

DUMMY_DEFAULT_V1_NAME = "dummy-default-v1"
