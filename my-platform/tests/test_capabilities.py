"""Capability/validation tests for the MyPlatform vendor ops across bus types."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from qprogram import QProgram
from qprogram.buses import BusSchema
from qprogram.errors import UnsupportedOperationError
from qprogram.executor import ExecutionWarning
from qprogram.protocol import (
    CAPABILITY_REGISTRY,
    BusCapabilities,
    CompilerCapabilities,
    PlatformCapabilities,
)
from qprogram.validation import validate
from qprogram.waveforms import IQDrag

from my_platform import MyPlatform
from my_platform.operations import SetCrosstalk, SetRFSwitch


def _codes(diags):
    return [d.code for d in diags]


# -- happy paths ----------------------------------------------------------------------------------


def test_set_crosstalk_on_flux_is_valid_and_software(platform, schema):
    prog = QProgram(label="xtalk", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])
    diags = platform.validate(prog)
    assert diags == []
    plan = platform.plan(prog)
    (op,) = [n for n in prog.body.walk() if isinstance(n, SetCrosstalk)]
    assert plan[op] == frozenset({"sw"})  # flux bus has no hardware engine


def test_set_rf_switch_on_switch_is_valid_and_realtime(platform, schema):
    prog = QProgram(label="sw", schema=schema)
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 2)
    diags = platform.validate(prog)
    assert diags == []
    plan = platform.plan(prog)
    (op,) = [n for n in prog.body.walk() if isinstance(n, SetRFSwitch)]
    assert plan[op] == frozenset({"hw", "sw"})  # RF switch is real-time capable


def test_sync_and_wait_valid_on_switch_bus(platform, schema):
    """The switch bus carries op.sync / op.wait so it can be timed against the pulse program."""
    prog = QProgram(label="sync_switch", schema=schema)
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 1)
    prog.wait(schema.switch[0].rf, 40)
    prog.sync(schema.switch[0].rf)
    assert platform.validate(prog) == []


def test_broadcast_sync_with_switch_bus_referenced(platform, schema):
    """A broadcast sync() intersects every program bus; the switch slot must support op.sync."""
    prog = QProgram(label="broadcast_sync", schema=schema)
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 1)
    prog.play(schema.q[0].drive, IQDrag(amplitude=0.2, duration=40, sigma=10, beta=0.5))
    prog.sync()  # targets=None -> broadcasts across q0/drive AND switch0/rf
    assert platform.validate(prog) == []


def test_swept_switch_loop_stays_hardware(platform, schema):
    prog = QProgram(label="sw_sweep", schema=schema)
    ch = prog.variable("ch")
    with prog.for_loop(ch, 0, 4, 1):
        prog.myplatform.set_rf_switch(schema.switch[0].rf, ch)
    assert platform.validate(prog) == []
    plan = platform.plan(prog)
    loop = next(n for n in prog.body.walk() if type(n).__name__ == "ForLoop")
    assert "hw" in plan[loop]  # a swept channel on a real-time switch bus stays hardware


# -- error routing --------------------------------------------------------------------------------


def test_set_crosstalk_on_drive_is_error(platform, schema):
    prog = QProgram(label="bad", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].drive, [[1.0]])
    assert "missing-capability" in _codes(platform.validate(prog))


def test_set_crosstalk_on_readout_is_error(platform, schema):
    prog = QProgram(label="bad", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].readout, [[1.0]])
    assert "missing-capability" in _codes(platform.validate(prog))


def test_set_rf_switch_on_flux_is_error(platform, schema):
    prog = QProgram(label="bad", schema=schema)
    prog.myplatform.set_rf_switch(schema.q[0].flux, 1)
    assert "missing-capability" in _codes(platform.validate(prog))


def test_set_rf_switch_on_drive_is_error(platform, schema):
    prog = QProgram(label="bad", schema=schema)
    prog.myplatform.set_rf_switch(schema.q[0].drive, 1)
    assert "missing-capability" in _codes(platform.validate(prog))


def test_core_op_on_switch_bus_is_error(platform, schema):
    """The switch bus speaks only its own dialect — a core op there is unsupported."""
    prog = QProgram(label="bad", schema=schema)
    prog.set_offset(schema.switch[0].rf, 0.1)
    assert "missing-capability" in _codes(platform.validate(prog))


# -- cross-schema applicability: fluxonium flux_x / flux_z ----------------------------------------


def _fluxonium_platform_caps() -> PlatformCapabilities:
    """A capabilities descriptor that reuses myplatform-flux-v1 on a fluxonium's flux_x/flux_z."""
    flux = CompilerCapabilities.from_profile("myplatform-flux-v1")
    base = CompilerCapabilities.from_profile("qprogram-base-v1")
    return PlatformCapabilities(
        bus={
            ("q", "flux_x"): BusCapabilities(hw=None, sw=flux),
            ("q", "flux_z"): BusCapabilities(hw=None, sw=flux),
        },
        platform=BusCapabilities(hw=base, sw=base),
        default_bus_profile=BusCapabilities(hw=base, sw=base),
    )


