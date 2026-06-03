"""Tests for the built-in waveform shapes."""

from __future__ import annotations

from typing import cast

import matplotlib as mpl
import numpy as np
import pytest
from matplotlib.axes import Axes

from qprogram import UnassignedVariableError, ValidationError, Variable
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

mpl.use("Agg")

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
    wf = Gaussian(amplitude=1.0, duration=40, sigma=8)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_peak_at_center():
    wf = Gaussian(amplitude=1.0, duration=40, sigma=8)
    env = wf.envelope()
    peak_idx = int(np.argmax(env))
    # Center of [0..39] is 19.5; peak is at 19 or 20.
    assert peak_idx in {19, 20}


def test_gaussian_get_duration():
    wf = Gaussian(amplitude=1.0, duration=40, sigma=8)
    assert wf.get_duration() == 40


def test_gaussian_with_expression_params():
    amp, dur, sig = Variable("a"), Variable("d"), Variable("sig")
    amp.set_value(0.5)
    dur.set_value(40)
    sig.set_value(8)
    wf = Gaussian(amplitude=amp, duration=dur, sigma=sig)
    env = wf.envelope()
    assert env.shape == (40,)
    # Peak is close to amplitude (not exact due to centre-between-samples).
    assert np.isclose(env.max(), 0.5, atol=0.01)


# ---------------------------------------------------------------------------
# GaussianDragCorrection
# ---------------------------------------------------------------------------


def test_gaussian_drag_correction_envelope_shape():
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, sigma=8, beta=0.1)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_drag_correction_zero_at_center():
    # Derivative of a centered Gaussian is zero at its peak.
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, sigma=8, beta=1.0)
    env = wf.envelope()
    assert abs(env[20]) < 0.05  # near zero


def test_gaussian_drag_correction_with_all_expressions():
    a, d, sig, beta = Variable("a"), Variable("d"), Variable("sig"), Variable("beta")
    a.set_value(1.0)
    d.set_value(40)
    sig.set_value(8)
    beta.set_value(0.1)
    wf = GaussianDragCorrection(amplitude=a, duration=d, sigma=sig, beta=beta)
    assert wf.envelope().shape == (40,)


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


def test_flat_top_get_duration():
    assert FlatTop(0.5, 100, 10).get_duration() == 100


def test_flat_top_with_expression_params():
    a, d, s = Variable("a"), Variable("d"), Variable("s")
    a.set_value(0.5)
    d.set_value(100)
    s.set_value(10)
    wf = FlatTop(amplitude=a, duration=d, smooth_duration=s)
    assert wf.envelope().shape == (100,)


def test_flat_top_buffer_default():
    wf = FlatTop(amplitude=0.5, duration=100, smooth_duration=10)
    assert wf.buffer == 0


def test_flat_top_buffer_pads_envelope_on_both_sides():
    wf = FlatTop(amplitude=1.0, duration=100, smooth_duration=10, buffer=25)
    env = wf.envelope()
    assert env.shape == (150,)  # 25 + 100 + 25
    assert np.allclose(env[:25], 0.0)
    assert np.allclose(env[-25:], 0.0)
    # The pulse content sits between the pads, identical to the unbuffered envelope.
    unbuffered = FlatTop(amplitude=1.0, duration=100, smooth_duration=10).envelope()
    assert np.allclose(env[25:125], unbuffered)


def test_flat_top_buffer_extends_duration():
    assert FlatTop(0.5, 100, 10, buffer=25).get_duration() == 150


def test_flat_top_buffer_respects_resolution():
    env = FlatTop(amplitude=1.0, duration=100, smooth_duration=10, buffer=25).envelope(resolution=5)
    assert env.shape == (30,)  # 5 + 20 + 5


# ---------------------------------------------------------------------------
# SuddenNetZero
# ---------------------------------------------------------------------------


def test_snz_envelope_structure():
    wf = SuddenNetZero(amplitude=1.0, duration=20, b=0.5, t_phi=4)
    env = wf.envelope()
    assert env.shape == (20,)
    # First half should be amplitude, second half should be -amplitude * b.
    assert np.allclose(env[:8], 1.0)
    assert np.allclose(env[12:], -0.5)
    # Middle section is the zero gap.
    assert np.allclose(env[8:12], 0.0)


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
    assert wf.envelope().shape == (20,)


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
    with pytest.raises(ValidationError, match="equal durations"):
        IQPair(Square(1.0, 100), Square(0.0, 999))


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
    wf = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
    assert isinstance(wf.get_I(), Gaussian)
    assert isinstance(wf.get_Q(), GaussianDragCorrection)


def test_iq_drag_get_duration():
    wf = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
    assert wf.get_duration() == 40


