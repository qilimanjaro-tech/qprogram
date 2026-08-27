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
"""Render the figures the example pages embed.

Every figure comes from running that page's own program on the reference platform, so a plot can
never drift from the code printed above it. Change a program on a page, re-run this, and the picture
follows.

Each figure is written twice, once per documentation theme, as ``<name>-light.png`` and
``<name>-dark.png``. The pages embed them with the ``#only-light`` / ``#only-dark`` fragments the
site's stylesheet keys on ``data-md-color-scheme``, so the reader gets the one built for the surface
they are looking at rather than a light figure dimmed by CSS.

Usage::

    uv run --extra viz python .claude/skills/qprogram-docs/scripts/build_example_plots.py
    uv run --extra viz python .claude/skills/qprogram-docs/scripts/build_example_plots.py rabi cpmg

The colours are the data-visualisation palette's categorical slots, in slot order, with a separate
set of steps chosen for the dark surface rather than the light ones re-used. The sequential ramp is
one hue and runs away from the surface in both themes: light to dark on white, dark to light on
slate, so that "more" is always the mark that stands furthest off the page.
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

import qprogram as qp

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

OUT = Path(__file__).resolve().parents[4] / "docs" / "assets" / "plots"

# --------------------------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------------------------
#
# The surfaces are the site's own: Material's ``default`` scheme is white, and ``slate`` is
# hsl(225, 15%, 14%). Matching them exactly is what lets a figure sit on the page without a seam.
# The series steps are the categorical slots 1-4; the dark column is the same four hues stepped for
# the dark surface, validated against it rather than flipped.

THEMES: dict[str, dict[str, Any]] = {
    "light": {
        "surface": "#ffffff",
        "text": "#0b0b0b",
        "muted": "#52514e",
        "grid": "#e6e6e3",
        "series": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),
        "ramp": ("#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#256abf", "#184f95", "#0d366b"),
    },
    "dark": {
        "surface": "#1e2129",
        "text": "#e2e4e9",
        "muted": "#a2a7b3",
        "grid": "#33373f",
        "series": ("#3987e5", "#d95926", "#199e70", "#c98500"),
        "ramp": ("#232733", "#1d3a63", "#1c5497", "#2a78d6", "#5598e7", "#9ec5f4", "#cde2fb"),
    },
}

DPI = 160
WIDE = (7.2, 4.0)


def _style(ax: Axes, theme: dict[str, Any]) -> None:
    """Push the frame back so the data reads first."""
    ax.set_facecolor(theme["surface"])
    ax.grid(visible=True, color=theme["grid"], linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["grid"])
    ax.tick_params(colors=theme["muted"], labelsize=9, length=0)
    ax.xaxis.label.set_color(theme["muted"])
    ax.yaxis.label.set_color(theme["muted"])
    ax.xaxis.label.set_fontsize(10)
    ax.yaxis.label.set_fontsize(10)


def _legend(ax: Axes, theme: dict[str, Any], **kwargs: Any) -> None:
    """A legend with no box, so it does not compete with the marks."""
    leg = ax.legend(frameon=False, fontsize=9, **kwargs)
    for text in leg.get_texts():
        text.set_color(theme["text"])


def _save(fig: Figure, name: str, mode: str, theme: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}-{mode}.png"
    fig.savefig(path, dpi=DPI, facecolor=theme["surface"], bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"  wrote {path.relative_to(OUT.parents[2])}")


FIGURES: dict[str, Callable[[], Callable[[str, dict[str, Any]], None]]] = {}


def figure(name: str) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Register a figure builder.

    The decorated function runs the page's program once and returns a ``draw(mode, theme)``
    closure, so the data is computed a single time and rendered once per theme.

    Args:
        name (str): The figure's name, used for the output files and on the command line.

    Returns:
        The decorator that registers the builder under ``name``.
    """

    def register(fn: Callable[[], Any]) -> Callable[[], Any]:
        FIGURES[name] = fn
        return fn

    return register


