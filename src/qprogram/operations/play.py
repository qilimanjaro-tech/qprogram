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
"""The ``play`` operation — waveform output on a bus."""

from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation
from qprogram.waveforms.waveform import IQWaveform, Waveform


class Play(Operation):
    """A waveform played on a bus.

    Args:
        bus (str): Bus to play on.
        waveform (Waveform | IQWaveform | str): Either a concrete
            [`Waveform`][qprogram.waveforms.Waveform] / [`IQWaveform`][qprogram.waveforms.IQWaveform], or a
            string alias to be resolved later by [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms].
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self.bus = bus
        self.waveform = waveform

    def required_capabilities(self) -> set[str]:
        """Return ``op.play`` plus the tokens describing the waveform.

        A string alias contributes ``waveform.alias``; a concrete waveform contributes its channel
        kind (``waveform.iq`` or ``waveform.single``) and, when its class is registered, the
        per-class token from [`qprogram.protocol.waveform_token`][].
        """
        from qprogram.protocol import waveform_token  # ruff: ignore[import-outside-top-level]

        caps = {"op.play"}
        if isinstance(self.waveform, str):
            caps.add("waveform.alias")
        else:
            caps.add("waveform.iq" if isinstance(self.waveform, IQWaveform) else "waveform.single")
            tok = waveform_token(self.waveform)
            if tok is not None:
                caps.add(tok)
        return caps
