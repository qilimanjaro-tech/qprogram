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
"""Tests for :class:`~qprogram.waveform_library.WaveformLibrary`.

Covers per-bus resolution and the portable ``.wfl`` text format.
"""

from __future__ import annotations

import pytest
from test_round_trip import UNICODE_LINE_BREAKS

from qprogram import BusSchema, ParseError, QProgram, ValidationError, WaveformLibrary
from qprogram.errors import SerializationError
from qprogram.waveforms import Gaussian, IQDrag, IQPair, Square


def _full_library() -> WaveformLibrary:
    library = WaveformLibrary()
    library.set("pi_pulse", IQDrag(0.5, 40, 8, 0.1), element="q", idx=0, kind="drive")  # Exact tier.
    library.set("pi_pulse", IQDrag(0.9, 40, 8, 0.1), element="q", idx=1, kind="drive")  # Exact tier.
    library.set("cz", Square(0.3, 200), element="c", idx=(0, 1), kind="flux")  # Exact tier, tuple index.
    library.set("readout", IQPair(Square(1.0, 2000), Square(0.0, 2000)), element="q", kind="readout")  # Family tier.
    library.set("weights", IQPair(Square(1.0, 2000), Square(1.0, 2000)))  # Global tier.
    return library


# ---------------------------------------------------------------------------
# Resolution tiers
# ---------------------------------------------------------------------------


def test_get_tries_exact_then_family_then_global():
    schema = BusSchema.transmon_coupled()
    q, c = schema.q, schema.c
    library = _full_library()
    assert library.get(q[0].drive, "pi_pulse").amplitude == 0.5  # Exact tier, q0.
    assert library.get(q[1].drive, "pi_pulse").amplitude == 0.9  # Exact tier, q1.
    assert library.get(c[0, 1].flux, "cz").amplitude == 0.3  # Exact tier, tuple index.
    assert library.get(q[7].readout, "readout") is not None  # Family tier: any index.
    assert library.get("raw_bus", "weights") is not None  # Global tier: even a raw-string bus.
    assert library.get(q[0].drive, "missing") is None


def test_raw_string_bus_reaches_only_global_tier():
    library = WaveformLibrary()
    library.set("pi_pulse", IQDrag(0.5, 40, 8, 0.1), element="q", idx=0, kind="drive")
    assert library.get("q0/drive", "pi_pulse") is None  # A raw string can't match the exact tier.


def test_set_requires_full_or_no_coordinate():
    library = WaveformLibrary()
    wf = Gaussian(0.5, 40, 8)
    with pytest.raises(ValidationError, match="exact entry"):
        library.set("pi", wf, element="q", idx=0)  # Missing kind.


def test_bool_reflects_emptiness():
    assert not WaveformLibrary()
    assert _full_library()


# ---------------------------------------------------------------------------
# .wfl serialization round-trip
# ---------------------------------------------------------------------------


def test_dumps_is_byte_stable_round_trip():
    library = _full_library()
    text = library.dumps()
    assert text.startswith("#!WaveformLibrary ")
    assert WaveformLibrary.loads(text).dumps() == text


def test_round_trip_preserves_every_tier():
    schema = BusSchema.transmon_coupled()
    q, c = schema.q, schema.c
    reloaded = WaveformLibrary.loads(_full_library().dumps())
    assert reloaded.get(q[0].drive, "pi_pulse").amplitude == 0.5
    assert reloaded.get(q[1].drive, "pi_pulse").amplitude == 0.9
    assert reloaded.get(c[0, 1].flux, "cz").amplitude == 0.3
    assert reloaded.get(q[5].readout, "readout") is not None
    assert reloaded.get("raw", "weights") is not None


def test_empty_library_round_trips():
    text = WaveformLibrary().dumps()
    assert WaveformLibrary.loads(text).dumps() == text


def test_save_and_load_file(tmp_path):
    library = _full_library()
    path = tmp_path / "cal.wfl"
    library.save(str(path))
    assert WaveformLibrary.load(str(path)).dumps() == library.dumps()


def test_blank_lines_and_comments_are_ignored():
    text = (
        "#!WaveformLibrary 1.0\n"
        "\n"
        "# a comment\n"
        '"pi" q[0].drive = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)\n'
        "\n"
    )
    library = WaveformLibrary.loads(text)
    schema = BusSchema.transmon()
    assert library.get(schema.q[0].drive, "pi").amplitude == 0.5


def test_name_with_spaces_round_trips():
    library = WaveformLibrary()
    library.set("pi over two", Gaussian(0.25, 40, 8))
    assert WaveformLibrary.loads(library.dumps()).get("bus", "pi over two") is not None


# ---------------------------------------------------------------------------
# .wfl error handling
# ---------------------------------------------------------------------------


def test_missing_header_raises():
    with pytest.raises(ParseError, match="Missing #!WaveformLibrary header"):
        WaveformLibrary.loads('"x" = Gaussian(0.5, 40, 8)\n')


def test_incompatible_major_version_raises():
    with pytest.raises(ParseError, match="Unsupported WaveformLibrary format version"):
        WaveformLibrary.loads("#!WaveformLibrary 9.0\n")


def test_unknown_waveform_raises():
    with pytest.raises(ParseError, match="Unknown waveform or sweep source type"):
        WaveformLibrary.loads('#!WaveformLibrary 1.0\n"x" = Bogus(1, 2)\n')


def test_unquoted_name_raises():
    with pytest.raises(ParseError, match="quoted waveform name"):
        WaveformLibrary.loads("#!WaveformLibrary 1.0\nx = Gaussian(0.5, 40, 8)\n")


def test_missing_equals_raises():
    with pytest.raises(ParseError, match="must contain '='"):
        WaveformLibrary.loads('#!WaveformLibrary 1.0\n"x" Gaussian(0.5, 40, 8)\n')


def test_bad_coordinate_raises():
    with pytest.raises(ParseError, match="invalid entry coordinate"):
        WaveformLibrary.loads('#!WaveformLibrary 1.0\n"x" not_a_coord = Gaussian(0.5, 40, 8)\n')


def test_non_concrete_waveform_rejected_on_dump():
    amp = QProgram().variable("amp")
    library = WaveformLibrary()
    library.set("x", Gaussian(amp, 40, 8))  # Symbolic amplitude.
    with pytest.raises(SerializationError, match="concrete waveforms"):
        library.dumps()


@pytest.mark.parametrize("char", UNICODE_LINE_BREAKS)
def test_wfl_round_trip_name_holding_a_unicode_line_break(char):
    """``.wfl`` splits its lines by the same rule, so a name may hold one of these too."""
    library = WaveformLibrary()
    library.set(f"pi{char}2", Gaussian(0.5, 40, 8))
    reloaded = WaveformLibrary.loads(library.dumps())
    assert reloaded.dumps() == library.dumps()
    assert reloaded.get("drive", f"pi{char}2") == Gaussian(0.5, 40, 8)
