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
"""The [`QProgram`][qprogram.QProgram] builder and the context managers its control-flow methods return.

Every builder call ([`QProgram.play`][qprogram.QProgram.play], [`QProgram.measure`][qprogram.QProgram.measure],
[`QProgram.set_frequency`][qprogram.QProgram.set_frequency]) appends a typed
[`Operation`][qprogram.operations.Operation] to the block that is currently active, and the control-flow methods
([`QProgram.sweep`][qprogram.QProgram.sweep], [`QProgram.average`][qprogram.QProgram.average],
[`QProgram.block`][qprogram.QProgram.block], [`QProgram.if_`][qprogram.QProgram.if_]) return context managers that push
a fresh [`Block`][qprogram.blocks.Block] onto the program's block stack, so nested ``with`` statements build a nested
tree.

The context-manager classes are private: each is the return type of a builder method and is never constructed directly.
Alongside them, this module holds the vendor-namespace registry that makes ``program.<vendor>.<op>(...)`` resolve on any
instance, and the whole-program transforms ([`QProgram.expand`][qprogram.QProgram.expand],
[`QProgram.rebind`][qprogram.QProgram.rebind], [`QProgram.with_waveforms`][qprogram.QProgram.with_waveforms]).
"""
# The context managers drive the builder through its private state (``_block_stack``,
# ``_append_to_active``), and every ``__exit__`` takes its three exception arguments unannotated.
# ruff: file-ignore[private-member-access, missing-type-function-argument]

from __future__ import annotations

import copy
import difflib
import re
from collections import deque
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, overload

from qprogram._reserved import is_reserved_vendor
from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.buses import BusRef, naming_substituted_schema, resolve_ref
from qprogram.errors import ValidationError
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.operation import MeasurementField, MeasurementOperation, Operation
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
from qprogram.operations.set_frequency import SetFrequency
from qprogram.operations.set_gain import SetGain
from qprogram.operations.set_offset import SetOffset
from qprogram.operations.set_parameter import SetParameter
from qprogram.operations.set_phase import SetPhase
from qprogram.operations.sync import Sync
from qprogram.operations.wait import Wait
from qprogram.result import MeasurementHandle
from qprogram.sweeps.builtin import File, Linspace, Logspace, Range, Values
from qprogram.sweeps.combinators import Repeat, Rotate
from qprogram.variable import Comparison, Expression, Variable
from qprogram.waveform_library import WaveformLibrary
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    import numpy.typing as npt

    from qprogram.buses import BusNaming, BusSchema
    from qprogram.fragments import Fragment
    from qprogram.sweeps.source import SweepSource
    from qprogram.vendor import VendorNamespace


class _LoopContext:
    """Context manager returned by [`QProgram.sweep`][qprogram.QProgram.sweep].

    Supports ``|`` to compose multiple sweeps into a [`Parallel`][qprogram.blocks.Parallel] block. ``__or__`` is pure —
    it returns a fresh context with the concatenated list and touches the program only on
    ``__enter__`` — so a list of sweeps can be folded programmatically::

        functools.reduce(operator.or_, [program.sweep(v, src) for v, src in specs])

    `repeat` and `rotate` are pure in the same way. They wrap the bound source in the
    matching combinator and hand back a fresh context, which is what lets a sweep be *shaped* inline
    without naming [`Repeat`][qprogram.Repeat] / [`Rotate`][qprogram.Rotate]::

        with program.sweep(phi).from_values(base).rotate(by=1).repeat(3):
            ...
    """

    def __init__(self, program: QProgram, block: Sweep) -> None:
        self._program = program
        self._block = block
        self._parallel_blocks: list[Sweep] = [block]

    def __or__(self, other: _LoopContext) -> _LoopContext:
        """Compose this sweep with ``other`` into a [`Parallel`][qprogram.blocks.Parallel] block.

        Args:
            other (_LoopContext): The sweep context to advance in lockstep with this one.

        Returns:
            A fresh context carrying both operands' sweeps; neither operand is modified.
        """
        ctx = _LoopContext(self._program, self._block)
        ctx._parallel_blocks = self._parallel_blocks + other._parallel_blocks
        return ctx

    def repeat(self, times: int) -> _LoopContext:
        """Run the bound source's points ``times`` times back to back — [`Repeat`][qprogram.Repeat].

        Each repetition is a distinct sweep point with its own result entry; reach for
        [`QProgram.average`][qprogram.QProgram.average] when you want the repetitions collapsed instead.

        Args:
            times (int): How many times to run the source. Must be at least 1.

        Returns:
            A fresh context bound to the wrapped source; this one is left untouched.

        Raises:
            ValidationError: If this context is already a ``|`` composition, which gives the
                combinator no single source to wrap.
        """
        return self._wrapped(lambda source: Repeat(source, times), method="repeat")

    def rotate(self, by: int = 1) -> _LoopContext:
        """Cyclically shift the bound source's points left by ``by`` — [`Rotate`][qprogram.Rotate].

        Args:
            by (int, optional): Positions to shift left. May be negative (shifts right) or exceed
                the point count (wraps, as `numpy.roll` does).

        Returns:
            A fresh context bound to the wrapped source; this one is left untouched.

        Raises:
            ValidationError: If this context is already a ``|`` composition.
        """
        return self._wrapped(lambda source: Rotate(source, by), method="rotate")

    def _wrapped(self, wrap: Callable[[SweepSource], SweepSource], *, method: str) -> _LoopContext:
        """Rebuild this context around ``wrap(source)``, keeping the same program and variable.

        Args:
            wrap (Callable[[SweepSource], SweepSource]): Wraps the bound source in a combinator.
            method (str): Name of the shortcut being called (``"repeat"`` / ``"rotate"``), quoted in
                the error message.

        Returns:
            A fresh context bound to the wrapped source.

        Raises:
            ValidationError: If this context composes several sweeps with ``|``, leaving the
                combinator no single source to wrap.
        """
        if len(self._parallel_blocks) > 1:
            msg = (
                f"{method}() shapes one sweep's source, but this context already composes "
                f"{len(self._parallel_blocks)} sweeps with `|`. Call {method}() on each sweep before "
                f"composing them."
            )
            raise ValidationError(msg)
        return _LoopContext(self._program, Sweep(variable=self._block.variable, source=wrap(self._block.source)))

    def __enter__(self) -> Sweep | Parallel:
        block = self._parallel_blocks[0] if len(self._parallel_blocks) == 1 else Parallel(loops=self._parallel_blocks)
        self._program._append_to_active(block)
        self._program._block_stack.append(block)
        return block

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._program._block_stack.pop()


class _Unset:
    """Type of the `_UNSET` sentinel. Singleton; do not instantiate directly."""

    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()
"""Marks [`QProgram.sweep`][qprogram.QProgram.sweep]'s ``source`` as *not passed*, which is what selects the fluent
`_SweepBuilder` return. A sentinel rather than ``None`` so that an explicit
``sweep(var, None)`` — a source that failed to be computed, say — still reaches
[`Sweep`][qprogram.blocks.Sweep] and is rejected there, instead of silently returning a builder."""


