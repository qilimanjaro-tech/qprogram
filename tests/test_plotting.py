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
"""Tests for qprogram.plotting: the figure model, the builder, and the renderers.

The builder half needs no display and no matplotlib: it turns an array into a description, and the
assertions read that description. The renderer half draws for real, on the Agg backend.
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr

import qprogram as qp
from qprogram import ValidationError
from qprogram.plotting import (
    DARK,
    LIGHT,
    Figure,
    Line,
    Mesh,
    Points,
    Quantity,
    Style,
    Theme,
    available_renderers,
    build_figure,
    matplotlib_renderer,
    register_renderer,
    resolve_renderer,
)

mpl.use("Agg")


@pytest.fixture(autouse=True)
def _close_figures():
    """Close every figure a test opened, so the suite never trips matplotlib's open-figure warning."""
    yield
    plt.close("all")


# ---------------------------------------------------------------------------
# Arrays shaped like the executor's output
# ---------------------------------------------------------------------------


def _sweep_iq(n: int = 5, *, attrs: dict[str, str] | None = None) -> xr.DataArray:
    """One sweep dimension named ``gain`` plus ``IQ``, the shape of a 1-D Rabi result."""
    gain = xr.DataArray(np.linspace(0.0, 1.0, n), dims="gain", attrs=attrs or {})
    values = np.stack([np.arange(n, dtype=float), np.arange(n, dtype=float) * 2], axis=-1)
    return xr.DataArray(values, dims=("gain", "IQ"), coords={"gain": gain, "IQ": ["I", "Q"]})


def _grid_iq() -> xr.DataArray:
    """Two sweep dimensions plus ``IQ``, the shape of a chevron result."""
    values = np.arange(2 * 3 * 2, dtype=float).reshape(2, 3, 2)
    return xr.DataArray(
        values,
        dims=("amp", "dur", "IQ"),
        coords={
            "amp": xr.DataArray([0.0, 1.0], dims="amp", attrs={"long_name": "Flux amplitude", "units": "V"}),
            "dur": xr.DataArray([10, 20, 30], dims="dur", attrs={"units": "ns"}),
            "IQ": ["I", "Q"],
        },
    )


def _state() -> xr.DataArray:
    """A ``state`` field over one sweep: no ``IQ`` dimension at all."""
    return xr.DataArray(
        np.array([0.0, 0.5, 1.0]),
        dims="tau",
        coords={"tau": xr.DataArray([1.0, 2.0, 3.0], dims="tau", attrs={"long_name": "Spacing"})},
    )


def _composed() -> xr.DataArray:
    """A parallel composition: one dimension, one coordinate per composed variable, no index."""
    return xr.DataArray(
        np.arange(4 * 2, dtype=float).reshape(4, 2),
        dims=("a|b", "IQ"),
        coords={
            "a": xr.DataArray([0.0, 1.0, 2.0, 3.0], dims="a|b", attrs={"long_name": "Gain", "units": "V"}),
            "b": xr.DataArray([10.0, 20.0, 30.0, 40.0], dims="a|b", attrs={"units": "ns"}),
            "IQ": ["I", "Q"],
        },
    )


# ---------------------------------------------------------------------------
# Kind inference
# ---------------------------------------------------------------------------


def test_one_dimension_besides_iq_gives_lines():
    figure = build_figure(_sweep_iq())
    assert [type(mark) for mark in figure.marks] == [Line, Line]
    assert [mark.label for mark in figure.marks] == ["I", "Q"]


def test_two_dimensions_besides_iq_give_a_heatmap():
    figure = build_figure(_grid_iq())
    assert [type(mark) for mark in figure.marks] == [Mesh]


def test_time_counts_as_a_plot_dimension():
    # A raw trace with no sweep is (time, IQ): one plot dimension, so it draws against time.
    raw = xr.DataArray(
        np.zeros((8, 2)),
        dims=("time", "IQ"),
        coords={"time": np.arange(8), "IQ": ["I", "Q"]},
    )
    figure = build_figure(raw)
    assert [type(mark) for mark in figure.marks] == [Line, Line]
    assert figure.x_label == "time"


def test_a_single_point_has_nothing_to_plot():
    scalar = xr.DataArray(np.zeros(2), dims="IQ", coords={"IQ": ["I", "Q"]})
    with pytest.raises(ValidationError, match="single measured point"):
        build_figure(scalar)


def test_three_dimensions_cannot_be_inferred():
    cube = xr.DataArray(np.zeros((2, 2, 2, 2)), dims=("a", "b", "c", "IQ"))
    with pytest.raises(ValidationError, match="more than a line or a heatmap"):
        build_figure(cube)


def test_unknown_kind_is_rejected():
    data = _sweep_iq()
    with pytest.raises(ValidationError, match="kind must be one of"):
        build_figure(data, kind="bar")


def test_line_rejects_a_two_dimensional_array():
    data = _grid_iq()
    with pytest.raises(ValidationError, match="exactly one dimension besides 'IQ'"):
        build_figure(data, kind="line")


def test_heatmap_rejects_a_one_dimensional_array():
    data = _sweep_iq()
    with pytest.raises(ValidationError, match="exactly two dimensions besides 'IQ'"):
        build_figure(data, kind="heatmap")


# ---------------------------------------------------------------------------
# Axes and their labels
# ---------------------------------------------------------------------------


def test_coordinate_attributes_become_the_axis_label():
    data = _sweep_iq(attrs={"long_name": "Drive amplitude", "units": "V"})
    assert build_figure(data).x_label == "Drive amplitude (V)"


def test_a_label_without_units_stands_alone():
    data = _sweep_iq(attrs={"long_name": "Drive amplitude"})
    assert build_figure(data).x_label == "Drive amplitude"


def test_units_without_a_label_fall_back_to_the_dimension_name():
    data = _sweep_iq(attrs={"units": "V"})
    assert build_figure(data).x_label == "gain (V)"


def test_a_bare_coordinate_labels_itself_with_the_variable_id():
    assert build_figure(_sweep_iq()).x_label == "gain"


def test_line_x_values_come_from_the_coordinate():
    figure = build_figure(_sweep_iq(n=4))
    assert np.allclose(figure.marks[0].x, [0.0, 1.0 / 3, 2.0 / 3, 1.0])


