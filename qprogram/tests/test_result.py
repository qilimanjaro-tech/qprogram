"""Tests for MeasurementHandle / MeasurementResult / QProgramResult."""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest
import xarray as xr

from qprogram import MeasurementHandle, MeasurementResult, QProgramResult, ValidationError

# ---------------------------------------------------------------------------
# MeasurementHandle
# ---------------------------------------------------------------------------


def test_handle_construction():
    h = MeasurementHandle("q0_m0")
    assert h.name == "q0_m0"


def test_handle_repr():
    h = MeasurementHandle("q0_m0")
    assert repr(h) == "MeasurementHandle('q0_m0')"


def test_handle_structural_equality():
    a = MeasurementHandle("q0_m0")
    b = MeasurementHandle("q0_m0")
    assert a == b
    assert hash(a) == hash(b)


def test_handle_inequality_different_names():
    assert MeasurementHandle("a") != MeasurementHandle("b")


def test_handle_unequal_to_non_handle():
    h = MeasurementHandle("q0_m0")
    assert h != "q0_m0"
    assert h != 42
    assert h != None  # noqa: E711


def test_handle_in_set_collapses_same_name():
    s = {MeasurementHandle("a"), MeasurementHandle("a"), MeasurementHandle("b")}
    assert len(s) == 2


def test_handle_can_be_dict_key():
    a = MeasurementHandle("a")
    b = MeasurementHandle("a")
    d = {a: "x"}
    assert d[b] == "x"


def test_handle_empty_name_raises():
    with pytest.raises(ValidationError, match="non-empty string"):
        MeasurementHandle("")


def test_handle_non_string_name_raises():
    # The literal ``42`` is intentionally the wrong type to exercise the
    # runtime check; ``cast`` smuggles it past the static signature.
    with pytest.raises(ValidationError):
        MeasurementHandle(cast("str", 42))


def test_handle_uses_slots():
    h = MeasurementHandle("a")
    with pytest.raises(AttributeError):
        # ``setattr`` lets the static checker see the slot-defined surface
        # while still exercising the runtime ``__slots__`` rejection.
        setattr(h, "extra", "x")  # noqa: B010


# ---------------------------------------------------------------------------
# MeasurementResult dataclass
# ---------------------------------------------------------------------------


def _fake_data() -> xr.DataArray:
    return xr.DataArray(np.zeros(2), dims=("IQ",), coords={"IQ": ["I", "Q"]})


def test_measurement_result_construction():
    data = _fake_data()
    mr = MeasurementResult(bus="q0/readout", name="q0_m0", data=data)
    assert mr.bus == "q0/readout"
    assert mr.name == "q0_m0"
    assert mr.data is data


# ---------------------------------------------------------------------------
# QProgramResult
# ---------------------------------------------------------------------------


def test_result_starts_empty():
    r = QProgramResult()
    assert len(r) == 0
    assert r.measurements == []


def test_result_append_measurement():
    r = QProgramResult()
    data = _fake_data()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=data)
    assert len(r) == 1


def test_result_repr_includes_names():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=_fake_data())
    s = repr(r)
    assert "q0_m0" in s
    assert "1" in s


def test_result_get_by_int():
    r = QProgramResult()
    a = _fake_data()
    b = _fake_data() * 2
    r.append_measurement(bus="q0/readout", name="q0_m0", data=a)
    r.append_measurement(bus="q0/readout", name="q0_m1", data=b)
    assert r.get(0) is a
    assert r.get(1) is b


def test_result_get_by_name_string():
    r = QProgramResult()
    a = _fake_data()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=a)
    assert r.get("q0_m0") is a


def test_result_get_by_handle():
    r = QProgramResult()
    a = _fake_data()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=a)
    handle = MeasurementHandle("q0_m0")
    assert r.get(handle) is a


def test_result_get_by_name_not_found_raises_keyerror():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=_fake_data())
    with pytest.raises(KeyError, match=r"not found|No measurement"):
        r.get("nonexistent")


def test_result_get_by_handle_not_found_raises_keyerror():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="q0_m0", data=_fake_data())
    with pytest.raises(KeyError):
        r.get(MeasurementHandle("nonexistent"))


def test_result_get_index_out_of_range_raises():
    r = QProgramResult()
    with pytest.raises(IndexError):
        r.get(0)


def test_result_get_bus_filter_by_index():
    r = QProgramResult()
    a = _fake_data()
    b = _fake_data() * 2
    a_plus_b = a + b
    r.append_measurement(bus="q0/readout", name="a", data=a)
    r.append_measurement(bus="q1/readout", name="b", data=b)
    r.append_measurement(bus="q0/readout", name="c", data=a_plus_b)
    # Index 1 within bus q0/readout is the third overall measurement.
    assert r.get(measurement=1, bus="q0/readout") is a_plus_b


def test_result_get_bus_filter_by_name():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="m0", data=_fake_data())
    r.append_measurement(bus="q1/readout", name="m1", data=_fake_data())
    # Looking up "m0" filtered by bus q0/readout works:
    r.get("m0", bus="q0/readout")
    # Looking up "m0" filtered by a different bus fails:
    with pytest.raises(KeyError, match="q1/readout"):
        r.get("m0", bus="q1/readout")


def test_result_get_bus_filter_index_out_of_range():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="m0", data=_fake_data())
    with pytest.raises(IndexError, match="q0/readout"):
        r.get(5, bus="q0/readout")


def test_result_get_default_index_zero():
    r = QProgramResult()
    a = _fake_data()
    r.append_measurement(bus="q0/readout", name="m0", data=a)
    assert r.get() is a


def test_result_measurements_property_returns_list():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="m0", data=_fake_data())
    assert isinstance(r.measurements, list)
    assert len(r.measurements) == 1
