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
"""The core ``measure`` operation — a readout pulse plus its acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import MeasurementField, MeasurementOperation, normalize_fields

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.result import MeasurementHandle
    from qprogram.waveforms.waveform import IQWaveform


class Measure(MeasurementOperation):
    """A readout pulse played on a bus, together with the acquisition of the response.

    Args:
        bus (str): Readout bus (must have ``acquires=True``).
        waveform (IQWaveform | str): Readout pulse — concrete
            :class:`~qprogram.waveforms.IQWaveform` or a string alias.
        weights (IQWaveform | str): Integration weights — concrete
            :class:`~qprogram.waveforms.IQWaveform` or a string alias.
        handle (MeasurementHandle): The canonical :class:`~qprogram.MeasurementHandle` for this
            measurement. The same Python instance is returned to user code, referenced by any
            :class:`~qprogram.MeasurementRef` in conditionals, and listed by
            :meth:`QProgram.measurement_handles` — the runtime writes per-measurement values onto
            this single object and every reader sees them.
        fields (Iterable[MeasurementField], optional): Which measurement fields to produce, as an
            iterable of :class:`~qprogram.MeasurementField` members. Default
            ``(MeasurementField.IQ,)``. :attr:`~qprogram.MeasurementField.RAW` requests the raw ADC
            trace; :attr:`~qprogram.MeasurementField.STATE` requests a classified outcome (required
            when the program references ``handle.state`` in a conditional). Stored canonically
            ordered and deduplicated — see :func:`~qprogram.operations.operation.normalize_fields`.

    Raises:
        ValidationError: If ``fields`` is a bare string, is not iterable, requests no field at all,
            or names a field that is not registered.
    """

    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform", "weights")

    def __init__(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        handle: MeasurementHandle,
        fields: Iterable[MeasurementField] = (MeasurementField.IQ,),
    ) -> None:
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.handle = handle
        self.fields: tuple[str, ...] = normalize_fields(fields)

    def required_capabilities(self) -> set[str]:
        """Return ``op.measure`` plus the waveform and requested-field tokens.

        ``waveform.iq`` is always required — a readout drives an IQ bus. The pulse and the
        integration weights each contribute ``waveform.alias`` when given as a string alias, and a
        concrete one contributes the per-class token from :func:`qprogram.protocol.waveform_token`
        when its class is registered. The ``measure.fields.<name>`` tokens come from
        :meth:`~qprogram.operations.operation.MeasurementOperation.required_capabilities`.
        """
        from qprogram.protocol import waveform_token  # ruff: ignore[import-outside-top-level]

        caps = super().required_capabilities() | {"op.measure", "waveform.iq"}
        for attr in (self.waveform, self.weights):
            if isinstance(attr, str):
                caps.add("waveform.alias")
            else:
                tok = waveform_token(attr)
                if tok is not None:
                    caps.add(tok)
        return caps
