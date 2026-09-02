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
"""Result types for executed QPrograms.

[`MeasurementHandle`][qprogram.MeasurementHandle] is returned by [`QProgram.measure`][qprogram.QProgram.measure] and
identifies a measurement across construction, ``.qp`` serialization, execution, and retrieval.
[`MeasurementResult`][qprogram.MeasurementResult] is the runtime's per-measurement record.
[`QProgramResult`][qprogram.QProgramResult] is the in-memory container of all measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from qprogram.errors import ValidationError
from qprogram.operations.operation import MeasurementField
from qprogram.plotting import Style, build_figure, resolve_renderer

if TYPE_CHECKING:
    import xarray as xr

    from qprogram.variable import _HandleFieldAccess, _UnassignedType

# The label for a field whose meaning the executor defines. ``iq`` and ``raw`` have none: what a
# demodulated point means is the readout chain's business, so their label comes from the channel.
_FIELD_LABELS = {MeasurementField.STATE.value: "State"}


class MeasurementHandle:
    """A reference to a measurement performed by a [`QProgram`][qprogram.QProgram].

    Equality is **structural**: two handles with the same name refer to the same measurement. Why this
    matters: after a ``.qp`` round-trip the original Python objects are gone but names survive in the
    AST, so a freshly-constructed ``MeasurementHandle("q0_m0")`` compares equal to the original.

    Runtime-supplied values (e.g. the classified qubit state when the measurement's ``fields``
    includes `STATE`) live in a private ``_values`` dict keyed by
    field name. They participate in [`MeasurementRef`][qprogram.MeasurementRef]
    evaluation but do not contribute to handle identity.

    Args:
        name (str): Stable identifier for the measurement. Auto-assigned by [`QProgram.measure`][qprogram.QProgram.measure]
            (``q0/readout/m0``, ``m0``, ...) or user-supplied. Emitted verbatim into ``.qp``.

    Raises:
        ValidationError: If ``name`` is not a non-empty string.

    Note:
        ``_auto_named`` records whether [`QProgram.measure`][qprogram.QProgram.measure] allocated the name (``True``) or the
        user supplied it (``False``). It distinguishes a bus-derived auto-name (``q0/readout/m0``) that
        [`QProgram.rebind`][qprogram.QProgram.rebind] must re-derive when the bus changes from a deliberate user label that
        must be preserved — the two are byte-identical once serialized, so the flag is the only sound
        signal. It is in-memory state (like the platform parameter store): it is not serialized, so it
        defaults to ``False`` on a handle reconstructed from ``.qp`` (rebind before dumping).
    """

    __slots__ = ("_auto_named", "_values", "name")

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            msg = f"MeasurementHandle name must be a non-empty string, got {name!r}"
            raise ValidationError(msg)
        self.name = name
        self._values: dict[str, int | float] = {}
        self._auto_named: bool = False

    @property
    def state(self) -> _HandleFieldAccess:
        """A proxy for referencing this measurement's classified state in a conditional.

        The returned proxy is a throwaway whose ``==`` and ``!=`` operators build
        [`Comparison`][qprogram.Comparison] AST nodes::

            with program.if_(handle.state == 0):
                ...

        The producing measurement op must request state classification (its ``fields`` must include
        `STATE`); the validator emits ``missing-classification``
        otherwise.
        """
        from qprogram.variable import _HandleFieldAccess  # ruff: ignore[import-outside-top-level]

        return _HandleFieldAccess(self, "state")  # pyright: ignore[reportArgumentType]

    def _value_for(self, field: str) -> int | float | _UnassignedType:
        """Return the runtime value recorded for ``field``, or ``UNASSIGNED``.

        Read by [`MeasurementRef`][qprogram.MeasurementRef] evaluation, so an expression over a measurement
        that has not produced this field yet propagates ``UNASSIGNED`` rather than guessing.

        Args:
            field (str): Measurement field name, e.g. ``"state"``.

        Returns:
            The recorded value, or ``UNASSIGNED`` when the runtime has not written this field.
        """
        from qprogram.variable import UNASSIGNED  # ruff: ignore[import-outside-top-level]

        return self._values.get(field, UNASSIGNED)

    def _set_value(self, field: str, value: float) -> None:
        """Record ``value`` as this measurement's runtime outcome for ``field``.

        Called by the executor once per measurement event. Every reference to the measurement shares
        this handle instance, which is what makes conditional feedback work without any wiring.

        Args:
            field (str): Measurement field name, e.g. ``"state"``.
            value (float): The outcome to record. Overwrites any earlier shot's value.
        """
        self._values[field] = value

    def __repr__(self) -> str:
        return f"MeasurementHandle({self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MeasurementHandle) and self.name == other.name

    def __hash__(self) -> int:
        return hash(("MeasurementHandle", self.name))


@dataclass
class MeasurementResult:
    """One measurement record produced by the runtime.

    Attributes:
        bus (str): The bus the measurement was taken on.
        name (str): The measurement handle's name, as assigned at program construction.
        data (xarray.DataArray): The **primary** result array — the ``"iq"`` field when the
            measurement requested it, else the first requested field in canonical order (see
            `MeasurementField`). Use it when you want *whatever* the measurement
            produced; [`QProgramResult.get`][qprogram.QProgramResult.get] names a field explicitly instead.
        fields (dict[str, xarray.DataArray]): One array per requested measurement field, keyed by
            field name. Shapes per spec §8: ``iq`` → ``(*sweeps, "IQ")``; ``state`` → ``(*sweeps)``
            (excited-state population under averaging); ``raw`` → ``(*sweeps, "time", "IQ")``.
            This is what [`QProgramResult.get`][qprogram.QProgramResult.get] reads.
    """

    bus: str
    name: str
    data: xr.DataArray
    fields: dict[str, xr.DataArray] = field(default_factory=dict)


class QProgramResult:
    """In-memory result of executing a [`QProgram`][qprogram.QProgram].

    Each measurement contributes one [`MeasurementResult`][qprogram.MeasurementResult] holding an `xarray.DataArray`
    per requested field. Dimensions are named after the enclosing loops, outermost first; the ``iq``
    field carries a trailing ``"IQ"`` dimension with coordinates ``["I", "Q"]``.

    Results are stored in construction order and addressable by handle, by name string, or by integer
    position via `get`, which returns the `IQ` field unless a
    different one is named. `plot` takes the same arguments and draws what it finds.
    """

    def __init__(self) -> None:
        self._measurements: list[MeasurementResult] = []

    def append_measurement(
        self,
        bus: str,
        name: str,
        data: xr.DataArray,
        fields: dict[str, xr.DataArray] | None = None,
    ) -> None:
        """Append a measurement record.

        Args:
            bus (str): The bus the measurement was taken on.
            name (str): The measurement handle name as it appears in the AST.
            data (xarray.DataArray): The primary result data (the ``"iq"`` field when requested).
            fields (dict[str, xarray.DataArray] | None): Per-measurement-field arrays, keyed by field
                name. Omitting it records ``data`` as the `IQ`
                field — the field a measurement requests when ``fields=`` is omitted, and the one
                `get` returns by default. A record whose primary array is *not* ``iq`` must
                pass the mapping explicitly, so that `get` never hands back an array under the
                wrong field name.
        """
        if not fields:
            fields = {MeasurementField.IQ.value: data}
        self._measurements.append(MeasurementResult(bus=bus, name=name, data=data, fields=dict(fields)))

    @property
    def measurements(self) -> list[MeasurementResult]:
        """All measurement records in construction order."""
        return self._measurements

    def get(
        self,
        measurement: MeasurementHandle | str | int = 0,
        bus: str | None = None,
        field: MeasurementField | str = MeasurementField.IQ,
    ) -> xr.DataArray:
        """Retrieve one measurement field's data.

        Args:
            measurement (MeasurementHandle | str | int): Which measurement to retrieve.

                - [`MeasurementHandle`][qprogram.MeasurementHandle]: looked up by name.
                - ``str``: looked up by name. Useful after a ``loads()`` round-trip when the original
                  handle objects are gone but handles can be reconstructed via
                  [`QProgram.measurement_handles`][qprogram.QProgram.measurement_handles].
                - ``int``: positional sugar; returns the N-th measurement in declaration order, or N-th
                  on ``bus`` when the filter is given. A handle or a name says what it means and
                  survives reordering, so prefer either to a position.

            bus (str | None): Bus name filter — narrows the search before the handle / name /
                position lookup.
            field (MeasurementField | str): Which measurement field to return — a
                `MeasurementField` member or a registered vendor field name (the
                members *are* strings, so both spell the same thing). Defaults to
                `IQ`, matching the default of
                ``measure(..., fields=)``; a measurement that did not request the field raises
                ``KeyError`` rather than silently substituting another one. Reach for
                `MeasurementResult.data` when you want the record's primary array whatever the
                requested fields were.

        Returns:
            The field's `xarray.DataArray`.

        Raises:
            KeyError: When ``measurement`` is a handle or name with no match in scope, or ``field``
                names a measurement field the measurement did not request.
            IndexError: When ``measurement`` is an integer position outside the range in scope.
            ValidationError: When ``field`` is ``None``. There is no spelling of "give me the primary
                array" here — read `MeasurementResult.data` for that.
        """
        if field is None:
            msg = (
                "field=None is not a valid field: get() returns the "
                f"{MeasurementField.IQ.value!r} field by default. Name the field explicitly "
                "(field=MeasurementField.STATE), or read MeasurementResult.data for the record's "
                "primary array."
            )
            raise ValidationError(msg)

        candidates = self._measurements if bus is None else [m for m in self._measurements if m.bus == bus]

        if isinstance(measurement, MeasurementHandle):
            record = self._lookup_by_name(candidates, measurement.name, bus)
        elif isinstance(measurement, str):
            record = self._lookup_by_name(candidates, measurement, bus)
        else:
            # int — positional sugar.
            if measurement >= len(candidates):
                scope = f" for bus '{bus}'" if bus is not None else ""
                msg = f"Measurement index {measurement} out of range{scope} ({len(candidates)} measurements)"
                raise IndexError(msg)
            record = candidates[measurement]
        # ``str(...)`` normalizes a MeasurementField member to its wire name, so the lookup and the
        # error message read the same whichever spelling the caller used.
        name = str(field)
        if name not in record.fields:
            available = ", ".join(sorted(record.fields)) or "none"
            msg = f"Measurement {record.name!r} has no field {name!r}; available: {available}"
            raise KeyError(msg)
        return record.fields[name]

    def plot(  # ruff: ignore[too-many-arguments]  # every argument is one decision about the figure
        self,
        measurement: MeasurementHandle | str | int = 0,
        bus: str | None = None,
        field: MeasurementField | str = MeasurementField.IQ,
        *,
        kind: str | None = None,
        x: str | None = None,
        y: str | None = None,
        channels: str | None = None,
        value_label: str | None = None,
        title: str | None = None,
        style: Style | None = None,
        renderer: str | None = None,
        target: object = None,
    ) -> Any:  # ruff: ignore[any-type]  # whatever handle the renderer gives back
        """Draw one measurement field.

        The array is looked up exactly as `get` looks it up, described as a
        [`Figure`][qprogram.plotting.Figure] by [`build_figure`][qprogram.plotting.build_figure], and handed to a
        renderer. The default renderer is matplotlib, from the ``viz`` extra, and it returns the
        `Axes` it drew on, so anything the figure does not decide — a limit, an
        annotation, a second series from elsewhere — is a call away on the object that comes back.

        The shape of the array chooses the figure. One dimension besides ``"IQ"`` gives a line per
        quadrature, two give a heatmap of the magnitude, and ``kind="scatter"`` plots I against Q,
        which no shape implies on its own. A swept variable's ``label`` and ``units`` reach the axis
        from the coordinate the executor wrote them onto.

        Args:
            measurement (MeasurementHandle | str | int): Which measurement to draw, by handle, by
                name, or by position, exactly as in `get`.
            bus (str | None): Bus name filter, applied before that lookup.
            field (MeasurementField | str): Which measurement field to draw. Defaults to
                `IQ`.
            kind (str | None): ``"line"``, ``"heatmap"`` or ``"scatter"``. Inferred from the shape
                when omitted.
            x (str | None): Dimension or coordinate for the x axis. Required when the dimension
                composes several swept variables in parallel, because it holds one coordinate per
                variable and the sweep index is a plausible-looking wrong answer.
            y (str | None): The same for the y axis of a heatmap.
            channels (str | None): What to make of the ``"IQ"`` dimension — ``"iq"``, ``"i"``,
                ``"q"``, ``"magnitude"`` or ``"phase"``. Defaults to both quadratures for a line and
                to the magnitude for a heatmap, which colours one surface.
            value_label (str | None): Label for the measured quantity — the y axis of a line figure,
                the colour bar of a heatmap. Defaults to what the field and the channel imply, since
                what a demodulated point means is the readout chain's business and not the
                executor's.
            title (str | None): Title for the figure. None by default.
            style (Style | None): Palette and drawing weights. Defaults to
                [`Style`][qprogram.plotting.Style]``()``, which is the light theme.
            renderer (str | None): A name passed to
                [`resolve_renderer`][qprogram.plotting.resolve_renderer]. Defaults to ``"matplotlib"``.
            target (object): An existing surface for the renderer to draw on — a matplotlib
                `Axes` for the default one. A new figure is made when omitted.

        Returns:
            Whatever the renderer returns: the `Axes` for the matplotlib one.

        Raises:
            KeyError: When the measurement or the field has no match, or ``renderer`` names none.
            IndexError: When ``measurement`` is a position outside the range in scope.
            ValidationError: When an argument does not suit the array's shape — see
                [`build_figure`][qprogram.plotting.build_figure].
            ModuleNotFoundError: When the matplotlib renderer is used without matplotlib
                installed — install ``qprogram[viz]``.
        """
        data = self.get(measurement, bus=bus, field=field)
        figure = build_figure(
            data,
            kind=kind,
            x=x,
            y=y,
            channels=channels,
            value_label=value_label or _FIELD_LABELS.get(str(field)),
            title=title,
        )
        return resolve_renderer(renderer)(figure, style or Style(), target)

    @staticmethod
    def _lookup_by_name(
        candidates: list[MeasurementResult],
        name: str,
        bus: str | None,
    ) -> MeasurementResult:
        """Return the first record in ``candidates`` named ``name``.

        Args:
            candidates (list[MeasurementResult]): Records already narrowed by the ``bus`` filter.
            name (str): Measurement name to match exactly.
            bus (str | None): Bus the candidates were filtered to, used only to phrase the error.

        Returns:
            The matching record.

        Raises:
            KeyError: When no candidate carries that name.
        """
        for m in candidates:
            if m.name == name:
                return m
        scope = f" on bus '{bus}'" if bus is not None else ""
        msg = f"No measurement named {name!r}{scope}"
        raise KeyError(msg)

    def __len__(self) -> int:
        return len(self._measurements)

    def __repr__(self) -> str:
        names = [m.name for m in self._measurements]
        return f"QProgramResult({len(self._measurements)} measurements, names={names})"
