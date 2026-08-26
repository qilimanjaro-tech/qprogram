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
"""Tests for the BusSchema / BusRef / BusNaming machinery (qprogram.buses)."""

from __future__ import annotations

import copy
import pickle

import pytest

from qprogram import BusNaming, BusRef, BusSchema, QProgram, dumps, loads
from qprogram.buses import (
    CouplerFactory,
    ElementSchema,
    FluxoniumCoupledSchema,
    FluxoniumSchema,
    FluxTunableTransmonCoupledSchema,
    FluxTunableTransmonSchema,
    TransmonCoupledSchema,
    TransmonSchema,
    _DynamicElementAccessor,
    _DynamicElementFactory,
)

# ---------------------------------------------------------------------------
# BusNaming
# ---------------------------------------------------------------------------


def test_bus_naming_default_pattern():
    n = BusNaming()
    assert n.pattern == "{element}{index}/{kind}"


def test_bus_naming_custom_pattern():
    n = BusNaming("{kind}_{element}{index}_bus")
    assert n.resolve("q", 0, "drive") == "drive_q0_bus"


def test_bus_naming_resolve_default():
    n = BusNaming()
    assert n.resolve("q", 0, "drive") == "q0/drive"


def test_bus_naming_resolve_tuple_index():
    n = BusNaming()
    assert n.resolve("c", (0, 1), "flux") == "c0_1/flux"


# ---------------------------------------------------------------------------
# BusRef
# ---------------------------------------------------------------------------


def test_busref_is_str_subclass():
    ref = BusRef("q0/drive", "q", 0, "drive", "IQ", acquires=False)
    assert isinstance(ref, str)
    assert str(ref) == "q0/drive"


def test_busref_metadata_attrs():
    ref = BusRef("q0/readout", "q", 0, "readout", "IQ", acquires=True)
    assert ref.element == "q"
    assert ref.idx == 0
    assert ref.kind == "readout"
    assert ref.channel == "IQ"
    assert ref.acquires is True
    assert ref.schema is None


def test_busref_tuple_index():
    ref = BusRef("c0_1/flux", "c", (0, 1), "flux", "single", acquires=False)
    assert ref.idx == (0, 1)


def test_busref_deepcopy_preserves_metadata():
    schema = BusSchema.transmon()
    ref = schema.q[0].drive
    copied = copy.deepcopy(ref)
    assert str(copied) == str(ref)
    assert copied.element == ref.element
    assert copied.idx == ref.idx
    assert copied.kind == ref.kind


def test_busref_pickle_roundtrip():
    schema = BusSchema.transmon()
    ref = schema.q[0].drive
    restored = pickle.loads(pickle.dumps(ref))
    assert str(restored) == str(ref)
    assert restored.element == ref.element


# ---------------------------------------------------------------------------
# ElementSchema
# ---------------------------------------------------------------------------


def test_element_schema_bus_names():
    es = ElementSchema(
        name="q",
        buses={"drive": ("IQ", False), "readout": ("IQ", True)},
        naming=BusNaming(),
    )
    assert es.bus_names == ["drive", "readout"]


# ---------------------------------------------------------------------------
# Dynamic schema (add_element)
# ---------------------------------------------------------------------------


def test_dynamic_schema_add_element_and_access():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})
    assert isinstance(schema.q, _DynamicElementFactory)
    assert isinstance(schema.q[0], _DynamicElementAccessor)
    ref = schema.q[0].drive
    assert str(ref) == "q0/drive"
    assert ref.channel == "IQ"
    assert ref.acquires is False


def test_dynamic_schema_element_with_tuple_index():
    schema = BusSchema()
    schema.add_element("c", {"flux": ("single", False)})
    ref = schema.c[(0, 1)].flux
    assert str(ref) == "c0_1/flux"


def test_dynamic_schema_invalid_element_raises():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    with pytest.raises(AttributeError, match="No element"):
        schema.r  # ruff: ignore[useless-expression]


def test_dynamic_schema_invalid_bus_kind_raises():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    with pytest.raises(AttributeError, match="no bus"):
        schema.q[0].flux  # ruff: ignore[useless-expression]


def test_dynamic_schema_dunder_attr_not_resolved():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    # Accessor's __getattr__ should pass through underscore-prefixed names.
    with pytest.raises(AttributeError):
        schema.q[0]._private  # ruff: ignore[useless-expression]


def test_dynamic_schema_underscore_attr_not_resolved():
    schema = BusSchema()
    with pytest.raises(AttributeError):
        schema._foo  # ruff: ignore[useless-expression]


