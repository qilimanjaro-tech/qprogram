# ruff: noqa: SLF001, ANN001
from __future__ import annotations

import copy
import re
from collections import deque
from typing import TYPE_CHECKING, ClassVar

from qprogram._reserved import is_reserved_vendor
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
    from qprogram.fragments import Fragment
    from qprogram.vendor import VendorNamespace


class _LoopContext:
    """Context manager returned by :meth:`QProgram.for_loop` and :meth:`QProgram.loop`.

    Supports ``|`` to compose multiple loops into a :class:`Parallel` block.
    """

    def __init__(self, program: QProgram, block: ForLoop | Loop) -> None:
        self._program = program
        self._block = block
        self._parallel_blocks: list[ForLoop | Loop] = [block]

    def __or__(self, other: _LoopContext) -> _LoopContext:
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
    """Context manager returned by :meth:`QProgram.average`."""

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
    """Context manager returned by :meth:`QProgram.block` — a generic grouping with no extra semantics."""

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
    """Context manager for the opening arm of an ``if_`` / ``elif_`` / ``else_`` chain.

    On entry: creates the :class:`Conditional`, appends it to the currently active block at the
    *parent* level (before any arm body is pushed), records the parent on
    :attr:`QProgram._pending_conditional` so a later ``elif_`` / ``else_`` can find it, and pushes
    the first arm body onto the block stack.
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
    """Context manager for an ``elif_`` arm extending the currently-open conditional chain."""

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
    """Context manager for the terminal ``else_`` arm of a conditional chain."""

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


# Public instance attributes assigned in ``QProgram.__init__`` — kept in sync with it so
# ``register_vendor`` can reject vendor names that an instance attribute would shadow.
_PUBLIC_INSTANCE_ATTRS: frozenset[str] = frozenset({"label", "description"})


class QProgram:
    """Top-level container for a pulse-level quantum program.

    The fluent builder for the QProgram AST. Methods like :meth:`play`, :meth:`measure`, and the
    control-flow context managers (:meth:`for_loop`, :meth:`average`, :meth:`if_`) append typed
    operation and block nodes to the current active block.

    Args:
        label: Short identifier for the program; surfaced in result metadata and ``.qp`` headers.
        description: Optional longer description.
        schema: Optional :class:`~qprogram.BusSchema` for typed bus references. Passing one turns on
            schema-aware bus validation and lets the ``.qp`` writer emit compact bus paths.
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
        # Fragments used by this program, keyed by name. Populated by :meth:`call` (transitively,
        # dependencies first — so iteration order is topological) and by the ``.qp`` parser (file
        # order, which is topological too since fragments must be defined before use).
        self._fragments: dict[str, Fragment] = {}
        # Structural-path → 1-based ``.qp`` line for every body node, filled by ``loads()``/
        # ``load()``. Empty for programs built in Python; cleared by :meth:`expand` (the
        # expansion restructures the tree, invalidating the recorded paths).
        self._qp_source_map: dict[tuple[int | str, ...], int] = {}
        # Holds the open if_/elif_/else_ chain so a following elif_/else_ can find the right
        # Conditional. The tuple is (open Conditional, parent block); it's cleared whenever something
        # else is appended at that parent level by :meth:`_append_to_active`.
        self._pending_conditional: tuple[Conditional, Block] | None = None

    # --- Properties ---

    @property
    def body(self) -> Block:
        """Return the root :class:`Block` containing every operation appended to this program."""
        return self._body

    @property
    def schema(self) -> BusSchema | None:
        """Return the attached :class:`BusSchema`, or ``None``.

        At most one schema per program — it defines the chip's elements and bus kinds. The ``.qp``
        writer uses it to emit bus paths (``q[0].drive``) rather than quoted strings; plain-string
        buses keep working either way.
        """
        return self._schema

    @property
    def buses(self) -> set[str]:
        """Return every bus name referenced anywhere in the program tree."""
        return self._body.buses()

    @property
    def variables(self) -> list[Variable]:
        """Return the list of :class:`Variable` s declared on this program (declaration order)."""
        return list(self._variables)

    @property
    def source_map(self) -> dict[tuple[int | str, ...], int]:
        """Return the ``.qp`` source map: structural path → 1-based line in the parsed file.

        Filled by ``loads()`` / ``load()`` for every node in the ``body:`` section (paths follow
        :mod:`qprogram.paths` — ``()`` is the body, ints index ``elements``, ``arm:<i>`` /
        ``else`` / ``loop:<i>`` address conditional arms and parallel loop headers). Empty for
        programs built in Python and after :meth:`expand`. Because the ``.qp`` round-trip
        preserves structure, a :attr:`~qprogram.Diagnostic.path` computed against a built program
        looks up directly in ``loads(dumps(p)).source_map``. Fragment-internal statements are not
        mapped (diagnostics always target the expanded body).
        """
        return dict(self._qp_source_map)

    @property
    def fragments(self) -> dict[str, Fragment]:
        """Return the :class:`~qprogram.Fragment` s used by this program, keyed by name.

        Populated by :meth:`call` (including each fragment's own dependencies, registered first) and
        by ``loads()`` for every ``fragment`` section in a ``.qp`` file. Iteration order is
        topological: a fragment always appears before any fragment that calls it.
        """
        return dict(self._fragments)

    @property
    def _active_block(self) -> Block:
        return self._block_stack[-1]

    def _append_to_active(self, element: Block | Operation) -> None:
        """Append an op or block to the currently active block.

        Also closes an open ``if_`` chain when the new append lands at its parent level — anything
        other than ``elif_`` / ``else_`` at that level makes the chain ambiguous. The chain stays open
        while appends happen inside an arm body (a deeper level of the block stack).
        ``_ElifContext`` / ``_ElseContext`` bypass this method: they mutate the existing
        :class:`Conditional` in place and just push a new arm body.
        """
        if self._pending_conditional is not None and self._active_block is self._pending_conditional[1]:
            self._pending_conditional = None
        self._active_block.append(element)

    # --- Measurement handles ---

    def measurement_handles(self) -> list[MeasurementHandle]:
        """Return the canonical :class:`MeasurementHandle` for every measurement in the AST.

        Walks the body in declaration order and returns ``op.handle`` for each
        :class:`MeasurementOperation`. The returned handles are the *same Python instances* the AST
        stores — writing per-measurement values via ``handle._set_value(...)`` is immediately visible
        to every :class:`MeasurementRef` regardless of whether the program was just built or loaded
        from a ``.qp`` file.
        """
        return [op.handle for op in _walk_measurement_ops(self._body)]

    def _allocate_measurement_name(self, bus: str, requested: str | None) -> str:
        """Choose a unique name for a new measurement.

        If ``requested`` is given, it is validated for uniqueness and used as-is. Otherwise an
        auto-name is generated: for a :class:`~qprogram.BusRef`, the prefix is the bus path followed by
        ``/m`` and a per-bus counter (``q0/readout/m0``, ``q0/readout/m1``, ...); for raw-string buses,
        a global ``m0``, ``m1``, ... counter shared across all raw-string measurements.

        Counters are derived from the AST on each call rather than stored on the program — this keeps
        :func:`copy.deepcopy`, ``with_waveforms``, and ``loads``/``dumps`` round-trips free of hidden
        state, at the cost of one AST walk per measurement construction.

        Raises:
            ValidationError: If ``requested`` is empty, non-string, or collides with an existing
                measurement name.
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
        """Declare a new :class:`Variable` on this program.

        Args:
            id: Short identifier matching ``[A-Za-z_][A-Za-z0-9_]*``. Doubles as the ``.qp``
                identifier and must be unique within the program.
            label: Human-readable name for plots and results.
            units: Unit string (e.g. ``"Hz"``, ``"ns"``).
            description: Longer free-form description.

        Returns:
            The new :class:`Variable`.

        Raises:
            ValidationError: If ``id`` already exists on this program.
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
        """Register a :class:`~qprogram.VendorNamespace` subclass under ``name``.

        After registration, ``program.<name>`` returns the namespace on any :class:`QProgram` instance.

        Re-registering the *same* namespace class under the same name is a no-op (import-time
        side-effect modules may run twice); registering a different class under a taken name is
        an error — silently replacing another vendor's namespace would be a supply-chain hazard.

        Args:
            name: Vendor identifier (also used as the dot-prefix in ``.qp`` operation names).
                Must not be a reserved keyword, the ``"core"`` sentinel, or the name of any
                :class:`QProgram` attribute (which would make the namespace unreachable — vendor
                lookup happens in ``__getattr__``, after normal attribute resolution).
            namespace_cls: The :class:`~qprogram.VendorNamespace` subclass to instantiate lazily.

        Raises:
            ValueError: If ``name`` is reserved, shadows a ``QProgram`` attribute, or is already
                registered to a different namespace class.
        """
        existing = cls._vendor_registry.get(name)
        if existing is namespace_cls:
            return  # idempotent re-registration
        if is_reserved_vendor(name):
            msg = (
                f"vendor name {name!r} is reserved (see qprogram.RESERVED_KEYWORDS plus the "
                f"'core' sentinel); pick a different namespace for this vendor extension"
            )
            raise ValueError(msg)
        # ``hasattr`` covers methods and properties; the frozenset covers the public *instance*
        # attributes assigned in ``__init__`` (invisible on the class but they shadow vendor
        # dispatch on every instance, since ``__getattr__`` only runs after normal lookup fails).
        if hasattr(cls, name) or name in _PUBLIC_INSTANCE_ATTRS:
            msg = (
                f"vendor name {name!r} collides with a QProgram attribute; the namespace would "
                f"be unreachable because normal attribute lookup wins over vendor dispatch"
            )
            raise ValueError(msg)
        if existing is not None:
            msg = (
                f"vendor name {name!r} is already registered to "
                f"{existing.__module__}.{existing.__qualname__}; refusing to replace it"
            )
            raise ValueError(msg)
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
        """Append a :class:`~qprogram.operations.Play` op — play a waveform on a bus.

        Args:
            bus: Target bus.
            waveform: Concrete waveform or a string alias resolved later by :meth:`with_waveforms`.
        """
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
        """Play a readout pulse, acquire the result, and return a stable handle.

        Args:
            bus: Readout bus (must have ``acquires=True``).
            waveform: Readout pulse — concrete :class:`~qprogram.waveforms.IQWaveform` or a string
                alias.
            weights: Integration weights — same shape options as ``waveform``.
            name: Optional explicit handle name. When omitted, an auto-name is allocated using the
                convention described on :meth:`_allocate_measurement_name`.
            returns: Return-type tokens. Default ``("iq",)``. Accepts a comma-separated string
                (``"iq,raw"``) or any iterable of strings. ``"state"`` requests classification;
                ``"raw"`` requests the raw ADC trace.

        Returns:
            The :class:`MeasurementHandle` identifying this measurement; pass it to ``result.get(...)``.
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
        """Append a :class:`~qprogram.operations.Wait` — idle on ``bus`` for ``duration`` ns."""
        self._validate_bus(bus)
        self._append_to_active(Wait(bus=bus, duration=duration))

    def sync(self, buses: list[str] | None = None) -> None:
        """Append a :class:`~qprogram.operations.Sync` — synchronise buses to a common time reference.

        Args:
            buses: Buses to sync, or ``None`` to sync every bus currently active in the program.

        Raises:
            ValidationError: If ``buses`` is an empty list — ambiguous between "sync nothing"
                and "sync everything"; pass ``None`` for the sync-all form.
        """
        # User-facing keyword stays ``buses`` for readability; the AST attribute is named ``targets``
        # (see :class:`Sync`).
        if buses is not None and len(buses) == 0:
            msg = "sync([]) is ambiguous; pass None (or no argument) to sync all buses"
            raise ValidationError(msg)
        if buses:
            for b in buses:
                self._validate_bus(b)
        self._append_to_active(Sync(targets=buses))

    def set_frequency(self, bus: str, frequency: float | Expression) -> None:
        """Append a :class:`~qprogram.operations.SetFrequency` — retune the NCO on ``bus`` to ``frequency`` Hz."""
        self._validate_bus(bus)
        self._append_to_active(SetFrequency(bus=bus, frequency=frequency))

    def set_phase(self, bus: str, phase: float | Expression) -> None:
        """Append a :class:`~qprogram.operations.SetPhase` — set the NCO phase on ``bus`` to ``phase`` rad."""
        self._validate_bus(bus)
        self._append_to_active(SetPhase(bus=bus, phase=phase))

    def reset_phase(self, bus: str) -> None:
        """Append a :class:`~qprogram.operations.ResetPhase` — reset the NCO phase on ``bus`` to zero."""
        self._validate_bus(bus)
        self._append_to_active(ResetPhase(bus=bus))

    def set_gain(self, bus: str, gain: float | Expression) -> None:
        """Append a :class:`~qprogram.operations.SetGain` — set the output gain on ``bus``."""
        self._validate_bus(bus)
        self._append_to_active(SetGain(bus=bus, gain=gain))

    def set_offset(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        """Append a :class:`~qprogram.operations.SetOffset` — set DC offset on one or both paths of ``bus``."""
        self._validate_bus(bus)
        self._append_to_active(SetOffset(bus=bus, offset_path0=offset_path0, offset_path1=offset_path1))

    def set_parameter(
        self,
        alias: str,
        parameter: str,
        value: float | Expression,
        channel_id: int | None = None,
    ) -> None:
        """Append a :class:`~qprogram.operations.SetParameter` — write a platform-defined parameter.

        Args:
            alias: Platform-defined identifier for the target.
            parameter: Name of the parameter to write.
            value: New value.
            channel_id: Optional channel index when the alias has multiple channels.
        """
        self._append_to_active(SetParameter(alias=alias, parameter=parameter, value=value, channel_id=channel_id))

    def get_parameter(self, alias: str, parameter: str, channel_id: int | None = None) -> Variable:
        """Append a :class:`~qprogram.operations.GetParameter` and return the freshly-declared variable.

        Auto-generates a unique, ``[A-Za-z_]``-pattern-safe variable id from ``f"{alias}_{parameter}"``;
        the original ``alias.parameter`` form is kept on the variable's :attr:`label` for traceability.

        Args:
            alias: Platform-defined identifier for the target.
            parameter: Name of the parameter to read.
            channel_id: Optional channel index when the alias has multiple channels.

        Returns:
            The :class:`Variable` the runtime will populate with the read value.
        """
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
        """Append a :class:`~qprogram.operations.SetCrosstalk` — install a program-wide crosstalk matrix."""
        self._append_to_active(SetCrosstalk(crosstalk=crosstalk))

    # --- Fragments ---

    def call(self, fragment: Fragment, *args: object, **kwargs: object) -> None:
        """Instantiate a :class:`~qprogram.Fragment` at the current position.

        Appends a first-class :class:`~qprogram.operations.Call` node — the fragment definition and
        the call site both survive serialization and round-trip through ``.qp``. Use
        :meth:`expand` to lower every call into the substituted fragment body.

        Arguments bind to the fragment's parameters with the Python calling convention (positional
        in declaration order, then keywords). Accepted values: numbers, expressions/variables,
        buses (strings or :class:`~qprogram.BusRef`), and waveforms.

        The fragment (and, transitively, any fragment it calls) is registered on this program so
        the ``.qp`` writer can emit its definition.

        Args:
            fragment: The fragment to call.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Raises:
            ValidationError: On a non-Fragment argument, a binding error (missing/extra/duplicate
                parameter, unsupported value type), a name clash with a different fragment already
                used by this program, a schema mismatch, or a call cycle.
        """
        from qprogram.fragments import Fragment, bind_arguments  # noqa: PLC0415
        from qprogram.operations.call import Call  # noqa: PLC0415

        if not isinstance(fragment, Fragment):
            msg = f"call() expects a Fragment, got {type(fragment).__name__}"
            raise ValidationError(msg)
        if fragment is self:
            msg = f"fragment {fragment.name!r} cannot call itself"
            raise ValidationError(msg)
        bound = bind_arguments(fragment, args, kwargs)
        for value in bound.values():
            if isinstance(value, BusRef):
                self._validate_bus(value)
        self._reconcile_fragment_schema(fragment)
        self._register_fragment(fragment, _stack=())
        self._append_to_active(Call(fragment=fragment, arguments=bound))

    def _reconcile_fragment_schema(self, fragment: Fragment) -> None:
        """Adopt the fragment's schema (or vice versa); reject two different schemas.

        A program and the fragments it calls must agree on a single :class:`~qprogram.BusSchema`
        so the ``.qp`` writer's one ``schema:`` section can resolve every bus path.
        """
        frag_schema = fragment.schema
        if frag_schema is None:
            return
        if self._schema is None:
            self._schema = frag_schema
            return
        if frag_schema is not self._schema:
            msg = (
                f"fragment {fragment.name!r} was built against a different BusSchema than this "
                f"program's; a program and its fragments must share one schema"
            )
            raise ValidationError(msg)

    def _register_fragment(self, fragment: Fragment, _stack: tuple[str, ...]) -> None:
        """Record ``fragment`` (dependencies first) in :attr:`_fragments`; detect cycles and clashes."""
        from qprogram.operations.call import Call  # noqa: PLC0415

        if fragment.name in _stack:
            cycle = " -> ".join((*_stack, fragment.name))
            msg = f"fragment call cycle: {cycle}"
            raise ValidationError(msg)
        existing = self._fragments.get(fragment.name)
        if existing is not None and existing is not fragment:
            msg = (
                f"a different fragment named {fragment.name!r} is already used by this program; "
                f"fragment names must be unique within a program"
            )
            raise ValidationError(msg)
        # Walk dependencies even when already registered — the fragment may have gained nested
        # calls since the first registration.
        for node in fragment.body.walk():
            if isinstance(node, Call):
                self._register_fragment(node.fragment, (*_stack, fragment.name))
        if existing is None:
            self._fragments[fragment.name] = fragment

    # --- Control flow ---

    def for_loop(
        self,
        variable: Variable,
        start: float,
        stop: float,
        step: float = 1,
    ) -> _LoopContext:
        """Open a linear-sweep loop binding ``variable`` to ``range(start, stop, step)``.

        Use ``|`` on the returned context manager to compose multiple loops into a :class:`Parallel`
        block.

        Args:
            variable: The :class:`Variable` rebound each iteration.
            start: First value (inclusive).
            stop: Final value (inclusive).
            step: Increment between consecutive iterations.
        """
        block = ForLoop(variable=variable, start=start, stop=stop, step=step)
        return _LoopContext(self, block)

    def loop(self, variable: Variable, values: np.ndarray) -> _LoopContext:
        """Open an arbitrary-sweep loop binding ``variable`` to each element of ``values``.

        Args:
            variable: The :class:`Variable` rebound each iteration.
            values: Sequence of values to iterate through.
        """
        block = Loop(variable=variable, values=values)
        return _LoopContext(self, block)

    def average(self, shots: int) -> _AverageContext:
        """Open an averaging block that repeats its body ``shots`` times and averages results."""
        return _AverageContext(self, shots)

    def block(self) -> _BlockContext:
        """Open a generic grouping block — no extra semantics, just a container for organisation."""
        return _BlockContext(self)

    def if_(self, condition: Expression) -> _IfContext:
        """Open an ``if`` arm gated on a measurement-state predicate.

        Build chains with sequential ``with`` blocks::

            with program.if_(m.state == 0):
                program.play(q[0].drive, "id_pulse")
            with program.elif_(m.state == 1):
                program.play(q[0].drive, "pi_pulse")
            with program.else_():
                pass

        The producing measurement **must** request state classification (``returns`` must include
        ``"state"``); the validator emits ``missing-classification`` otherwise.

        Args:
            condition: A :class:`~qprogram.Comparison` between a :class:`~qprogram.MeasurementRef`
                (from ``handle.state``) and an ``int`` literal. v1 deliberately limits the surface to
                this shape; richer conditions will land in a follow-up.
        """
        self._validate_conditional_condition(condition, where="if_")
        return _IfContext(self, condition)

    def elif_(self, condition: Expression) -> _ElifContext:
        """Extend the open ``if_`` chain with another arm.

        Must appear immediately after the matching ``if_()`` / ``elif_()`` at the same nesting level;
        any other append in between closes the chain and ``elif_()`` raises :class:`ValidationError`.
        Condition shape is the same as :meth:`if_`.
        """
        self._validate_conditional_condition(condition, where="elif_")
        return _ElifContext(self, condition)

    def else_(self) -> _ElseContext:
        """Close the open ``if_`` chain with an unconditional arm.

        Must appear immediately after the matching ``if_()`` / ``elif_()`` at the same nesting level.
        At most one ``else_()`` per chain.
        """
        return _ElseContext(self)

    @staticmethod
    def _validate_conditional_condition(condition: Expression, *, where: str) -> None:
        """Reject conditional conditions outside the v1-supported shape.

        v1 accepts a single :class:`Comparison` whose operands are :class:`MeasurementRef` (from
        ``handle.state``) or :class:`Constant` (an ``int`` literal), with at least one
        :class:`MeasurementRef`. This is the single gate where future widening (bare-variable
        comparisons, etc.) will land.

        Args:
            condition: The condition expression.
            where: The user-facing call site name (``"if_"`` / ``"elif_"``) for error messages.

        Raises:
            ValidationError: If ``condition`` is not a Comparison, has no MeasurementRef, has an
                operand outside the allowed types, or compares against a non-int Constant.
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

    def expand(self) -> QProgram:
        """Return a deep copy with every fragment :class:`~qprogram.operations.Call` inlined.

        The canonical lowering from the composed form to a fragment-free program: each call site is
        replaced by a plain :class:`Block` containing the fragment body with parameters substituted
        by the bound arguments. Fragment-local variables are hygienically renamed onto this program
        (``{fragment}_{id}``, numeric suffix on collision); colliding measurement names gain a
        ``_2`` / ``_3`` suffix (the shared handle is renamed, keeping ``handle.state`` conditionals
        consistent). Nested calls expand recursively; expansion is deterministic, so expanding twice
        yields structurally equal programs.

        Programs without calls return an ordinary deep copy.

        Returns:
            A new, fragment-free :class:`QProgram`; the original is untouched.

        Raises:
            ValidationError: On a fragment call cycle or a binding used in an incompatible
                position (e.g. a waveform bound to a parameter used inside arithmetic).
        """
        from qprogram.fragments import expand_program  # noqa: PLC0415

        return expand_program(self)

    def with_bus_mapping(self, bus_mapping: dict[str, str]) -> QProgram:
        """Return a deep copy with bus references rewritten by ``bus_mapping``.

        The ``buses`` property is computed from the AST, so it reflects the remapping automatically.

        Args:
            bus_mapping: Mapping of old bus name to new bus name. Unmentioned buses pass through.

        Returns:
            A new :class:`QProgram` with the remapped buses; the original is untouched.
        """
        new_program = copy.deepcopy(self)
        _remap_buses(new_program._body, bus_mapping)
        return new_program

    def with_waveforms(self, waveform_mapping: dict[str, Waveform | IQWaveform]) -> QProgram:
        """Return a deep copy with string waveform aliases replaced by concrete waveforms.

        Args:
            waveform_mapping: Maps alias names to concrete :class:`Waveform` / :class:`IQWaveform`
                instances. Unmentioned aliases pass through.

        Returns:
            A new :class:`QProgram` with matching string aliases replaced.
        """
        new_program = copy.deepcopy(self)
        _remap_waveforms(new_program._body, waveform_mapping)
        return new_program