# ---------------------------------------------------------------------------
# Composed sweep dimensions
# ---------------------------------------------------------------------------


def test_a_composed_dimension_draws_its_first_coordinate_and_twins_the_second():
    figure = build_figure(_composed())
    assert figure.x_label == "Gain (V)"
    assert np.allclose(figure.marks[0].x, [0.0, 1.0, 2.0, 3.0])
    assert figure.x_twin.label == "b (ns)"
    assert np.allclose(figure.x_twin.values, [10.0, 20.0, 30.0, 40.0])


def test_a_twin_carries_the_positions_of_the_axis_it_doubles():
    figure = build_figure(_composed())
    assert np.allclose(figure.x_twin.positions, figure.marks[0].x)


def test_the_dimension_name_orders_the_axis_and_its_twin():
    data = _composed().rename({"a|b": "b|a"})
    figure = build_figure(data)
    assert (figure.x_label, figure.x_twin.label) == ("b (ns)", "Gain (V)")
    assert np.allclose(figure.marks[0].x, [10.0, 20.0, 30.0, 40.0])


def test_a_composition_of_three_draws_the_first_two():
    data = _composed().assign_coords(c=xr.DataArray([7.0, 8.0, 9.0, 10.0], dims="a|b")).rename({"a|b": "a|b|c"})
    figure = build_figure(data)
    assert (figure.x_label, figure.x_twin.label) == ("Gain (V)", "b (ns)")


def test_x_names_one_of_the_composed_coordinates_and_leaves_no_twin():
    figure = build_figure(_composed(), x="a")
    assert figure.x_label == "Gain (V)"
    assert np.allclose(figure.marks[0].x, [0.0, 1.0, 2.0, 3.0])
    assert figure.x_twin is None


def test_x_can_name_the_second_composed_coordinate():
    figure = build_figure(_composed(), x="b")
    assert figure.x_label == "b (ns)"
    assert np.allclose(figure.marks[0].x, [10.0, 20.0, 30.0, 40.0])
    assert figure.x_twin is None


def test_naming_the_composed_dimension_plots_the_sweep_index():
    figure = build_figure(_composed(), x="a|b")
    assert figure.x_label == "a|b"
    assert np.allclose(figure.marks[0].x, [0, 1, 2, 3])
    assert figure.x_twin is None


def test_an_ordinary_sweep_has_no_twin():
    figure = build_figure(_sweep_iq())
    assert figure.x_twin is None
    assert figure.y_twin is None


def test_a_twin_is_restated_by_its_own_key():
    figure = build_figure(_composed(), coords={"b": Quantity(units="us", transform=lambda v: v / 1e3)})
    assert figure.x_twin.label == "b (us)"
    assert np.allclose(figure.x_twin.values, [0.01, 0.02, 0.03, 0.04])


def test_a_twin_carries_the_restated_positions_of_its_axis():
    figure = build_figure(_composed(), coords={"a": Quantity(units="mV", transform=lambda v: v * 1e3)})
    assert np.allclose(figure.x_twin.positions, [0.0, 1000.0, 2000.0, 3000.0])


def test_the_third_of_a_composition_is_told_it_is_not_drawn():
    data = _composed().assign_coords(c=xr.DataArray([7.0, 8.0, 9.0, 10.0], dims="a|b")).rename({"a|b": "a|b|c"})
    coords = {"c": Quantity("C")}
    with pytest.raises(ValidationError, match=r"'c'\] names a coordinate this figure does not draw"):
        build_figure(data, coords=coords)


def test_a_twin_is_named_as_a_twin_when_a_key_reaches_nothing():
    data = _composed()
    coords = {"nope": Quantity("X")}
    with pytest.raises(ValidationError, match=r"'a' \(the x axis\), 'b' \(the twin of the x axis\)"):
        build_figure(data, coords=coords)


def test_a_heatmap_twins_both_of_its_axes():
    values = np.arange(4 * 3 * 2, dtype=float).reshape(4, 3, 2)
    data = xr.DataArray(
        values,
        dims=("a|b", "c|d", "IQ"),
        coords={
            "a": xr.DataArray([0.0, 1.0, 2.0, 3.0], dims="a|b"),
            "b": xr.DataArray([10.0, 20.0, 30.0, 40.0], dims="a|b"),
            "c": xr.DataArray([0.0, 0.5, 1.0], dims="c|d"),
            "d": xr.DataArray([100.0, 200.0, 300.0], dims="c|d"),
            "IQ": ["I", "Q"],
        },
    )
    figure = build_figure(data)
    # The inner dimension runs along x, so 'c|d' is across and 'a|b' is up.
    assert (figure.x_label, figure.x_twin.label) == ("c", "d")
    assert (figure.y_label, figure.y_twin.label) == ("a", "b")


def test_an_unknown_x_names_what_is_available():
    data = _composed()
    with pytest.raises(ValidationError, match="Coordinates: a, b, IQ"):
        build_figure(data, x="nope")


def test_a_lone_coordinate_on_a_bare_dimension_needs_no_choosing():
    data = xr.DataArray(
        np.zeros((3, 2)),
        dims=("a|", "IQ"),
        coords={"a": xr.DataArray([1.0, 2.0, 3.0], dims="a|"), "IQ": ["I", "Q"]},
    )
    assert build_figure(data).x_label == "a"


def test_a_coordinate_belonging_to_another_dimension_is_not_this_axis_s():
    # xarray allows a coordinate named after one dimension to live on a second, and the executor
    # builds exactly that when one variable is swept at two nesting levels. Taking the name at face
    # value would draw the other dimension's values under this one's label.
    data = xr.DataArray(
        np.arange(12.0).reshape(3, 4),
        dims=("a", "b"),
        coords={"a": ("b", np.arange(4.0))},
    )
    mesh = build_figure(data, x="b", y="a").marks[0]
    assert len(mesh.y) == data.sizes["a"]
    assert np.allclose(mesh.y, [0, 1, 2])
    assert mesh.values.shape == (len(mesh.y), len(mesh.x))