def test_dynamic_element_accessor_repr():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    r = repr(schema.q[0])
    assert "q" in r
    assert "drive" in r


def test_dynamic_element_factory_repr():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    r = repr(schema.q)
    assert "q" in r


def test_bus_schema_repr():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    r = repr(schema)
    assert "BusSchema" in r
    assert "q" in r


def test_bus_schema_elements_property():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    elements = schema.elements
    assert "q" in elements
    assert elements["q"].buses == {"drive": ("IQ", False)}


def test_bus_schema_naming_property():
    schema = BusSchema()
    assert schema.naming.pattern == BusNaming.DEFAULT_PATTERN


def test_bus_schema_custom_naming():
    schema = BusSchema(naming=BusNaming("{kind}_{element}{index}_bus"))
    schema.add_element("q", {"drive": ("IQ", False)})
    assert str(schema.q[0].drive) == "drive_q0_bus"


# ---------------------------------------------------------------------------
# Preset schemas — KIND attributes and element/bus structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset_factory", "expected_kind", "expected_elements"),
    [
        (BusSchema.transmon, "transmon", ["q"]),
        (BusSchema.transmon_coupled, "transmon_coupled", ["q", "c"]),
        (BusSchema.flux_tunable_transmon, "flux_tunable_transmon", ["q"]),
        (
            BusSchema.flux_tunable_transmon_coupled,
            "flux_tunable_transmon_coupled",
            ["q", "c"],
        ),
        (BusSchema.fluxonium, "fluxonium", ["q"]),
        (BusSchema.fluxonium_coupled, "fluxonium_coupled", ["q", "c"]),
    ],
)
def test_preset_kind_and_elements(preset_factory, expected_kind, expected_elements):
    schema = preset_factory()
    assert expected_kind == schema.KIND
    assert list(schema.elements.keys()) == expected_elements


def test_transmon_q_buses():
    schema = BusSchema.transmon()
    assert str(schema.q[0].drive) == "q0/drive"
    assert schema.q[0].drive.channel == "IQ"
    assert schema.q[0].drive.acquires is False
    assert str(schema.q[0].readout) == "q0/readout"
    assert schema.q[0].readout.acquires is True


def test_flux_tunable_transmon_has_flux():
    schema = BusSchema.flux_tunable_transmon()
    assert str(schema.q[0].flux) == "q0/flux"
    assert schema.q[0].flux.channel == "single"


def test_fluxonium_has_flux_x_and_z():
    schema = BusSchema.fluxonium()
    assert str(schema.q[0].flux_x) == "q0/flux_x"
    assert str(schema.q[0].flux_z) == "q0/flux_z"


def test_transmon_coupled_has_c():
    schema = BusSchema.transmon_coupled()
    assert isinstance(schema.c, CouplerFactory)
    assert str(schema.c[(0, 1)].flux) == "c0_1/flux"


def test_flux_tunable_coupled_has_c():
    schema = BusSchema.flux_tunable_transmon_coupled()
    assert isinstance(schema.c, CouplerFactory)


def test_fluxonium_coupled_has_c():
    schema = BusSchema.fluxonium_coupled()
    assert isinstance(schema.c, CouplerFactory)


# ---------------------------------------------------------------------------
# Preset schema types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset_factory", "expected_cls"),
    [
        (BusSchema.transmon, TransmonSchema),
        (BusSchema.transmon_coupled, TransmonCoupledSchema),
        (BusSchema.flux_tunable_transmon, FluxTunableTransmonSchema),
        (BusSchema.flux_tunable_transmon_coupled, FluxTunableTransmonCoupledSchema),
        (BusSchema.fluxonium, FluxoniumSchema),
        (BusSchema.fluxonium_coupled, FluxoniumCoupledSchema),
    ],
)
def test_preset_returns_typed_subclass(preset_factory, expected_cls):
    assert isinstance(preset_factory(), expected_cls)


# ---------------------------------------------------------------------------
# Schema-attached BusRefs carry a back-pointer
# ---------------------------------------------------------------------------


def test_schema_backed_busref_has_schema():
    schema = BusSchema.transmon()
    ref = schema.q[0].drive
    assert ref.schema is schema


def test_dynamic_schema_backed_busref_has_schema():
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False)})
    ref = schema.q[0].drive
    assert ref.schema is schema


# ---------------------------------------------------------------------------
# Preset schemas with custom naming
# ---------------------------------------------------------------------------


