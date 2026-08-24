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
"""Tests for MeasurementHandle / MeasurementResult / QProgramResult."""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest
import xarray as xr

from qprogram import (
    MeasurementField,
    MeasurementHandle,
    MeasurementResult,
    QProgramResult,
    ValidationError,
)

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
    assert h != None  # ruff: ignore[none-comparison]


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
        setattr(h, "extra", "x")  # ruff: ignore[set-attr-with-constant]


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
    # The bare call is an assertion: a name on the bus it was recorded against must not raise.
    r.get("m0", bus="q0/readout")
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


# ---------------------------------------------------------------------------
# QProgramResult.get — the ``field`` argument
# ---------------------------------------------------------------------------


def test_append_without_fields_records_data_as_iq():
    r = QProgramResult()
    a = _fake_data()
    r.append_measurement(bus="q0/readout", name="m0", data=a)
    assert r.measurements[0].fields == {"iq": a}


def test_result_get_defaults_to_iq():
    r = QProgramResult()
    iq = _fake_data()
    state = xr.DataArray(np.float64(1.0))
    r.append_measurement(bus="q0/readout", name="m0", data=iq, fields={"iq": iq, "state": state})
    assert r.get("m0") is iq
    assert r.get("m0", field="iq") is iq


def test_result_get_accepts_enum_member_and_plain_string():
    r = QProgramResult()
    iq = _fake_data()
    state = xr.DataArray(np.float64(1.0))
    r.append_measurement(bus="q0/readout", name="m0", data=iq, fields={"iq": iq, "state": state})
    assert r.get("m0", field=MeasurementField.STATE) is state
    assert r.get("m0", field="state") is state


def test_result_get_accepts_vendor_field_name():
    # ``field`` is typed ``MeasurementField | str`` because vendors register their own names;
    # ``get`` checks against the record, not the core enum.
    r = QProgramResult()
    counts = xr.DataArray(np.zeros(3), dims=("shot",))
    r.append_measurement(bus="q0/readout", name="m0", data=counts, fields={"counts": counts})
    assert r.get("m0", field="counts") is counts


def test_result_get_missing_field_names_available_fields():
    r = QProgramResult()
    iq = _fake_data()
    r.append_measurement(bus="q0/readout", name="m0", data=iq, fields={"iq": iq})
    with pytest.raises(KeyError, match=r"no field 'raw'.*available: iq"):
        r.get("m0", field="raw")


def test_result_get_default_iq_misses_when_not_requested():
    r = QProgramResult()
    state = xr.DataArray(np.float64(1.0))
    r.append_measurement(bus="q0/readout", name="m0", data=state, fields={"state": state})
    with pytest.raises(KeyError, match=r"no field 'iq'.*available: state"):
        r.get("m0")


def test_result_get_field_none_is_rejected():
    r = QProgramResult()
    r.append_measurement(bus="q0/readout", name="m0", data=_fake_data())
    # ``field=None`` is rejected outright: ``get()`` names a field, and the record's primary
    # array is reached through ``MeasurementResult.data``.
    with pytest.raises(ValidationError, match=r"field=None is not a valid field"):
        r.get("m0", field=cast("str", None))