# --------------------------------------------------------------------------------------------
# Shared calibration
# --------------------------------------------------------------------------------------------

SCHEMA = qp.BusSchema.transmon()
Q = SCHEMA.q


def _iq(amplitude: float, duration: int) -> qp.waveforms.IQPair:
    return qp.waveforms.IQPair(qp.waveforms.Square(amplitude, duration), qp.waveforms.Square(0.0, duration))


READOUT = _iq(1.0, 2000)
WEIGHTS = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000))


# --------------------------------------------------------------------------------------------
# Rabi oscillation
# --------------------------------------------------------------------------------------------


@figure("rabi")
def _rabi() -> Callable[[str, dict[str, Any]], None]:
    program = qp.QProgram(label="rabi", schema=SCHEMA)
    gain = program.variable("gain", label="Drive amplitude", units="V")
    with program.average(shots=1000), program.sweep(gain, qp.Range(start=0.0, stop=1.0, step=0.01)):
        program.set_gain(Q[0].drive, gain)
        program.play(Q[0].drive, "pi_pulse")
        program.sync()
        m0 = program.measure(Q[0].readout, "readout", "weights")

    library = {
        "pi_pulse": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": READOUT,
        "weights": WEIGHTS,
    }
    model = qp.MockMeasurementModel(
        response=lambda bus, env: np.sin(np.pi * env["gain"]) ** 2 + 0j,  # ruff: ignore[unused-lambda-argument]
        noise=0.02,
    )
    data = qp.simulate(program.with_waveforms(library), model=model).get(m0)

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        x = data.coords["gain"]
        ax.plot(x, data.sel(IQ="I"), color=theme["series"][0], linewidth=1.8, label="I", zorder=3)
        ax.plot(x, data.sel(IQ="Q"), color=theme["series"][1], linewidth=1.8, label="Q", zorder=3)
        ax.set_xlabel("Drive amplitude (V)")
        ax.set_ylabel("Readout response")
        _legend(ax, theme, loc="upper left")
        _save(fig, "rabi", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# Qubit spectroscopy
# --------------------------------------------------------------------------------------------


@figure("qubit-spectroscopy")
def _qubit_spectroscopy() -> Callable[[str, dict[str, Any]], None]:
    program = qp.QProgram(label="qubit_spectroscopy", schema=SCHEMA)
    freq = program.variable("freq", label="Drive frequency", units="Hz")
    with program.average(shots=1000), program.sweep(freq, qp.Linspace(4.6e9, 5.4e9, num=201)):
        program.set_frequency(Q[0].drive, freq)
        program.play(Q[0].drive, "saturation")
        program.sync()
        m0 = program.measure(Q[0].readout, "readout", "weights")

    library = {"saturation": _iq(0.02, 20000), "readout": READOUT, "weights": WEIGHTS}

    def lorentzian(bus: str, env: dict[str, float]) -> complex:  # ruff: ignore[unused-function-argument]
        f0, hwhm = 5.0e9, 8e6
        return 1.0 / (1.0 + ((env["freq"] - f0) / hwhm) ** 2) + 0j

    model = qp.MockMeasurementModel(response=lorentzian, noise=0.01)
    data = qp.simulate(program.with_waveforms(library), model=model).get(m0)
    magnitude = np.hypot(data.sel(IQ="I"), data.sel(IQ="Q"))
    ghz = data.coords["freq"] / 1e9
    peak = float(ghz[int(np.argmax(magnitude.values))])

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        ax.plot(ghz, magnitude, color=theme["series"][0], linewidth=1.8, zorder=3)
        ax.axvline(peak, color=theme["muted"], linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate(
            f"f01 = {peak:.3f} GHz",
            xy=(peak, float(magnitude.max())),
            xytext=(8, -6),
            textcoords="offset points",
            color=theme["text"],
            fontsize=9,
        )
        ax.set_xlabel("Drive frequency (GHz)")
        ax.set_ylabel("Readout magnitude")
        _save(fig, "qubit-spectroscopy", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# T1 and Ramsey
# --------------------------------------------------------------------------------------------

_COHERENCE_LIBRARY = {
    "pi_pulse": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
    "pi_half": qp.waveforms.IQDrag(amplitude=0.25, duration=40, sigma=8, beta=0.1),
    "readout": READOUT,
    "weights": WEIGHTS,
}


@figure("t1")
def _t1() -> Callable[[str, dict[str, Any]], None]:
    t1 = qp.QProgram(label="t1", schema=SCHEMA)
    delay = t1.variable("delay", label="Delay", units="ns")
    with t1.average(shots=1000), t1.sweep(delay, qp.Linspace(0.0, 40_000.0, num=41)):
        t1.play(Q[0].drive, "pi_pulse")
        t1.wait(Q[0].drive, delay)
        t1.sync()
        m0 = t1.measure(
            Q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )

    model = qp.MockMeasurementModel(
        p_excited=lambda bus, env: float(np.exp(-env["delay"] / 12_000.0)),  # ruff: ignore[unused-lambda-argument]
        seed=3,
    )
    population = qp.simulate(t1.with_waveforms(_COHERENCE_LIBRARY), model=model).get(
        m0, field=qp.MeasurementField.STATE
    )

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        micro = population.coords["delay"] / 1000.0
        ax.plot(
            micro,
            population,
            color=theme["series"][0],
            linewidth=1.8,
            marker="o",
            markersize=3.5,
            zorder=3,
        )
        ax.set_xlabel("Delay (μs)")
        ax.set_ylabel("Excited-state population")
        ax.set_ylim(-0.05, 1.05)
        _save(fig, "t1", mode, theme)

    return draw


@figure("ramsey")
def _ramsey() -> Callable[[str, dict[str, Any]], None]:
    ramsey = qp.QProgram(label="ramsey", schema=SCHEMA)
    delay = ramsey.variable("delay", label="Free evolution", units="ns")
    with ramsey.average(shots=1000), ramsey.sweep(delay, qp.Linspace(0.0, 3000.0, num=151)):
        ramsey.reset_phase(Q[0].drive)
        ramsey.play(Q[0].drive, "pi_half")
        ramsey.wait(Q[0].drive, delay)
        ramsey.set_phase(Q[0].drive, 2 * np.pi * 2e-3 * delay)
        ramsey.play(Q[0].drive, "pi_half")
        ramsey.sync()
        m1 = ramsey.measure(
            Q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )

    def fringe(bus: str, env: dict[str, float]) -> float:  # ruff: ignore[unused-function-argument]
        t = env["delay"]
        return 0.5 * (1 - np.cos(2 * np.pi * 2e-3 * t) * np.exp(-t / 1500.0))

    model = qp.MockMeasurementModel(p_excited=fringe, seed=1)
    population = qp.simulate(ramsey.with_waveforms(_COHERENCE_LIBRARY), model=model).get(
        m1, field=qp.MeasurementField.STATE
    )

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        t = population.coords["delay"]
        ax.plot(t, population, color=theme["series"][0], linewidth=1.8, zorder=3)
        envelope = 0.5 * (1 + np.exp(-t / 1500.0))
        ax.plot(t, envelope, color=theme["muted"], linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate(
            "decay envelope",
            xy=(float(t[110]), float(envelope[110])),
            xytext=(0, 10),
            textcoords="offset points",
            color=theme["muted"],
            fontsize=9,
        )
        ax.set_xlabel("Free evolution (ns)")
        ax.set_ylabel("Excited-state population")
        ax.set_ylim(-0.05, 1.05)
        _save(fig, "ramsey", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# CZ chevron
# --------------------------------------------------------------------------------------------


@figure("cz-chevron")
def _cz_chevron() -> Callable[[str, dict[str, Any]], None]:
    schema = qp.BusSchema.flux_tunable_transmon()
    q = schema.q
    program = qp.QProgram(label="cz_chevron", schema=schema)
    amp = program.variable("amp", label="Flux amplitude", units="V")
    dur = program.variable("dur", label="Flux duration", units="ns")

    with program.average(shots=1000), program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):  # ruff: ignore[multiple-with-statements]
        with program.sweep(dur, qp.Range(10, 210, 2)):
            program.play(q[1].drive, "pi")
            program.sync()
            program.play(
                q[0].flux,
                qp.waveforms.FlatTop(amplitude=amp, duration=dur, smooth_duration=5),
            )
            program.sync()
            m0 = program.measure(q[0].readout, "readout", "weights")
            program.measure(q[1].readout, "readout", "weights")

    library = {
        "pi": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": READOUT,
        "weights": WEIGHTS,
    }

    def chevron(bus: str, env: dict[str, float]) -> complex:  # ruff: ignore[unused-function-argument]
        coupling = 0.01
        detuning = 0.05 * (env["amp"] - 0.5)
        rate = np.hypot(coupling, detuning)
        return (coupling / rate) ** 2 * np.sin(np.pi * rate * env["dur"]) ** 2 + 0j

    # The documented size: a 101 x 101 grid at 1000 shots with two measurements is 20 million model
    # samples and around eight minutes on one core. It is run as written rather than coarsened,
    # which is the point of building the figure from the page's own program.
    result = qp.simulate(program.with_waveforms(library), model=qp.MockMeasurementModel(response=chevron))
    data0 = result.get(m0)

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=(7.2, 4.4))
        _style(ax, theme)
        ax.grid(visible=False)
        cmap = LinearSegmentedColormap.from_list("qp-sequential", theme["ramp"])
        mesh = ax.pcolormesh(
            data0.coords["dur"],
            data0.coords["amp"],
            data0.sel(IQ="I"),
            cmap=cmap,
            shading="nearest",
            rasterized=True,
        )
        bar = fig.colorbar(mesh, ax=ax, pad=0.02)
        bar.set_label("Population transferred", color=theme["muted"], fontsize=10)
        bar.ax.tick_params(colors=theme["muted"], labelsize=9, length=0)
        bar.outline.set_visible(True)
        bar.outline.set_edgecolor(theme["grid"])
        bar.outline.set_linewidth(0.8)
        ax.set_xlabel("Flux duration (ns)")
        ax.set_ylabel("Flux amplitude (V)")
        _save(fig, "cz-chevron", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# Active reset
# --------------------------------------------------------------------------------------------


@figure("active-reset")
def _active_reset() -> Callable[[str, dict[str, Any]], None]:
    program = qp.QProgram(label="active_reset", schema=SCHEMA)
    amp = program.variable("amp", label="Drive amplitude", units="V")
    with program.average(shots=1000), program.sweep(amp, qp.Linspace(0.0, 1.0, num=21)):
        check = program.measure(Q[0].readout, "readout", "weights", fields=(qp.MeasurementField.STATE,))
        with program.if_(check.state == 1):
            program.play(Q[0].drive, "pi")
        with program.else_():
            program.wait(Q[0].drive, 40)
        program.sync()
        program.set_gain(Q[0].drive, amp)
        program.play(Q[0].drive, "pi")
        program.sync()
        m0 = program.measure(
            Q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )

    library = {
        "pi": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": READOUT,
        "weights": WEIGHTS,
    }
    model = qp.MockMeasurementModel(
        response=lambda bus, env: np.sin(np.pi * env["amp"]) ** 2 + 0j,  # ruff: ignore[unused-lambda-argument]
        p_excited=lambda bus, env: 0.1,  # ruff: ignore[unused-lambda-argument]
    )
    result = qp.simulate(program.with_waveforms(library), model=model)
    rabi = result.get(m0)
    heralds = result.get(check, field=qp.MeasurementField.STATE)

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, (top, bottom) = plt.subplots(2, 1, figsize=(7.2, 4.8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        for ax in (top, bottom):
            _style(ax, theme)
        x = rabi.coords["amp"]
        top.plot(x, rabi.sel(IQ="I"), color=theme["series"][0], linewidth=1.8, marker="o", markersize=3.5, zorder=3)
        top.set_ylabel("Readout response")
        bottom.plot(x, heralds, color=theme["series"][1], linewidth=1.8, marker="o", markersize=3.5, zorder=3)
        bottom.set_ylim(0.0, 0.2)
        bottom.set_ylabel("Herald rate")
        bottom.set_xlabel("Drive amplitude (V)")
        for ax, title in ((top, "Rabi sweep behind the reset"), (bottom, "Shots that needed the pi pulse")):
            ax.set_title(title, color=theme["text"], fontsize=10, loc="left", pad=6)
        fig.align_ylabels((top, bottom))
        _save(fig, "active-reset", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# Resonator spectroscopy
# --------------------------------------------------------------------------------------------


@figure("resonator-spectroscopy")
def _resonator_spectroscopy() -> Callable[[str, dict[str, Any]], None]:
    program = qp.QProgram(label="resonator_spectroscopy", schema=SCHEMA)
    lo = program.variable("lo", label="Readout LO", units="Hz")
    with program.average(shots=1000), program.sweep(lo, qp.Linspace(7.0e9, 7.4e9, num=101)):
        program.set_parameter(Q[0].readout, "lo_frequency", lo)
        m0 = program.measure(Q[0].readout, "readout", "weights")

    def notch(bus: str, env: dict[str, float]) -> complex:  # ruff: ignore[unused-function-argument]
        f = env["q0/readout.lo_frequency"]
        return 1.0 - 1.0 / (1.0 + ((f - 7.2e9) / 4e6) ** 2) + 0j

    platform = qp.ReferencePlatform(schema=SCHEMA, model=qp.MockMeasurementModel(response=notch, noise=0.005))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", qp.ExecutionWarning)
        data = platform.execute(program.with_waveforms({"readout": READOUT, "weights": WEIGHTS})).get(m0)

    ghz = data.coords["lo"] / 1e9
    magnitude = np.hypot(data.sel(IQ="I"), data.sel(IQ="Q"))
    dip = float(ghz[int(np.argmin(magnitude.values))])

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        ax.plot(ghz, magnitude, color=theme["series"][0], linewidth=1.8, zorder=3)
        ax.axvline(dip, color=theme["muted"], linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate(
            f"{dip:.3f} GHz",
            xy=(dip, float(magnitude.min())),
            xytext=(8, 14),
            textcoords="offset points",
            color=theme["text"],
            fontsize=9,
        )
        ax.set_xlabel("Readout LO frequency (GHz)")
        ax.set_ylabel("Transmitted magnitude")
        _save(fig, "resonator-spectroscopy", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# CPMG on two qubits
# --------------------------------------------------------------------------------------------


@figure("cpmg")
def _cpmg() -> Callable[[str, dict[str, Any]], None]:
    @qp.fragment
    def cpmg(f, drive, readout, tau):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Two pi/2 pulses around a train of four refocusing pi pulses."""
        f.play(drive, "pi_half")
        for _ in range(4):
            f.wait(drive, tau / 2)
            f.play(drive, "pi")
            f.wait(drive, tau / 2)
        f.play(drive, "pi_half")
        f.sync([drive, readout])
        f.measure(readout, "readout", "weights", fields=(qp.MeasurementField.STATE,))

    program = qp.QProgram(label="cpmg", schema=SCHEMA)
    tau = program.variable("tau", label="Pulse spacing", units="ns")
    with program.average(shots=1000), program.sweep(tau, qp.Linspace(20.0, 2000.0, num=100)):
        program.call(cpmg, Q[0].drive, Q[0].readout, tau)
        program.call(cpmg, Q[1].drive, Q[1].readout, tau)

    library = {
        "pi_half": qp.waveforms.IQDrag(amplitude=0.25, duration=40, sigma=8, beta=0.1),
        "pi": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": READOUT,
        "weights": WEIGHTS,
    }

    def coherence(bus: str, env: dict[str, float]) -> float:
        t2 = 3000.0 if bus.startswith("q0") else 1200.0
        return 0.5 * (1.0 - np.exp(-(4 * env["tau"]) / t2))

    result = qp.simulate(program.with_waveforms(library), model=qp.MockMeasurementModel(p_excited=coherence, seed=0))
    q0 = result.get("m0", field=qp.MeasurementField.STATE)
    q1 = result.get("m0_2", field=qp.MeasurementField.STATE)

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        x = q0.coords["tau"]
        for series, data, label in ((0, q0, "q0"), (1, q1, "q1")):
            ax.plot(x, data, color=theme["series"][series], linewidth=1.8, label=label, zorder=3)
        ax.set_xlabel("Pulse spacing (ns)")
        ax.set_ylabel("Excited-state population")
        # The two curves converge at the long-spacing end, so a direct label there would sit on
        # top of its neighbour; with two series the legend carries identity on its own.
        _legend(ax, theme, loc="lower right")
        _save(fig, "cpmg", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# Multiplexed readout
# --------------------------------------------------------------------------------------------


@figure("multiplexed-readout")
def _multiplexed_readout() -> Callable[[str, dict[str, Any]], None]:
    qubits = (0, 1, 2, 3)
    program = qp.QProgram(label="multiplexed_rabi", schema=SCHEMA)
    amp = program.variable("amp", label="Drive amplitude", units="V")
    with program.average(shots=1000), program.sweep(amp, qp.Linspace(0.0, 1.0, num=21)):
        for i in qubits:
            program.set_gain(Q[i].drive, amp)
            program.play(Q[i].drive, "pi_pulse")
        program.sync()
        for i in qubits:
            program.measure(
                Q[i].readout,
                "readout",
                "weights",
                fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
            )

    library = qp.WaveformLibrary()
    library.set("readout", _iq(0.9, 1000), element="q", idx=0, kind="readout")
    library.set("readout", _iq(0.7, 3000), element="q", idx=2, kind="readout")
    library.set("readout", _iq(0.5, 2000), element="q", kind="readout")
    library.set("weights", WEIGHTS)
    library.set("pi_pulse", qp.waveforms.IQDrag(0.5, 40, 8, 0.1))

    period = {"q0/readout": 1.0, "q1/readout": 2.0, "q2/readout": 3.0, "q3/readout": 4.0}

    def rabi(bus: str, env: dict[str, float]) -> float:
        return float(np.sin(np.pi * env["amp"] / period[bus]) ** 2)

    model = qp.MockMeasurementModel(response=lambda bus, env: rabi(bus, env) + 0j, p_excited=rabi, noise=0.02, seed=0)
    result = qp.simulate(program.with_waveforms(library), model=model)
    series = [result.get(i, field=qp.MeasurementField.STATE) for i in qubits]

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=WIDE)
        _style(ax, theme)
        x = series[0].coords["amp"]
        for i, data in enumerate(series):
            label = f"q{i}"
            ax.plot(x, data, color=theme["series"][i], linewidth=1.8, label=label, zorder=3)
            ax.annotate(
                label,
                xy=(float(x[-1]), float(data[-1])),
                xytext=(6, 0),
                textcoords="offset points",
                color=theme["text"],
                fontsize=9,
                va="center",
            )
        ax.set_xlabel("Drive amplitude (V)")
        ax.set_ylabel("Excited-state population")
        ax.set_xlim(float(x.min()), float(x.max()) * 1.07)
        # q0 peaks at the top of the axes, so the legend goes above them rather than inside.
        _legend(ax, theme, loc="lower left", bbox_to_anchor=(0.0, 1.01), ncol=4)
        _save(fig, "multiplexed-readout", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------
# Single-shot readout
# --------------------------------------------------------------------------------------------


@figure("single-shot-readout")
def _single_shot_readout() -> Callable[[str, dict[str, Any]], None]:
    program = qp.QProgram(label="single_shot_readout", schema=SCHEMA)
    prepared = program.variable("prepared", label="Prepared state")
    shot = program.variable("shot", label="Shot index")
    with program.sweep(prepared, qp.Values([0, 1])), program.sweep(shot, qp.Range(0, 1999, 1)):
        program.set_gain(Q[0].drive, prepared)
        program.play(Q[0].drive, "pi_pulse")
        program.sync()
        shots = program.measure(
            Q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
            name="shots",
        )

    class ReadoutModel:
        """Two gaussian blobs in the IQ plane, classified by a threshold on I."""

        def __init__(self, separation: float = 4.0, sigma: float = 1.0, seed: int = 0) -> None:
            self.separation = separation
            self.sigma = sigma
            self._rng = np.random.default_rng(seed)

        def sample(self, bus: str, env: dict[str, float]) -> qp.MeasurementSample:  # ruff: ignore[unused-method-argument]
            center = self.separation if env["prepared"] else 0.0
            i = center + self._rng.normal(0.0, self.sigma)
            qv = self._rng.normal(0.0, self.sigma)
            return qp.MeasurementSample(i=i, q=qv, state=int(i > self.separation / 2))

    library = {
        "pi_pulse": qp.waveforms.IQDrag(0.5, 40, 8, 0.1),
        "readout": READOUT,
        "weights": WEIGHTS,
    }
    result = qp.simulate(program.with_waveforms(library), model=ReadoutModel(seed=0))
    iq = result.get(shots)
    state = result.get("shots", field=qp.MeasurementField.STATE).values
    fidelity = 1 - (state[0].mean() + (1 - state[1].mean())) / 2

    def draw(mode: str, theme: dict[str, Any]) -> None:
        fig, ax = plt.subplots(figsize=(5.6, 5.0))
        _style(ax, theme)
        for prep, series, label in ((0, 0, "prepared |0>"), (1, 1, "prepared |1>")):
            ax.scatter(
                iq[prep].sel(IQ="I"),
                iq[prep].sel(IQ="Q"),
                s=5,
                alpha=0.45,
                linewidths=0,
                color=theme["series"][series],
                label=label,
                zorder=3,
            )
        ax.axvline(2.0, color=theme["muted"], linewidth=1.0, linestyle=(0, (4, 3)), zorder=4)
        ax.annotate(
            "threshold",
            xy=(2.0, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(6, -12),
            textcoords="offset points",
            color=theme["muted"],
            fontsize=9,
            va="top",
        )
        ax.set_xlabel("I (arb.)")
        ax.set_ylabel("Q (arb.)")
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(f"Assignment fidelity {fidelity:.3f}", color=theme["text"], fontsize=10, loc="left", pad=6)
        _legend(ax, theme, loc="upper left", markerscale=2.4)
        _save(fig, "single-shot-readout", mode, theme)

    return draw


# --------------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Build the requested figures, or every one of them when given no names.

    Args:
        argv (list[str]): Figure names to build. Empty means all of them.

    Returns:
        A process exit status: ``0`` on success, ``1`` when a name is not a known figure.
    """
    wanted = argv or sorted(FIGURES)
    unknown = [name for name in wanted if name not in FIGURES]
    if unknown:
        print(f"unknown figure(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(sorted(FIGURES))}", file=sys.stderr)
        return 1
    for name in wanted:
        started = time.perf_counter()
        print(f"{name}: running the program...")
        draw = FIGURES[name]()
        for mode, theme in THEMES.items():
            draw(mode, theme)
        print(f"  {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