_FROM_PREFIX = "from_"
"""Attribute prefix that makes `_SweepBuilder` look up a sweep source by name."""

_SHAPING_METHODS = frozenset({"repeat", "rotate"})
"""Combinator shortcuts that live on `_LoopContext`. Named here only so that reaching for one
on a `_SweepBuilder` — before any values are picked — says so instead of raising a bare
missing-attribute error."""


def _builder_key(name: str) -> str:
    """Normalize a source class name or a ``from_*`` suffix for matching — no underscores, no case.

    ``from_iq_table`` and ``IQTable`` both reduce to ``iqtable``, so a class name that carries an
    acronym stays spellable in snake_case without the builder having to guess where the word breaks
    are.

    Args:
        name (str): A sweep-source class name, or the suffix of a ``from_*`` attribute.

    Returns:
        The matching key: ``name`` with underscores dropped and case folded.
    """
    return name.replace("_", "").casefold()


def _builder_method_name(source_name: str) -> str:
    """Spell the ``from_*`` attribute that builds the source class named ``source_name``.

    The inverse of `_builder_key` for the common case — used for did-you-mean lists, never for
    matching (matching is normalized in both directions instead).

    Args:
        source_name (str): Name of a sweep-source class, in ``CamelCase``.

    Returns:
        The ``from_*`` attribute name that builds that source.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", source_name).lower()
    return f"{_FROM_PREFIX}{snake}"


def _sweep_source_for_builder(suffix: str) -> type[SweepSource] | None:
    """Return the registered sweep source a ``from_<suffix>`` attribute names, or ``None``.

    Matching ignores case and underscores on both sides, so ``from_iq_table`` finds ``IQTable``.

    Args:
        suffix (str): The part of the attribute name that follows ``from_``.

    Returns:
        The matching sweep-source class, or ``None`` when no registered source matches.
    """
    # Deferred: reaching the registry initializes the whole ``qprogram.serialization`` package —
    # the core specs and the writer — which nothing here needs until a ``from_*`` is resolved.
    from qprogram.serialization.registry import (  # ruff: ignore[import-outside-top-level]
        get_sweep_source_class,
        known_sweep_sources,
    )

    want = _builder_key(suffix)
    for source_name in known_sweep_sources():
        if _builder_key(source_name) == want:
            return get_sweep_source_class(source_name)
    return None


def _unknown_source_message(attribute: str) -> str:
    """Compose the ``AttributeError`` text for a ``from_*`` attribute no registered source answers.

    Args:
        attribute (str): The attribute that was reached for.

    Returns:
        The message: a did-you-mean shortlist, every available ``from_*`` builder, and how to
        register a source of one's own.
    """
    from qprogram.serialization.registry import known_sweep_sources  # ruff: ignore[import-outside-top-level]

    available = sorted(_builder_method_name(name) for name in known_sweep_sources())
    suggestions = difflib.get_close_matches(attribute, available, n=3)
    hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
    return (
        f"no sweep source is registered for {attribute!r}.{hint} "
        f"Available: {', '.join(available)}. Add one with qp.register_sweep_source(cls) and its "
        f"builder appears here too."
    )


class _SweepBuilder:
    """Source picker returned by ``program.sweep(variable)`` when no source is passed.

    Each ``from_*`` builds one [`SweepSource`][qprogram.SweepSource] and returns exactly what the
    two-argument ``sweep(variable, source)`` form returns — the same [`Sweep`][qprogram.blocks.Sweep] node, the same
    ``.qp`` line, the same ``|`` composition. The only thing it changes is the call site, which does
    not have to name a source class::

        with program.sweep(freq).from_range(4e9, 6e9, 1e6):
            ...
        with program.sweep(freq).from_range(4e9, 6e9, 1e6) | program.sweep(amp).from_values(table):
            ...

    Both spellings are supported on purpose. Reach for ``from_*`` when writing a sweep by hand; pass
    the source object when *computing* one — holding it in a variable, building it in a
    comprehension, or composing combinators more deeply than `_LoopContext.rotate` and
    `_LoopContext.repeat` reach.

    Every registered source is reachable here, not just the built-ins: an unknown ``from_<name>``
    attribute is resolved against the live sweep-source registry (see `__getattr__`), so a
    vendor source gets its builder with no core change. The five built-ins are additionally written
    out as real methods, so editors complete and type-check them.

    A builder is not a context manager — it has no values yet. ``with program.sweep(v):`` raises
    instead of quietly sweeping nothing.
    """

    def __init__(self, program: QProgram, variable: Variable) -> None:
        self._program = program
        self._variable = variable

    # --- Built-in sources. Spelled out for autocomplete; __getattr__ covers every registered one. ---

    def from_range(self, start: float, stop: float, step: float = 1) -> _LoopContext:
        """Sweep a ramp from ``start`` to ``stop`` in increments of ``step``, both ends inclusive.

        Builds [`Range`][qprogram.Range], which is where the validation rules live.

        Args:
            start (float): First value (inclusive).
            stop (float): Final value (inclusive).
            step (float, optional): Increment between consecutive points. Defaults to ``1``.

        Returns:
            The context manager that opens the sweep block.
        """
        return self._bind(Range(start, stop, step))

    def from_linspace(self, start: float, stop: float, num: int) -> _LoopContext:
        """Sweep ``num`` evenly spaced points from ``start`` to ``stop``, both ends inclusive.

        Builds [`Linspace`][qprogram.Linspace] — the ramp to prefer when you know the point count
        rather than the spacing.

        Args:
            start (float): First value (inclusive).
            stop (float): Final value (inclusive).
            num (int): Number of points. ``1`` yields ``[start]``.

        Returns:
            The context manager that opens the sweep block.
        """
        return self._bind(Linspace(start, stop, num))

    def from_logspace(self, start: float, stop: float, num: int) -> _LoopContext:
        """Sweep ``num`` points spaced evenly on a log scale between ``start`` and ``stop``.

        Builds [`Logspace`][qprogram.Logspace]. Both bounds are actual values, not exponents.

        Args:
            start (float): First value (inclusive). Must be strictly positive.
            stop (float): Final value (inclusive). Must be strictly positive.
            num (int): Number of points.

        Returns:
            The context manager that opens the sweep block.
        """
        return self._bind(Logspace(start, stop, num))

    def from_values(self, points: npt.ArrayLike) -> _LoopContext:
        """Sweep an explicit list of points.

        Builds [`Values`][qprogram.Values], which is ``KIND = "arbitrary"`` even when the points
        happen to be evenly spaced — use `from_range` or `from_linspace` when the sweep
        really is a ramp and you want a platform to be able to compile it as one.

        Args:
            points (ArrayLike): Sequence of values to iterate through. Anything
                `numpy.asarray` accepts.

        Returns:
            The context manager that opens the sweep block.
        """
        return self._bind(Values(points))

    def from_file(self, path: str) -> _LoopContext:
        """Sweep the points held in the ``.npy`` file at ``path``.

        Builds [`File`][qprogram.File], which stores the path rather than the values — the
        file must be readable wherever the program is validated or run.

        Args:
            path (str): Path to a ``.npy`` file holding a 1-D array.

        Returns:
            The context manager that opens the sweep block.
        """
        return self._bind(File(path))

    # --- Plumbing ---

    def _bind(self, source: SweepSource) -> _LoopContext:
        """Put ``source`` in a [`Sweep`][qprogram.blocks.Sweep] and hand back the standard loop context.

        Args:
            source (SweepSource): The source whose values the sweep iterates.

        Returns:
            The same context the two-argument ``sweep(variable, source)`` form returns.
        """
        return _LoopContext(self._program, Sweep(variable=self._variable, source=source))

    def __getattr__(self, attribute: str) -> Callable[..., _LoopContext]:
        """Resolve an unknown ``from_<source>`` attribute against the live sweep-source registry.

        This is what keeps the fluent form open: whatever
        `register_sweep_source` knows about is spellable here — vendor sources and
        the combinators included — matched on the class name with underscores and case ignored, so
        ``from_iq_table`` finds ``IQTable``. The returned callable forwards its arguments to the
        source's constructor, so the source itself still does the validating.

        Args:
            attribute (str): The attribute being looked up.

        Returns:
            A callable that constructs the named source from the arguments it is given and returns
            the loop context for the resulting sweep.

        Raises:
            AttributeError: For a ``from_*`` attribute no registered source answers (with a
                did-you-mean list), for a shaping method that belongs on the sweep rather than on
                the builder, and for any other attribute, as usual.
        """
        if attribute in _SHAPING_METHODS:
            msg = (
                f"{attribute}() shapes a sweep that already has values, so it lives on the sweep, "
                f"not on the builder: pick the values first — "
                f"sweep(variable).from_values([...]).{attribute}(...)."
            )
            raise AttributeError(msg)
        if attribute.startswith("__") or not attribute.startswith(_FROM_PREFIX):
            msg = f"{type(self).__name__!r} object has no attribute {attribute!r}"
            raise AttributeError(msg)
        source_cls = _sweep_source_for_builder(attribute[len(_FROM_PREFIX) :])
        if source_cls is None:
            raise AttributeError(_unknown_source_message(attribute))

        def build(*args: Any, **kwargs: Any) -> _LoopContext:
            return self._bind(source_cls(*args, **kwargs))

        build.__name__ = attribute
        build.__qualname__ = f"{type(self).__name__}.{attribute}"
        build.__doc__ = f"Sweep the values of {source_cls.__name__}(...), resolved from the sweep-source registry."
        return build

    def __enter__(self) -> NoReturn:
        """Refuse to open a block: a builder has no source yet, so there is nothing to sweep.

        Raises:
            ValidationError: Always, naming the ``from_*`` methods that pick values and the
                two-argument ``sweep(variable, source)`` form.
        """
        msg = (
            f"sweep({self._variable.id!r}) picked no values — it returned a source builder, not a "
            f"block. Choose the values with a from_* method "
            f"(.from_range(start, stop, step), .from_linspace(start, stop, num), "
            f".from_values([...]), .from_file(path), .from_logspace(start, stop, num)), or pass a "
            f"source directly: sweep(variable, qp.Range(...))."
        )
        raise ValidationError(msg)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Unreachable — `__enter__` always raises. Defined so that ``with`` gets that far.

        CPython looks ``__exit__`` up *before* it calls ``__enter__``; without this method the
        statement would fail with a generic "does not support the context manager protocol"
        TypeError instead of the message `__enter__` raises.
        """


