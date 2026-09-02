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
            "b": xr.DataArray([10.0, 20.0, 30.0, 40.0], dims="a|b"),
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
    with pytest.raises(ValidationError, match="kind must be one of"):
        build_figure(_sweep_iq(), kind="bar")


def test_line_rejects_a_two_dimensional_array():
    with pytest.raises(ValidationError, match="exactly one dimension besides 'IQ'"):
        build_figure(_grid_iq(), kind="line")


def test_heatmap_rejects_a_one_dimensional_array():
    with pytest.raises(ValidationError, match="exactly two dimensions besides 'IQ'"):
        build_figure(_sweep_iq(), kind="heatmap")


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


def test_a_composed_dimension_refuses_to_guess_an_axis():
    with pytest.raises(ValidationError, match="composes 2 swept variables"):
        build_figure(_composed())


def test_x_names_one_of_the_composed_coordinates():
    figure = build_figure(_composed(), x="a")
    assert figure.x_label == "Gain (V)"
    assert np.allclose(figure.marks[0].x, [0.0, 1.0, 2.0, 3.0])


def test_naming_the_composed_dimension_plots_the_sweep_index():
    figure = build_figure(_composed(), x="a|b")
    assert figure.x_label == "a|b"
    assert np.allclose(figure.marks[0].x, [0, 1, 2, 3])


def test_an_unknown_x_names_what_is_available():
    with pytest.raises(ValidationError, match="Coordinates: a, b, IQ"):
        build_figure(_composed(), x="nope")


def test_a_lone_coordinate_on_a_bare_dimension_needs_no_choosing():
    data = xr.DataArray(
        np.zeros((3, 2)),
        dims=("a|", "IQ"),
        coords={"a": xr.DataArray([1.0, 2.0, 3.0], dims="a|"), "IQ": ["I", "Q"]},
    )
    assert build_figure(data).x_label == "a"


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
    with pytest.raises(ValidationError, match="channels must be one of"):
        build_figure(_sweep_iq(), channels="abs")


def test_channels_need_quadratures_to_choose_between():
    with pytest.raises(ValidationError, match="needs an 'IQ' dimension"):
        build_figure(_state(), channels="i")


def test_an_array_without_quadratures_draws_one_unnamed_line():
    figure = build_figure(_state())
    assert [mark.label for mark in figure.marks] == [None]
    assert figure.y_label == "Value"


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
    with pytest.raises(ValidationError, match="a heatmap needs one on each axis"):
        build_figure(_grid_iq(), x="amp", y="amp")


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
    with pytest.raises(ValidationError, match="colours one surface"):
        build_figure(_grid_iq(), channels="iq")


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
    with pytest.raises(ValidationError, match="no 'IQ' dimension"):
        build_figure(_state(), kind="scatter")


@pytest.mark.parametrize("argument", ["x", "y", "channels"])
def test_scatter_has_nothing_left_for_an_axis_argument_to_choose(argument):
    with pytest.raises(ValidationError, match=f"{argument} has nothing left to choose"):
        build_figure(_sweep_iq(), kind="scatter", **{argument: "i"})


def test_a_line_figure_has_no_second_dimension_for_y_to_name():
    with pytest.raises(ValidationError, match="only a heatmap has"):
        build_figure(_sweep_iq(), y="gain")


# ---------------------------------------------------------------------------
# The label on the measured quantity
# ---------------------------------------------------------------------------


def test_the_caller_s_value_label_wins():
    assert build_figure(_sweep_iq(), value_label="Readout response").y_label == "Readout response"


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
    resolve_renderer()  # make sure the default is registered, so the message is not empty
    with pytest.raises(KeyError, match="registered: "):
        resolve_renderer("svg")


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
    ax = result.plot(handle, value_label="Readout response")
    assert ax.get_ylabel() == "Readout response"


def test_plot_reports_a_missing_field_the_way_get_does():
    result, handle = _rabi_result()
    with pytest.raises(KeyError, match="has no field 'raw'"):
        result.plot(handle, field=qp.MeasurementField.RAW)


def test_plot_of_a_parallel_sweep_asks_which_variable_to_use():
    schema = qp.BusSchema.transmon()
    program = qp.QProgram(label="parallel", schema=schema)
    amp = program.variable("amp", label="Amplitude", units="V")
    freq = program.variable("freq", label="Frequency", units="Hz")
    with program.sweep(amp, qp.Range(0.0, 1.0, 0.25)) | program.sweep(freq, qp.Range(4e9, 5e9, 0.25e9)):
        handle = program.measure(schema.q[0].readout, "readout", "weights")
    result = qp.simulate(program.with_waveforms(_LIBRARY))
    with pytest.raises(ValidationError, match="composes 2 swept variables"):
        result.plot(handle)
    assert result.plot(handle, x="freq").get_xlabel() == "Frequency (Hz)"