def test_iq_drag_duration_via_expression():
    d = Variable("d")
    d.set_value(40)
    wf = IQDrag(amplitude=0.5, duration=d, sigma=8, beta=0.1)
    assert wf.get_duration() == 40


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
    with pytest.raises(UnassignedVariableError):
        Sine(amplitude=Variable("a"), duration=10, frequency=1e8).envelope()


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
    # Odd duration so the centre lands on an exact sample and the peak == amplitude.
    wf = Sech(amplitude=0.5, duration=51, tau=tau)
    env = wf.envelope()
    assert env.max() == pytest.approx(0.5, rel=1e-6)


def test_sech_get_duration():
    assert Sech(0.5, 100, 12.5).get_duration() == 100


# ---------------------------------------------------------------------------
# Modulated
# ---------------------------------------------------------------------------


def test_modulated_produces_iq_channels():
    wf = Modulated(envelope=Gaussian(1.0, 100, 20), frequency=1e8)
    i = wf.get_I().envelope()
    q = wf.get_Q().envelope()
    assert i.shape == q.shape == (100,)


def test_modulated_unit_freq_zero_phase_starts_at_envelope():
    g = Gaussian(1.0, 100, 20)
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
    with pytest.raises(TypeError, match="must be a Waveform"):
        Modulated(envelope=cast("Waveform", "not a waveform"), frequency=1e8)


def test_modulated_get_duration_matches_envelope():
    g = Gaussian(0.5, 73, 14.6)
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
    with pytest.raises(TypeError, match="must be an IQWaveform"):
        IQRotation(base=cast("IQWaveform", Square(1.0, 10)), phase=0.0)


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
    g = Gaussian(0.3, 30, 6)
    wf = IQZero(envelope=g)
    assert np.allclose(wf.get_I().envelope(), g.envelope())


def test_iq_zero_rejects_non_waveform():
    with pytest.raises(TypeError, match="must be a Waveform"):
        IQZero(envelope=cast("Waveform", "bad"))


def test_iq_zero_duration():
    assert IQZero(Square(0.5, 99)).get_duration() == 99


# ---------------------------------------------------------------------------
# Waveform __add__ → Chained
# ---------------------------------------------------------------------------


def test_add_two_waveforms_produces_chained():
    chain = Square(0.5, 10) + Gaussian(0.3, 20, 4)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 2
    assert chain.get_duration() == 30


def test_add_flattens_existing_chain():
    chain = Square(0.5, 10) + Gaussian(0.3, 20, 4) + Square(0.7, 5)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_flattens_left_chain():
    left = Chained([Square(0.5, 10), Square(0.6, 5)])
    chain = left + Gaussian(0.3, 20, 4)
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_flattens_right_chain():
    right = Chained([Gaussian(0.3, 20, 4), Square(0.7, 5)])
    chain = Square(0.5, 10) + right
    assert isinstance(chain, Chained)
    assert len(chain.waveforms) == 3


def test_add_with_non_waveform_returns_notimplemented():
    # Triggered indirectly: Python falls back to TypeError when both sides return NotImplemented.
    with pytest.raises(TypeError):
        _ = Square(0.5, 10) + cast("Waveform", "not a waveform")


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
# Visualization helpers (smoke tests — visual correctness is human-checked)
# ---------------------------------------------------------------------------


def test_waveform_plot_returns_axes():
    ax = Square(0.5, 20).plot()
    assert isinstance(ax, Axes)


def test_iq_plot_returns_axes_pair():
    axes = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20)).plot()
    assert len(axes) == 2
    assert all(isinstance(a, Axes) for a in axes)


def test_waveform_repr_html_returns_svg():
    html = Square(0.5, 20)._repr_html_()
    assert "<svg" in html


def test_iq_repr_html_returns_svg():
    html = IQPair(I=Square(0.5, 20), Q=Square(0.3, 20))._repr_html_()
    assert "<svg" in html


# ---------------------------------------------------------------------------
# Serialization round-trip for new waveforms
# ---------------------------------------------------------------------------


def test_new_waveforms_registered_for_serialization():
    from qprogram.serialization.registry import get_waveform_class  # noqa: PLC0415

    for cls in (Sine, Cosine, Tukey, Sech, Modulated, IQRotation, IQZero):
        assert get_waveform_class(cls.__name__) is cls


def test_new_waveforms_have_capability_tokens():
    from qprogram.protocol import waveform_token  # noqa: PLC0415

    expected = {
        Sine(0.5, 10, 1e8): "waveform.sine",
        Cosine(0.5, 10, 1e8): "waveform.cosine",
        Tukey(0.5, 10): "waveform.tukey",
        Sech(0.5, 10, 4): "waveform.sech",
        Modulated(Gaussian(0.5, 10, 2), 1e8): "waveform.modulated",
        IQRotation(IQPair(Square(1.0, 10), Square(0.0, 10)), phase=0.0): "waveform.iq_rotation",
        IQZero(Square(0.5, 10)): "waveform.iq_zero",
    }
    for wf, token in expected.items():
        assert waveform_token(wf) == token
