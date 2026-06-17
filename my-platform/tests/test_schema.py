"""Tests for the RF-switch schema and for combining it with a flux-tunable transmon via ``+``."""

from __future__ import annotations

import pytest

from qprogram import QProgram, dumps, loads
from qprogram.buses import BusNaming, BusRef, BusSchema, FluxTunableTransmonSchema
from qprogram.errors import ValidationError

from my_platform import MyPlatform
from my_platform.schema import RFSwitchSchema


def test_rf_switch_schema_kind():
    assert RFSwitchSchema.KIND == "rf_switch"


def test_rf_switch_bus_refs():
    schema = RFSwitchSchema()
    ref = schema.switch[0].rf
    assert isinstance(ref, BusRef)
    assert str(ref) == "switch0/rf"
    assert str(schema.switch[1].rf) == "switch1/rf"


def test_rf_switch_bus_metadata():
    schema = RFSwitchSchema()
    ref = schema.switch[3].rf
    assert ref.element == "switch"
    assert ref.idx == 3
    assert ref.kind == "rf"
    assert ref.channel == "single"
    assert ref.acquires is False
    assert ref.schema is schema


def test_rf_switch_custom_naming():
    schema = RFSwitchSchema(naming=BusNaming("{kind}_{element}{index}"))
    assert str(schema.switch[0].rf) == "rf_switch0"


# -- combining flux-tunable transmon + RF switch via the core ``+`` operator -----------------------


def test_combined_schema_has_both_element_families():
    schema = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    # Qubit buses from the flux-tunable-transmon preset…
    assert str(schema.q[0].drive) == "q0/drive"
    assert str(schema.q[0].readout) == "q0/readout"
    assert str(schema.q[0].flux) == "q0/flux"
    # …plus the RF-switch element unioned in.
    assert str(schema.switch[0].rf) == "switch0/rf"
    assert set(schema.elements) == {"q", "switch"}


def test_combined_schema_refs_are_tagged_with_combined_schema():
    schema = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    assert schema.switch[0].rf.schema is schema
    assert schema.q[0].flux.schema is schema


def test_combined_via_class_addition():
    """The class-level form (metaclass ``+``) works too and yields the same combined schema."""
    schema = FluxTunableTransmonSchema + RFSwitchSchema
    assert set(schema.elements) == {"q", "switch"}
    assert str(schema.q[0].drive) == "q0/drive"
    assert str(schema.switch[0].rf) == "switch0/rf"


def test_combined_schema_round_trips_structurally():
    schema = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    prog = QProgram(label="rt", schema=schema)
    prog.set_offset(schema.q[0].flux, 0.1)
    prog.wait(schema.switch[0].rf, 16)
    reloaded = loads(dumps(prog))
    # Reloaded schema is a generic BusSchema with the same structural content.
    assert isinstance(reloaded.schema, BusSchema)
    assert set(reloaded.schema.elements) == {"q", "switch"}
    assert reloaded.schema.elements["switch"].buses == {"rf": ("single", False)}
    assert reloaded.body == prog.body


def test_platform_schema_is_the_combined_schema():
    schema = MyPlatform().get_bus_schema()
    assert set(schema.elements) == {"q", "switch"}
    assert str(schema.q[0].drive) == "q0/drive"
    assert str(schema.switch[0].rf) == "switch0/rf"


def test_foreign_switch_bus_rejected():
    """A switch BusRef from another schema instance must be rejected by program-side validation."""
    schema_a = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    schema_b = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
    prog = QProgram(label="foreign", schema=schema_a)
    foreign = schema_b.switch[0].rf
    with pytest.raises(ValidationError):
        prog.myplatform.set_rf_switch(foreign, 1)
