"""Tests for VendorNamespace base class."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest

from qprogram import MeasurementHandle, QProgram, ValidationError
from qprogram.buses import BusSchema
from qprogram.operations.operation import MeasurementOperation, Operation
from qprogram.vendor import VendorNamespace

if TYPE_CHECKING:
    from collections.abc import Iterator

# A minimal vendor op set we can register temporarily.


class _ToyOp(Operation):
    """Toy op to test _append: takes a bus + a value."""

    def __init__(self, bus: str, value: int) -> None:
        self.bus = bus
        self.value = value


class _ToyMeasurement(MeasurementOperation):
    """Toy measurement op for testing _append_measurement."""

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("weights",)

    def __init__(self, bus: str, weights: str, handle: MeasurementHandle) -> None:
        self.bus = bus
        self.weights = weights
        self.handle = handle
        self.returns: tuple[str, ...] = ()


class _ToyNamespace(VendorNamespace):
    """A vendor namespace exposing _ToyOp and _ToyMeasurement."""

    def toy(self, bus: str, value: int) -> None:
        self._append(_ToyOp(bus=bus, value=value))

    def toy_measurement(self, bus: str, weights: str, *, name: str | None = None) -> MeasurementHandle:
        return self._append_measurement(_ToyMeasurement, bus=bus, weights=weights, name=name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def toy_program() -> Iterator[QProgram]:
    """A QProgram with a temporary 'toy' vendor namespace registered for the test."""
    # Register only for this fixture's duration; cleanup via teardown.
    QProgram.register_vendor("toy", _ToyNamespace)
    yield QProgram()
    # Best-effort cleanup: remove the registry entry.
    QProgram._vendor_registry.pop("toy", None)


def test_namespace_attached_on_first_access(toy_program: QProgram):
    ns = toy_program.toy
    assert isinstance(ns, _ToyNamespace)


def test_namespace_cached_per_instance(toy_program: QProgram):
    ns1 = toy_program.toy
    ns2 = toy_program.toy
    assert ns1 is ns2


def test_namespace_appends_via_append(toy_program: QProgram):
    toy_program.toy.toy("bus1", value=42)  # ty:ignore[unresolved-attribute]
    op = toy_program.body.elements[0]
    assert isinstance(op, _ToyOp)
    assert op.bus == "bus1"
    assert op.value == 42


def test_append_validates_busref():
    # Build a program with one schema and try to append a vendor op carrying
    # a BusRef from a different schema. Should raise via _validate_bus.
    schema_a = BusSchema.transmon()
    schema_b = BusSchema.transmon()
    QProgram.register_vendor("toy", _ToyNamespace)
    try:
        p = QProgram(schema=schema_a)
        with pytest.raises(ValidationError, match="different BusSchema"):
            # ``_ToyOp.bus`` is typed str but accepts a BusRef (subclasses str);
            # the vendor's _append walks attribute values and validates.
            p.toy.toy(schema_b.q[0].drive, value=1)  # ty:ignore[unresolved-attribute]
    finally:
        QProgram._vendor_registry.pop("toy", None)


def test_append_measurement_returns_handle(toy_program: QProgram):
    handle = toy_program.toy.toy_measurement("readout", "weights")  # ty:ignore[unresolved-attribute]
    assert isinstance(handle, MeasurementHandle)
    assert handle.name == "m0"


def test_append_measurement_with_explicit_name(toy_program: QProgram):
    handle = toy_program.toy.toy_measurement("readout", "weights", name="rabi")  # ty:ignore[unresolved-attribute]
    assert handle.name == "rabi"


def test_append_measurement_shares_counter_with_core_measure(transmon_schema):
    """Vendor measurement on the same bus picks up the next name after a core measure on that bus."""
    QProgram.register_vendor("toy", _ToyNamespace)
    try:
        p = QProgram(schema=transmon_schema)
        m_core = p.measure(transmon_schema.q[0].readout, "r", "w")
        m_vendor = p.toy.toy_measurement(transmon_schema.q[0].readout, "weights")  # ty:ignore[unresolved-attribute]
        assert m_core.name == "q0/readout/m0"
        assert m_vendor.name == "q0/readout/m1"
    finally:
        QProgram._vendor_registry.pop("toy", None)


def test_append_validates_list_of_busrefs():
    """Sync-like ops carry a list of buses; _append should validate each."""
    schema_a = BusSchema.transmon()
    schema_b = BusSchema.transmon()

    class _ListOp(Operation):
        BUS_ATTRS: ClassVar[tuple[str, ...]] = ("targets",)

        def __init__(self, targets: list) -> None:
            self.targets = targets

    class _NS(VendorNamespace):
        def list_op(self, targets: list) -> None:
            self._append(_ListOp(targets=targets))

    QProgram.register_vendor("listns", _NS)
    try:
        p = QProgram(schema=schema_a)
        with pytest.raises(ValidationError, match="different BusSchema"):
            p.listns.list_op([schema_b.q[0].drive])  # ty:ignore[unresolved-attribute]
    finally:
        QProgram._vendor_registry.pop("listns", None)


# ---------------------------------------------------------------------------
# register_vendor enforcement
# ---------------------------------------------------------------------------


def test_register_vendor_rejects_reserved_names():
    with pytest.raises(ValueError, match="reserved"):
        QProgram.register_vendor("core", _ToyNamespace)
    with pytest.raises(ValueError, match="reserved"):
        QProgram.register_vendor("if", _ToyNamespace)


def test_register_vendor_rejects_qprogram_attribute_collision():
    """A vendor named like a QProgram attribute would be silently shadowed forever.

    Covers both class attributes (methods, properties) and the public *instance* attributes
    assigned in ``__init__`` (``label``, ``description``) — instance lookup also wins over
    ``__getattr__`` vendor dispatch.
    """
    for taken in ("play", "measure", "body", "variables", "register_vendor", "label", "description"):
        with pytest.raises(ValueError, match="collides with a QProgram attribute"):
            QProgram.register_vendor(taken, _ToyNamespace)


def test_register_vendor_rejects_different_class_under_taken_name():
    class _OtherNamespace(VendorNamespace):
        pass

    QProgram.register_vendor("collide_ns", _ToyNamespace)
    try:
        with pytest.raises(ValueError, match="already registered"):
            QProgram.register_vendor("collide_ns", _OtherNamespace)
    finally:
        QProgram._vendor_registry.pop("collide_ns", None)


def test_register_vendor_same_class_is_idempotent():
    QProgram.register_vendor("idem_ns", _ToyNamespace)
    try:
        QProgram.register_vendor("idem_ns", _ToyNamespace)  # no raise
        assert QProgram._vendor_registry["idem_ns"] is _ToyNamespace
    finally:
        QProgram._vendor_registry.pop("idem_ns", None)