def _remap_waveforms(block: Block, mapping: dict[str, Waveform | IQWaveform]) -> None:
    """Replace string waveform aliases with concrete waveforms in place.

    Walks via :meth:`Block.walk` and uses each op's :attr:`WAVEFORM_ATTRS` rather than hard-coding
    ``"waveform"`` / ``"weights"``, so vendor ops with custom attribute names work as long as they
    declare their ``WAVEFORM_ATTRS``.
    """
    for op in block.walk():
        if not isinstance(op, Operation):
            continue
        for attr_name in op.WAVEFORM_ATTRS:
            value = getattr(op, attr_name, None)
            if isinstance(value, str) and value in mapping:
                setattr(op, attr_name, mapping[value])


def _remap_buses(block: Block, mapping: dict[str, str]) -> None:
    """Rewrite bus references in place across a block tree.

    Walks via :meth:`Block.walk` and uses each op's :attr:`BUS_ATTRS`. Handles both scalar
    (``op.bus``) and list-shaped (``Sync.targets``) attributes via type dispatch.
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
    """Validate the waveform's channel kind against the bus's declared channel.

    Raw-string buses and string-alias waveforms skip validation — there's no channel metadata to
    check. Schema-bound :class:`~qprogram.BusRef` + concrete waveform mismatches raise.

    Raises:
        ValidationError: When an IQ bus is given a single-channel waveform or a single-channel bus
            is given an IQ waveform.
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
    """Return every :class:`MeasurementOperation` in ``block`` in declaration order."""
    return [node for node in block.walk() if isinstance(node, MeasurementOperation)]


def _measurement_name_prefix(bus: str) -> str:
    """Compute the auto-name prefix for a measurement on ``bus``.

    Schema-backed buses get a per-bus prefix (``{bus}/m``); raw-string buses share a global ``m``
    prefix. The caller appends a free integer to produce the final name.
    """
    if isinstance(bus, BusRef):
        return f"{bus}/m"
    return "m"


def _sanitize_id(s: str) -> str:
    """Map an arbitrary string to a valid :class:`Variable` id.

    Replaces every non-``[A-Za-z0-9_]`` character with ``_``, prefixes a leading underscore if the
    first character is a digit, and falls back to ``"var"`` for empty input.
    """
    out = re.sub(r"\W", "_", s) if s else ""
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