class _AverageContext:
    """Context manager returned by [`QProgram.average`][qprogram.QProgram.average]."""

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
    """Context manager returned by [`QProgram.block`][qprogram.QProgram.block] — a generic grouping with no extra semantics."""

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

    On entry: creates the [`Conditional`][qprogram.blocks.Conditional], appends it to the currently active block at the
    *parent* level (before any arm body is pushed), records the parent on
    `QProgram._pending_conditional` so a later ``elif_`` / ``else_`` can find it, and pushes
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

    The fluent builder for the QProgram AST. Methods like `play`, `measure`, and the
    control-flow context managers (`sweep`, `average`, `if_`) append typed
    operation and block nodes to the current active block.

    Args:
        label (str, optional): Short identifier for the program; surfaced in result metadata and
            ``.qp`` headers.
        description (str | None): Longer description of what the program does.
        schema (BusSchema | None): Schema backing typed bus references. Passing one turns on
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
        # Fragments used by this program, keyed by name. Populated by `call` (transitively,
        # dependencies first — so iteration order is topological) and by the ``.qp`` parser (file
        # order, which is topological too since fragments must be defined before use).
        self._fragments: dict[str, Fragment] = {}
        # Structural-path → 1-based ``.qp`` line for every body node, filled by ``loads()``/
        # ``load()``. Empty for programs built in Python; cleared by `expand` (the
        # expansion restructures the tree, invalidating the recorded paths).
        self._qp_source_map: dict[tuple[int | str, ...], int] = {}
        # Holds the open if_/elif_/else_ chain so a following elif_/else_ can find the right
        # Conditional. The tuple is (open Conditional, parent block); it's cleared whenever something
        # else is appended at that parent level by `_append_to_active`.
        self._pending_conditional: tuple[Conditional, Block] | None = None

    # --- Properties ---

    @property
    def body(self) -> Block:
        """The root [`Block`][qprogram.blocks.Block] containing every operation appended to this program."""
        return self._body

    @property
    def schema(self) -> BusSchema | None:
        """The attached [`BusSchema`][qprogram.BusSchema], or ``None`` for a program built from raw-string buses.

        At most one schema per program — it defines the chip's elements and bus kinds. The ``.qp``
        writer uses it to emit bus paths (``q[0].drive``) rather than quoted strings; plain-string
        buses keep working either way.
        """
        return self._schema

    @property
    def buses(self) -> set[str]:
        """Every bus name referenced anywhere in the program tree."""
        return self._body.buses()

    @property
    def variables(self) -> list[Variable]:
        """The [`Variable`][qprogram.Variable] s declared on this program, in declaration order."""
        return list(self._variables)

    @property
    def source_map(self) -> dict[tuple[int | str, ...], int]:
        """The ``.qp`` source map: structural path → 1-based line in the parsed file.

        Filled by ``loads()`` / ``load()`` for every node in the ``body:`` section (paths follow
        `qprogram.paths` — ``()`` is the body, ints index ``elements``, ``arm:<i>`` /
        ``else`` / ``loop:<i>`` address conditional arms and parallel loop headers). Empty for
        programs built in Python and after `expand`. Because the ``.qp`` round-trip
        preserves structure, a `path` computed against a built program
        looks up directly in ``loads(dumps(p)).source_map``. Fragment-internal statements are not
        mapped (diagnostics always target the expanded body).
        """
        return dict(self._qp_source_map)

    @property
    def fragments(self) -> dict[str, Fragment]:
        """The [`Fragment`][qprogram.Fragment] s used by this program, keyed by name.

        Populated by `call` (including each fragment's own dependencies, registered first) and
        by ``loads()`` for every ``fragment`` section in a ``.qp`` file. Iteration order is
        topological: a fragment always appears before any fragment that calls it.
        """
        return dict(self._fragments)

    @property
    def _active_block(self) -> Block:
        """The block a new operation is appended to: the innermost block still open on the stack."""
        return self._block_stack[-1]

    def _append_to_active(self, element: Block | Operation) -> None:
        """Append an op or block to the currently active block.

        Also closes an open ``if_`` chain when the new append lands at its parent level — anything
        other than ``elif_`` / ``else_`` at that level makes the chain ambiguous. The chain stays open
        while appends happen inside an arm body (a deeper level of the block stack).
        ``_ElifContext`` / ``_ElseContext`` bypass this method: they mutate the existing
        [`Conditional`][qprogram.blocks.Conditional] in place and push a new arm body themselves.

        Args:
            element (Block | Operation): The node to append to the active block.
        """
        if self._pending_conditional is not None and self._active_block is self._pending_conditional[1]:
            self._pending_conditional = None
        self._active_block.append(element)

    # --- Measurement handles ---

    def measurement_handles(self) -> list[MeasurementHandle]:
        """Return the canonical [`MeasurementHandle`][qprogram.MeasurementHandle] for every measurement in the AST.

        Walks the body in declaration order and returns ``op.handle`` for each
        `MeasurementOperation`. The returned handles are the *same Python instances* the AST
        stores — writing per-measurement values via ``handle._set_value(...)`` is immediately visible
        to every [`MeasurementRef`][qprogram.MeasurementRef], whether the program was built in Python or loaded from a
        ``.qp`` file.

        Returns:
            One handle per measurement, in declaration order.
        """
        return [op.handle for op in _walk_measurement_ops(self._body)]

    def _allocate_measurement_name(self, bus: str, requested: str | None) -> str:
        """Choose a unique name for a new measurement.

        If ``requested`` is given, it is validated for uniqueness and used as-is. Otherwise an
        auto-name is generated: for a [`BusRef`][qprogram.BusRef], the prefix is the bus path followed by
        ``/m`` and a per-bus counter (``q0/readout/m0``, ``q0/readout/m1``, ...); for raw-string buses,
        a global ``m0``, ``m1``, ... counter shared across all raw-string measurements.

        Counters are derived from the AST on each call rather than stored on the program — this keeps
        `copy.deepcopy`, ``with_waveforms``, and ``loads``/``dumps`` round-trips free of hidden
        state, at the cost of one AST walk per measurement construction.

        Args:
            bus (str): The bus the measurement runs on, which decides the auto-name prefix.
            requested (str | None): An explicit name, or ``None`` to allocate one.

        Returns:
            The name for the new measurement's handle, unique within this program.

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
        id: str,  # ruff: ignore[builtin-argument-shadowing]
        *,
        label: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> Variable:
        """Declare a new [`Variable`][qprogram.Variable] on this program.

        Args:
            id (str): Short identifier matching ``[A-Za-z_][A-Za-z0-9_]*``. Doubles as the ``.qp``
                identifier and must be unique within the program.
            label (str | None): Human-readable name for plots and results.
            units (str | None): Unit string (e.g. ``"Hz"``, ``"ns"``).
            description (str | None): Longer free-form description.

        Returns:
            The new [`Variable`][qprogram.Variable].

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
        """Register a [`VendorNamespace`][qprogram.VendorNamespace] subclass under ``name``.

        After registration, ``program.<name>`` returns the namespace on any [`QProgram`][qprogram.QProgram] instance.

        Re-registering the *same* namespace class under the same name is a no-op (import-time
        side-effect modules may run twice); registering a different class under a taken name is
        an error — silently replacing another vendor's namespace would be a supply-chain hazard.

        Args:
            name (str): Vendor identifier (also used as the dot-prefix in ``.qp`` operation names).
                Must not be a reserved keyword, the ``"core"`` sentinel, or the name of any
                [`QProgram`][qprogram.QProgram] attribute (which would make the namespace unreachable — vendor
                lookup happens in ``__getattr__``, after normal attribute resolution).
            namespace_cls (type[VendorNamespace]): The [`VendorNamespace`][qprogram.VendorNamespace] subclass
                to instantiate lazily.

        Raises:
            ValueError: If ``name`` is reserved, shadows a ``QProgram`` attribute, or is already
                registered to a different namespace class.
        """
        existing = cls._vendor_registry.get(name)
        if existing is namespace_cls:
            # idempotent re-registration
            return
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
        """Resolve ``program.<vendor>`` to its registered namespace, caching it on the instance.

        Only reached when normal attribute lookup fails, which is why a vendor may not be named
        after a [`QProgram`][qprogram.QProgram] attribute. Underscore-prefixed names are refused immediately, so
        protocol probes (``__deepcopy__``, ``__getstate__``) fail fast instead of reaching the
        registry.

        Args:
            name (str): The attribute normal lookup did not find.

        Returns:
            The vendor namespace for ``name``, bound to this program.

        Raises:
            AttributeError: If ``name`` is private, or no vendor is registered under it.
        """
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
        """Reject a bus reference that belongs to a different schema than this program's.

        Plain strings and [`BusRef`][qprogram.BusRef] s carrying no schema metadata pass through
        unchecked. A schema-backed ref is compared against the program's schema: a program with no
        schema yet adopts the ref's, and a ref from any other schema is refused.

        This catches a ``schema2.q[0].drive`` ref held onto and then used on a program built with
        ``schema=schema1``. Such a program serializes cleanly, but its semantics differ silently
        from what was written — better to reject it loudly.

        Args:
            bus (str): The bus value handed to a builder method.

        Raises:
            ValidationError: If ``bus`` is a schema-backed reference produced by a different
                [`BusSchema`][qprogram.BusSchema] than the one attached to this program.
        """
        if not isinstance(bus, BusRef):
            return
        if not bus.element or not bus.kind:
            # opaque/manually-constructed BusRef with no schema metadata
            return
        if bus.schema is None:
            # no producer recorded — defer to other validators
            return
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
        """Append a [`Play`][qprogram.operations.Play] op — play a waveform on a bus.

        Args:
            bus (str): Bus to play on.
            waveform (Waveform | IQWaveform | str): Concrete waveform, or a string alias resolved
                later by `with_waveforms`.

        Raises:
            ValidationError: If ``bus`` comes from another schema, or a concrete waveform's channel
                count does not match the bus's (an IQ pulse on a single-channel bus, or vice versa).
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
        fields: Iterable[MeasurementField] = (MeasurementField.IQ,),
    ) -> MeasurementHandle:
        """Play a readout pulse, acquire the result, and return a stable handle.

        Args:
            bus (str): Readout bus (must have ``acquires=True``).
            waveform (IQWaveform | str): Readout pulse — concrete
                [`IQWaveform`][qprogram.waveforms.IQWaveform] or a string alias.
            weights (IQWaveform | str): Integration weights — same shape options as ``waveform``.
            name (str | None): Explicit handle name. When omitted, an auto-name is allocated using
                the convention described on `_allocate_measurement_name`.
            fields (Iterable[MeasurementField], optional): Which measurement fields to produce — an
                iterable of `MeasurementField` members (registered field-name
                strings are also accepted, which is how vendors extend the set). Default
                ``(MeasurementField.IQ,)``; `STATE` requests
                classification, `RAW` the raw ADC trace. Order and
                duplicates don't matter — the stored tuple is canonical. An unknown field name
                raises [`ValidationError`][qprogram.ValidationError] here, at the call site.

        Returns:
            The [`MeasurementHandle`][qprogram.MeasurementHandle] identifying this measurement; pass it to
            ``result.get(...)``.

        Raises:
            ValidationError: If ``bus`` has no ADC, comes from another schema, a waveform's channel
                count does not match the bus's, ``name`` collides with another measurement, or
                ``fields`` is a bare string, is not iterable, requests nothing, or names something
                other than a registered field.
        """
        self._validate_bus(bus)
        _validate_acquires(bus)
        _validate_waveform_channel(bus, waveform)
        _validate_waveform_channel(bus, weights)
        allocated = self._allocate_measurement_name(bus, requested=name)
        handle = MeasurementHandle(allocated)
        handle._auto_named = name is None
        self._append_to_active(
            Measure(bus=bus, waveform=waveform, weights=weights, handle=handle, fields=fields),
        )
        return handle

    def wait(self, bus: str, duration: int | Expression) -> None:
        """Append a [`Wait`][qprogram.operations.Wait] — idle on ``bus`` for ``duration`` ns.

        Args:
            bus (str): Bus to idle on.
            duration (int | Expression): Wait duration in nanoseconds. Accepts an
                [`Expression`][qprogram.Expression] for sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(Wait(bus=bus, duration=duration))

    def sync(self, buses: list[str] | None = None) -> None:
        """Append a [`Sync`][qprogram.operations.Sync] — synchronize buses to a common time reference.

        Args:
            buses (list[str] | None): Buses to sync, or ``None`` to sync every bus currently active
                in the program.

        Raises:
            ValidationError: If ``buses`` is an empty list — ambiguous between "sync nothing"
                and "sync everything"; pass ``None`` for the sync-all form. Also if a listed bus
                comes from another schema.
        """
        # The user-facing keyword is ``buses`` for readability; the AST attribute is ``targets``
        # (see `Sync`).
        if buses is not None and len(buses) == 0:
            msg = "sync([]) is ambiguous; pass None (or no argument) to sync all buses"
            raise ValidationError(msg)
        if buses:
            for b in buses:
                self._validate_bus(b)
        self._append_to_active(Sync(targets=buses))

    def set_frequency(self, bus: str, frequency: float | Expression) -> None:
        """Append a [`SetFrequency`][qprogram.operations.SetFrequency] — retune the NCO on ``bus``.

        Args:
            bus (str): Bus whose oscillator to retune.
            frequency (float | Expression): New frequency in Hz. Accepts an
                [`Expression`][qprogram.Expression] for sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetFrequency(bus=bus, frequency=frequency))

    def set_phase(self, bus: str, phase: float | Expression) -> None:
        """Append a [`SetPhase`][qprogram.operations.SetPhase] — set the NCO phase on ``bus``.

        Args:
            bus (str): Bus whose oscillator phase to set.
            phase (float | Expression): Phase in radians. Accepts an
                [`Expression`][qprogram.Expression] for sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetPhase(bus=bus, phase=phase))

    def reset_phase(self, bus: str) -> None:
        """Append a [`ResetPhase`][qprogram.operations.ResetPhase] — reset the NCO phase on ``bus`` to zero.

        Args:
            bus (str): Bus whose oscillator phase to reset.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(ResetPhase(bus=bus))

    def set_gain(self, bus: str, gain: float | Expression) -> None:
        """Append a [`SetGain`][qprogram.operations.SetGain] — set the output gain on ``bus``.

        Args:
            bus (str): Bus whose output gain to set.
            gain (float | Expression): New gain. Accepts an [`Expression`][qprogram.Expression] for
                sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetGain(bus=bus, gain=gain))

    def set_offset(
        self,
        bus: str,
        offset_path0: float | Expression,
        offset_path1: float | Expression | None = None,
    ) -> None:
        """Append a [`SetOffset`][qprogram.operations.SetOffset] — set DC offset on one or both paths of ``bus``.

        Args:
            bus (str): Bus whose DC offset to set.
            offset_path0 (float | Expression): Offset on path 0 (the only path for single-channel
                buses, I for IQ buses).
            offset_path1 (float | Expression | None): Offset on path 1 (Q for IQ buses). ``None``
                leaves that path's offset unchanged.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetOffset(bus=bus, offset_path0=offset_path0, offset_path1=offset_path1))

    def set_parameter(
        self,
        bus: str,
        parameter: str,
        value: float | Expression,
    ) -> None:
        """Append a [`SetParameter`][qprogram.operations.SetParameter] — write a bus-scoped parameter.

        A parameter write is platform configuration rather than a real-time instruction, so
        platforms expose it host-side only.

        Args:
            bus (str): The bus whose parameter is written.
            parameter (str): Name of the parameter to write.
            value (float | Expression): New value. Accepts an [`Expression`][qprogram.Expression] for
                sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetParameter(bus=bus, parameter=parameter, value=value))

    def get_parameter(self, bus: str, parameter: str) -> Variable:
        """Append a [`GetParameter`][qprogram.operations.GetParameter] and return the freshly-declared variable.

        Derives a unique variable id from ``f"{bus}_{parameter}"``, replacing non-word characters
        with underscores and appending a numeric suffix on collision; the original
        ``bus.parameter`` form is kept on the variable's `label` for traceability.

        Args:
            bus (str): The bus whose parameter is read.
            parameter (str): Name of the parameter to read.

        Returns:
            The [`Variable`][qprogram.Variable] the runtime populates with the read value.

        Raises:
            ValidationError: If ``bus`` comes from another schema, or the derived id is not a valid
                [`Variable`][qprogram.Variable] id — which is what a bus or parameter name carrying letters or
                digits outside ASCII produces.
        """
        self._validate_bus(bus)
        base = _sanitize_id(f"{bus}_{parameter}")
        existing = {v.id for v in self._variables}
        var_id = base
        n = 2
        while var_id in existing:
            var_id = f"{base}_{n}"
            n += 1
        var = self.variable(var_id, label=f"{bus}.{parameter}")
        self._append_to_active(GetParameter(variable=var, bus=bus, parameter=parameter))
        return var

    # --- Fragments ---

    def call(self, fragment: Fragment, *args: object, **kwargs: object) -> None:
        """Instantiate a [`Fragment`][qprogram.Fragment] at the current position.

        Appends a first-class [`Call`][qprogram.operations.Call] node — the fragment definition and
        the call site both survive serialization and round-trip through ``.qp``. Use
        `expand` to lower every call into the substituted fragment body.

        Arguments bind to the fragment's parameters with the Python calling convention (positional
        in declaration order, then keywords). Accepted values: numbers, expressions/variables,
        buses (strings or [`BusRef`][qprogram.BusRef]), and waveforms.

        The fragment (and, transitively, any fragment it calls) is registered on this program so
        the ``.qp`` writer can emit its definition.

        Args:
            fragment (Fragment): The fragment to call.
            *args (object): Positional arguments, bound in parameter declaration order.
            **kwargs (object): Keyword arguments, bound by parameter name.

        Raises:
            ValidationError: On a non-Fragment argument, a binding error (missing/extra/duplicate
                parameter, unsupported value type), a name clash with a different fragment already
                used by this program, a schema mismatch, or a call cycle.
        """
        from qprogram.fragments import Fragment, bind_arguments  # ruff: ignore[import-outside-top-level]
        from qprogram.operations.call import Call  # ruff: ignore[import-outside-top-level]

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

        A program and the fragments it calls must agree on a single [`BusSchema`][qprogram.BusSchema]
        so the ``.qp`` writer's one ``schema:`` section can resolve every bus path.

        Args:
            fragment (Fragment): The fragment about to be called.

        Raises:
            ValidationError: If the fragment and this program were built against different schemas.
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
        """Record ``fragment`` (dependencies first) in `_fragments`; detect cycles and clashes.

        Args:
            fragment (Fragment): The fragment to register.
            _stack (tuple[str, ...]): Names of the fragments whose registration is still in
                progress, outermost first; a name reappearing in it is a call cycle.

        Raises:
            ValidationError: If registering ``fragment`` closes a call cycle, or a different
                fragment is already registered under its name.
        """
        from qprogram.operations.call import Call  # ruff: ignore[import-outside-top-level]

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

    @overload
    def sweep(self, variable: Variable) -> _SweepBuilder: ...

    @overload
    def sweep(self, variable: Variable, source: SweepSource) -> _LoopContext: ...

    def sweep(self, variable: Variable, source: SweepSource | _Unset = _UNSET) -> _SweepBuilder | _LoopContext:
        """Open a [`Sweep`][qprogram.blocks.Sweep] binding ``variable`` to a source's values.

        The DSL's only loop. What varies between a linear ramp, an explicit table, a log-spaced set
        and a composed pattern is the *source*, not the block — and there are two equal-billing ways
        to say which source.

        Pick the values fluently, which is the shorter spelling and needs no source class in scope::

            with program.sweep(freq).from_range(4e9, 6e9, 1e6):
                ...
            with program.sweep(amp).from_linspace(0.0, 1.0, num=101):
                ...
            with program.sweep(det).from_logspace(1e6, 1e9, num=50):
                ...
            with program.sweep(phi).from_values(calibrated_phases):
                ...
            with program.sweep(phi).from_file("phases.npy"):
                ...
            with program.sweep(phi).from_values(base).rotate(by=1).repeat(3):
                ...

        Or pass the source object, which is what you want when you are *computing* the source rather
        than writing it out — and the form that reaches combinator nestings the fluent
        `rotate` / `repeat` shortcuts don't::

            with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):
                ...
            with program.sweep(phi, qp.Concat(qp.Rotate(base, by=i) for i in range(4))):
                ...
            with program.sweep(freq, source):  # held in a variable, from a scan spec, ...
                ...

        Both build the identical [`Sweep`][qprogram.blocks.Sweep] node and serialize to the identical
        ``.qp`` line. Every registered source has a fluent builder, vendor sources included — see
        `_SweepBuilder`.

        Use ``|`` on the returned context manager to compose several sweeps into a
        [`Parallel`][qprogram.blocks.Parallel] block that advances them in lockstep.

        Args:
            variable (Variable): The [`Variable`][qprogram.Variable] rebound each iteration.
            source (SweepSource, optional): A [`SweepSource`][qprogram.SweepSource]. A bare 1-D
                sequence is accepted as shorthand for [`Values`][qprogram.Values]. Omit it to
                get a `_SweepBuilder` and pick the values with a ``from_*`` method instead.

        Returns:
            A context manager opening the sweep block, or — when ``source`` is omitted — the
            `_SweepBuilder` that produces one.

        Raises:
            ValidationError: If ``source`` is given but is neither a sweep source nor a 1-D sequence
                of values.
        """
        if isinstance(source, _Unset):
            return _SweepBuilder(self, variable)
        return _LoopContext(self, Sweep(variable=variable, source=source))

    def average(self, shots: int) -> _AverageContext:
        """Open an averaging block that repeats its body ``shots`` times and averages the results.

        Args:
            shots (int): How many times to repeat the body.

        Returns:
            The context manager that opens the [`Average`][qprogram.blocks.Average] block.
        """
        return _AverageContext(self, shots)

    def block(self) -> _BlockContext:
        """Open a generic grouping block — a container that carries no semantics of its own.

        Returns:
            The context manager that opens the [`Block`][qprogram.blocks.Block].
        """
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

        The producing measurement **must** request state classification (``fields`` must include
        `STATE`); the validator emits ``missing-classification``
        otherwise.

        Args:
            condition (Expression): A [`Comparison`][qprogram.Comparison] between a
                [`MeasurementRef`][qprogram.MeasurementRef] (from ``handle.state``) and an ``int`` literal.
                That is the only accepted shape.

        Returns:
            The context manager that opens the conditional's first arm.

        Raises:
            ValidationError: If ``condition`` is anything other than a comparison of a
                measurement-state reference against an ``int`` literal.
        """
        self._validate_conditional_condition(condition, where="if_")
        return _IfContext(self, condition)

    def elif_(self, condition: Expression) -> _ElifContext:
        """Extend the open ``if_`` chain with another arm.

        Must appear immediately after the matching ``if_()`` / ``elif_()`` at the same nesting level;
        any other append in between closes the chain. Condition shape is the same as `if_`.

        Args:
            condition (Expression): The arm's condition, in the shape `if_` documents.

        Returns:
            The context manager that opens the new arm.

        Raises:
            ValidationError: If ``condition`` has the wrong shape, no conditional chain is open at
                this nesting level, or the chain already has an ``else_()`` arm.
        """
        self._validate_conditional_condition(condition, where="elif_")
        return _ElifContext(self, condition)

    def else_(self) -> _ElseContext:
        """Close the open ``if_`` chain with an unconditional arm.

        Must appear immediately after the matching ``if_()`` / ``elif_()`` at the same nesting level.
        At most one ``else_()`` per chain.

        Returns:
            The context manager that opens the ``else`` body.

        Raises:
            ValidationError: If no conditional chain is open at this nesting level, or the chain
                already has an ``else_()`` arm.
        """
        return _ElseContext(self)

    @staticmethod
    def _validate_conditional_condition(condition: Expression, *, where: str) -> None:
        """Reject a conditional condition outside the supported shape.

        The supported shape is a single [`Comparison`][qprogram.Comparison] whose operands are
        [`MeasurementRef`][qprogram.MeasurementRef] (from ``handle.state``) or [`Constant`][qprogram.Constant] (an
        ``int`` literal), with at least one [`MeasurementRef`][qprogram.MeasurementRef]. Every arm of every chain goes
        through this one gate, so it is the single place the accepted shape is defined.

        Args:
            condition (Expression): The condition expression.
            where (str): The user-facing call site name (``"if_"`` / ``"elif_"``) for error
                messages.

        Raises:
            ValidationError: If ``condition`` is not a Comparison, has no MeasurementRef, has an
                operand outside the allowed types, or compares against a non-int Constant.
        """
        from qprogram.variable import Constant, MeasurementRef  # ruff: ignore[import-outside-top-level]

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
        """Return a deep copy with every fragment [`Call`][qprogram.operations.Call] inlined.

        The canonical lowering from the composed form to a fragment-free program: each call site is
        replaced by a plain [`Block`][qprogram.blocks.Block] containing the fragment body with parameters substituted
        by the bound arguments. Fragment-local variables are hygienically renamed onto this program
        (``{fragment}_{id}``, numeric suffix on collision); colliding measurement names gain a
        ``_2`` / ``_3`` suffix (the shared handle is renamed, keeping ``handle.state`` conditionals
        consistent). Nested calls expand recursively; expansion is deterministic, so expanding twice
        yields structurally equal programs.

        A program with no calls is deep-copied and returned unchanged in structure.

        Returns:
            A new, fragment-free [`QProgram`][qprogram.QProgram]; the original is untouched. Its fragment
            registry and its ``.qp`` source map are both empty either way, because expansion
            restructures the tree the recorded paths address.

        Raises:
            ValidationError: On a fragment call cycle or a binding used in an incompatible
                position (e.g. a waveform bound to a parameter used inside arithmetic).
        """
        from qprogram.fragments import expand_program  # ruff: ignore[import-outside-top-level]

        return expand_program(self)

    def rebind(
        self,
        *,
        schema: BusSchema | None = None,
        elements: Mapping[tuple[str, int | tuple[int, ...]], tuple[str, int | tuple[int, ...]]] | None = None,
        naming: BusNaming | None = None,
        strings: Mapping[str, str] | None = None,
        allow_unported_strings: bool = False,
    ) -> QProgram:
        """Return a copy of this program with its bus references re-resolved structurally.

        Rather than rewriting bus *strings*, ``rebind`` re-resolves every schema-backed
        [`BusRef`][qprogram.BusRef] through a schema factory, so the result stays a typed ``BusRef``
        (serializing as a ``q[1].drive`` path, not a quoted string) and can re-index a qubit, move to a
        different element, swap naming conventions, or move onto another chip's schema — all checked
        against the schema (an absent bus kind raises ``AttributeError``).

        Auto-allocated measurement names embed the bus (``q0/readout/m0``); ``rebind`` re-derives them for
        the rebound buses while leaving user-supplied names untouched (see
        [`MeasurementHandle`][qprogram.MeasurementHandle]). Fragment calls are expanded first.

        Args:
            schema (BusSchema | None): Target schema. Defaults to the program's current schema
                (re-index within one chip).
            elements (Mapping[tuple[str, int | tuple[int, ...]], tuple[str, int | tuple[int, ...]]] | None):
                Maps ``(element, idx)`` to ``(element, idx)`` — e.g. ``{("q", 0): ("q", 1)}`` to port
                qubit 0's operations onto qubit 1. Unlisted ``(element, idx)`` pairs pass through.
            naming (BusNaming | None): Re-resolve every ref under a new
                [`BusNaming`][qprogram.BusNaming] (cross-platform names).
            strings (Mapping[str, str] | None): Escape hatch for raw-string buses (which carry no
                schema metadata): an old→new map. Map a string to itself to mark it intentionally
                untouched.
            allow_unported_strings (bool, optional): When ``False`` (default), a raw-string bus not
                covered by ``strings`` raises — a partial port is a loud choice, not a silent
                accident. Set ``True`` to leave uncovered raw-string buses in place.

        Returns:
            A new [`QProgram`][qprogram.QProgram]; the original is untouched.

        Raises:
            ValidationError: If ``naming`` is given without a schema, or raw-string buses are left
                unported without ``allow_unported_strings``.
            AttributeError: If a rebound ``(element, idx, kind)`` does not resolve against the target
                schema (e.g. the target element lacks that bus kind).
            KeyError: If the target schema's naming pattern names a placeholder other than
                ``{element}``, ``{index}`` or ``{kind}``.
            ValueError: If the target schema's naming pattern is not a well-formed format string.
        """
        program = self.expand() if self.fragments else copy.deepcopy(self)
        target_schema = schema if schema is not None else program._schema
        if naming is not None:
            if target_schema is None:
                msg = "rebind(naming=...) requires the program to have a schema to re-resolve against"
                raise ValidationError(msg)
            target_schema = naming_substituted_schema(target_schema, naming)
        element_map = dict(elements or {})
        string_map = dict(strings or {})
        unported: set[str] = set()

        # swap in lockstep; None stays None for raw-string programs
        program._schema = target_schema

        for op in program._body.walk():
            if not isinstance(op, Operation):
                continue
            for attr_name in op.BUS_ATTRS:
                value = getattr(op, attr_name, None)
                if isinstance(value, list):
                    setattr(
                        op,
                        attr_name,
                        [_rebind_bus(b, target_schema, element_map, string_map, unported) for b in value],
                    )
                elif value is not None:
                    setattr(op, attr_name, _rebind_bus(value, target_schema, element_map, string_map, unported))

        if unported and not allow_unported_strings:
            names = ", ".join(repr(b) for b in sorted(unported))
            msg = (
                f"rebind left raw-string bus(es) unported: {names}. Raw strings carry no schema metadata "
                f"to re-resolve — map them via strings={{...}} (map a name to itself to keep it), or pass "
                f"allow_unported_strings=True to leave them in place."
            )
            raise ValidationError(msg)

        program._rederive_auto_measurement_names()

        for op in program._body.walk():
            if not isinstance(op, Operation):
                continue
            for attr_name in op.BUS_ATTRS:
                value = getattr(op, attr_name, None)
                if isinstance(value, list):
                    for bus in value:
                        program._validate_bus(bus)
                elif value is not None:
                    program._validate_bus(value)

        return program

    def with_waveforms(
        self,
        waveforms: WaveformLibrary | Mapping[str, Waveform | IQWaveform],
    ) -> QProgram:
        """Return a copy with string waveform names resolved to concrete waveforms, scoped per bus.

        For each operation whose waveform attribute is still a string, the name is looked up against
        ``waveforms`` *for that operation's bus* — so a shared name like ``"pi_pulse"`` can resolve to a
        different concrete pulse on ``q[0].drive`` than on ``q[1].drive``. Concrete waveforms and names
        with no matching entry pass through unchanged. Each replacement re-runs the channel-type check,
        so an IQ pulse landing on a single-channel bus is caught here rather than at the hardware compiler.

        Args:
            waveforms (WaveformLibrary | Mapping[str, Waveform | IQWaveform]): A
                [`WaveformLibrary`][qprogram.WaveformLibrary] (resolved per bus), or a plain
                ``{name: waveform}`` mapping (one global tier, resolved on every bus).

        Returns:
            A new [`QProgram`][qprogram.QProgram] with matching names replaced; the original is untouched.

        Raises:
            ValidationError: If a resolved waveform's channel count does not match its bus's, or if
                ``waveforms`` is a mapping keyed by anything other than non-empty strings.
        """
        library = waveforms if isinstance(waveforms, WaveformLibrary) else WaveformLibrary.from_mapping(waveforms)
        new_program = copy.deepcopy(self)
        _resolve_waveforms(new_program._body, library)
        return new_program

    def _rederive_auto_measurement_names(self) -> None:
        """Recompute auto-allocated measurement names from each op's (possibly rebound) bus.

        User-supplied names (``handle._auto_named is False``) are reserved and never rewritten. Auto
        names are re-derived in declaration order against the reserved set plus already-assigned auto
        names, reproducing `_allocate_measurement_name`'s per-prefix counter. Mutating
        ``handle.name`` in place keeps every [`MeasurementRef`][qprogram.MeasurementRef] pointing at it consistent.
        """
        measurement_ops = _walk_measurement_ops(self._body)
        used: set[str] = {op.handle.name for op in measurement_ops if not op.handle._auto_named}
        for op in measurement_ops:
            handle = op.handle
            if not handle._auto_named:
                continue
            prefix = _measurement_name_prefix(getattr(op, "bus", ""))
            n = 0
            while f"{prefix}{n}" in used:
                n += 1
            handle.name = f"{prefix}{n}"
            used.add(handle.name)


def _rebind_bus(
    bus: str,
    target_schema: BusSchema | None,
    element_map: Mapping[tuple[str, int | tuple[int, ...]], tuple[str, int | tuple[int, ...]]],
    string_map: Mapping[str, str],
    unported: set[str],
) -> str:
    """Re-resolve one bus value during [`QProgram.rebind`][qprogram.QProgram.rebind].

    Schema-backed [`BusRef`][qprogram.BusRef] buses are re-resolved through ``target_schema`` after applying
    the ``(element, idx)`` remap. Raw strings (and metadata-less BusRefs) use the ``string_map``; any not
    covered there are recorded in ``unported`` for the caller to report.

    Args:
        bus (str): The bus value to re-resolve.
        target_schema (BusSchema | None): Schema to resolve refs against, or ``None`` for a program
            built from raw strings.
        element_map (Mapping[tuple[str, int | tuple[int, ...]], tuple[str, int | tuple[int, ...]]]):
            The ``(element, idx)`` remap; missing keys pass through unchanged.
        string_map (Mapping[str, str]): Old→new names for raw-string buses.
        unported (set[str]): Collects raw-string buses no mapping covered. Mutated in place.

    Returns:
        The rebound bus: a typed [`BusRef`][qprogram.BusRef] for schema-backed input, the mapped name
        for a covered raw string, and the original value otherwise.

    Raises:
        AttributeError: If the remapped element, or the ref's bus kind, is not declared on
            ``target_schema``.
        KeyError: If ``target_schema``'s naming pattern names a placeholder other than
            ``{element}``, ``{index}`` or ``{kind}``.
        ValueError: If ``target_schema``'s naming pattern is not a well-formed format string.
    """
    if isinstance(bus, BusRef) and bus.element and bus.kind and target_schema is not None:
        new_element, new_idx = element_map.get((bus.element, bus.idx), (bus.element, bus.idx))
        return resolve_ref(target_schema, new_element, new_idx, bus.kind)
    if bus in string_map:
        return string_map[bus]
    unported.add(str(bus))
    return bus


def _resolve_waveforms(block: Block, library: WaveformLibrary) -> None:
    """Resolve string waveform names to concrete waveforms in place, scoped per bus.

    Walks via `Block.walk` and uses each op's `WAVEFORM_ATTRS` so vendor ops with custom
    waveform attribute names work as long as they declare them. Each replacement is channel-validated
    against the op's bus.

    Args:
        block (Block): Root of the tree to rewrite. Mutated in place.
        library (WaveformLibrary): Where names are looked up, scoped by the op's bus.

    Raises:
        ValidationError: If a resolved waveform's channel count does not match its bus's.
    """
    for op in block.walk():
        if not isinstance(op, Operation):
            continue
        bus = getattr(op, "bus", "")
        for attr_name in op.WAVEFORM_ATTRS:
            value = getattr(op, attr_name, None)
            if isinstance(value, str):
                resolved = library.get(bus, value)
                if resolved is not None:
                    _validate_waveform_channel(bus, resolved)
                    setattr(op, attr_name, resolved)


def _validate_waveform_channel(bus: str, waveform: Waveform | IQWaveform | str) -> None:
    """Validate the waveform's channel kind against the bus's declared channel.

    Raw-string buses and string-alias waveforms skip validation — there's no channel metadata to
    check. Schema-bound [`BusRef`][qprogram.BusRef] + concrete waveform mismatches raise.

    Args:
        bus (str): The bus the waveform is destined for.
        waveform (Waveform | IQWaveform | str): The waveform or alias being placed on it.

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
    """Return every `MeasurementOperation` in ``block`` in declaration order.

    Args:
        block (Block): Root of the tree to walk.

    Returns:
        The measurement operations, in the pre-order `Block.walk` yields them.
    """
    return [node for node in block.walk() if isinstance(node, MeasurementOperation)]


