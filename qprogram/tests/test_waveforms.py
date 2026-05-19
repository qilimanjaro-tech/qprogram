"""Tests for the built-in waveform shapes."""

from __future__ import annotations

import numpy as np
import pytest

from qprogram import UnassignedVariableError, Variable
from qprogram.waveforms import (
    Arbitrary,
    Chained,
    FlatTop,
    Gaussian,
    GaussianDragCorrection,
    IQDrag,
    IQPair,
    IQWaveform,
    Ramp,
    Square,
    SuddenNetZero,
    Waveform,
)

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
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=2.5)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_peak_at_center():
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=2.5)
    env = wf.envelope()
    peak_idx = int(np.argmax(env))
    # Center of [0..39] is 19.5; peak is at 19 or 20.
    assert peak_idx in {19, 20}


def test_gaussian_get_duration():
    wf = Gaussian(amplitude=1.0, duration=40, num_sigmas=2.5)
    assert wf.get_duration() == 40


def test_gaussian_with_expression_params():
    amp, dur, ns = Variable("a"), Variable("d"), Variable("n")
    amp.set_value(0.5)
    dur.set_value(40)
    ns.set_value(2.5)
    wf = Gaussian(amplitude=amp, duration=dur, num_sigmas=ns)
    env = wf.envelope()
    assert env.shape == (40,)
    # Peak is close to amplitude (not exact due to centre-between-samples).
    assert np.isclose(env.max(), 0.5, atol=0.01)


# ---------------------------------------------------------------------------
# GaussianDragCorrection
# ---------------------------------------------------------------------------


def test_gaussian_drag_correction_envelope_shape():
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=2.5, drag_coefficient=0.1)
    env = wf.envelope()
    assert env.shape == (40,)


def test_gaussian_drag_correction_zero_at_center():
    # Derivative of a centered Gaussian is zero at its peak.
    wf = GaussianDragCorrection(amplitude=1.0, duration=40, num_sigmas=2.5, drag_coefficient=1.0)
    env = wf.envelope()
    assert abs(env[20]) < 0.05  # near zero


def test_gaussian_drag_correction_with_all_expressions():
    a, d, n, dc = Variable("a"), Variable("d"), Variable("n"), Variable("dc")
    a.set_value(1.0)
    d.set_value(40)
    n.set_value(2.5)
    dc.set_value(0.1)
    wf = GaussianDragCorrection(amplitude=a, duration=d, num_sigmas=n, drag_coefficient=dc)
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
        IQPair(i, q)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# IQDrag
# ---------------------------------------------------------------------------


def test_iq_drag_components_are_gaussian_and_correction():
    wf = IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1)
    assert isinstance(wf.get_I(), Gaussian)
    assert isinstance(wf.get_Q(), GaussianDragCorrection)


def test_iq_drag_get_duration():
    wf = IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1)
    assert wf.get_duration() == 40


def test_iq_drag_duration_via_expression():
    d = Variable("d")
    d.set_value(40)
    wf = IQDrag(amplitude=0.5, duration=d, num_sigmas=2.5, drag_coefficient=0.1)
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
        Waveform()  # type: ignore[abstract]


def test_iq_waveform_is_abstract():
    with pytest.raises(TypeError):
        IQWaveform()  # type: ignore[abstract]
