# ruff: noqa: SLF001, ANN001
from __future__ import annotations

import copy
import re
from collections import deque
from typing import TYPE_CHECKING, ClassVar

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.buses import BusRef
from qprogram.errors import ValidationError
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
from qprogram.variable import Comparison, Expression, Variable
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
        self._program._append_to_active(block)
        self._program._block_stack.append(block)
        return block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()


class _AverageContext:
    """Context manager for average blocks."""

    def __init__(self, program: QProgram, shots: int) -> None:
        self._program = program
        self._block = Average(shots=shots)

    def __enter__(self) -> Average:
        self._program._append_to_active(self._block)
        self._program._block_stack.append(self._block)
        return self._block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()


class _BlockContext:
    """Context manager for generic blocks."""

    def __init__(self, program: QProgram) -> None:
        self._program = program
        self._block = Block()

    def __enter__(self) -> Block:
        self._program._append_to_active(self._block)
        self._program._block_stack.append(self._block)
        return self._block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()


class _IfContext:
    """Context manager for the opening arm of an ``if_/elif_/else_`` chain.

    On ``__enter__`` creates a new :class:`Conditional`, appends it to
    the currently active block (at the *parent* level, before any arm
    body is pushed), records the parent block on
    :attr:`QProgram._pending_conditional` so a subsequent
    :class:`_ElifContext` / :class:`_ElseContext` can find it, and
    pushes the first arm body onto the block stack.
    """

    def __init__(self, program: QProgram, condition: Expression) -> None:
        self._program = program
        self._condition = condition
        self._conditional = Conditional()
        self._arm_body = Block()

    def __enter__(self) -> Conditional:
        parent = self._program._active_block
        self._conditional.arms.append((self._condition, self._arm_body))
        # _append_to_active clears any *previous* pending chain (broken by this
        # new if_) before appending the new Conditional.
        self._program._append_to_active(self._conditional)
        self._program._pending_conditional = (self._conditional, parent)
        self._program._block_stack.append(self._arm_body)
        return self._conditional

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()


class _ElifContext:
    """Context manager for an ``elif_`` arm extending the open chain."""

    def __init__(self, program: QProgram, condition: Expression) -> None:
        pending = program._pending_conditional
        if pending is None:
            msg = (
                "elif_() must immediately follow an if_() / elif_() block at "
                "the same nesting level; no open conditional chain"
            )
            raise ValidationError(msg)
        conditional, parent = pending
        if program._active_block is not parent:
            msg = (
                "elif_() must be at the same nesting level as the matching "
                "if_(); active block does not match the chain's parent"
            )
            raise ValidationError(msg)
        if conditional.else_body is not None:
            msg = "elif_() cannot follow else_() in the same chain"
            raise ValidationError(msg)
        self._program = program
        self._condition = condition
        self._conditional = conditional
        self._arm_body = Block()

    def __enter__(self) -> Conditional:
        self._conditional.arms.append((self._condition, self._arm_body))
        self._program._block_stack.append(self._arm_body)
        return self._conditional

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()
        # _pending_conditional stays set so a following elif_ / else_ can grab it.


