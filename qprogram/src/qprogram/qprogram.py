# ruff: noqa: SLF001, ANN001
from __future__ import annotations

import copy
import re
from collections import deque
from typing import TYPE_CHECKING, ClassVar

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusRef
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.operation import MeasurementOperation, Operation
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
from qprogram.operations.set_crosstalk import SetCrosstalk
from qprogram.operations.set_frequency import SetFrequency
from qprogram.operations.set_gain import SetGain
from qprogram.operations.set_offset import SetOffset
from qprogram.operations.set_parameter import SetParameter
from qprogram.operations.set_phase import SetPhase
from qprogram.operations.sync import Sync
from qprogram.operations.wait import Wait
from qprogram.result import MeasurementHandle
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np

    from qprogram.buses import BusSchema
    from qprogram.crosstalk_matrix import CrosstalkMatrix
    from qprogram.vendor import VendorNamespace


class _LoopContext:
    """Context manager for loop blocks. Supports | for parallel composition."""

    def __init__(self, program: QProgram, block: ForLoop | Loop) -> None:
        self._program = program
        self._block = block
        self._parallel_blocks: list[ForLoop | Loop] = [block]

    def __or__(self, other: _LoopContext) -> _LoopContext:
        """Combine loops in parallel."""
        ctx = _LoopContext(self._program, self._block)
        ctx._parallel_blocks = self._parallel_blocks + other._parallel_blocks
        return ctx

    def __enter__(self) -> ForLoop | Loop | Parallel:
        block = self._parallel_blocks[0] if len(self._parallel_blocks) == 1 else Parallel(loops=self._parallel_blocks)
        self._program._active_block.append(block)
        self._program._block_stack.append(block)
        return block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._program._block_stack.pop()


class _AverageContext:
    """Context manager for average blocks."""

    def __init__(self, program: QProgram, shots: int) -> None:
        self._program = program
        self._block = Average(shots=shots)

    def __enter__(self) -> Average:
        self._program._active_block.append(self._block)
        self._program._block_stack.append(self._block)
        return self._block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._program._block_stack.pop()


class _BlockContext:
    """Context manager for generic blocks."""

    def __init__(self, program: QProgram) -> None:
        self._program = program
        self._block = Block()

    def __enter__(self) -> Block:
        self._program._active_block.append(self._block)
        self._program._block_stack.append(self._block)
        return self._block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._program._block_stack.pop()


