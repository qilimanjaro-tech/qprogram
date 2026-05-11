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
from qprogram.operations.operation import Operation
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
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    import numpy as np

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

    def __init__(self, label: str = "", description: str | None = None) -> None:
        self.label = label
        self.description = description
        self._body = Block()
        self._block_stack: deque[Block] = deque([self._body])
        self._variables: list[Variable] = []

    # --- Properties ---

    @property
    def body(self) -> Block:
        return self._body

    @property
    def buses(self) -> set[str]:
        """Set of bus names referenced anywhere in the program tree.

        Computed by walking the AST on each access — there is no separate
        tracking field, so this stays consistent across deserialization,
        ``with_bus_mapping``, and any other path that appends operations
        directly to a block.
        """
        return _collect_buses(self._body)

    @property
    def variables(self) -> list[Variable]:
        return list(self._variables)

    @property
    def _active_block(self) -> Block:
        return self._block_stack[-1]

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

    # --- Core operations ---

    def play(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        _validate_waveform_channel(bus, waveform)
        self._active_block.append(Play(bus=bus, waveform=waveform))

    def measure(self, bus: str, waveform: IQWaveform | str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        _validate_acquires(bus)
        _validate_waveform_channel(bus, waveform)
        _validate_waveform_channel(bus, weights)
        self._active_block.append(Measure(bus=bus, waveform=waveform, weights=weights, save_adc=save_adc))

    def wait(self, bus: str, duration: int | Expression) -> None:
        self._active_block.append(Wait(bus=bus, duration=duration))

    def sync(self, buses: list[str] | None = None) -> None:
        self._active_block.append(Sync(buses=buses))

    def set_frequency(self, bus: str, frequency: float | Expression) -> None:
        self._active_block.append(SetFrequency(bus=bus, frequency=frequency))

    def set_phase(self, bus: str, phase: float | Expression) -> None:
        self._active_block.append(SetPhase(bus=bus, phase=phase))

    def reset_phase(self, bus: str) -> None:
        self._active_block.append(ResetPhase(bus=bus))

    def set_gain(self, bus: str, gain: float | Expression) -> None:
        self._active_block.append(SetGain(bus=bus, gain=gain))

    def set_offset(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
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
    """Recursively replace string waveform aliases with concrete waveforms."""
    for element in block.elements:
        if isinstance(element, Block):
            _remap_waveforms(element, mapping)
        elif isinstance(element, Operation):
            if hasattr(element, "waveform") and isinstance(element.waveform, str) and element.waveform in mapping:
                element.waveform = mapping[element.waveform]
            if hasattr(element, "weights") and isinstance(element.weights, str) and element.weights in mapping:
                element.weights = mapping[element.weights]


def _remap_buses(block: Block, mapping: dict[str, str]) -> None:
    """Recursively remap bus names in a block tree."""
    for element in block.elements:
        if isinstance(element, Block):
            _remap_buses(element, mapping)
        elif isinstance(element, Operation):
            if hasattr(element, "bus"):
                element.bus = mapping.get(element.bus, element.bus)
            if hasattr(element, "buses") and element.buses is not None:
                element.buses = [mapping.get(b, b) for b in element.buses]


def _validate_waveform_channel(bus: str, waveform: Waveform | IQWaveform | str) -> None:
    """Validate waveform type against bus channel type if the bus is a BusRef.

    - IQ bus + Waveform (single-channel) -> TypeError
    - Single bus + IQWaveform -> TypeError
    - Raw string bus or string alias waveform -> no validation (no metadata available)
    """
    if not isinstance(bus, BusRef) or isinstance(waveform, str):
        return

    channel = bus.channel_type
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


def _collect_buses(block: Block) -> set[str]:
    """Walk a block tree and return the set of bus names it references.

    Looks for the ``bus`` and ``control_bus`` string attributes (covers all
    core operations plus most vendor ops) and the ``buses`` list attribute
    (``Sync``). Vendor operations that use a different attribute name for a
    bus will not be picked up here — vendor authors should follow the
    ``bus`` / ``control_bus`` convention.
    """
    result: set[str] = set()
    for el in block.elements:
        if isinstance(el, Block):
            result |= _collect_buses(el)
            continue
        for attr in ("bus", "control_bus"):
            value = getattr(el, attr, None)
            if isinstance(value, str):
                result.add(value)
        buses_list = getattr(el, "buses", None)
        if isinstance(buses_list, list):
            result.update(b for b in buses_list if isinstance(b, str))
    return result


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