class _ElseContext:
    """Context manager for the terminal ``else_`` arm."""

    def __init__(self, program: QProgram) -> None:
        pending = program._pending_conditional
        if pending is None:
            msg = (
                "else_() must immediately follow an if_() / elif_() block at "
                "the same nesting level; no open conditional chain"
            )
            raise ValidationError(msg)
        conditional, parent = pending
        if program._active_block is not parent:
            msg = (
                "else_() must be at the same nesting level as the matching "
                "if_(); active block does not match the chain's parent"
            )
            raise ValidationError(msg)
        if conditional.else_body is not None:
            msg = "else_() cannot follow another else_() in the same chain"
            raise ValidationError(msg)
        self._program = program
        self._conditional = conditional
        self._else_body = Block()

    def __enter__(self) -> Conditional:
        self._conditional.else_body = self._else_body
        self._program._block_stack.append(self._else_body)
        return self._conditional

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()
        # else_ terminates the chain — no more elif_/else_ may follow.
        self._program._pending_conditional = None


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
        # Tracks an open ``if_/elif_/else_`` chain so that a following
        # ``elif_`` / ``else_`` can find the right Conditional. The tuple
        # stores both the open Conditional and the parent block it lives
        # in; the chain is cleared automatically whenever something else
        # is appended at that parent level, by :meth:`_append_to_active`.
        self._pending_conditional: tuple[Conditional, Block] | None = None

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

    def _append_to_active(self, element: Block | Operation) -> None:
        """Append an op or block to the currently active block.

        Also maintains the ``_pending_conditional`` chain state: if an
        ``if_`` chain is open and the new append lands at the *parent*
        level of that chain (i.e., the same block the chain lives in),
        the chain is closed — the user has appended something other
        than an ``elif_`` / ``else_`` at that level, which makes any
        subsequent ``elif_`` / ``else_`` ambiguous. The chain stays
        open while appends happen inside the arm body (a deeper level
        of the block stack).

        ``_ElifContext`` / ``_ElseContext`` do not call this method —
        they mutate an existing ``Conditional`` in place and only push
        a new arm body onto the stack.
        """
        if self._pending_conditional is not None and self._active_block is self._pending_conditional[1]:
            self._pending_conditional = None
        self._active_block.append(element)

    # --- Measurement handles ---

    def measurement_handles(self) -> list[MeasurementHandle]:
        """Return the canonical :class:`MeasurementHandle` for every measurement in the AST.

        Walks the program body in declaration order and returns
        ``op.handle`` for each :class:`MeasurementOperation`. The handle
        objects returned are the *same Python instances* the AST stores
        — writing per-measurement values via ``handle._set_value(...)``
        is immediately visible to every :class:`MeasurementRef` that
        references the same measurement, regardless of whether the
        program was just built or loaded from a ``.qp`` file.
        """
        return [op.handle for op in _walk_measurement_ops(self._body)]

    def _allocate_measurement_name(self, bus: str, requested: str | None) -> str:
        """Choose the name for a new measurement and verify uniqueness.

        - If ``requested`` is provided, it is used verbatim after checking
          that no existing measurement already has that name. Raises
          :class:`ValueError` on collision.
        - Otherwise, an auto-name is generated using the convention:

            - **Schema-backed bus** (``BusRef``): the prefix is the
              bus's full string form followed by ``/m``. For the default
              :class:`BusNaming` pattern, ``q[0].readout`` becomes
              ``q0/readout/m0``, ``q0/readout/m1``, ...; ``q[0].drive``
              gets its own counter (``q0/drive/m0``, ...). Each unique
              bus carries an independent counter, so measurements on
              different buses never collide.

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
                raise ValidationError(msg)
            if requested in used_names:
                msg = f"measurement name {requested!r} is already used by another measurement in this program"
                raise ValidationError(msg)
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
            raise ValidationError(msg)
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
        :class:`ValidationError` with a pointer to the right call site.

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
        raise ValidationError(msg)

    # --- Core operations ---

    def play(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self._validate_bus(bus)
        _validate_waveform_channel(bus, waveform)
        self._append_to_active(Play(bus=bus, waveform=waveform))

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
        handle = MeasurementHandle(allocated)
        self._append_to_active(
            Measure(bus=bus, waveform=waveform, weights=weights, handle=handle, returns=returns),
        )
        return handle

    def wait(self, bus: str, duration: int | Expression) -> None:
        self._validate_bus(bus)
        self._append_to_active(Wait(bus=bus, duration=duration))

    def sync(self, buses: list[str] | None = None) -> None:
        # User-facing kw arg stays ``buses`` for readability; internally the
        # AST field is named ``targets`` (see :class:`Sync`).
        if buses:
            for b in buses:
                self._validate_bus(b)
        self._append_to_active(Sync(targets=buses))

    def set_frequency(self, bus: str, frequency: float | Expression) -> None:
        self._validate_bus(bus)
        self._append_to_active(SetFrequency(bus=bus, frequency=frequency))

    def set_phase(self, bus: str, phase: float | Expression) -> None:
        self._validate_bus(bus)
        self._append_to_active(SetPhase(bus=bus, phase=phase))

    def reset_phase(self, bus: str) -> None:
        self._validate_bus(bus)
        self._append_to_active(ResetPhase(bus=bus))

    def set_gain(self, bus: str, gain: float | Expression) -> None:
        self._validate_bus(bus)
        self._append_to_active(SetGain(bus=bus, gain=gain))

    def set_offset(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        self._validate_bus(bus)
        self._append_to_active(SetOffset(bus=bus, offset_path0=offset_path0, offset_path1=offset_path1))

    def set_parameter(
        self,
        alias: str,
        parameter: str,
        value: float | Expression,
        channel_id: int | None = None,
    ) -> None:
        self._append_to_active(SetParameter(alias=alias, parameter=parameter, value=value, channel_id=channel_id))

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
        self._append_to_active(GetParameter(variable=var, alias=alias, parameter=parameter, channel_id=channel_id))
        return var

    def set_crosstalk(self, crosstalk: CrosstalkMatrix) -> None:
        self._append_to_active(SetCrosstalk(crosstalk=crosstalk))

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

    def if_(self, condition: Expression) -> _IfContext:
        """Open an ``if`` arm gated on a measurement-state predicate.

        ``condition`` must be a :class:`~qprogram.Comparison` between a
        :class:`~qprogram.MeasurementRef` (built via ``handle.state``) and
        an ``int`` literal — i.e., ``handle.state == 0`` or
        ``handle.state != 1``. v1 deliberately limits the surface to
        that shape; richer conditions (variable comparisons, logical
        combinations) will land in a follow-up change.

        Build chains with sequential ``with`` blocks::

            with program.if_(m.state == 0):
                program.play(q[0].drive, "id_pulse")
            with program.elif_(m.state == 1):
                program.play(q[0].drive, "pi_pulse")
            with program.else_():
                pass

        The measurement op whose handle is referenced **must** request
        state classification (``returns`` must include ``"state"``); the
        validator emits ``missing-classification`` otherwise.
        """
        self._validate_conditional_condition(condition, where="if_")
        return _IfContext(self, condition)

    def elif_(self, condition: Expression) -> _ElifContext:
        """Extend the open ``if_`` chain with another arm.

        Must appear immediately after the matching ``if_()`` /
        ``elif_()`` block at the same nesting level; any other append
        in between closes the chain and ``elif_`` raises
        :class:`~qprogram.ValidationError`. See :meth:`if_` for the
        accepted condition shape.
        """
        self._validate_conditional_condition(condition, where="elif_")
        return _ElifContext(self, condition)

    def else_(self) -> _ElseContext:
        """Close the open ``if_`` chain with an unconditional arm.

        Must appear immediately after the matching ``if_()`` /
        ``elif_()`` block at the same nesting level. Only one
        ``else_()`` per chain.
        """
        return _ElseContext(self)

    @staticmethod
    def _validate_conditional_condition(condition: Expression, *, where: str) -> None:
        """Reject conditions outside the v1-supported shape.

        The accepted shape is a single :class:`~qprogram.Comparison`
        whose operands are :class:`~qprogram.MeasurementRef` (from
        ``handle.state``) or :class:`~qprogram.Constant` (an ``int``
        literal), with at least one ``MeasurementRef`` somewhere in
        the comparison. All of these are valid:

        - ``handle.state == 0``           — ``(MeasurementRef, Constant)``
        - ``0 == handle.state``           — ``(Constant, MeasurementRef)``
        - ``m1.state == m2.state``        — ``(MeasurementRef, MeasurementRef)``
        - ``qp.eq(handle.state, 0)``      — same as the first form
        - ``qp.ne(handle.state, 1)``      — same as ``handle.state != 1``

        Operators ``==`` / ``!=`` are emitted by the
        :class:`_HandleFieldAccess` proxy and the :func:`qp.eq` /
        :func:`qp.ne` helpers; the alphabet is constrained at those
        construction sites, not here. Other Comparison operators
        (``<``, ``<=``, ...) round-trip through the AST but have no
        builder ergonomic today.

        Bare :class:`Variable` comparisons (``var == 5``) are out of
        v1 scope; this method is the single gate where future
        widening will land.
        """
        from qprogram.variable import Constant, MeasurementRef  # noqa: PLC0415

        if not isinstance(condition, Comparison):
            msg = (
                f"{where}() expects a Comparison condition such as "
                f"`handle.state == 0` or `handle.state != 1`; got "
                f"{type(condition).__name__}"
            )
            raise ValidationError(msg)

        operands = (condition.left, condition.right)
        if not any(isinstance(o, MeasurementRef) for o in operands):
            msg = (
                f"{where}() condition must reference at least one "
                f"measurement-state ref (e.g. `handle.state`); got a "
                f"comparison of {type(condition.left).__name__} and "
                f"{type(condition.right).__name__}"
            )
            raise ValidationError(msg)
        for operand in operands:
            if not isinstance(operand, (MeasurementRef, Constant)):
                msg = f"{where}() operands must be measurement-state refs or int literals; got {type(operand).__name__}"
                raise ValidationError(msg)
            if isinstance(operand, Constant) and not isinstance(operand.value, int):
                msg = f"{where}() int literal expected; got {type(operand.value).__name__} ({operand.value})"
                raise ValidationError(msg)

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

    - IQ bus + Waveform (single-channel) -> :class:`ValidationError`
    - Single bus + IQWaveform -> :class:`ValidationError`
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
        raise ValidationError(msg)
    if channel == "single" and isinstance(waveform, IQWaveform):
        msg = (
            f"Bus '{bus}' is a single channel but received an IQWaveform "
            f"({type(waveform).__name__}). Use a single-channel Waveform (e.g. Square, FlatTop) instead."
        )
        raise ValidationError(msg)


def _walk_measurement_ops(block: Block) -> list[MeasurementOperation]:
    """Return every :class:`MeasurementOperation` in ``block`` in declaration order.

    Uses :meth:`Block.walk` so the traversal stays consistent with the rest
    of the introspection API; the local filter just selects measurement
    ops out of the broader Operation/Block stream.
    """
    return [node for node in block.walk() if isinstance(node, MeasurementOperation)]


def _measurement_name_prefix(bus: str) -> str:
    """Compute the auto-name prefix for a measurement on ``bus``.

    Schema-backed buses (``BusRef``) get a per-bus prefix built from the
    bus's full string form: ``{bus}/m`` — e.g. ``q0/readout/m`` for the
    default :class:`BusNaming` pattern on ``q[0].readout``, or
    ``readout_q0_bus/m`` for a custom-naming bus. Each unique bus
    string carries an independent counter, so measurements on
    different buses never share a counter (or a name).

    Raw-string buses fall back to a global ``m`` prefix shared across
    all raw-string measurements. The caller appends a free integer to
    produce the final name. See :meth:`QProgram._allocate_measurement_name`.
    """
    if isinstance(bus, BusRef):
        return f"{bus}/m"
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
    """Validate that the bus supports acquisition (has ADC) if it's a BusRef.

    Raises :class:`ValidationError` on the structural mismatch.
    """
    if not isinstance(bus, BusRef):
        return
    if not bus.acquires:
        msg = (
            f"Bus '{bus}' does not support acquisition (acquires=False). "
            f"measure() can only be called on buses with an ADC (e.g. readout buses)."
        )
        raise ValidationError(msg)