def test_a_dimension_with_no_coordinate_at_all_plots_its_index():
    data = xr.DataArray(np.zeros((3, 2)), dims=("shot", "IQ"), coords={"IQ": ["I", "Q"]})
    figure = build_figure(data)
    assert figure.x_label == "shot"
    assert np.allclose(figure.marks[0].x, [0, 1, 2])


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channels", "labels"),
    [
        ("iq", ["I", "Q"]),
        ("i", ["I"]),
        ("q", ["Q"]),
        ("magnitude", ["Magnitude"]),
        ("phase", ["Phase"]),
    ],
)
def test_each_channel_draws_its_own_series(channels, labels):
    figure = build_figure(_sweep_iq(), channels=channels)
    assert [mark.label for mark in figure.marks] == labels


def test_magnitude_is_the_hypotenuse():
    data = _sweep_iq(n=3)
    figure = build_figure(data, channels="magnitude")
    assert np.allclose(figure.marks[0].y, np.hypot(data.sel(IQ="I"), data.sel(IQ="Q")))


def test_phase_is_the_arctangent():
    data = _sweep_iq(n=3)
    figure = build_figure(data, channels="phase")
    assert np.allclose(figure.marks[0].y, np.arctan2(data.sel(IQ="Q"), data.sel(IQ="I")))


def test_unknown_channels_are_rejected():
    data = _sweep_iq()
    with pytest.raises(ValidationError, match="channels must be one of"):
        build_figure(data, channels="abs")


@pytest.mark.parametrize("channels", ["iq", "i", "q", "magnitude", "phase"])
def test_every_channel_needs_quadratures_to_choose_between(channels):
    data = _state()
    with pytest.raises(ValidationError, match="needs an 'IQ' dimension"):
        build_figure(data, channels=channels)


def test_an_array_without_quadratures_draws_one_unnamed_line():
    figure = build_figure(_state())
    assert [mark.label for mark in figure.marks] == [None]
    assert figure.y_label == "Value"


def test_a_derived_channel_names_the_quantity_it_derived():
    # The array names the values that went in; an arctangent of them is a different quantity, so
    # its own name travels with its own unit rather than borrowing half of each.
    data = _sweep_iq()
    data.attrs = {"long_name": "Readout voltage", "units": "V"}
    assert build_figure(data, channels="phase").y_label == "Phase (rad)"
    assert build_figure(data, channels="magnitude").y_label == "Readout voltage (V)"


# ---------------------------------------------------------------------------
# Heatmaps
# ---------------------------------------------------------------------------


def test_the_innermost_sweep_runs_along_the_x_axis():
    figure = build_figure(_grid_iq())
    assert figure.x_label == "dur (ns)"
    assert figure.y_label == "Flux amplitude (V)"


def test_x_swaps_the_two_axes():
    figure = build_figure(_grid_iq(), x="amp")
    assert figure.x_label == "Flux amplitude (V)"
    assert figure.y_label == "dur (ns)"


def test_y_alone_settles_both_axes():
    figure = build_figure(_grid_iq(), y="dur")
    assert figure.x_label == "Flux amplitude (V)"
    assert figure.y_label == "dur (ns)"


def test_x_and_y_cannot_name_the_same_dimension():
    data = _grid_iq()
    with pytest.raises(ValidationError, match="a heatmap needs one on each axis"):
        build_figure(data, x="amp", y="amp")


def test_the_grid_is_indexed_row_by_column():
    data = _grid_iq()
    mesh = build_figure(data, channels="i").marks[0]
    assert mesh.values.shape == (len(mesh.y), len(mesh.x))
    assert np.allclose(mesh.values, data.sel(IQ="I").transpose("amp", "dur").values)


def test_a_swapped_heatmap_transposes_its_grid():
    data = _grid_iq()
    mesh = build_figure(data, x="amp", channels="i").marks[0]
    assert np.allclose(mesh.values, data.sel(IQ="I").transpose("dur", "amp").values)


def test_a_heatmap_defaults_to_the_magnitude():
    data = _grid_iq()
    mesh = build_figure(data).marks[0]
    assert mesh.label == "Magnitude"
    assert np.allclose(mesh.values, np.hypot(data.sel(IQ="I"), data.sel(IQ="Q")).transpose("amp", "dur"))


def test_a_heatmap_cannot_colour_both_quadratures():
    data = _grid_iq()
    with pytest.raises(ValidationError, match="colours one surface"):
        build_figure(data, channels="iq")


def test_a_heatmap_without_quadratures_needs_no_channel():
    data = xr.DataArray(np.zeros((2, 3)), dims=("a", "b"))
    mesh = build_figure(data).marks[0]
    assert isinstance(mesh, Mesh)
    assert mesh.label == "Value"


# ---------------------------------------------------------------------------
# Scatter
# ---------------------------------------------------------------------------


def test_scatter_is_never_inferred():
    assert [type(mark) for mark in build_figure(_sweep_iq()).marks] == [Line, Line]


def test_scatter_puts_i_against_q():
    data = _sweep_iq(n=4)
    figure = build_figure(data, kind="scatter")
    (points,) = figure.marks
    assert isinstance(points, Points)
    assert (figure.x_label, figure.y_label) == ("I", "Q")
    assert np.allclose(points.x, data.sel(IQ="I").values)
    assert np.allclose(points.y, data.sel(IQ="Q").values)


def test_scatter_flattens_every_other_dimension():
    (points,) = build_figure(_grid_iq(), kind="scatter").marks
    assert points.x.shape == (6,)


def test_scatter_needs_quadratures():
    data = _state()
    with pytest.raises(ValidationError, match="no 'IQ' dimension"):
        build_figure(data, kind="scatter")


@pytest.mark.parametrize("argument", ["x", "y", "channels"])
def test_scatter_has_nothing_left_for_an_axis_argument_to_choose(argument):
    data = _sweep_iq()
    with pytest.raises(ValidationError, match=f"{argument} has nothing left to choose"):
        build_figure(data, kind="scatter", **{argument: "i"})


def test_a_line_figure_has_no_second_dimension_for_y_to_name():
    data = _sweep_iq()
    with pytest.raises(ValidationError, match="only a heatmap has"):
        build_figure(data, y="gain")