class QProgram:
    """Top-level container for a pulse-level quantum program.

    Provides the unified API for operations, control flow, variables,
    and vendor extensions.
    """

    _vendor_registry: ClassVar[dict[str, type[VendorNamespace]]] = {}

    def __init__(
        self,
        label: str = "",
        description: str | None = None,
        schema: BusSchema | None = None,
    ) -> None:
        self.label = label
        self.description = description
        self._body = Block()
        self._block_stack: deque[Block] = deque([self._body])
        self._variables: list[Variable] = []
        self._schema = schema

    # --- Properties ---

    @property
    def body(self) -> Block:
        return self._body

    @property
    def schema(self) -> BusSchema | None:
        """The :class:`BusSchema` attached to this program, or ``None``.

        At most one schema per program: it defines the chip's elements and
        bus kinds, and the ``.qp`` writer reads it to emit bus references as
        compact paths (``q[0].drive``) rather than quoted strings. Plain-string
        buses continue to work regardless.
        """
        return self._schema

    @property
    def buses(self) -> set[str]:
        """Set of bus names referenced anywhere in the program tree.

        Delegates to :meth:`Block.buses`, which is recursive — so this
        stays consistent across deserialization, ``with_bus_mapping``, and
        any other path that appends operations directly to a block.
        """
        return self._body.buses()

    @property
    def variables(self) -> list[Variable]:
        return list(self._variables)

    @property
    def _active_block(self) -> Block:
        return self._block_stack[-1]

    # --- Measurement handles ---

    def measurement_handles(self) -> list[MeasurementHandle]:
        """Return a fresh :class:`MeasurementHandle` for every measurement in the AST.

        Recovers handles in declaration order — the order :meth:`measure`
        / vendor measurement ops were called. Useful after
        :func:`qprogram.loads`, where the original Python handle locals are
        gone but the names survive in the AST.

        Returns a *new* list of fresh handle objects on every call; handles
        are structurally compared by name, so equality with previously-held
        handles (or those reconstructed by name) still works.
        """
        return [MeasurementHandle(op.name) for op in _walk_measurement_ops(self._body)]

    def _allocate_measurement_name(self, bus: str, requested: str | None) -> str:
        """Choose the name for a new measurement and verify uniqueness.

        - If ``requested`` is provided, it is used verbatim after checking
          that no existing measurement already has that name. Raises
          :class:`ValueError` on collision.
        - Otherwise, an auto-name is generated using the convention:

            - **Schema-backed bus** (``BusRef`` with ``element`` and
              ``index``): ``{element}{flat_index}_m{counter}``. ``flat_index``
              flattens tuple indices with ``_`` (so ``c[0,1]`` →
              ``c0_1_m0``). ``counter`` is per-``(element, index)`` and
              always starts at ``0`` — so the second measurement on q0 is
              ``q0_m1`` and the first measurement on q4 is ``q4_m0``,
              regardless of source-order interleaving.

            - **Raw-string bus**: falls back to ``m{counter}`` with a
              global counter shared across all raw-string measurements.

        The counters are not stored on the program — they are derived from
        the AST on each call. This keeps :func:`copy.deepcopy`,
        ``with_waveforms``, and ``loads``/``dumps`` round-trips free of any
        hidden state, at the cost of one AST walk per measurement
        construction. Programs with thousands of measurements would feel
        this; a memoised counter is a small future optimisation.
        """
        used_names = {op.name for op in _walk_measurement_ops(self._body)}
        if requested is not None:
            if not isinstance(requested, str) or not requested:
                msg = f"measurement name must be a non-empty string, got {requested!r}"
                raise ValueError(msg)
            if requested in used_names:
                msg = (
                    f"measurement name {requested!r} is already used by another "
                    f"measurement in this program"
                )
                raise ValueError(msg)
            return requested

        prefix = _measurement_name_prefix(bus)
        # Per-prefix counter: walk used names, count how many already share
        # this prefix, return the first free index.
        n = 0
        while f"{prefix}{n}" in used_names:
            n += 1
        return f"{prefix}{n}"

    # --- Variables ---

    def variable(
        self,
        id: str,  # noqa: A002
        *,
        label: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> Variable:
        """Declare a new variable.

        The ``id`` must match ``[A-Za-z_][A-Za-z0-9_]*`` (it doubles as the
        identifier in ``.qp`` files) and must be unique within the QProgram.
        Optional ``label``, ``units``, and ``description`` carry
        human-readable metadata for plotting, results, and documentation.
        """
        if any(v.id == id for v in self._variables):
            msg = f"Variable {id!r} is already declared on this QProgram"
            raise ValueError(msg)
        var = Variable(id, label=label, units=units, description=description)
        self._variables.append(var)
        return var

    # --- Vendor extensions ---

    @classmethod
    def register_vendor(cls, name: str, namespace_cls: type[VendorNamespace]) -> None:
        """Register a vendor namespace class."""
        cls._vendor_registry[name] = namespace_cls

    def __getattr__(self, name: str) -> VendorNamespace:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._vendor_registry:
            ns = self._vendor_registry[name](self)
            object.__setattr__(self, name, ns)
            return ns
        msg = f"No vendor namespace '{name}' registered on QProgram"
        raise AttributeError(msg)

    # --- Bus validation ---

    def _validate_bus(self, bus: str) -> None:
        """Reject buses that don't belong to this program's schema.

        Plain strings and BusRefs without metadata pass through. Schema-bound
        BusRefs are checked against ``self._schema``: if the BusRef came from
        a different schema, or the program has no schema attached, raise
        ``ValueError`` with a pointer to the right call site.

        This catches the case where a user holds onto a ``schema2.q[0].drive``
        BusRef and uses it on a program built with ``schema=schema1``. Such a
        program would serialize fine but its semantics would silently differ
        from what the user wrote — better to reject loudly.
        """
        if not isinstance(bus, BusRef):
            return
        if not bus.element or not bus.kind:
            return  # opaque/manually-constructed BusRef with no schema metadata
        if bus.schema is None:
            return  # no producer recorded — defer to other validators
        if self._schema is None:
            self._schema = bus.schema
            return
        if bus.schema is self._schema:
            return
        msg = (
            f"BusRef {str(bus)!r} (element={bus.element!r}, kind={bus.kind!r}) comes "
            f"from a different BusSchema than the one attached to this QProgram. "
            f"A program may use only one schema; use a plain string bus name if you "
            f"need to reference a bus that lives outside the schema."
        )
        raise ValueError(msg)

    # --- Core operations ---

    def play(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self._validate_bus(bus)
        _validate_waveform_channel(bus, waveform)
        self._active_block.append(Play(bus=bus, waveform=waveform))

    def measure(
        self,
        bus: str,
        waveform: IQWaveform | str,
        weights: IQWaveform | str,
        *,
        name: str | None = None,
        returns: str | Iterable[str] = ("iq",),
    ) -> MeasurementHandle:
        """Play a readout pulse, acquire the result, and return a handle.

        The returned :class:`~qprogram.MeasurementHandle` is the stable
        identifier for this measurement; use it later with
        ``result.get(handle)`` to retrieve the data, or pass it into future
        classification / conditional operations.

        Args:
            bus: Readout bus (validated to support acquisition).
            waveform: Readout pulse, as a concrete :class:`IQWaveform` or
                a string alias resolved later via ``with_waveforms``.
            weights: Integration weights, same shape as ``waveform``.
            name: Optional explicit name. When omitted, an auto-name is
                allocated using the per-qubit convention described on
                :meth:`_allocate_measurement_name`.
            returns: What this measurement should return. Default
                ``("iq",)`` — in-phase + quadrature. Accepts a
                comma-separated string (``"iq,raw"``) or any iterable of
                strings (``["iq", "raw"]``). Platforms decide which tokens
                they recognise; ``"raw"`` is the canonical name for the raw
                ADC trace.

        Returns:
            The :class:`MeasurementHandle` for this measurement.
        """
        self._validate_bus(bus)
        _validate_acquires(bus)
        _validate_waveform_channel(bus, waveform)
        _validate_waveform_channel(bus, weights)
        allocated = self._allocate_measurement_name(bus, requested=name)
        self._active_block.append(
            Measure(bus=bus, waveform=waveform, weights=weights, name=allocated, returns=returns),
        )
        return MeasurementHandle(allocated)

    def wait(self, bus: str, duration: int | Expression) -> None:
        self._validate_bus(bus)
        self._active_block.append(Wait(bus=bus, duration=duration))

    def sync(self, buses: list[str] | None = None) -> None:
        # User-facing kw arg stays ``buses`` for readability; internally the
        # AST field is named ``targets`` (see :class:`Sync`).
        if buses:
            for b in buses:
                self._validate_bus(b)
        self._active_block.append(Sync(targets=buses))

    def set_frequency(self, bus: str, frequency: float | Expression) -> None:
        self._validate_bus(bus)
        self._active_block.append(SetFrequency(bus=bus, frequency=frequency))

    def set_phase(self, bus: str, phase: float | Expression) -> None:
        self._validate_bus(bus)
        self._active_block.append(SetPhase(bus=bus, phase=phase))

    def reset_phase(self, bus: str) -> None:
        self._validate_bus(bus)
        self._active_block.append(ResetPhase(bus=bus))

    def set_gain(self, bus: str, gain: float | Expression) -> None:
        self._validate_bus(bus)
        self._active_block.append(SetGain(bus=bus, gain=gain))

    def set_offset(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self._validate_bus(bus)
        self._active_block.append(SetOffset(bus=bus, offset_path0=offset_path0, offset_path1=offset_path1))

    def set_parameter(
        self,
        alias: str,
        parameter: str,
        value: float | Expression,
        channel_id: int | None = None,
    ) -> None:
        self._active_block.append(SetParameter(alias=alias, parameter=parameter, value=value, channel_id=channel_id))

    def get_parameter(self, alias: str, parameter: str, channel_id: int | None = None) -> Variable:
        # Auto-generate a unique, valid id. The original "alias.parameter"
        # form is preserved as label for traceability.
        base = _sanitize_id(f"{alias}_{parameter}")
        existing = {v.id for v in self._variables}
        var_id = base
        n = 2
        while var_id in existing:
            var_id = f"{base}_{n}"
            n += 1
        var = self.variable(var_id, label=f"{alias}.{parameter}")
        self._active_block.append(GetParameter(variable=var, alias=alias, parameter=parameter, channel_id=channel_id))
        return var

    def set_crosstalk(self, crosstalk: CrosstalkMatrix) -> None:
        self._active_block.append(SetCrosstalk(crosstalk=crosstalk))

    # --- Control flow ---

    def for_loop(
        self,
        variable: Variable,
        start: float,
        stop: float,
        step: float = 1,
    ) -> _LoopContext:
        block = ForLoop(variable=variable, start=start, stop=stop, step=step)
        return _LoopContext(self, block)

    def loop(self, variable: Variable, values: np.ndarray) -> _LoopContext:
        block = Loop(variable=variable, values=values)
        return _LoopContext(self, block)

    def average(self, shots: int) -> _AverageContext:
        return _AverageContext(self, shots)

    def block(self) -> _BlockContext:
        return _BlockContext(self)

    # --- Transformations ---

    def with_bus_mapping(self, bus_mapping: dict[str, str]) -> QProgram:
        """Return a copy with bus references remapped.

        The ``buses`` property is computed from the AST, so it automatically
        reflects the remapping — no separate bookkeeping needed.
        """
        new_program = copy.deepcopy(self)
        _remap_buses(new_program._body, bus_mapping)
        return new_program

    def with_waveforms(self, waveform_mapping: dict[str, Waveform | IQWaveform]) -> QProgram:
        """Return a copy with string waveform aliases replaced by concrete waveforms.

        Args:
            waveform_mapping: Maps alias names to concrete Waveform/IQWaveform instances.
                e.g. {"pi_pulse": IQDrag(0.5, 40, 2.5, 0.1), "readout": IQPair(...)}

        Returns:
            A new QProgram with all matching string references replaced.
        """
        new_program = copy.deepcopy(self)
        _remap_waveforms(new_program._body, waveform_mapping)
        return new_program


def _remap_waveforms(block: Block, mapping: dict[str, Waveform | IQWaveform]) -> None:
    """Replace string waveform aliases with concrete waveforms across a block tree.

    Walks via :meth:`Block.walk` and uses each op's ``WAVEFORM_ATTRS`` to
    locate waveform-bearing attributes — no hard-coded ``"waveform"`` /
    ``"weights"`` names, so vendor ops with non-standard attribute names
    Just Work as long as they declare their ``WAVEFORM_ATTRS``.
    """
    for op in block.walk():
        if not isinstance(op, Operation):
            continue
        for attr_name in op.WAVEFORM_ATTRS:
            value = getattr(op, attr_name, None)
            if isinstance(value, str) and value in mapping:
                setattr(op, attr_name, mapping[value])


def _remap_buses(block: Block, mapping: dict[str, str]) -> None:
    """Remap bus names across a block tree.

    Walks via :meth:`Block.walk` and uses each op's ``BUS_ATTRS``. Handles
    both scalar bus attributes (``op.bus``) and list-shaped ones
    (``Sync.targets``) uniformly via type dispatch on the attribute value.
    """
    for op in block.walk():
        if not isinstance(op, Operation):
            continue
        for attr_name in op.BUS_ATTRS:
            value = getattr(op, attr_name, None)
            if isinstance(value, str):
                setattr(op, attr_name, mapping.get(value, value))
            elif isinstance(value, list):
                setattr(op, attr_name, [mapping.get(b, b) for b in value])


def _validate_waveform_channel(bus: str, waveform: Waveform | IQWaveform | str) -> None:
    """Validate waveform type against bus channel type if the bus is a BusRef.

    - IQ bus + Waveform (single-channel) -> TypeError
    - Single bus + IQWaveform -> TypeError
    - Raw string bus or string alias waveform -> no validation (no metadata available)
    """
    if not isinstance(bus, BusRef) or isinstance(waveform, str):
        return

    channel = bus.channel
    if channel == "IQ" and isinstance(waveform, Waveform) and not isinstance(waveform, IQWaveform):
        msg = (
            f"Bus '{bus}' is an IQ channel but received a single-channel Waveform "
            f"({type(waveform).__name__}). Use an IQWaveform (e.g. IQPair, IQDrag) instead."
        )
        raise TypeError(
            msg,
        )
    if channel == "single" and isinstance(waveform, IQWaveform):
        msg = (
            f"Bus '{bus}' is a single channel but received an IQWaveform "
            f"({type(waveform).__name__}). Use a single-channel Waveform (e.g. Square, FlatTop) instead."
        )
        raise TypeError(
            msg,
        )


def _walk_measurement_ops(block: Block) -> list[MeasurementOperation]:
    """Return every :class:`MeasurementOperation` in ``block`` in declaration order.

    Uses :meth:`Block.walk` so the traversal stays consistent with the rest
    of the introspection API; the local filter just selects measurement
    ops out of the broader Operation/Block stream.
    """
    return [node for node in block.walk() if isinstance(node, MeasurementOperation)]


def _measurement_name_prefix(bus: str) -> str:
    """Compute the auto-name prefix for a measurement on ``bus``.

    Schema-backed buses (``BusRef`` with element + index metadata) get a
    per-qubit prefix — e.g. ``q0_m``, ``c0_1_m``. Raw-string buses fall
    back to a global ``m`` prefix. The caller appends a free integer to
    produce the final name. See :meth:`QProgram._allocate_measurement_name`.
    """
    if isinstance(bus, BusRef) and bus.element and bus.index is not None:
        idx_str = (
            "_".join(str(i) for i in bus.index) if isinstance(bus.index, tuple) else str(bus.index)
        )
        return f"{bus.element}{idx_str}_m"
    return "m"


def _sanitize_id(s: str) -> str:
    """Map an arbitrary string to a valid Variable id.

    Replaces every non-``[A-Za-z0-9_]`` character with ``_``. Prefixes a
    leading underscore if the first character is a digit. Falls back to
    ``"var"`` if the input is empty.
    """
    out = re.sub(r"[^A-Za-z0-9_]", "_", s) if s else ""
    if not out:
        return "var"
    if out[0].isdigit():
        out = "_" + out
    return out


def _validate_acquires(bus: str) -> None:
    """Validate that the bus supports acquisition (has ADC) if it's a BusRef."""
    if not isinstance(bus, BusRef):
        return
    if not bus.acquires:
        msg = (
            f"Bus '{bus}' does not support acquisition (acquires=False). "
            f"measure() can only be called on buses with an ADC (e.g. readout buses)."
        )
        raise TypeError(
            msg,
        )