def test_preset_with_custom_naming():
    schema = BusSchema.transmon(naming=BusNaming("{kind}_{element}{index}_bus"))
    assert str(schema.q[0].drive) == "drive_q0_bus"
    assert str(schema.q[0].readout) == "readout_q0_bus"


# ---------------------------------------------------------------------------
# Schema composition (combine / +)
# ---------------------------------------------------------------------------


def _switch_schema(naming: BusNaming | None = None) -> BusSchema:
    """A small dynamic schema with one ``switch`` element — used as a combine() operand."""
    schema = BusSchema(naming=naming)
    schema.add_element("switch", {"rf": ("single", False)})
    return schema


def test_combine_instance_plus_instance():
    combined = BusSchema.flux_tunable_transmon() + _switch_schema()
    assert isinstance(combined, BusSchema)
    assert set(combined.elements) == {"q", "switch"}
    assert str(combined.q[0].drive) == "q0/drive"
    assert str(combined.switch[0].rf) == "switch0/rf"


def test_combine_busref_backpointer_is_the_combined_schema():
    combined = BusSchema.flux_tunable_transmon() + _switch_schema()
    assert combined.q[0].flux.schema is combined
    assert combined.switch[0].rf.schema is combined


def test_combine_class_plus_class_via_metaclass():
    # A schema subclass whose __init__ cooperates via super() composes by class addition too.
    class SwitchSchema(BusSchema):
        def __init__(self, naming: BusNaming | None = None) -> None:
            super().__init__(naming=naming)
            self.add_element("switch", {"rf": ("single", False)})

    combined = FluxTunableTransmonSchema + SwitchSchema
    assert isinstance(combined, BusSchema)
    assert set(combined.elements) == {"q", "switch"}


def test_combine_class_plus_instance():
    combined = FluxTunableTransmonSchema + _switch_schema()
    assert set(combined.elements) == {"q", "switch"}


def test_combine_chaining():
    coupler = BusSchema()
    coupler.add_element("c", {"flux": ("single", False)})
    combined = BusSchema.flux_tunable_transmon() + _switch_schema() + coupler
    assert set(combined.elements) == {"q", "switch", "c"}


def test_combine_static_method_multiple():
    coupler = BusSchema()
    coupler.add_element("c", {"flux": ("single", False)})
    combined = BusSchema.combine(BusSchema.flux_tunable_transmon(), _switch_schema(), coupler)
    assert set(combined.elements) == {"q", "switch", "c"}


def test_combine_requires_at_least_one():
    with pytest.raises(ValueError, match="at least one schema"):
        BusSchema.combine()


def test_combine_duplicate_element_different_buses_raises():
    plain = BusSchema.transmon()
    tunable = BusSchema.flux_tunable_transmon()
    with pytest.raises(ValueError, match="defined differently"):
        _ = plain + tunable  # both define 'q' differently


def test_combine_duplicate_identical_element_is_idempotent():
    combined = BusSchema.transmon() + BusSchema.transmon()
    assert set(combined.elements) == {"q"}
    assert combined.elements["q"].buses == {"drive": ("IQ", False), "readout": ("IQ", True)}


def test_combine_conflicting_naming_raises():
    a = BusSchema.flux_tunable_transmon()
    b = _switch_schema(naming=BusNaming("{kind}_{element}{index}"))
    with pytest.raises(ValueError, match="different naming patterns"):
        _ = a + b


def test_combine_explicit_naming_resolves_conflict():
    a = BusSchema.flux_tunable_transmon()
    b = _switch_schema(naming=BusNaming("{kind}_{element}{index}"))
    combined = BusSchema.combine(a, b, naming=BusNaming())
    assert str(combined.switch[0].rf) == "switch0/rf"
    assert str(combined.q[0].drive) == "q0/drive"


def test_combine_custom_shared_naming_preserved():
    naming = BusNaming("{kind}_{element}{index}")
    combined = BusSchema.transmon(naming=naming) + _switch_schema(naming=naming)
    assert str(combined.q[0].drive) == "drive_q0"
    assert str(combined.switch[0].rf) == "rf_switch0"


def test_combine_bad_operand_returns_notimplemented():
    with pytest.raises(TypeError):
        _ = BusSchema.flux_tunable_transmon() + 5


def test_combined_schema_program_round_trips():
    combined = BusSchema.flux_tunable_transmon() + _switch_schema()
    prog = QProgram(label="rt", schema=combined)
    prog.set_offset(combined.q[0].flux, 0.1)
    prog.wait(combined.switch[0].rf, 16)
    reloaded = loads(dumps(prog))
    assert reloaded.body == prog.body
    assert set(reloaded.schema.elements) == {"q", "switch"}