def _measurement_name_prefix(bus: str) -> str:
    """Compute the auto-name prefix for a measurement on ``bus``.

    Schema-backed buses get a per-bus prefix (``{bus}/m``); raw-string buses share a global ``m``
    prefix. The caller appends a free integer to produce the final name.

    Args:
        bus (str): The bus the measurement runs on.

    Returns:
        ``{bus}/m`` for a schema-backed bus, ``m`` for a raw-string one.
    """
    if isinstance(bus, BusRef):
        return f"{bus}/m"
    return "m"


def _sanitize_id(s: str) -> str:
    """Derive a [`Variable`][qprogram.Variable] id from an arbitrary string.

    Replaces every non-word character with ``_``, prefixes a leading underscore if the first
    character is a digit, and falls back to ``"var"`` for empty input. The character class is
    Unicode-aware, so an ASCII input yields an id matching ``[A-Za-z_][A-Za-z0-9_]*`` but letters
    and digits outside ASCII survive untouched — and [`Variable`][qprogram.Variable] rejects those. Uniqueness
    is the caller's problem: two different strings can sanitize to the same id.

    Args:
        s (str): The string to sanitize, such as ``"q[0].drive_frequency"``.

    Returns:
        The sanitized string — an id matching ``[A-Za-z_][A-Za-z0-9_]*`` whenever ``s`` is ASCII.
    """
    out = re.sub(r"\W", "_", s) if s else ""
    if not out:
        return "var"
    if out[0].isdigit():
        out = "_" + out
    return out


def _validate_acquires(bus: str) -> None:
    """Reject a bus that cannot acquire, so ``measure()`` fails at the call site.

    Raw-string buses pass through: without schema metadata there is nothing to check.

    Args:
        bus (str): The bus a measurement is about to be appended to.

    Raises:
        ValidationError: If ``bus`` is a schema-backed reference whose element has no ADC
            (``acquires=False``).
    """
    if not isinstance(bus, BusRef):
        return
    if not bus.acquires:
        msg = (
            f"Bus '{bus}' does not support acquisition (acquires=False). "
            f"measure() can only be called on buses with an ADC (e.g. readout buses)."
        )
        raise ValidationError(msg)