# ---------------------------------------------------------------------------
# The label on the measured quantity
# ---------------------------------------------------------------------------


def test_the_caller_s_label_for_the_measured_quantity_wins():
    assert build_figure(_sweep_iq(), value=Quantity("Readout response")).y_label == "Readout response"


def test_the_array_s_own_attributes_are_read_next():
    data = _sweep_iq()
    data.attrs = {"long_name": "Transmission", "units": "dB"}
    assert build_figure(data).y_label == "Transmission (dB)"


def test_an_attribute_without_units_stands_alone():
    data = _sweep_iq()
    data.attrs = {"long_name": "Transmission"}
    assert build_figure(data).y_label == "Transmission"


def test_the_array_name_is_read_after_that():
    assert build_figure(_sweep_iq().rename("m0")).y_label == "m0"


@pytest.mark.parametrize(
    ("channels", "expected"),
    [("iq", "Signal"), ("i", "I"), ("q", "Q"), ("magnitude", "Magnitude"), ("phase", "Phase (rad)")],
)
def test_otherwise_the_channel_says_what_the_axis_is(channels, expected):
    assert build_figure(_sweep_iq(), channels=channels).y_label == expected


def test_a_title_is_carried_through():
    assert build_figure(_sweep_iq(), title="Rabi").title == "Rabi"


def test_no_title_by_default():
    assert build_figure(_sweep_iq()).title is None


# ---------------------------------------------------------------------------
# Quantity: construction
# ---------------------------------------------------------------------------


def test_a_quantity_that_restates_nothing_is_refused():
    with pytest.raises(ValidationError, match="restates nothing"):
        Quantity()


@pytest.mark.parametrize("field", ["label", "units"])
def test_the_words_of_a_quantity_must_be_words(field):
    with pytest.raises(ValidationError, match=f"Quantity {field} must be a string"):
        Quantity(**{field: 1e-9})


def test_the_arithmetic_of_a_quantity_must_be_arithmetic():
    with pytest.raises(ValidationError, match="transform must be callable"):
        Quantity(units="GHz", transform=1e-9)


def test_a_quantity_reads_positionally_as_the_sentence_the_axis_does():
    quantity = Quantity("Detuning", "MHz", abs)
    assert (quantity.label, quantity.units, quantity.transform) == ("Detuning", "MHz", abs)


# ---------------------------------------------------------------------------
# Restating a coordinate
# ---------------------------------------------------------------------------


_HERTZ = np.linspace(4.6e9, 5.4e9, 5)


def _hertz() -> xr.DataArray:
    """A frequency sweep whose coordinate declares a name and a unit, as the executor writes them."""
    freq = xr.DataArray(_HERTZ, dims="freq", attrs={"long_name": "Drive frequency", "units": "Hz"})
    values = np.stack([np.arange(5.0), np.arange(5.0) * 2], axis=-1)
    return xr.DataArray(values, dims=("freq", "IQ"), coords={"freq": freq, "IQ": ["I", "Q"]})


def test_a_restatement_moves_the_numbers_and_the_unit_together():
    figure = build_figure(_hertz(), coords={"freq": Quantity(units="GHz", transform=lambda v: v / 1e9)})
    assert figure.x_label == "Drive frequency (GHz)"
    assert np.allclose(figure.marks[0].x, _HERTZ / 1e9)


def test_a_restatement_can_rename_as_well_as_rescale():
    figure = build_figure(_hertz(), coords={"freq": Quantity("Detuning", "MHz", lambda v: (v - 5e9) / 1e6)})
    assert figure.x_label == "Detuning (MHz)"
    assert np.allclose(figure.marks[0].x, [-400.0, -200.0, 0.0, 200.0, 400.0])


def test_a_label_alone_leaves_the_unit_it_inherited():
    assert build_figure(_hertz(), coords={"freq": Quantity("Frequency")}).x_label == "Frequency (Hz)"


def test_emptying_a_unit_without_the_arithmetic_is_refused():
    data = _hertz()
    quantity = Quantity(units="")
    with pytest.raises(ValidationError, match="carry no unit at all"):
        build_figure(data, coords={"freq": quantity})


def test_a_transform_that_leaves_a_bare_ratio_says_so_with_an_empty_unit():
    figure = build_figure(_hertz(), coords={"freq": Quantity(units="", transform=lambda v: v / v[-1])})
    assert figure.x_label == "Drive frequency"
    assert figure.marks[0].x[-1] == 1.0


def test_an_empty_unit_drops_nothing_where_the_coordinate_declared_none():
    data = _sweep_iq(attrs={"long_name": "Shot"})
    assert build_figure(data, coords={"gain": Quantity(units="")}).x_label == "Shot"


def test_a_unit_the_coordinate_never_declared_is_taken_as_a_correction():
    data = _sweep_iq(attrs={"long_name": "Drive amplitude"})
    assert build_figure(data, coords={"gain": Quantity(units="V")}).x_label == "Drive amplitude (V)"


def test_rescaling_without_saying_the_new_unit_is_refused():
    data = _hertz()
    quantity = Quantity(transform=lambda v: v / 1e9)
    with pytest.raises(ValidationError, match="no longer describes them"):
        build_figure(data, coords={"freq": quantity})


def test_renaming_the_unit_without_the_arithmetic_is_refused():
    data = _hertz()
    quantity = Quantity(units="GHz")
    with pytest.raises(ValidationError, match="without changing the numbers"):
        build_figure(data, coords={"freq": quantity})


def test_a_shift_that_keeps_its_unit_says_so_by_repeating_it():
    figure = build_figure(_hertz(), coords={"freq": Quantity(units="Hz", transform=lambda v: v - v[0])})
    assert figure.x_label == "Drive frequency (Hz)"
    assert figure.marks[0].x[0] == 0.0


def test_a_coordinate_with_no_unit_takes_a_bare_transform():
    data = _sweep_iq(attrs={"long_name": "Shot"})
    figure = build_figure(data, coords={"gain": Quantity(transform=lambda v: v * 2)})
    assert figure.x_label == "Shot"


