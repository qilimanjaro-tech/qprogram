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
"""Reference software executor — the in-tree interpreter that actually runs a QProgram.

[`ReferencePlatform`][qprogram.ReferencePlatform] is a complete [`PlatformProtocol`][qprogram.PlatformProtocol]: it
validates against a permissive capability descriptor (every core token; orchestration ops host-only, mirroring real
platforms), then walks the AST in pure Python — driving loop variables via
[`Variable.set_value`][qprogram.Variable.set_value], writing measurement outcomes onto the shared
[`MeasurementHandle`][qprogram.MeasurementHandle] (so ``handle.state`` feedback works by construction), and assembling a
[`QProgramResult`][qprogram.QProgramResult] of `xarray.DataArray` s with the result-shape contract of spec §8:

- dims = enclosing ``Sweep`` blocks, outermost first, named by variable id;
  ``Parallel`` contributes one shared ``"a|b"`` dim carrying every composed variable's coords.
- ``average(shots)`` contributes **no** dim — ``iq``/``raw`` are means over shots, ``state``
  averages to the excited-state population.
- Measurement-field shapes: ``iq`` → ``(*sweeps, "IQ"[2])``; ``state`` → ``(*sweeps)``;
  ``raw`` → ``(*sweeps, "time"[N], "IQ")``.
- A measurement inside a [`Conditional`][qprogram.blocks.Conditional] arm holds NaN at sweep points where the arm never
  executed (count-based averaging).

Measurement outcomes come from a pluggable [`MeasurementModel`][qprogram.MeasurementModel]; the default
[`MockMeasurementModel`][qprogram.MockMeasurementModel] is deterministic (seeded) and lets demos shape the response —
``response=lambda bus, env: ...`` receives the currently bound variables and platform parameters,
so a simulated Rabi oscillation is one lambda away.

This executor is the **reference semantics**: vendor compilers are tested against what it
produces. It performs no timing simulation — pulse ops validate their expressions and otherwise
act as no-ops.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import xarray as xr

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.blocks.sweep import Sweep
from qprogram.errors import UnsupportedOperationError
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.operation import MeasurementField, MeasurementOperation, Operation
from qprogram.operations.set_parameter import SetParameter
from qprogram.platform import PlatformProtocol
from qprogram.protocol import (
    BusCapabilities,
    CompilerCapabilities,
    DomainConstraint,
    PlatformCapabilities,
)
from qprogram.result import QProgramResult
from qprogram.validation import validate
from qprogram.variable import Expression, Variable
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from qprogram.buses import BusSchema
    from qprogram.protocol import Diagnostic, ValidationContext
    from qprogram.qprogram import QProgram

    #: A runtime handler for a vendor op with side effects on the parameter store. Receives the op
    #: instance and the mutable ``{"<bus-or-alias>.<parameter>": value}`` store, and stands in for the
    #: interpreter's eager expression evaluation, which is skipped for a handled op — so a get-style
    #: handler's output variable is never force-evaluated, and the handler evaluates any value
    #: expression itself. Passed to `ReferencePlatform` by platforms whose vendor ops must
    #: read/write the store during simulation (a vendor's ``set_parameter`` /
    #: ``get_parameter``).
    VendorOpHandler = Callable[[Operation, dict[str, float]], None]


class ExecutionWarning(UserWarning):
    """Category for warning-severity diagnostics surfaced during [`ReferencePlatform.execute`][qprogram.ReferencePlatform.execute]."""


# ---------------------------------------------------------------------------
# Measurement model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementSample:
    """One shot's worth of simulated measurement outcomes.

    Attributes:
        i (float): In-phase value.
        q (float): Quadrature value.
        state (int): Classified outcome, ``0`` or ``1``.
        raw (numpy.ndarray): Raw trace of shape ``(raw_samples, 2)`` (I and Q per time sample).
    """

    i: float
    q: float
    state: int
    raw: np.ndarray


@runtime_checkable
class MeasurementModel(Protocol):
    """What the executor asks of a measurement back-end — one `sample` per shot.

    ``env`` carries the currently bound loop variables (by id) and the platform parameters (by
    ``"bus.parameter"``), so a model can shape its response as a function of the sweep.
    """

    def sample(self, bus: str, env: Mapping[str, float]) -> MeasurementSample:
        """Return one shot's outcomes for a measurement on ``bus`` under ``env``.

        Args:
            bus (str): The bus the measurement runs on, as a plain string (``""`` for a measurement
                op with no bus attribute).
            env (Mapping[str, float]): Bound loop variables by id, plus platform parameters keyed
                ``"bus.parameter"``.

        Returns:
            The outcomes for a single shot.
        """
        ...


class MockMeasurementModel:
    """Deterministic mock measurement model — the executor's default.

    The noiseless IQ point comes from ``response`` (default ``0j``); per-shot gaussian noise is
    added on both quadratures. The classified ``state`` is a Bernoulli sample of
    ``p_excited`` (default ``0.0``). The ``raw`` trace replicates the IQ point over
    ``raw_samples`` time samples with per-sample noise. All randomness flows from one seeded
    generator, so identical programs produce identical results.

    Args:
        response (Callable[[str, Mapping[str, float]], complex] | None): ``(bus, env) -> complex``
            noiseless IQ response. ``env`` holds the bound loop variables and platform parameters,
            so e.g. a Rabi oscillation is
            ``lambda bus, env: np.sin(np.pi * env["g"] / 2) ** 2 + 0j``. ``None`` responds ``0j``.
        p_excited (Callable[[str, Mapping[str, float]], float] | None): ``(bus, env) -> float``
            excited-state probability for the classified outcome. ``None`` keeps every shot in the
            ground state.
        noise (float): Standard deviation of the gaussian noise added per quadrature, per shot.
            Zero skips the noise draws entirely rather than drawing zero-width ones.
        raw_samples (int): Number of time samples in the ``raw`` trace.
        seed (int): Seed for the model's private `numpy.random.default_rng`.
    """

    def __init__(
        self,
        response: Callable[[str, Mapping[str, float]], complex] | None = None,
        p_excited: Callable[[str, Mapping[str, float]], float] | None = None,
        noise: float = 0.0,
        raw_samples: int = 16,
        seed: int = 0,
    ) -> None:
        self._response = response
        self._p_excited = p_excited
        self._noise = noise
        self.raw_samples = raw_samples
        self._rng = np.random.default_rng(seed)

    def sample(self, bus: str, env: Mapping[str, float]) -> MeasurementSample:
        """Return one deterministic-given-the-seed shot for ``bus`` under ``env``.

        Args:
            bus (str): The bus the measurement runs on; forwarded to ``response`` and ``p_excited``.
            env (Mapping[str, float]): Bound loop variables by id, plus platform parameters keyed
                ``"bus.parameter"``; forwarded to ``response`` and ``p_excited``.

        Returns:
            The shot's IQ point, classified state, and raw trace.
        """
        center = complex(self._response(bus, env)) if self._response is not None else 0j
        i = center.real + (self._rng.normal(0.0, self._noise) if self._noise else 0.0)
        q = center.imag + (self._rng.normal(0.0, self._noise) if self._noise else 0.0)
        p1 = float(self._p_excited(bus, env)) if self._p_excited is not None else 0.0
        state = int(self._rng.random() < p1)
        raw = np.empty((self.raw_samples, 2), dtype=float)
        raw[:, 0] = center.real
        raw[:, 1] = center.imag
        if self._noise:
            raw += self._rng.normal(0.0, self._noise, size=raw.shape)
        return MeasurementSample(i=i, q=q, state=state, raw=raw)


# ---------------------------------------------------------------------------
# Sweep axes — the static shape of each measurement's result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Axis:
    """One result dimension: the loop node that drives it, its dim name, size, and coords.

    Attributes:
        node (Block): The [`Sweep`][qprogram.blocks.Sweep] or [`Parallel`][qprogram.blocks.Parallel] whose
            iteration index selects a position along this dimension. Identity-keyed by the
            interpreter, so two structurally identical loops stay distinct.
        dim (str): The xarray dimension name — the swept variable's id, or the ``"a|b"`` join of
            every variable in a parallel composition.
        size (int): Number of points along the dimension.
        coords (dict[str, numpy.ndarray]): Coordinate name to values. One entry for a plain sweep;
            a parallel composition attaches one per composed variable, all on the shared dimension.
    """

    node: Block  # Sweep | Parallel
    dim: str
    size: int
    coords: dict[str, np.ndarray]  # coord name -> values (Parallel attaches one per variable)


def _axis_for(node: Block) -> _Axis | None:
    """Build the `_Axis` a block contributes to enclosed measurements, if any.

    Args:
        node (Block): Any block encountered during the setup walk.

    Returns:
        The axis for a [`Sweep`][qprogram.blocks.Sweep] or [`Parallel`][qprogram.blocks.Parallel];
        ``None`` for a block that contributes no dimension.
    """
    if isinstance(node, Parallel):
        dim = "|".join(lp.variable.id for lp in node.loops)
        size = node.loops[0].num_iterations()
        coords = {lp.variable.id: lp.source.values() for lp in node.loops}
        return _Axis(node=node, dim=dim, size=size, coords=coords)
    if isinstance(node, Sweep):
        values = node.source.values()
        return _Axis(node=node, dim=node.variable.id, size=len(values), coords={node.variable.id: values})
    return None  # Average / Conditional / plain Block contribute no dimension


@dataclass
class _MeasurementSlot:
    """Accumulators for one measurement operation: a running sum per requested measurement field.

    Attributes:
        op (MeasurementOperation): The measurement this slot accumulates for.
        axes (tuple[_Axis, ...]): The enclosing loop axes, outermost first. Their sizes are the
            shape of every accumulator here.
        sums (dict[str, numpy.ndarray]): Running sum per requested field name, shaped
            ``(*axis sizes, *field trailing dims)``.
        counts (numpy.ndarray): Shots executed per sweep point, shared across fields. Dividing by it
            turns the sums into means; a zero entry marks a sweep point where the measurement never
            ran, which is what happens inside a conditional arm the branch did not select.
    """

    op: MeasurementOperation
    axes: tuple[_Axis, ...]
    sums: dict[str, np.ndarray]
    counts: np.ndarray  # shots executed per sweep point (shared across fields)


# ---------------------------------------------------------------------------
# The interpreter
# ---------------------------------------------------------------------------


class _Interpreter:
    """Pure-Python AST evaluator producing [`QProgramResult`][qprogram.QProgramResult] xarrays.

    Two phases: a *setup* walk computes each measurement's sweep axes (the ``Sweep``
    / ``Parallel`` blocks enclosing it, outermost first) and allocates accumulators; the *run*
    walk drives loop variables, samples the model per measurement shot, and accumulates at the
    current index of each axis.

    One instance runs one program once — the accumulators and iteration indices are per-run state.

    Args:
        program (QProgram): The program to interpret. Must already be free of fragment calls and
            validated; the interpreter assumes both.
        model (MeasurementModel): Back-end consulted once per measurement shot.
        parameters (dict[str, float]): Mutable platform parameter store keyed ``"bus.parameter"``.
            Read by ``get_parameter``, written by ``set_parameter``, and exposed to the model, so
            the caller sees the writes a run performs.
        vendor_op_handlers (Mapping[type[Operation], VendorOpHandler] | None): Per-operation-class
            runtime handlers for vendor ops with side effects on the store. An op with a handler
            bypasses the eager expression evaluation, so a get-style handler can write its output
            variable itself.
    """

    def __init__(
        self,
        program: QProgram,
        model: MeasurementModel,
        parameters: dict[str, float],
        vendor_op_handlers: Mapping[type[Operation], VendorOpHandler] | None = None,
    ) -> None:
        self._program = program
        self._model = model
        self._parameters = parameters
        self._vendor_op_handlers: Mapping[type[Operation], VendorOpHandler] = dict(vendor_op_handlers or {})
        self._raw_samples = int(getattr(model, "raw_samples", 16))
        self._slots: list[_MeasurementSlot] = []
        self._slot_by_op: dict[int, _MeasurementSlot] = {}
        self._indices: dict[int, int] = {}  # id(axis.node) -> current iteration index

    # -- setup ---------------------------------------------------------------

    def _setup(self) -> None:
        """Allocate one accumulator slot per measurement, sized from its enclosing loops.

        A [`Conditional`][qprogram.blocks.Conditional] passes its axes straight through to every arm body:
        an arm adds no dimension of its own; it only leaves some sweep points unvisited.
        """

        def collect(block: Block, axes: tuple[_Axis, ...]) -> None:
            if isinstance(block, Conditional):
                for _, arm_body in block.arms:
                    collect(arm_body, axes)
                if block.else_body is not None:
                    collect(block.else_body, axes)
                return
            for element in block.elements:
                if isinstance(element, MeasurementOperation):
                    self._allocate_slot(element, axes)
                elif isinstance(element, Block):
                    axis = _axis_for(element)
                    collect(element, (*axes, axis) if axis is not None else axes)

        collect(self._program.body, ())

    def _allocate_slot(self, op: MeasurementOperation, axes: tuple[_Axis, ...]) -> None:
        """Create the zeroed accumulators for one measurement and index them by op identity.

        Each requested field gets the trailing dimensions its result shape calls for: ``iq`` a pair,
        ``raw`` a trace of pairs, anything else a scalar per sweep point.

        Args:
            op (MeasurementOperation): The measurement to allocate for.
            axes (tuple[_Axis, ...]): Its enclosing loop axes, outermost first.
        """
        shape = tuple(axis.size for axis in axes)
        sums: dict[str, np.ndarray] = {}
        for field_name in op.fields:
            if field_name == MeasurementField.IQ:
                sums[field_name] = np.zeros((*shape, 2))
            elif field_name == MeasurementField.RAW:
                sums[field_name] = np.zeros((*shape, self._raw_samples, 2))
            else:  # state and any vendor-registered scalar-per-shot field
                sums[field_name] = np.zeros(shape)
        slot = _MeasurementSlot(op=op, axes=axes, sums=sums, counts=np.zeros(shape))
        self._slots.append(slot)
        self._slot_by_op[id(op)] = slot

    # -- run -----------------------------------------------------------------

    def run(self) -> QProgramResult:
        """Interpret the program end to end and return its results.

        Returns:
            One [`MeasurementResult`][qprogram.MeasurementResult] per measurement operation, in the order the
            setup walk found them.

        Raises:
            UnassignedVariableError: When an operation's expression references a variable no
                enclosing loop binds.
        """
        self._setup()
        self._execute_children(self._program.body)
        return self._finalize()

    def _execute_children(self, block: Block) -> None:
        """Execute every element of ``block`` in order, dispatching blocks and operations apart.

        Args:
            block (Block): The block whose immediate children to run.
        """
        for element in block.elements:
            if isinstance(element, Block):
                self._execute_block(element)
            else:
                self._execute_operation(element)

    def _execute_block(self, block: Block) -> None:
        """Run one block, applying its repetition or branching semantics.

        A sweep binds its variable per iteration; a parallel composition advances every loop in
        lockstep under one shared index; an average re-runs its body without adding a dimension; a
        conditional picks one arm. Anything else is a plain grouping and runs its children.

        The two loop forms publish their current iteration index under ``id(block)`` while the body
        runs and drop it afterwards, which is how a measurement finds its position along each
        enclosing axis.

        Args:
            block (Block): The block to run.
        """
        if isinstance(block, Parallel):
            values = [lp.source.values() for lp in block.loops]
            for k in range(block.loops[0].num_iterations()):
                self._indices[id(block)] = k
                for lp, vals in zip(block.loops, values, strict=True):
                    lp.variable.set_value(float(vals[k]))
                self._execute_children(block)
            self._indices.pop(id(block), None)
            return
        if isinstance(block, Sweep):
            for k, value in enumerate(block.source.values()):
                self._indices[id(block)] = k
                block.variable.set_value(float(value))
                self._execute_children(block)
            self._indices.pop(id(block), None)
            return
        if isinstance(block, Average):
            for _ in range(block.shots):
                self._execute_children(block)
            return
        if isinstance(block, Conditional):
            self._execute_conditional(block)
            return
        self._execute_children(block)  # plain grouping Block

    def _execute_conditional(self, cond: Conditional) -> None:
        """Run the first arm whose condition holds, or the ``else`` body.

        Args:
            cond (Conditional): The conditional to evaluate.

        Raises:
            UnassignedVariableError: When a condition references a measurement that has not produced
                the field yet, or an unbound variable.
        """
        for condition, body in cond.arms:
            # evaluate_or_raise: a condition over a measurement that hasn't produced a state yet
            # (or an unbound variable) is a programming error and fails loudly.
            if condition.evaluate_or_raise():
                self._execute_children(body)
                return
        if cond.else_body is not None:
            self._execute_children(cond.else_body)

    def _execute_operation(self, op: Operation) -> None:
        """Run one operation, in dispatch order: parameter reads, vendor handlers, then the rest.

        Args:
            op (Operation): The operation to run.

        Raises:
            UnassignedVariableError: When one of the operation's expressions references a variable no
                enclosing loop binds.
        """
        if isinstance(op, GetParameter):
            # `variable` is the op's *output target* — written here, never read, so it is
            # exempt from the eager expression evaluation below.
            op.variable.set_value(self._parameters.get(f"{op.bus}.{op.parameter}", 0.0))
            return
        handler = self._vendor_op_handlers.get(type(op))
        if handler is not None:
            # Vendor op with runtime effects (e.g. a vendor set_parameter/get_parameter that reads or
            # writes the shared parameter store). The handler takes the place of the eager expression
            # evaluation below, which never runs for a handled op, so a get-style handler's output
            # variable is not force-evaluated; the handler evaluates any value expression itself (loop
            # variables are already bound at this point).
            handler(op, self._parameters)
            return
        _evaluate_op_expressions(op)
        if isinstance(op, MeasurementOperation):
            self._execute_measurement(op)
            return
        if isinstance(op, SetParameter):
            value = op.value.evaluate_or_raise() if isinstance(op.value, Expression) else op.value
            self._parameters[f"{op.bus}.{op.parameter}"] = float(value)
            return
        # Pulse/timing ops (play, wait, sync, set_*) and vendor ops without a handler: their
        # expressions were forced above and nothing else happens here — the reference executor
        # simulates results, not waveform physics or timing.

    def _execute_measurement(self, op: MeasurementOperation) -> None:
        """Sample the model once and accumulate the shot at the current sweep point.

        The classified state is written onto the shared handle before the accumulation, so a
        conditional later in the same iteration reads this shot's outcome.

        Args:
            op (MeasurementOperation): The measurement being executed.
        """
        sample = self._model.sample(str(op.bus) if hasattr(op, "bus") else "", self._environment())
        op.handle._set_value("state", sample.state)  # ruff: ignore[private-member-access] — the runtime contract of MeasurementHandle
        slot = self._slot_by_op[id(op)]
        index = tuple(self._indices[id(axis.node)] for axis in slot.axes)
        for field_name, accumulator in slot.sums.items():
            if field_name == MeasurementField.IQ:
                accumulator[index] += (sample.i, sample.q)
            elif field_name == MeasurementField.RAW:
                accumulator[index] += sample.raw
            elif field_name == MeasurementField.STATE:
                accumulator[index] += sample.state
            # A vendor field the model has no value for stays zero. It was accepted against the
            # platform's measure.fields.* capabilities, so it is legal to request but the reference
            # model produces only the core fields.
        slot.counts[index] += 1

    def _environment(self) -> dict[str, float]:
        """Return the environment handed to the measurement model for this shot.

        Only numeric variable values are included — an unbound variable is absent rather than present
        with a placeholder, so a model that indexes it fails loudly.

        Returns:
            The bound loop variables by id, plus the platform parameters keyed ``"bus.parameter"``.
            Parameter keys carry a dot, so they can never shadow a variable id.
        """
        env: dict[str, float] = {}
        for var in self._program.variables:
            value = var.value
            if isinstance(value, (int, float)):
                env[var.id] = float(value)
        env.update(self._parameters)
        return env

    # -- finalize --------------------------------------------------------------

    def _finalize(self) -> QProgramResult:
        """Turn the accumulators into labeled arrays and assemble the result container.

        Returns:
            One record per measurement, each carrying an array per requested field plus the primary
            ``data`` array.
        """
        result = QProgramResult()
        for slot in self._slots:
            dims = [axis.dim for axis in slot.axes]
            coords: dict[str, object] = {}
            for axis in slot.axes:
                for name, values in axis.coords.items():
                    coords[name] = (axis.dim, values) if name != axis.dim else values
            fields: dict[str, xr.DataArray] = {}
            for field_name in slot.op.fields:
                fields[field_name] = self._field_array(slot, field_name, dims, coords)
            # ``iq`` is the primary whenever requested; otherwise the first field in canonical
            # order (see MeasurementField's declaration order).
            primary = fields.get(MeasurementField.IQ, fields[slot.op.fields[0]])
            bus = str(getattr(slot.op, "bus", ""))
            result.append_measurement(bus=bus, name=slot.op.name, data=primary, fields=fields)
        return result

    def _field_array(
        self,
        slot: _MeasurementSlot,
        field_name: str,
        dims: list[str],
        coords: dict[str, object],
    ) -> xr.DataArray:
        """Average one field's accumulator and wrap it in a labeled array.

        The count is broadcast over each field's trailing dimensions, so ``iq`` and ``raw`` divide by
        the same per-sweep-point shot count as ``state``.

        Args:
            slot (_MeasurementSlot): The measurement's accumulators.
            field_name (str): Which field of ``slot.sums`` to average.
            dims (list[str]): The sweep dimension names, outermost first.
            coords (dict[str, object]): Sweep coordinates, already shaped for xarray.

        Returns:
            The field's array: ``(*sweeps, "IQ")`` for ``iq``, ``(*sweeps, "time", "IQ")`` for
            ``raw``, ``(*sweeps)`` for anything else.
        """
        counts = slot.counts
        if field_name == MeasurementField.IQ:
            mean = _safe_mean(slot.sums[field_name], counts[..., np.newaxis])
            return xr.DataArray(mean, dims=(*dims, "IQ"), coords={**coords, "IQ": ["I", "Q"]})
        if field_name == MeasurementField.RAW:
            mean = _safe_mean(slot.sums[field_name], counts[..., np.newaxis, np.newaxis])
            return xr.DataArray(
                mean,
                dims=(*dims, "time", "IQ"),
                coords={**coords, "time": np.arange(self._raw_samples), "IQ": ["I", "Q"]},
            )
        mean = _safe_mean(slot.sums[field_name], counts)
        return xr.DataArray(mean, dims=dims, coords=coords)


def _safe_mean(total: np.ndarray, count: np.ndarray) -> np.ndarray:
    """Divide ``total`` by ``count``, leaving NaN where ``count`` is zero.

    A zero count means no shot ever reached that sweep point, which happens when the measurement
    sits inside a conditional arm that the branch never selected. NaN says "not measured" rather
    than the zero a plain division would leave.

    Args:
        total (numpy.ndarray): Summed outcomes.
        count (numpy.ndarray): Shots per sweep point, broadcastable to ``total``'s shape.

    Returns:
        The element-wise mean, NaN at every zero-count position.
    """
    count_b = np.broadcast_to(count, total.shape)
    return np.divide(total, count_b, out=np.full_like(total, np.nan), where=count_b != 0)


def _evaluate_op_expressions(value: object) -> None:
    """Force-evaluate every [`Expression`][qprogram.Expression] reachable from an op's public attributes.

    Pins the reference semantics that all referenced variables must be bound at execution time —
    an unassigned variable raises [`UnassignedVariableError`][qprogram.UnassignedVariableError] here rather than
    silently producing nonsense downstream.

    Recurses through operations, waveforms, and lists or tuples of either. Private attributes and
    the measurement ``handle`` are skipped: the handle holds runtime values written per shot, not
    program input.

    Args:
        value (object): An operation, waveform, expression, or container of those.

    Raises:
        UnassignedVariableError: When a reachable expression still references an unbound variable.
    """
    if isinstance(value, Expression):
        value.evaluate_or_raise()
        return
    if isinstance(value, (Operation, Waveform, IQWaveform)):
        for name, attr in vars(value).items():
            if not name.startswith("_") and name != "handle":
                _evaluate_op_expressions(attr)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _evaluate_op_expressions(item)


# ---------------------------------------------------------------------------
# The reference platform
# ---------------------------------------------------------------------------


# Core parameter ops route to a bus slot (``BUS_ATTRS == ("bus",)``) but are host-side-only: setting
# or reading a parameter is a platform-configuration action, not a real-time sequencer instruction. A
# bus slot is made to expose them in its host half but not its rt half (below).
_PARAM_OPS = frozenset({"op.set_parameter", "op.get_parameter"})


def _swept_parameter_forces_host(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic | DomainConstraint]:
    """Constrain the binding loop of a variable that feeds ``set_parameter`` to host-side dispatch.

    Setting a platform parameter is a host-side action, so a loop that sweeps one cannot run in the
    real-time sequencer — but it runs perfectly well with the host dispatching each iteration. The
    constraint targets the *loop*, not the operation, which is what lets the operation stay real-time
    while the loop around it drops to host-side.

    Args:
        node (Operation | Block): The AST node currently being checked.
        ctx (ValidationContext): Validation context, used to find the loop that binds the variable.

    Yields:
        One [`DomainConstraint`][qprogram.DomainConstraint] excluding ``"rt"`` from the binding loop, when
        ``node`` is a ``set_parameter`` whose value is a bound variable. Nothing otherwise.
    """
    if isinstance(node, SetParameter) and isinstance(node.value, Variable):
        loop = ctx.binding_loop_of(node.value)
        if loop is not None:
            yield DomainConstraint(
                node=loop,
                exclude=frozenset({"rt"}),
                reason=f"parameter '{node.parameter}' is swept via set_parameter (host-side dispatch per iteration)",
            )


def reference_capabilities() -> PlatformCapabilities:
    """Build the reference platform's permissive capability descriptor.

    Every token in the live `CAPABILITY_REGISTRY` is supported — core
    *and* vendor tokens (the reference executor runs vendor operations generically, so importing
    a vendor extension makes its programs executable here). Computed fresh on each call so
    late-registered vendor tokens are picked up. Each bus slot supports everything host-side while its
    **rt half excludes the bus-scoped parameter ops** (``set_parameter`` / ``get_parameter``), so those
    stay host-side-only on every bus — mirroring real platforms, so plans, ``forced-host`` warnings,
    and `explain` are meaningful against the reference platform too.

    Returns:
        A descriptor with an empty per-bus map, a platform slot for blocks and expressions, and a
        default bus profile every bus falls back to.
    """
    from qprogram.protocol import CAPABILITY_REGISTRY  # ruff: ignore[import-outside-top-level] — live, mutable registry

    tokens = frozenset(CAPABILITY_REGISTRY)

    def cc(profile: str, capability_tokens: frozenset[str]) -> CompilerCapabilities:
        return CompilerCapabilities(
            profile=profile,
            version=(0, 1, 0),
            capabilities=capability_tokens,
            limits={},
            predicates=(_swept_parameter_forces_host,),
            vendor_versions={},
        )

    # Parameter ops are bus-scoped but host-side-only: present in each bus slot's host half, absent
    # from its rt half. The platform slot (blocks / expressions) never carries them.
    bus_slot = BusCapabilities(
        rt=cc("qprogram-reference-bus", tokens - _PARAM_OPS),
        host=cc("qprogram-reference-bus", tokens),
    )
    platform_slot = BusCapabilities(
        rt=cc("qprogram-reference-platform", tokens - _PARAM_OPS),
        host=cc("qprogram-reference-platform", tokens - _PARAM_OPS),
    )
    return PlatformCapabilities(bus={}, platform=platform_slot, default_bus_profile=bus_slot)


class ReferencePlatform(PlatformProtocol):
    """The in-tree software platform: validates, interprets, and returns real result xarrays.

    Follows the documented convention exactly: `execute` raises
    [`UnsupportedOperationError`][qprogram.UnsupportedOperationError] on any error diagnostic, surfaces warnings via
    `warnings` (category [`ExecutionWarning`][qprogram.ExecutionWarning]), and passes info through silently.
    Fragment calls are expanded before execution. This is the reference semantics vendor
    compilers are tested against.

    Args:
        schema (BusSchema | None): Bus schema reported by `get_bus_schema`. ``None`` makes that
            method raise and `get_buses` return nothing.
        model (MeasurementModel | None): Measurement model; ``None`` builds a fresh
            [`MockMeasurementModel`][qprogram.MockMeasurementModel] (all-zero response, no noise, ground state).
        parameters (dict[str, float] | None): Initial platform parameter store, keyed
            ``"bus.parameter"``. Copied once, then that copy is read by ``get_parameter``, written by
            ``set_parameter``, and exposed to the model — so a run's writes persist across calls to
            `execute` on the same platform.
        vendor_op_handlers (Mapping[type[Operation], VendorOpHandler] | None): Map of vendor
            [`Operation`][qprogram.operations.Operation] class to a `VendorOpHandler` invoked when
            that op executes — the seam a platform uses to give its own vendor ops runtime effects on
            the parameter store (a vendor's ``set_parameter`` / ``get_parameter`` operations,
            which target an alias rather than a bus). Ops without a handler execute generically
            (expressions evaluated, then no-op).
    """

    def __init__(
        self,
        schema: BusSchema | None = None,
        model: MeasurementModel | None = None,
        parameters: dict[str, float] | None = None,
        vendor_op_handlers: Mapping[type[Operation], VendorOpHandler] | None = None,
    ) -> None:
        self._schema = schema
        self._model: MeasurementModel = model if model is not None else MockMeasurementModel()
        self.parameters: dict[str, float] = dict(parameters or {})
        self._vendor_op_handlers: dict[type[Operation], VendorOpHandler] = dict(vendor_op_handlers or {})

    def get_bus_schema(self) -> BusSchema:
        """Return the configured schema.

        Returns:
            The schema this platform was constructed with.

        Raises:
            ValueError: When the platform was built without one.
        """
        if self._schema is None:
            msg = "this ReferencePlatform was created without a BusSchema"
            raise ValueError(msg)
        return self._schema

    def get_buses(self) -> list[str]:
        """Return the schema's bus names, or an empty list without a schema.

        Returns:
            One name per ``(element, bus kind)`` pair the schema declares, with the index position
            left as ``*`` because the schema names kinds rather than enumerating indices.
        """
        if self._schema is None:
            return []
        return [
            self._schema.naming.pattern.format(element=element, index="*", kind=kind)
            for element, element_schema in self._schema.elements.items()
            for kind in element_schema.buses
        ]

    def get_parameters(self, bus: str) -> list[str]:
        """Return parameter names stored under ``bus`` (keys shaped ``"bus.parameter"``).

        Args:
            bus (str): Bus whose parameters to list.

        Returns:
            The parameter names currently present in the store for that bus. The store grows as
            ``set_parameter`` runs, so this reflects what has been set, not what the bus accepts.
        """
        return [key.split(".", 1)[1] for key in self.parameters if key.split(".", 1)[0] == bus]

    def get_global_parameters(self) -> list[str]:
        """Return every known ``bus.parameter`` key.

        Returns:
            The store's keys, sorted. These are fully qualified, not the bus-less parameters the
            name might suggest — the reference platform keeps one flat store.
        """
        return sorted(self.parameters)

    @property
    def capabilities(self) -> PlatformCapabilities:
        """The permissive descriptor built by [`reference_capabilities`][qprogram.reference_capabilities], recomputed per access.

        Recomputing is what lets a vendor extension imported after the platform was constructed have
        its tokens honored.
        """
        return reference_capabilities()

    def execute(self, qprogram: QProgram, **kwargs: object) -> QProgramResult:  # ruff: ignore[unused-method-argument]
        """Validate and run ``qprogram``, returning its [`QProgramResult`][qprogram.QProgramResult].

        Warning-severity diagnostics are re-emitted through `warnings` as
        [`ExecutionWarning`][qprogram.ExecutionWarning] and do not stop the run; info-severity ones are dropped.

        Args:
            qprogram (QProgram): Program to run. Fragment calls are expanded first, on a copy.
            **kwargs (object): Accepted and ignored, so callers can pass the platform-specific
                options a real back-end would take.

        Returns:
            One record per measurement in the program.

        Raises:
            UnsupportedOperationError: When validation produces any ``severity="error"``
                diagnostic (all of them are listed in the message).
            UnassignedVariableError: When an operation's expression references a variable no
                enclosing loop binds.
        """
        if qprogram.fragments:
            qprogram = qprogram.expand()
        diagnostics, _plan = validate(qprogram, self.capabilities)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            msg = "program is not executable on the reference platform:\n" + "\n".join(str(d) for d in errors)
            raise UnsupportedOperationError(msg)
        for diag in diagnostics:
            if diag.severity == "warning":
                warnings.warn(str(diag), ExecutionWarning, stacklevel=2)
        return _Interpreter(qprogram, self._model, self.parameters, self._vendor_op_handlers).run()


def simulate(
    program: QProgram,
    *,
    model: MeasurementModel | None = None,
    schema: BusSchema | None = None,
    parameters: dict[str, float] | None = None,
) -> QProgramResult:
    """Execute ``program`` on a one-off [`ReferencePlatform`][qprogram.ReferencePlatform] — the quickest path to results.

    The program should already be concrete; resolve any string waveform names first with
    ``program.with_waveforms(library)`` (or ``library.apply(program)``).

    Args:
        program (QProgram): Program to run.
        model (MeasurementModel | None): Measurement model; ``None`` uses a deterministic, all-zero
            [`MockMeasurementModel`][qprogram.MockMeasurementModel].
        schema (BusSchema | None): Bus schema for the throwaway platform.
        parameters (dict[str, float] | None): Initial parameter store, keyed ``"bus.parameter"``.
            Copied, so the caller's dict is left untouched.

    Returns:
        One record per measurement in the program.

    Raises:
        UnsupportedOperationError: When the program uses something the reference platform cannot
            run.
        UnassignedVariableError: When an operation's expression references a variable no enclosing
            loop binds.
    """
    return ReferencePlatform(schema=schema, model=model, parameters=parameters).execute(program)


__all__ = [
    "ExecutionWarning",
    "MeasurementModel",
    "MeasurementSample",
    "MockMeasurementModel",
    "ReferencePlatform",
    "reference_capabilities",
    "simulate",
]
