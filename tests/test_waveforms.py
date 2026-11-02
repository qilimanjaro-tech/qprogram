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
"""Tests for the built-in waveform shapes."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib.axes import Axes

import qprogram as qp
from qprogram import UnassignedVariableError, ValidationError, Variable
from qprogram.plotting import DARK, ENVELOPE_SIZE, IQ_ENVELOPE_SIZE, LIGHT, Style, register_renderer
from qprogram.waveforms import (
    Arbitrary,
    Chained,
    Cosine,
    FlatTop,
    Gaussian,
    GaussianDragCorrection,
    IQDrag,
    IQPair,
    IQRotation,
    IQWaveform,
    IQZero,
    Modulated,
    Ramp,
    Sech,
    Sine,
    Square,
    SuddenNetZero,
    Tukey,
    Waveform,
)

if TYPE_CHECKING:
    from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# Square
# ---------------------------------------------------------------------------


def test_square_envelope():
    wf = Square(amplitude=0.5, duration=10)
    env = wf.envelope()
    assert env.shape == (10,)
    assert np.allclose(env, 0.5)


def test_square_get_duration():
    wf = Square(amplitude=0.5, duration=10)
    assert wf.get_duration() == 10


def test_square_with_expression_amplitude():
    v = Variable("amp")
    v.set_value(0.7)
    wf = Square(amplitude=v, duration=10)
    assert np.allclose(wf.envelope(), 0.7)


def test_square_with_unassigned_expression_raises():
    v = Variable("amp")
    wf = Square(amplitude=v, duration=10)
    with pytest.raises(UnassignedVariableError):
        wf.envelope()


def test_square_duration_as_expression():
    v = Variable("dur")
    v.set_value(20)
    wf = Square(amplitude=0.5, duration=v)
    assert wf.get_duration() == 20


def test_square_resolution_changes_envelope_length():
    wf = Square(amplitude=0.5, duration=10)
    env = wf.envelope(resolution=2)
    assert env.shape == (5,)


# ---------------------------------------------------------------------------
# Gaussian
# ---------------------------------------------------------------------------


def test_gaussian_envelope_shape():
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=4)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_starts_at_zero_and_peaks_at_amplitude_on_duration_over_two():
    env = Gaussian(amplitude=0.8, duration=40, num_sigmas=4).envelope()
    assert env[0] == 0.0
    assert int(np.argmax(env)) == 20
    assert env[20] == pytest.approx(0.8)
    # Centred on duration / 2 rather than on the middle sample, so the last sample is not 0.
    assert env[-1] > 0.0


def test_gaussian_num_sigmas_narrows_the_pulse():
    wide = Gaussian(amplitude=1.0, duration=40, num_sigmas=2).envelope()
    narrow = Gaussian(amplitude=1.0, duration=40, num_sigmas=6).envelope()
    assert narrow.sum() < wide.sum()


def test_gaussian_with_negative_amplitude_is_not_rescaled():
    # The shifted curve's largest sample is its first, 0, so the rescale back to the peak is skipped.
    env = Gaussian(amplitude=-0.5, duration=40, num_sigmas=4).envelope()
    assert env.min() == pytest.approx(-0.5 * (1 - np.exp(-(4**2) / 8)))


def test_gaussian_shape_is_the_same_at_any_resolution():
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=4)
    assert np.allclose(wf.envelope(resolution=2), wf.envelope()[::2])


def test_gaussian_get_duration():
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=4)
    assert wf.get_duration() == 40


def test_gaussian_with_expression_params():
    amp, dur, num_sigmas = Variable("a"), Variable("d"), Variable("n")
    amp.set_value(0.5)
    dur.set_value(40)
    num_sigmas.set_value(4)
    wf = Gaussian(amplitude=amp, duration=dur, num_sigmas=num_sigmas)
    assert np.array_equal(wf.envelope(), Gaussian(amplitude=0.5, duration=40, num_sigmas=4).envelope())


# ---------------------------------------------------------------------------
# GaussianDragCorrection
# ---------------------------------------------------------------------------


def test_gaussian_drag_correction_envelope_shape():
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=0.1)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_drag_correction_crosses_zero_at_duration_over_two():
    env = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=1.0).envelope()
    assert env[20] == 0.0
    assert env[19] > 0.0
    assert env[21] < 0.0


def test_gaussian_drag_correction_scales_with_drag_coefficient():
    unit = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=1.0).envelope()
    scaled = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=-0.3).envelope()
    assert np.allclose(scaled, -0.3 * unit)


def test_gaussian_drag_correction_shape_is_the_same_at_any_resolution():
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=1.0)
    assert np.allclose(wf.envelope(resolution=2), wf.envelope()[::2])


def test_gaussian_drag_correction_with_all_expressions():
    a, d, n, coef = Variable("a"), Variable("d"), Variable("n"), Variable("coef")
    a.set_value(1.0)
    d.set_value(40)
    n.set_value(4)
    coef.set_value(0.1)
    wf = GaussianDragCorrection(amplitude=a, duration=d, num_sigmas=n, drag_coefficient=coef)
    expected = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=4, drag_coefficient=0.1).envelope()
    assert np.array_equal(wf.envelope(), expected)


# ---------------------------------------------------------------------------
# Ramp
# ---------------------------------------------------------------------------


def test_ramp_endpoints():
    wf = Ramp(from_amplitude=0.0, to_amplitude=1.0, duration=10)
    env = wf.envelope()
    assert env.shape == (10,)
    assert env[0] == 0.0
    assert env[-1] == 1.0


def test_ramp_get_duration():
    assert Ramp(0.0, 1.0, 10).get_duration() == 10


def test_ramp_with_expression_params():
    a, b, d = Variable("a"), Variable("b"), Variable("d")
    a.set_value(0.0)
    b.set_value(2.0)
    d.set_value(5)
    wf = Ramp(from_amplitude=a, to_amplitude=b, duration=d)
    env = wf.envelope()
    assert env[0] == 0.0
    assert env[-1] == 2.0


# ---------------------------------------------------------------------------
# FlatTop
# ---------------------------------------------------------------------------


def test_flat_top_envelope_shape():
    wf = FlatTop(amplitude=1.0, duration=100, smooth_duration=10)
    env = wf.envelope()
    assert env.shape == (100,)


def test_flat_top_reaches_amplitude_between_the_edges():
    env = FlatTop(amplitude=0.8, duration=100, smooth_duration=10).envelope()
    assert np.allclose(env[10:90], 0.8, atol=0.01)
    assert env[0] < 0.01
    assert env[-1] < 0.01


def test_flat_top_edges_cross_half_amplitude_half_a_smooth_duration_after_the_buffer():
    # Read half a nanosecond into each sample: sample 10 sits at 10.5 = buffer + smooth_duration / 2.
    env = FlatTop(amplitude=1.0, duration=100, smooth_duration=11, buffer=5).envelope()
    assert env[10] == pytest.approx(0.5)
    assert env[89] == pytest.approx(0.5)


def test_flat_top_get_duration():
    assert FlatTop(0.5, 100, 10).get_duration() == 100


def test_flat_top_with_expression_params():
    a, d, s = Variable("a"), Variable("d"), Variable("s")
    a.set_value(0.5)
    d.set_value(100)
    s.set_value(10)
    wf = FlatTop(amplitude=a, duration=d, smooth_duration=s)
    assert np.array_equal(wf.envelope(), FlatTop(amplitude=0.5, duration=100, smooth_duration=10).envelope())


def test_flat_top_buffer_default():
    wf = FlatTop(amplitude=0.5, duration=100, smooth_duration=10)
    assert wf.buffer == 0


def test_flat_top_buffer_moves_the_edges_inwards():
    env = FlatTop(amplitude=1.0, duration=100, smooth_duration=10, buffer=5).envelope()
    assert env.shape == (100,)
    # The same pulse as an unbuffered one 2 * buffer shorter, centred in the window.
    shorter = FlatTop(amplitude=1.0, duration=90, smooth_duration=10).envelope()
    assert np.allclose(env[5:95], shorter)


def test_flat_top_buffer_does_not_extend_duration():
    assert FlatTop(0.5, 100, 10, buffer=25).get_duration() == 100


def test_flat_top_shape_is_the_same_at_any_resolution():
    wf = FlatTop(amplitude=1.0, duration=100, smooth_duration=10, buffer=25)
    assert np.allclose(wf.envelope(resolution=5), wf.envelope()[::5])


# ---------------------------------------------------------------------------
# SuddenNetZero
# ---------------------------------------------------------------------------


def test_snz_envelope_structure():
    env = SuddenNetZero(amplitude=1.0, duration=20, b=0.5, t_phi=4).envelope()
    # Halves of (20 - 4 - 2) / 2 = 7 samples, one b sample either side of the four-sample zero hold.
    assert np.array_equal(env, [1.0] * 7 + [0.5] + [0.0] * 4 + [-0.5] + [-1.0] * 7)


def test_snz_is_net_zero():
    env = SuddenNetZero(amplitude=0.8, duration=41, b=0.3, t_phi=3).envelope()
    assert env.sum() == pytest.approx(0.0)


def test_snz_get_duration():
    wf = SuddenNetZero(amplitude=1.0, duration=20, b=0.5, t_phi=4)
    assert wf.get_duration() == 20


def test_snz_with_expression_params():
    a, d, b, t = Variable("a"), Variable("d"), Variable("b"), Variable("t")
    a.set_value(1.0)
    d.set_value(20)
    b.set_value(0.5)
    t.set_value(4)
    wf = SuddenNetZero(amplitude=a, duration=d, b=b, t_phi=t)
    assert np.array_equal(wf.envelope(), SuddenNetZero(amplitude=1.0, duration=20, b=0.5, t_phi=4).envelope())


@pytest.mark.parametrize("t_phi", [2.0, 2.5])
def test_snz_with_float_t_phi_raises(t_phi):
    wf = SuddenNetZero(amplitude=1.0, duration=12, b=0.5, t_phi=t_phi)
    with pytest.raises(IndexError):
        wf.envelope()


def test_snz_with_odd_duration_minus_t_phi_raises():
    wf = SuddenNetZero(amplitude=1.0, duration=11, b=0.5, t_phi=2)
    with pytest.raises(ValueError, match=r"positive half has 3 samples and the negative half would have 4"):
        wf.envelope()


# ---------------------------------------------------------------------------
# Arbitrary
# ---------------------------------------------------------------------------


def test_arbitrary_envelope_copy():
    samples = np.array([0.1, 0.2, 0.3, 0.4])
    wf = Arbitrary(samples)
    env = wf.envelope()
    assert np.array_equal(env, samples)
    # Should be a copy, not the original buffer.
    env[0] = 99.0
    assert wf.samples[0] == 0.1


def test_arbitrary_duration_equals_sample_length():
    samples = np.zeros(50)
    wf = Arbitrary(samples)
    assert wf.get_duration() == 50


def test_arbitrary_accepts_list():
    wf = Arbitrary([1, 2, 3])
    assert isinstance(wf.samples, np.ndarray)
    assert wf.get_duration() == 3


# ---------------------------------------------------------------------------
# Chained
# ---------------------------------------------------------------------------


def test_chained_concatenation():
    a = Square(0.5, 4)
    b = Square(1.0, 6)
    wf = Chained([a, b])
    env = wf.envelope()
    assert env.shape == (10,)
    assert np.allclose(env[:4], 0.5)
    assert np.allclose(env[4:], 1.0)


def test_chained_duration_sum():
    a = Square(0.5, 4)
    b = Square(1.0, 6)
    wf = Chained([a, b])
    assert wf.get_duration() == 10


def test_chained_empty():
    wf = Chained([])
    assert wf.get_duration() == 0


# ---------------------------------------------------------------------------
# IQPair
# ---------------------------------------------------------------------------


def test_iq_pair_components():
    i_wf = Square(1.0, 10)
    q_wf = Square(0.0, 10)
    wf = IQPair(i_wf, q_wf)
    assert wf.get_I() is i_wf
    assert wf.get_Q() is q_wf


def test_iq_pair_get_duration():
    wf = IQPair(Square(1.0, 10), Square(0.0, 10))
    assert wf.get_duration() == 10


@pytest.mark.parametrize(
    ("i", "q"),
    [
        ("not a wf", Square(0.0, 10)),
        (Square(1.0, 10), 42),
        (None, None),
    ],
)
def test_iq_pair_rejects_non_waveform_args(i, q):
    with pytest.raises(TypeError, match="must be Waveform"):
        IQPair(i, q)


def test_iq_pair_rejects_mismatched_durations():
    i, q = Square(1.0, 100), Square(0.0, 999)
    with pytest.raises(ValidationError, match="equal durations"):
        IQPair(i, q)


def test_iq_pair_defers_duration_check_for_symbolic_durations():
    # Unassigned symbolic durations can't be compared at construction time —
    # the check is deferred to the platform compiler.
    dur = Variable("dur")
    wf = IQPair(Square(1.0, dur), Square(0.0, dur))
    assert wf.get_I().duration is dur  # ty:ignore[unresolved-attribute]


# ---------------------------------------------------------------------------
# IQDrag
# ---------------------------------------------------------------------------


def test_iq_drag_components_are_gaussian_and_correction():
    wf = IQDrag(amplitude=0.5, duration=40, num_sigmas=4, drag_coefficient=0.1)
    assert wf.get_I() == Gaussian(amplitude=0.5, duration=40, num_sigmas=4)
    assert wf.get_Q() == GaussianDragCorrection(amplitude=0.5, duration=40, num_sigmas=4, drag_coefficient=0.1)


def test_iq_drag_get_duration():
    wf = IQDrag(amplitude=0.5, duration=40, num_sigmas=4, drag_coefficient=0.1)
    assert wf.get_duration() == 40


def test_iq_drag_duration_via_expression():
    d = Variable("d")
    d.set_value(40)
    wf = IQDrag(amplitude=0.5, duration=d, num_sigmas=4, drag_coefficient=0.1)
    assert wf.get_duration() == 40


# ---------------------------------------------------------------------------
# Reference samples
# ---------------------------------------------------------------------------

# Gaussian, the DRAG pair, FlatTop and SuddenNetZero are pinned sample for sample against
# data/waveform_samples.json, so a change to their shape fails here rather than shifting calibrated pulses.
_REFERENCE = json.loads((Path(__file__).parent / "data" / "waveform_samples.json").read_text(encoding="utf-8"))


def _reference_case_id(case: dict) -> str:
    params = ",".join(f"{key}={value}" for key, value in case["params"].items())
    return f"{case['waveform']}({params})@{case['resolution']}ns"


@pytest.mark.parametrize("case", _REFERENCE["cases"], ids=_reference_case_id)
def test_envelope_matches_reference_samples(case):
    wf = getattr(qp.waveforms, case["waveform"])(**case["params"])
    channels = {"I": wf.get_I(), "Q": wf.get_Q()} if isinstance(wf, IQWaveform) else {"envelope": wf}
    for key, channel in channels.items():
        # Not bit for bit: the references were computed on one platform, numpy's exp can differ in the last
        # place on another, and the FlatTop references used scipy's erf where FlatTop uses Python's.
        np.testing.assert_allclose(channel.envelope(resolution=case["resolution"]), case[key], rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# Hashability + structural equality (from §11)
# ---------------------------------------------------------------------------


def test_waveform_structural_equality():
    a = Square(0.5, 100)
    b = Square(0.5, 100)
    c = Square(0.6, 100)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_iq_waveform_structural_equality():
    a = IQPair(Square(0.5, 100), Square(0.0, 100))
    b = IQPair(Square(0.5, 100), Square(0.0, 100))
    assert a == b
    assert hash(a) == hash(b)


def test_arbitrary_structural_equality_with_ndarray():
    a = Arbitrary(np.array([1.0, 2.0, 3.0]))
    b = Arbitrary(np.array([1.0, 2.0, 3.0]))
    assert a == b
    assert hash(a) == hash(b)


def test_arbitrary_structural_inequality():
    a = Arbitrary(np.array([1.0, 2.0, 3.0]))
    b = Arbitrary(np.array([1.0, 2.0, 4.0]))
    assert a != b


def test_waveform_unequal_to_other_type():
    assert Square(0.5, 100) != "anything"


def test_iq_waveform_unequal_to_other_type():
    assert IQPair(Square(0.5, 100), Square(0.0, 100)) != "anything"


def test_chained_with_nested_waveforms_equality():
    a = Chained([Square(0.5, 10), Square(1.0, 5)])
    b = Chained([Square(0.5, 10), Square(1.0, 5)])
    assert a == b
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------


def test_waveform_is_abstract():
    with pytest.raises(TypeError):
        Waveform()


def test_iq_waveform_is_abstract():
    with pytest.raises(TypeError):
        IQWaveform()


# ---------------------------------------------------------------------------
# Sine / Cosine
# ---------------------------------------------------------------------------


def test_sine_envelope_zero_at_origin():
    wf = Sine(amplitude=1.0, duration=100, frequency=1e7, phase=0.0)
    env = wf.envelope()
    assert env[0] == pytest.approx(0.0, abs=1e-12)
    assert wf.get_duration() == 100


def test_sine_phase_offset_shifts_initial_value():
    wf = Sine(amplitude=1.0, duration=10, frequency=0.0, phase=np.pi / 2)
    env = wf.envelope()
    assert env[0] == pytest.approx(1.0)


def test_sine_with_expression_params():
    amp = Variable("a")
    freq = Variable("f")
    amp.set_value(0.5)
    # 25 MHz over 50 ns → 1.25 periods → quarter period at n=10 hits the peak exactly.
    freq.set_value(2.5e7)
    wf = Sine(amplitude=amp, duration=50, frequency=freq)
    env = wf.envelope()
    assert env.shape == (50,)
    assert np.max(np.abs(env)) == pytest.approx(0.5, rel=1e-3)


def test_sine_unassigned_raises():
    wave = Sine(amplitude=Variable("a"), duration=10, frequency=1e8)
    with pytest.raises(UnassignedVariableError):
        wave.envelope()


def test_cosine_envelope_one_at_origin():
    wf = Cosine(amplitude=0.8, duration=10, frequency=0.0)
    env = wf.envelope()
    assert env[0] == pytest.approx(0.8)


def test_cosine_get_duration():
    assert Cosine(0.5, 200, 1e8).get_duration() == 200


# ---------------------------------------------------------------------------
# Tukey
# ---------------------------------------------------------------------------


def test_tukey_alpha_zero_is_rectangular():
    wf = Tukey(amplitude=0.7, duration=20, alpha=0.0)
    env = wf.envelope()
    assert np.allclose(env, 0.7)


def test_tukey_alpha_one_is_hann():
    wf = Tukey(amplitude=1.0, duration=21, alpha=1.0)
    env = wf.envelope()
    assert env[0] == pytest.approx(0.0, abs=1e-12)
    assert env[-1] == pytest.approx(0.0, abs=1e-12)
    assert env.max() == pytest.approx(1.0)


def test_tukey_flat_region_at_amplitude():
    wf = Tukey(amplitude=0.5, duration=100, alpha=0.4)
    env = wf.envelope()
    assert env[50] == pytest.approx(0.5)
    assert env[0] == pytest.approx(0.0, abs=1e-12)


def test_tukey_with_expression_alpha():
    alpha = Variable("a")
    alpha.set_value(0.3)
    wf = Tukey(amplitude=1.0, duration=40, alpha=alpha)
    env = wf.envelope()
    assert env.max() == pytest.approx(1.0)


def test_tukey_get_duration():
    assert Tukey(1.0, 80).get_duration() == 80


# ---------------------------------------------------------------------------
# Sech
# ---------------------------------------------------------------------------


def test_sech_peak_at_center():
    wf = Sech(amplitude=1.0, duration=100, tau=12.5)
    env = wf.envelope()
    centre = env[50]
    assert centre == pytest.approx(env.max(), rel=1e-3)


def test_sech_decays_at_edges():
    wf = Sech(amplitude=1.0, duration=100, tau=8.33)
    env = wf.envelope()
    assert env[0] < env[50]
    assert env[-1] < env[50]


def test_sech_with_expression_params():
    tau = Variable("tau")
    tau.set_value(5.1)
    # Odd duration so the center lands on an exact sample and the peak == amplitude.
    wf = Sech(amplitude=0.5, duration=51, tau=tau)
    env = wf.envelope()
    assert env.max() == pytest.approx(0.5, rel=1e-6)


def test_sech_get_duration():
    assert Sech(0.5, 100, 12.5).get_duration() == 100


# ---------------------------------------------------------------------------
# Modulated
# ---------------------------------------------------------------------------


def test_modulated_produces_iq_channels():
    wf = Modulated(envelope=Gaussian(1.0, 100, 5), frequency=1e8)
    i = wf.get_I().envelope()
    q = wf.get_Q().envelope()
    assert i.shape == q.shape == (100,)


def test_modulated_unit_freq_zero_phase_starts_at_envelope():
    g = Gaussian(1.0, 100, 5)
    wf = Modulated(envelope=g, frequency=0.0, phase=0.0)
    # I = env * cos(0) = env, Q = env * sin(0) = 0
    assert np.allclose(wf.get_I().envelope(), g.envelope())
    assert np.allclose(wf.get_Q().envelope(), 0.0)


def test_modulated_phase_shift_rotates_iq():
    g = Square(1.0, 10)
    base = Modulated(envelope=g, frequency=0.0, phase=0.0)
    rotated = Modulated(envelope=g, frequency=0.0, phase=np.pi / 2)
    assert np.allclose(base.get_I().envelope(), 1.0)
    assert np.allclose(rotated.get_Q().envelope(), 1.0)


def test_modulated_rejects_non_waveform_envelope():
    envelope = cast("Waveform", "not a waveform")
    with pytest.raises(TypeError, match="must be a Waveform"):
        Modulated(envelope=envelope, frequency=1e8)


def test_modulated_get_duration_matches_envelope():
    g = Gaussian(0.5, 73, 5)
    assert Modulated(g, 1e8).get_duration() == 73


# ---------------------------------------------------------------------------
# IQRotation
# ---------------------------------------------------------------------------


def test_iq_rotation_by_zero_is_identity():
    base = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20))
    rotated = IQRotation(base=base, phase=0.0)
    assert np.allclose(rotated.get_I().envelope(), base.get_I().envelope())
    assert np.allclose(rotated.get_Q().envelope(), base.get_Q().envelope())


def test_iq_rotation_by_half_pi_swaps_channels():
    base = IQPair(I=Square(1.0, 10), Q=Square(0.0, 10))
    rotated = IQRotation(base=base, phase=np.pi / 2)
    # cos(pi/2)=0, sin(pi/2)=1, so I' = -Q = 0, Q' = I = 1
    assert np.allclose(rotated.get_I().envelope(), 0.0, atol=1e-12)
    assert np.allclose(rotated.get_Q().envelope(), 1.0)


def test_iq_rotation_with_expression():
    phase = Variable("p")
    phase.set_value(0.0)
    rotated = IQRotation(base=IQPair(Square(1.0, 5), Square(0.0, 5)), phase=phase)
    assert np.allclose(rotated.get_I().envelope(), 1.0)


def test_iq_rotation_rejects_non_iqwaveform():
    base = cast("IQWaveform", Square(1.0, 10))
    with pytest.raises(TypeError, match="must be an IQWaveform"):
        IQRotation(base=base, phase=0.0)


def test_iq_rotation_duration_passthrough():
    base = IQPair(I=Square(0.5, 47), Q=Square(0.0, 47))
    assert IQRotation(base, phase=1.0).get_duration() == 47


# ---------------------------------------------------------------------------
# IQZero
# ---------------------------------------------------------------------------


def test_iq_zero_q_channel_is_zero():
    wf = IQZero(envelope=Square(0.5, 20))
    assert np.allclose(wf.get_Q().envelope(), 0.0)


def test_iq_zero_i_channel_is_envelope():
    g = Gaussian(0.3, 30, 5)
    wf = IQZero(envelope=g)
    assert np.allclose(wf.get_I().envelope(), g.envelope())


def test_iq_zero_rejects_non_waveform():
    envelope = cast("Waveform", "bad")
    with pytest.raises(TypeError, match="must be a Waveform"):
        IQZero(envelope=envelope)


def test_iq_zero_duration():
    assert IQZero(Square(0.5, 99)).get_duration() == 99


# ---------------------------------------------------------------------------
# Waveform __add__ → Chained
# ---------------------------------------------------------------------------


def test_add_two_waveforms_produces_chained():
    chain = Square(0.5, 10) + Gaussian(0.3, 20, 5)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 2
    assert chain.get_duration() == 30


def test_add_flattens_existing_chain():
    chain = Square(0.5, 10) + Gaussian(0.3, 20, 5) + Square(0.7, 5)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_flattens_left_chain():
    left = Chained([Square(0.5, 10), Square(0.6, 5)])
    chain = left + Gaussian(0.3, 20, 5)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_flattens_right_chain():
    right = Chained([Gaussian(0.3, 20, 5), Square(0.7, 5)])
    chain = Square(0.5, 10) + right
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_with_non_waveform_returns_notimplemented():
    # Triggered indirectly: Python falls back to TypeError when both sides return NotImplemented.
    square = Square(0.5, 10)
    other = cast("Waveform", "not a waveform")
    with pytest.raises(TypeError):
        _ = square + other


# ---------------------------------------------------------------------------
# Analysis methods (Waveform)
# ---------------------------------------------------------------------------


def test_waveform_peak_amplitude_square():
    assert Square(0.5, 20).peak_amplitude() == pytest.approx(0.5)
    assert Square(-0.5, 20).peak_amplitude() == pytest.approx(0.5)


def test_waveform_rms_amplitude_constant():
    assert Square(0.3, 100).rms_amplitude() == pytest.approx(0.3)


def test_waveform_area_constant():
    assert Square(0.5, 100).area() == pytest.approx(0.5 * 99)


def test_waveform_area_negative_cancels():
    chain = Square(1.0, 50) + Square(-1.0, 50)
    assert chain.area() == pytest.approx(0.0, abs=1.0)


def test_waveform_spectrum_returns_freqs_and_complex():
    freqs, spec = Square(1.0, 64).spectrum()
    assert freqs.shape == spec.shape
    assert np.iscomplexobj(spec)
    # DC bin should hold the integrated value (= number of samples).
    assert spec[0].real == pytest.approx(64.0)


# ---------------------------------------------------------------------------
# Analysis methods (IQWaveform)
# ---------------------------------------------------------------------------


def test_iq_peak_amplitude_uses_magnitude():
    wf = IQPair(I=Square(0.6, 10), Q=Square(0.8, 10))
    # |0.6 + j0.8| = 1.0
    assert wf.peak_amplitude() == pytest.approx(1.0)


def test_iq_rms_amplitude():
    wf = IQPair(I=Square(0.6, 10), Q=Square(0.8, 10))
    assert wf.rms_amplitude() == pytest.approx(1.0)


def test_iq_area_magnitude():
    wf = IQPair(I=Square(0.5, 10), Q=Square(0.0, 10))
    assert wf.area() == pytest.approx(0.5 * 9)


def test_iq_spectrum_returns_complex():
    wf = IQPair(I=Square(1.0, 32), Q=Square(0.0, 32))
    freqs, spec = wf.spectrum()
    assert freqs.shape == spec.shape == (32,)
    assert np.iscomplexobj(spec)


# ---------------------------------------------------------------------------
# Visualization helpers (appearance is human-checked; what is decidable is asserted)
# ---------------------------------------------------------------------------


def _svg_size(html: str, index: int = -1) -> tuple[float, float]:
    """Return the ``(width, height)`` in points of one figure in a ``_repr_html_`` fragment.

    The fragment holds the dark render first and the light one second, so the default index is the
    light figure, which is the one an ``<img>`` shows.
    """
    uri = re.findall(r'data:image/svg\+xml;base64,([^"]+)', html)[index]
    svg = base64.b64decode(uri).decode("utf-8")
    width, height = re.findall(r'(?:width|height)="([\d.]+)pt"', svg)[:2]
    return float(width), float(height)


def test_waveform_plot_returns_axes():
    ax = Square(0.5, 20).plot()
    assert isinstance(ax, Axes)


def test_iq_plot_returns_axes_pair():
    axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot()
    assert len(axes) == 2
    assert all(isinstance(a, Axes) for a in axes)


def test_a_style_naming_no_size_is_drawn_at_the_envelope_size():
    ax = Square(0.5, 20).plot(style=Style(theme=DARK))
    assert tuple(cast("Figure", ax.get_figure()).get_size_inches()) == ENVELOPE_SIZE
    axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot(style=Style(theme=DARK))
    assert tuple(cast("Figure", axes[0].get_figure()).get_size_inches()) == IQ_ENVELOPE_SIZE


def test_the_default_style_is_drawn_at_the_envelope_size():
    ax = Square(0.5, 20).plot()
    assert tuple(cast("Figure", ax.get_figure()).get_size_inches()) == ENVELOPE_SIZE
    axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot()
    assert tuple(cast("Figure", axes[0].get_figure()).get_size_inches()) == IQ_ENVELOPE_SIZE


def test_a_size_on_the_style_wins():
    ax = Square(0.5, 20).plot(style=Style(size=(4.0, 1.5)))
    assert tuple(cast("Figure", ax.get_figure()).get_size_inches()) == (4.0, 1.5)
    axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot(style=Style(size=(4.0, 2.5)))
    assert tuple(cast("Figure", axes[0].get_figure()).get_size_inches()) == (4.0, 2.5)


def test_plot_draws_on_the_surface_the_style_names():
    light = Square(0.5, 20).plot()
    dark = Square(0.5, 20).plot(style=Style(theme=DARK))
    assert mpl.colors.to_hex(light.get_facecolor()) == LIGHT.surface
    assert mpl.colors.to_hex(dark.get_facecolor()) == DARK.surface


def test_plot_draws_the_envelope_in_the_first_series_colour():
    ax = Square(0.5, 20).plot()
    assert ax.get_lines()[0].get_color() == LIGHT.series[0]


def test_iq_plot_gives_the_two_channels_the_first_two_colours():
    i_axes, q_axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot()
    assert i_axes.get_lines()[0].get_color() == LIGHT.series[0]
    assert q_axes.get_lines()[0].get_color() == LIGHT.series[1]


def test_plot_draws_on_the_target_it_is_given():
    _, ax = plt.subplots()
    assert Square(0.5, 20).plot(target=ax) is ax
    _, (top, bottom) = plt.subplots(2, 1)
    assert IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot(target=(top, bottom)) == (top, bottom)


def test_plot_uses_the_renderer_it_is_told_to():
    figures = []

    def fake(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        figures.append(figure)
        return figure

    try:
        register_renderer("test-waveform", fake)
        one = Square(0.5, 20).plot(renderer="test-waveform")
        _, (top, bottom) = plt.subplots(2, 1)
        pair = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot(renderer="test-waveform", target=(top, bottom))
    finally:
        qp.plotting.renderers._renderers.pop("test-waveform", None)
    assert one.x_label == "Time (ns)"
    assert one.y_label == "Amplitude"
    assert one.title == "Square"
    assert [f.y_label for f in pair] == ["I", "Q"]
    assert [f.series for f in pair] == [0, 1]
    assert len(figures) == 3


def test_an_iq_pair_refuses_a_foreign_renderer_with_no_target_to_draw_on():
    def fake(figure, style, target=None):  # ruff: ignore[unused-function-argument]
        return figure

    wf = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20))
    try:
        register_renderer("test-no-panels", fake)
        with pytest.raises(ValidationError, match="target=\\(I, Q\\)"):
            wf.plot(renderer="test-no-panels")
    finally:
        qp.plotting.renderers._renderers.pop("test-no-panels", None)


def test_repr_html_carries_one_figure_per_surface():
    html = Square(0.5, 20)._repr_html_()
    assert html.count("data:image/svg+xml;base64,") == 2
    assert "prefers-color-scheme: dark" in html


def test_iq_repr_html_carries_one_figure_per_surface():
    html = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20))._repr_html_()
    assert html.count("data:image/svg+xml;base64,") == 2


def test_repr_html_ignores_pyplots_current_figure():
    """The markup comes from the axes ``plot`` returned, not from whatever pyplot last drew on."""
    plt.subplots(figsize=(3, 9))
    width, height = _svg_size(Square(0.5, 20)._repr_html_())
    assert width > height


def test_repr_html_closes_the_figures_it_drew():
    Square(0.5, 20)._repr_html_()
    assert plt.get_fignums() == []


# ---------------------------------------------------------------------------
# Registry and capability-token coverage
# ---------------------------------------------------------------------------


def test_new_waveforms_registered_for_serialization():
    from qprogram.serialization.registry import get_waveform_class  # ruff: ignore[import-outside-top-level]

    for cls in (Sine, Cosine, Tukey, Sech, Modulated, IQRotation, IQZero):
        assert get_waveform_class(cls.__name__) is cls


def test_new_waveforms_have_capability_tokens():
    from qprogram.protocol import waveform_token  # ruff: ignore[import-outside-top-level]

    expected = {
        Sine(0.5, 10, 1e8): "waveform.sine",
        Cosine(0.5, 10, 1e8): "waveform.cosine",
        Tukey(0.5, 10): "waveform.tukey",
        Sech(0.5, 10, 4): "waveform.sech",
        Modulated(Gaussian(0.5, 10, 5), 1e8): "waveform.modulated",
        IQRotation(IQPair(Square(1.0, 10), Square(0.0, 10)), phase=0.0): "waveform.iq_rotation",
        IQZero(Square(0.5, 10)): "waveform.iq_zero",
    }
    for wf, token in expected.items():
        assert waveform_token(wf) == token