def test_the_array_the_figure_came_from_is_left_alone():
    data = _hertz()
    before = data.coords["freq"].values.copy()
    build_figure(data, coords={"freq": Quantity(units="GHz", transform=lambda v: v / 1e9)})
    assert np.array_equal(data.coords["freq"].values, before)


def test_an_in_place_transform_cannot_reach_the_stored_result():
    data = _hertz()
    before = data.coords["freq"].values.copy()

    def baseline(values):
        values -= values[0]
        return values

    build_figure(data, coords={"freq": Quantity(units="Hz", transform=baseline)})
    assert np.array_equal(data.coords["freq"].values, before)


def test_both_axes_of_a_heatmap_can_be_restated():
    figure = build_figure(
        _grid_iq(),
        channels="i",
        coords={
            "amp": Quantity(units="mV", transform=lambda v: v * 1e3),
            "dur": Quantity(units="us", transform=lambda v: v / 1e3),
        },
    )
    assert (figure.x_label, figure.y_label) == ("dur (us)", "Flux amplitude (mV)")
    assert np.allclose(figure.marks[0].y, [0.0, 1000.0])


def test_a_composed_dimension_is_keyed_by_the_coordinate_that_won_the_axis():
    figure = build_figure(_composed(), x="a", coords={"a": Quantity(units="mV", transform=lambda v: v * 1e3)})
    assert figure.x_label == "Gain (mV)"


def test_the_sweep_index_of_a_composed_dimension_is_keyed_by_the_dimension():
    figure = build_figure(_composed(), x="a|b", coords={"a|b": Quantity("Sweep step")})
    assert figure.x_label == "Sweep step"


# ---------------------------------------------------------------------------
# A key that reaches no axis
# ---------------------------------------------------------------------------


def test_a_misspelled_key_names_what_the_figure_draws():
    data = _hertz()
    coords = {"frequency": Quantity("Frequency")}
    with pytest.raises(ValidationError, match="names nothing on this result"):
        build_figure(data, coords=coords)


def test_the_error_lists_the_axes_with_their_roles():
    data = _grid_iq()
    coords = {"nope": Quantity("X")}
    with pytest.raises(ValidationError, match=r"'dur' \(the x axis\), 'amp' \(the y axis\)"):
        build_figure(data, channels="i", coords=coords)


def test_the_error_points_at_the_argument_the_measured_quantity_uses():
    data = _hertz()
    coords = {"nope": Quantity("X")}
    with pytest.raises(ValidationError, match="name it with value=Quantity"):
        build_figure(data, coords=coords)


def test_a_sibling_coordinate_that_lost_the_axis_is_told_apart_from_a_typo():
    data = _composed()
    coords = {"b": Quantity("B")}
    with pytest.raises(ValidationError, match=r"'b'\] names a coordinate this figure does not draw"):
        build_figure(data, x="a", coords=coords)


def test_a_dimension_name_where_a_coordinate_is_drawn_is_told_apart_too():
    data = _composed()
    coords = {"a|b": Quantity("Step")}
    with pytest.raises(ValidationError, match="names a dimension, and this figure draws a coordinate"):
        build_figure(data, x="a", coords=coords)


def test_every_unused_key_is_reported_at_once():
    data = _hertz()
    coords = {"second": Quantity("B"), "first": Quantity("A")}
    with pytest.raises(ValidationError, match=r"'first'.*'second'"):
        build_figure(data, coords=coords)


# ---------------------------------------------------------------------------
# The type gates
# ---------------------------------------------------------------------------


def test_a_bare_function_says_what_it_is_missing():
    data = _hertz()
    with pytest.raises(ValidationError, match="A bare function rescales the numbers"):
        build_figure(data, coords={"freq": lambda v: v / 1e9})


def test_a_bare_string_says_what_to_wrap_it_in():
    data = _sweep_iq()
    with pytest.raises(ValidationError, match=r"write it as Quantity\('Readout response'\)"):
        build_figure(data, value="Readout response")


def test_a_coords_argument_that_is_not_a_mapping_is_refused():
    data = _sweep_iq()
    pairs = [("gain", Quantity("G"))]
    with pytest.raises(ValidationError, match="must be a mapping"):
        build_figure(data, coords=pairs)


# ---------------------------------------------------------------------------
# What a transform is checked for
# ---------------------------------------------------------------------------


def test_an_exception_inside_a_transform_names_the_argument_that_carried_it():
    data = _hertz()
    quantity = Quantity(units="GHz", transform=lambda v: v[99])
    with pytest.raises(ValidationError, match=r"coords\['freq'\] raised IndexError"):
        build_figure(data, coords={"freq": quantity})


def test_a_transform_that_changes_the_shape_is_refused():
    data = _hertz()
    quantity = Quantity(units="GHz", transform=lambda v: v[:2])
    with pytest.raises(ValidationError, match=r"returned shape \(2,\) for an input of shape \(5,\)"):
        build_figure(data, coords={"freq": quantity})


def test_a_transform_that_returns_complex_numbers_is_refused():
    data = _hertz()
    quantity = Quantity(units="GHz", transform=lambda v: v.astype(complex))
    with pytest.raises(ValidationError, match="an axis is drawn on real numbers"):
        build_figure(data, coords={"freq": quantity})


def test_a_transform_that_introduces_a_non_finite_value_names_the_first_one():
    data = _hertz()
    quantity = Quantity(units="GHz", transform=lambda v: np.full_like(v, np.nan))
    with pytest.raises(ValidationError, match=r"turned 5 of 5 finite values into inf or nan"):
        build_figure(data, coords={"freq": quantity})


def test_a_nan_the_measurement_already_carried_is_not_blamed_on_the_transform():
    data = _sweep_iq(n=4)
    data.values[0, 0] = np.nan
    figure = build_figure(data, channels="i", value=Quantity(transform=lambda v: v * 2))
    assert np.isnan(figure.marks[0].y[0])


def test_a_coordinate_that_is_not_numbers_skips_the_finiteness_check():
    data = xr.DataArray(
        np.zeros((3, 2)),
        dims=("label", "IQ"),
        coords={"label": ["a", "b", "c"], "IQ": ["I", "Q"]},
    )
    figure = build_figure(data, coords={"label": Quantity(transform=lambda v: np.arange(len(v), dtype=float))})
    assert np.allclose(figure.marks[0].x, [0, 1, 2])