def test_set_crosstalk_valid_on_fluxonium_flux_x_and_flux_z():
    schema = BusSchema.fluxonium()
    prog = QProgram(label="fluxonium_xtalk", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux_x, [[1.0, 0.05], [0.05, 1.0]])
    prog.myplatform.set_crosstalk(schema.q[0].flux_z, np.eye(2))
    diags, _plan = validate(prog, _fluxonium_platform_caps())
    assert diags == []


def test_set_crosstalk_invalid_on_fluxonium_drive():
    schema = BusSchema.fluxonium()
    prog = QProgram(label="fluxonium_bad", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].drive, [[1.0]])
    diags, _plan = validate(prog, _fluxonium_platform_caps())
    assert "missing-capability" in _codes(diags)


# -- execution ------------------------------------------------------------------------------------


def test_execute_runs_program_with_both_vendor_ops(platform, schema):
    prog = QProgram(label="run", schema=schema)
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])
    ch = prog.variable("ch")
    with prog.average(20), prog.for_loop(ch, 0, 2, 1):
        prog.myplatform.set_rf_switch(schema.switch[0].rf, ch)
        prog.play(schema.q[0].drive, IQDrag(amplitude=0.3, duration=40, sigma=10, beta=0.5))
        prog.measure(schema.q[0].readout, "ro", "w", name="m0")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExecutionWarning)
        result = platform.execute(prog)
    assert result is not None


def test_execute_raises_on_op_on_wrong_bus(platform, schema):
    prog = QProgram(label="bad", schema=schema)
    prog.myplatform.set_rf_switch(schema.q[0].drive, 1)
    with pytest.raises(UnsupportedOperationError):
        platform.execute(prog)


def test_get_buses_includes_switches(platform):
    buses = platform.get_buses()
    assert "switch0/rf" in buses
    assert "switch1/rf" in buses
    assert "q0/drive" in buses


def test_capabilities_has_switch_slot(platform):
    assert ("switch", "rf") in platform.capabilities.bus
    slot = platform.capabilities.bus[("switch", "rf")]
    assert slot.supported_domains() == frozenset({"hw", "sw"})


def test_get_parameters_for_switch(platform):
    assert platform.get_parameters("switch0/rf") == ["active_channel", "insertion_loss_db"]


# -- injected capabilities: one platform, per-client grants ---------------------------------------


def _core_only_caps() -> PlatformCapabilities:
    """A grant that withholds every ``vendor.*`` token — the 'core library only' client."""
    core_tokens = frozenset(
        t
        for t in CAPABILITY_REGISTRY
        if not t.startswith("vendor.") and t.startswith(("op.", "waveform.", "measure.returns."))
    ) - {"op.set_parameter", "op.get_parameter"}
    core = CompilerCapabilities(
        profile="core-only",
        version=(1, 0, 0),
        capabilities=core_tokens,
        limits={},
        predicates=(),
        vendor_versions={},
    )
    base = CompilerCapabilities.from_profile("qprogram-base-v1")
    return PlatformCapabilities(
        bus={
            ("q", "drive"): BusCapabilities(hw=core, sw=core),
            ("q", "readout"): BusCapabilities(hw=core, sw=core),
            ("q", "flux"): BusCapabilities(hw=None, sw=core),
            ("switch", "rf"): BusCapabilities(hw=core, sw=core),
        },
        platform=BusCapabilities(hw=base, sw=base),
        default_bus_profile=BusCapabilities(hw=core, sw=core),
    )


def test_injected_capabilities_override_default():
    """MyPlatform(capabilities=...) reports the injected grant verbatim."""
    custom = _core_only_caps()
    assert MyPlatform(capabilities=custom).capabilities is custom


def test_default_capabilities_when_not_injected(platform):
    """With no grant injected, MyPlatform still reports its own full per-bus grant."""
    assert ("switch", "rf") in platform.capabilities.bus
    assert platform.capabilities.bus[("switch", "rf")].supported_domains() == frozenset({"hw", "sw"})


def test_injected_core_only_grant_gates_vendor_ops(schema):
    """The same platform rejects a vendor op yet accepts a core program under a core-only grant."""
    platform = MyPlatform(capabilities=_core_only_caps())

    vendor_prog = QProgram(label="vendor", schema=schema)
    vendor_prog.myplatform.set_rf_switch(schema.switch[0].rf, 1)
    assert "missing-capability" in _codes(platform.validate(vendor_prog))

    core_prog = QProgram(label="core", schema=schema)
    core_prog.play(schema.q[0].drive, IQDrag(amplitude=0.2, duration=40, sigma=10, beta=0.5))
    assert platform.validate(core_prog) == []
