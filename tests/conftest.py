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
"""Shared fixtures for the qprogram test suite.

The matplotlib backend and the figure teardown live here rather than in the modules that draw:
`filterwarnings = ["error"]` turns matplotlib's too-many-figures warning into a failure, and which
module leaked the twentieth figure is not something the failure would say.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import _dummy_vendor
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pytest

from qprogram import QProgram, Variable
from qprogram.buses import BusNaming, BusSchema
from qprogram.sweeps import Range
from qprogram.waveforms import Gaussian, IQDrag, IQPair, Square

if TYPE_CHECKING:
    from collections.abc import Iterator

mpl.use("Agg")


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    """Close every figure a test opened, so the suite never trips matplotlib's open-figure warning."""
    yield
    plt.close("all")


@pytest.fixture
def transmon_schema() -> BusSchema:
    """A fresh transmon preset schema (q with drive + readout)."""
    return BusSchema.transmon()


@pytest.fixture
def flux_tunable_schema() -> BusSchema:
    """A flux-tunable transmon preset (q with drive + readout + flux)."""
    return BusSchema.flux_tunable_transmon()


@pytest.fixture
def fluxonium_schema() -> BusSchema:
    """A fluxonium preset (q with drive + readout + flux_x + flux_z)."""
    return BusSchema.fluxonium()


@pytest.fixture
def coupled_schema() -> BusSchema:
    """A transmon-coupled preset (adds .c with flux)."""
    return BusSchema.transmon_coupled()


@pytest.fixture
def custom_naming_schema() -> BusSchema:
    """A transmon with a custom naming pattern."""
    return BusSchema.transmon(naming=BusNaming("{kind}_{element}{index}_bus"))


@pytest.fixture
def dynamic_schema() -> BusSchema:
    """A dynamically-built schema with element ``q`` (drive + readout)."""
    schema = BusSchema()
    schema.add_element("q", {"drive": ("IQ", False), "readout": ("IQ", True)})
    return schema


@pytest.fixture
def empty_program() -> QProgram:
    """An empty QProgram with no schema."""
    return QProgram(label="empty")


@pytest.fixture
def schema_program(transmon_schema: BusSchema) -> QProgram:
    """A QProgram with a transmon schema attached, no body."""
    return QProgram(label="schema_program", schema=transmon_schema)


@pytest.fixture
def freq_var() -> Variable:
    """A bare Variable named 'freq', unattached to any program."""
    return Variable("freq")


@pytest.fixture
def gain_var() -> Variable:
    """A bare Variable named 'gain', unattached to any program."""
    return Variable("gain")


@pytest.fixture
def square_pulse() -> Square:
    """A Square waveform with simple numeric params."""
    return Square(amplitude=0.5, duration=100)


@pytest.fixture
def gaussian_pulse() -> Gaussian:
    """A Gaussian waveform with simple numeric params."""
    return Gaussian(amplitude=0.5, duration=40, sigma=8)


@pytest.fixture
def iq_pulse() -> IQDrag:
    """An IQ DRAG pulse with simple numeric params."""
    return IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)


@pytest.fixture
def iq_pair_pulse() -> IQPair:
    """An IQPair built from two Square waveforms."""
    return IQPair(I=Square(0.5, 100), Q=Square(0.0, 100))


@pytest.fixture
def rabi_program(transmon_schema: BusSchema) -> QProgram:
    """A small but realistic Rabi-style program with average + sweep + measure."""
    p = QProgram(label="rabi", schema=transmon_schema)
    gain = p.variable("gain")
    with p.average(1000), p.sweep(gain, Range(0.0, 1.0, 0.01)):
        p.set_gain(transmon_schema.q[0].drive, gain)
        p.play(transmon_schema.q[0].drive, "pi_pulse")
        p.sync()
        p.measure(transmon_schema.q[0].readout, "readout", "weights")
    return p


@pytest.fixture
def array_values() -> np.ndarray:
    """A small numeric array suitable for a ``Values`` sweep source."""
    return np.array([0.0, 0.1, 0.3, 0.5, 0.7, 1.0])


@pytest.fixture
def dummy_vendor() -> Iterator[None]:
    """Activate the in-tree dummy vendor for one test, then tear it down.

    See :mod:`tests._dummy_vendor` for what gets registered.
    """
    _dummy_vendor.activate()
    try:
        yield
    finally:
        _dummy_vendor.deactivate()