# ---------------------------------------------------------------------------
# Restating the measured quantity
# ---------------------------------------------------------------------------


def test_the_measured_values_of_a_line_are_restated_per_series():
    data = _sweep_iq(n=3)
    figure = build_figure(data, value=Quantity("Change", transform=lambda v: v - v[0]))
    assert figure.y_label == "Change"
    assert [float(mark.y[0]) for mark in figure.marks] == [0.0, 0.0]


def test_the_coloured_values_of_a_heatmap_are_restated_with_their_bar():
    figure = build_figure(
        _grid_iq(),
        channels="i",
        value=Quantity("Population transferred", "%", lambda v: v * 100),
    )
    mesh = figure.marks[0]
    assert mesh.label == "Population transferred (%)"
    assert np.allclose(mesh.values, _grid_iq().sel(IQ="I").transpose("amp", "dur").values * 100)


def test_a_scatter_restates_both_quadratures_at_once():
    data = _sweep_iq(n=4)
    figure = build_figure(data, kind="scatter", value=Quantity(units="mV", transform=lambda v: v * 1e3))
    assert (figure.x_label, figure.y_label) == ("I (mV)", "Q (mV)")
    assert np.allclose(figure.marks[0].x, data.sel(IQ="I").values * 1e3)
    assert np.allclose(figure.marks[0].y, data.sel(IQ="Q").values * 1e3)


def test_a_scatter_refuses_one_label_for_its_two_axes():
    data = _sweep_iq()
    quantity = Quantity("Readout")
    with pytest.raises(ValidationError, match="which already name themselves"):
        build_figure(data, kind="scatter", value=quantity)


def test_a_scatter_draws_no_coordinate_to_restate():
    data = _sweep_iq()
    coords = {"gain": Quantity("G")}
    with pytest.raises(ValidationError, match="A scatter draws no coordinate"):
        build_figure(data, kind="scatter", coords=coords)


def test_the_unit_of_a_phase_is_its_own_and_can_be_restated():
    data = _sweep_iq()
    assert build_figure(data, channels="phase").y_label == "Phase (rad)"
    figure = build_figure(data, channels="phase", value=Quantity(units="deg", transform=np.degrees))
    assert figure.y_label == "Phase (deg)"


# ---------------------------------------------------------------------------
# Themes and styles
# ---------------------------------------------------------------------------


def test_series_colours_cycle():
    style = Style(theme=Theme(surface="w", text="k", muted="k", grid="k", series=("a", "b"), ramp=("a",)))
    assert [style.color(i) for i in range(4)] == ["a", "b", "a", "b"]


def test_the_default_style_is_the_light_theme():
    assert Style().theme is LIGHT


def test_the_two_themes_differ_where_it_matters():
    assert LIGHT.surface != DARK.surface
    assert LIGHT.ramp[0] != DARK.ramp[0]


# ---------------------------------------------------------------------------
# The renderer registry
# ---------------------------------------------------------------------------


def test_the_default_renderer_is_matplotlib():
    assert resolve_renderer() is matplotlib_renderer.render
    assert "matplotlib" in available_renderers()


def test_a_renderer_can_be_registered_and_resolved():
    def fake(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return "drawn"

    try:
        register_renderer("test-registry", fake)
        assert resolve_renderer("test-registry") is fake
        assert "test-registry" in available_renderers()
    finally:
        qp.plotting.renderers._renderers.pop("test-registry", None)


def test_registering_the_same_renderer_twice_is_a_no_op():
    def fake(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return None

    try:
        assert register_renderer("test-idempotent", fake) is fake
        register_renderer("test-idempotent", fake)
    finally:
        qp.plotting.renderers._renderers.pop("test-idempotent", None)


def test_a_taken_name_cannot_be_reassigned():
    def one(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return None

    def two(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return None

    try:
        register_renderer("test-taken", one)
        with pytest.raises(ValueError, match="already registered"):
            register_renderer("test-taken", two)
    finally:
        qp.plotting.renderers._renderers.pop("test-taken", None)


def test_an_unknown_renderer_lists_the_known_ones():
    with pytest.raises(KeyError, match="registered: matplotlib"):
        resolve_renderer("svg")


def test_an_empty_renderer_name_is_a_lost_name_and_not_a_request_for_the_default():
    with pytest.raises(KeyError, match="No renderer named ''"):
        resolve_renderer("")


# ---------------------------------------------------------------------------
# The matplotlib renderer
# ---------------------------------------------------------------------------


def test_rendering_returns_the_axes_it_drew_on():
    ax = matplotlib_renderer.render(build_figure(_sweep_iq()), Style())
    assert isinstance(ax, plt.Axes)
    assert len(ax.lines) == 2


def test_the_axis_labels_reach_the_axes():
    data = _sweep_iq(attrs={"long_name": "Drive amplitude", "units": "V"})
    ax = matplotlib_renderer.render(build_figure(data, title="Rabi"), Style())
    assert ax.get_xlabel() == "Drive amplitude (V)"
    assert ax.get_ylabel() == "Signal"
    assert ax.get_title(loc="left") == "Rabi"


def test_an_existing_axes_is_drawn_on_rather_than_replaced():
    _, ax = plt.subplots()
    returned = matplotlib_renderer.render(build_figure(_sweep_iq()), Style(), ax)
    assert returned is ax


def test_two_series_get_a_legend_and_one_does_not():
    with_two = matplotlib_renderer.render(build_figure(_sweep_iq()), Style())
    assert with_two.get_legend() is not None
    with_one = matplotlib_renderer.render(build_figure(_sweep_iq(), channels="i"), Style())
    assert with_one.get_legend() is None


def test_the_legend_can_be_switched_off():
    ax = matplotlib_renderer.render(build_figure(_sweep_iq()), Style(legend=False))
    assert ax.get_legend() is None


def test_the_theme_paints_the_surface():
    ax = matplotlib_renderer.render(build_figure(_sweep_iq()), Style(theme=DARK))
    assert mpl.colors.to_hex(ax.get_facecolor()) == DARK.surface


def test_the_grid_can_be_switched_off():
    ax = matplotlib_renderer.render(build_figure(_sweep_iq()), Style(grid=False))
    assert not any(line.get_visible() for line in ax.get_xgridlines())


def test_markers_are_off_by_default_and_can_be_turned_on():
    plain = matplotlib_renderer.render(build_figure(_sweep_iq()), Style())
    assert plain.lines[0].get_marker() == "None"
    marked = matplotlib_renderer.render(build_figure(_sweep_iq()), Style(markers=True))
    assert marked.lines[0].get_marker() == "o"


def test_a_heatmap_draws_a_mesh_and_a_colour_bar():
    ax = matplotlib_renderer.render(build_figure(_grid_iq()), Style())
    assert len(ax.collections) == 1
    assert any(other is not ax for other in ax.get_figure().axes)


def test_the_colour_bar_can_be_switched_off():
    ax = matplotlib_renderer.render(build_figure(_grid_iq()), Style(colorbar=False))
    assert ax.get_figure().axes == [ax]


def test_a_scatter_draws_one_collection():
    ax = matplotlib_renderer.render(build_figure(_sweep_iq(), kind="scatter"), Style())
    assert len(ax.collections) == 1


def test_a_twin_reads_from_the_top_of_a_line_figure():
    ax = matplotlib_renderer.render(build_figure(_composed()), Style())
    (twin,) = ax.child_axes
    assert twin.xaxis.get_label_position() == "top"
    assert twin.get_xlabel() == "b (ns)"
    assert twin.spines["top"].get_edgecolor() == mpl.colors.to_rgba(LIGHT.grid)


def test_a_twin_ticks_every_sample_of_a_sweep_shorter_than_the_style_asks_for():
    data = _composed().isel({"a|b": [0, 1, 2]})
    ax = matplotlib_renderer.render(build_figure(data), Style())
    (twin,) = ax.child_axes
    assert [text.get_text() for text in twin.get_xticklabels()] == ["10", "20", "30"]


def test_a_twinned_figure_is_laid_out_constrained_to_make_room_for_the_scale():
    twinned = matplotlib_renderer.render(build_figure(_composed()), Style())
    assert twinned.get_figure().get_layout_engine() is not None
    plain = matplotlib_renderer.render(build_figure(_sweep_iq()), Style())
    assert plain.get_figure().get_layout_engine() is None


def test_a_twinned_heatmap_keeps_the_colour_bar_clear_of_the_right_hand_scale():
    values = np.arange(4 * 3 * 2, dtype=float).reshape(4, 3, 2)
    data = xr.DataArray(
        values,
        dims=("a|b", "dur", "IQ"),
        coords={
            "a": xr.DataArray([0.0, 1.0, 2.0, 3.0], dims="a|b"),
            "b": xr.DataArray([10.0, 20.0, 30.0, 40.0], dims="a|b"),
            "dur": xr.DataArray([1.0, 2.0, 3.0], dims="dur"),
            "IQ": ["I", "Q"],
        },
    )
    ax = matplotlib_renderer.render(build_figure(data), Style())
    (twin,) = ax.child_axes
    assert twin.yaxis.get_label_position() == "right"
    fig = ax.get_figure()
    fig.canvas.draw()
    (bar,) = (other for other in fig.axes if other is not ax)
    assert bar.get_position().x0 > twin.get_tightbbox().transformed(fig.transFigure.inverted()).x1


def test_a_figure_can_mix_marks():
    figure = Figure(
        marks=(
            Mesh(x=np.arange(3), y=np.arange(2), values=np.zeros((2, 3)), label=None),
            Line(x=np.arange(3), y=np.zeros(3), label=None),
        ),
        x_label="x",
        y_label="y",
    )
    ax = matplotlib_renderer.render(figure, Style())
    assert len(ax.collections) == 1
    assert len(ax.lines) == 1


# ---------------------------------------------------------------------------
# QProgramResult.plot
# ---------------------------------------------------------------------------

_READOUT = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 200), qp.waveforms.Square(0.0, 200))
_WEIGHTS = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 200), qp.waveforms.Square(1.0, 200))
_LIBRARY = {"pi": qp.waveforms.IQDrag(0.5, 40, 8, 0.1), "readout": _READOUT, "weights": _WEIGHTS}


def _rabi_response(bus: str, env: dict) -> complex:  # ruff: ignore[unused-function-argument]
    return np.sin(np.pi * env["gain"]) ** 2 + 0j


def _rabi_population(bus: str, env: dict) -> float:  # ruff: ignore[unused-function-argument]
    return float(env["gain"])


def _rabi_result() -> tuple[qp.QProgramResult, qp.MeasurementHandle]:
    """Run a small labelled sweep, so the coordinate attributes are the executor's own."""
    schema = qp.BusSchema.transmon()
    program = qp.QProgram(label="rabi", schema=schema)
    gain = program.variable("gain", label="Drive amplitude", units="V")
    with program.average(4), program.sweep(gain, qp.Range(0.0, 1.0, 0.25)):
        program.set_gain(schema.q[0].drive, gain)
        program.play(schema.q[0].drive, "pi")
        program.sync()
        handle = program.measure(
            schema.q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )
    model = qp.MockMeasurementModel(response=_rabi_response, p_excited=_rabi_population, seed=0)
    return qp.simulate(program.with_waveforms(_LIBRARY), model=model), handle


def test_plot_labels_its_axis_from_the_swept_variable():
    result, handle = _rabi_result()
    ax = result.plot(handle)
    assert ax.get_xlabel() == "Drive amplitude (V)"
    assert len(ax.lines) == 2


def test_plot_finds_a_measurement_the_same_ways_get_does():
    result, handle = _rabi_result()
    by_position = result.plot(0)
    by_name = result.plot(handle.name)
    by_bus = result.plot(0, bus="q0/readout")
    assert all(isinstance(ax, plt.Axes) for ax in (by_position, by_name, by_bus))


def test_plot_names_the_state_field_on_the_axis():
    result, handle = _rabi_result()
    ax = result.plot(handle, field=qp.MeasurementField.STATE)
    assert ax.get_ylabel() == "State"
    assert len(ax.lines) == 1


def test_plot_passes_the_style_through():
    result, handle = _rabi_result()
    ax = result.plot(handle, style=Style(theme=DARK))
    assert mpl.colors.to_hex(ax.get_facecolor()) == DARK.surface


def test_plot_draws_on_an_axes_it_is_given():
    result, handle = _rabi_result()
    _, ax = plt.subplots()
    assert result.plot(handle, target=ax) is ax


def test_plot_uses_the_renderer_it_is_told_to():
    result, handle = _rabi_result()

    def fake(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return figure

    try:
        register_renderer("test-plot", fake)
        figure = result.plot(handle, renderer="test-plot")
    finally:
        qp.plotting.renderers._renderers.pop("test-plot", None)
    assert isinstance(figure, Figure)
    assert figure.x_label == "Drive amplitude (V)"


def test_plot_takes_a_label_for_the_measured_quantity():
    result, handle = _rabi_result()
    ax = result.plot(handle, value=Quantity("Readout response"))
    assert ax.get_ylabel() == "Readout response"


def test_plot_restates_a_coordinate_for_the_axis():
    result, handle = _rabi_result()
    ax = result.plot(handle, coords={"gain": Quantity(units="mV", transform=lambda v: v * 1e3)})
    assert ax.get_xlabel() == "Drive amplitude (mV)"
    assert np.allclose(ax.lines[0].get_xdata(), [0.0, 250.0, 500.0, 750.0, 1000.0])


def test_plot_leaves_the_result_in_the_units_it_was_measured_in():
    result, handle = _rabi_result()
    result.plot(handle, coords={"gain": Quantity(units="mV", transform=lambda v: v * 1e3)})
    assert np.allclose(result.get(handle).coords["gain"].values, [0.0, 0.25, 0.5, 0.75, 1.0])


def test_plot_fills_in_the_label_a_field_implies_and_yields_to_the_caller_s():
    result, handle = _rabi_result()
    implied = result.plot(handle, field=qp.MeasurementField.STATE)
    assert implied.get_ylabel() == "State"
    named = result.plot(handle, field=qp.MeasurementField.STATE, value=Quantity("Excited population"))
    assert named.get_ylabel() == "Excited population"


def test_plot_restates_a_field_whose_label_it_supplies():
    result, handle = _rabi_result()
    ax = result.plot(handle, field=qp.MeasurementField.STATE, value=Quantity(units="%", transform=lambda v: v * 100))
    assert ax.get_ylabel() == "State (%)"


def test_plot_refuses_a_bare_string_where_a_quantity_belongs():
    result, handle = _rabi_result()
    with pytest.raises(ValidationError, match="must be a Quantity, got str"):
        result.plot(handle, field=qp.MeasurementField.STATE, value="Excited population")


def test_plot_of_a_scatter_takes_no_label_from_the_field():
    result, handle = _rabi_result()
    ax = result.plot(handle, kind="scatter", value=Quantity(units="mV", transform=lambda v: v * 1e3))
    assert (ax.get_xlabel(), ax.get_ylabel()) == ("I (mV)", "Q (mV)")


def test_plot_reports_a_missing_field_the_way_get_does():
    result, handle = _rabi_result()
    with pytest.raises(KeyError, match="has no field 'raw'"):
        result.plot(handle, field=qp.MeasurementField.RAW)


def _parallel_result() -> tuple[qp.QProgramResult, qp.MeasurementHandle]:
    """Run a parallel composition, so the dimension and its coordinates are the executor's own."""
    schema = qp.BusSchema.transmon()
    program = qp.QProgram(label="parallel", schema=schema)
    amp = program.variable("amp", label="Amplitude", units="V")
    freq = program.variable("freq", label="Frequency", units="Hz")
    with program.sweep(amp, qp.Range(0.0, 1.0, 0.25)) | program.sweep(freq, qp.Range(4e9, 5e9, 0.25e9)):
        handle = program.measure(schema.q[0].readout, "readout", "weights")
    return qp.simulate(program.with_waveforms(_LIBRARY)), handle


def test_plot_of_a_parallel_sweep_reads_the_second_variable_on_a_twin_axis():
    result, handle = _parallel_result()
    ax = result.plot(handle)
    assert ax.get_xlabel() == "Amplitude (V)"
    (twin,) = ax.child_axes
    assert twin.get_xlabel() == "Frequency (Hz)"
    assert twin.xaxis.get_label_position() == "top"


def test_plot_of_a_parallel_sweep_takes_the_axis_it_is_told_to():
    result, handle = _parallel_result()
    ax = result.plot(handle, x="freq")
    assert ax.get_xlabel() == "Frequency (Hz)"
    assert ax.child_axes == []


def test_a_twin_ticks_at_the_samples_it_shares_with_its_axis():
    result, handle = _parallel_result()
    ax = result.plot(handle)
    (twin,) = ax.child_axes
    ax.get_figure().canvas.draw()
    assert np.allclose(twin.get_xticks(), [0.0, 0.25, 0.5, 0.75, 1.0])
    assert [text.get_text() for text in twin.get_xticklabels()] == ["4e+09", "4.25e+09", "4.5e+09", "4.75e+09", "5e+09"]


def test_twin_ticks_thin_out_a_sweep_longer_than_the_style_asks_for():
    result, handle = _parallel_result()
    ax = result.plot(handle, style=Style(twin_ticks=3))
    (twin,) = ax.child_axes
    assert np.allclose(twin.get_xticks(), [0.0, 0.5, 1.0])


def test_a_twin_takes_the_limits_of_the_axis_it_doubles():
    result, handle = _parallel_result()
    ax = result.plot(handle)
    (twin,) = ax.child_axes
    ax.set_xlim(0.2, 0.8)
    ax.get_figure().canvas.draw()
    assert twin.get_xlim() == ax.get_xlim()


def test_a_twin_reads_in_whatever_unit_it_was_restated_to():
    result, handle = _parallel_result()
    ax = result.plot(handle, coords={"freq": Quantity(units="GHz", transform=lambda v: v / 1e9)})
    (twin,) = ax.child_axes
    assert twin.get_xlabel() == "Frequency (GHz)"
    assert [text.get_text() for text in twin.get_xticklabels()] == ["4", "4.25", "4.5", "4.75", "5"]
