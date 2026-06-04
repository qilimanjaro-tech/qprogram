"""Reference software executor — the in-tree interpreter that actually runs a QProgram.

:class:`ReferencePlatform` is a complete :class:`~qprogram.PlatformProtocol`: it validates against
a permissive capability descriptor (every core token; orchestration ops sw-only, mirroring real
platforms), then walks the AST in pure Python — driving loop variables via
:meth:`Variable.set_value`, writing measurement outcomes onto the shared
:class:`~qprogram.MeasurementHandle` (so ``handle.state`` feedback works by construction), and
assembling a :class:`~qprogram.QProgramResult` of :class:`xarray.DataArray` s with the result-shape
contract of spec §8:

- dims = enclosing ``ForLoop`` / ``Loop`` sweeps, outermost first, named by variable id;
  ``Parallel`` contributes one shared ``"a|b"`` dim carrying every composed variable's coords.
- ``average(shots)`` contributes **no** dim — ``iq``/``raw`` are means over shots, ``state``
  averages to the excited-state population.
- Return-token shapes: ``iq`` → ``(*sweeps, "IQ"[2])``; ``state`` → ``(*sweeps)``;
  ``raw`` → ``(*sweeps, "time"[N], "IQ")``.
- A measurement inside a :class:`Conditional` arm holds NaN at sweep points where the arm never
  executed (count-based averaging).

Measurement outcomes come from a pluggable :class:`MeasurementModel`; the default
:class:`MockMeasurementModel` is deterministic (seeded) and lets demos shape the response —
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
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.blocks.parallel import Parallel
from qprogram.errors import UnsupportedOperationError
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.operation import MeasurementOperation, Operation
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


class ExecutionWarning(UserWarning):
    """Category for warning-severity diagnostics surfaced during :meth:`ReferencePlatform.execute`."""


# ---------------------------------------------------------------------------
# Measurement model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementSample:
    """One shot's worth of simulated measurement outcomes.

    Attributes:
        i: In-phase value.
        q: Quadrature value.
        state: Classified outcome, ``0`` or ``1``.
        raw: Raw trace of shape ``(raw_samples, 2)`` (I and Q per time sample).
    """

    i: float
    q: float
    state: int
    raw: np.ndarray


@runtime_checkable
class MeasurementModel(Protocol):
    """What the executor asks of a measurement back-end — one :meth:`sample` per shot.

    ``env`` carries the currently bound loop variables (by id) and the platform parameters (by
    ``"alias.parameter"``), so a model can shape its response as a function of the sweep.
    """

    def sample(self, bus: str, env: Mapping[str, float]) -> MeasurementSample:
        """Return one shot's outcomes for a measurement on ``bus`` under ``env``."""
        ...


class MockMeasurementModel:
    """Deterministic mock measurement model — the executor's default.

    The noiseless IQ point comes from ``response`` (default ``0j``); per-shot gaussian noise is
    added on both quadratures. The classified ``state`` is a Bernoulli sample of
    ``p_excited`` (default ``0.0``). The ``raw`` trace replicates the IQ point over
    ``raw_samples`` time samples with per-sample noise. All randomness flows from one seeded
    generator, so identical programs produce identical results.

    Args:
        response: ``(bus, env) -> complex`` noiseless IQ response. ``env`` holds the bound loop
            variables and platform parameters, so e.g. a Rabi oscillation is
            ``lambda bus, env: np.sin(np.pi * env["g"] / 2) ** 2 + 0j``.
        p_excited: ``(bus, env) -> float`` excited-state probability for the classified outcome.
        noise: Standard deviation of the gaussian noise added per quadrature, per shot.
        raw_samples: Number of time samples in the ``raw`` trace.
        seed: Seed for the model's private :func:`numpy.random.default_rng`.
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
        """Return one deterministic-given-the-seed shot for ``bus`` under ``env``."""
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
    """One result dimension: the loop node that drives it, its dim name, size, and coords."""

    node: Block  # ForLoop | Loop | Parallel
    dim: str
    size: int
    coords: dict[str, np.ndarray]  # coord name -> values (Parallel attaches one per variable)


def _loop_values(loop: ForLoop | Loop) -> np.ndarray:
    """The sweep values a loop binds, pinned by spec §8.2.

    ``ForLoop``: ``start + step * arange(num_iterations())`` — consistent with
    :meth:`ForLoop.num_iterations` (both ends inclusive). ``Loop``: its ``values`` array.
    """
    if isinstance(loop, ForLoop):
        return loop.start + loop.step * np.arange(loop.num_iterations())
    return np.asarray(loop.values)


def _axis_for(node: Block) -> _Axis | None:
    """Build the :class:`_Axis` a block contributes to enclosed measurements, if any."""
    if isinstance(node, Parallel):
        dim = "|".join(lp.variable.id for lp in node.loops)
        size = node.loops[0].num_iterations()
        coords = {lp.variable.id: _loop_values(lp) for lp in node.loops}
        return _Axis(node=node, dim=dim, size=size, coords=coords)
    if isinstance(node, (ForLoop, Loop)):
        values = _loop_values(node)
        return _Axis(node=node, dim=node.variable.id, size=len(values), coords={node.variable.id: values})
    return None  # Average / Conditional / plain Block contribute no dimension


@dataclass
class _MeasurementSlot:
    """Accumulators for one measurement op: a running sum + visit count per return token."""

    op: MeasurementOperation
    axes: tuple[_Axis, ...]
    sums: dict[str, np.ndarray]
    counts: np.ndarray  # shots executed per sweep point (shared across tokens)


# ---------------------------------------------------------------------------
# The interpreter
# ---------------------------------------------------------------------------


class _Interpreter:
    """Pure-Python AST evaluator producing :class:`QProgramResult` xarrays.

    Two phases: a *setup* walk computes each measurement's sweep axes (the ``ForLoop`` / ``Loop``
    / ``Parallel`` blocks enclosing it, outermost first) and allocates accumulators; the *run*
    walk drives loop variables, samples the model per measurement shot, and accumulates at the
    current index of each axis.
    """

    def __init__(self, program: QProgram, model: MeasurementModel, parameters: dict[str, float]) -> None:
        self._program = program
        self._model = model
        self._parameters = parameters
        self._raw_samples = int(getattr(model, "raw_samples", 16))
        self._slots: list[_MeasurementSlot] = []
        self._slot_by_op: dict[int, _MeasurementSlot] = {}
        self._indices: dict[int, int] = {}  # id(axis.node) -> current iteration index

    # -- setup ---------------------------------------------------------------

    def _setup(self) -> None:
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
        shape = tuple(axis.size for axis in axes)
        sums: dict[str, np.ndarray] = {}
        for field_name in op.returns:
            if field_name == "iq":
                sums[field_name] = np.zeros((*shape, 2))
            elif field_name == "raw":
                sums[field_name] = np.zeros((*shape, self._raw_samples, 2))
            else:  # state and any future scalar-per-shot token
                sums[field_name] = np.zeros(shape)
        slot = _MeasurementSlot(op=op, axes=axes, sums=sums, counts=np.zeros(shape))
        self._slots.append(slot)
        self._slot_by_op[id(op)] = slot

    # -- run -----------------------------------------------------------------

    def run(self) -> QProgramResult:
        self._setup()
        self._execute_children(self._program.body)
        return self._finalize()

    def _execute_children(self, block: Block) -> None:
        for element in block.elements:
            if isinstance(element, Block):
                self._execute_block(element)
            else:
                self._execute_operation(element)

    def _execute_block(self, block: Block) -> None:
        if isinstance(block, Parallel):
            values = [_loop_values(lp) for lp in block.loops]
            for k in range(block.loops[0].num_iterations()):
                self._indices[id(block)] = k
                for lp, vals in zip(block.loops, values, strict=True):
                    lp.variable.set_value(float(vals[k]))
                self._execute_children(block)
            self._indices.pop(id(block), None)
            return
        if isinstance(block, (ForLoop, Loop)):
            for k, value in enumerate(_loop_values(block)):
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
        for condition, body in cond.arms:
            # evaluate_or_raise: a condition over a measurement that hasn't produced a state yet
            # (or an unbound variable) is a programming error and fails loudly.
            if condition.evaluate_or_raise():
                self._execute_children(body)
                return
        if cond.else_body is not None:
            self._execute_children(cond.else_body)

    def _execute_operation(self, op: Operation) -> None:
        if isinstance(op, GetParameter):
            # `variable` is the op's *output target* — written here, never read, so it is
            # exempt from the eager expression evaluation below.
            op.variable.set_value(self._parameters.get(f"{op.alias}.{op.parameter}", 0.0))
            return
        _evaluate_op_expressions(op)
        if isinstance(op, MeasurementOperation):
            self._execute_measurement(op)
            return
        if isinstance(op, SetParameter):
            value = op.value.evaluate_or_raise() if isinstance(op.value, Expression) else op.value
            self._parameters[f"{op.alias}.{op.parameter}"] = float(value)
            return
        # Pulse/timing ops (play, wait, sync, set_*) and vendor ops: validated above, no-ops here —
        # the reference executor simulates results, not waveform physics or timing.

    def _execute_measurement(self, op: MeasurementOperation) -> None:
        sample = self._model.sample(str(op.bus) if hasattr(op, "bus") else "", self._environment())
        op.handle._set_value("state", sample.state)  # noqa: SLF001 — the runtime contract of MeasurementHandle
        slot = self._slot_by_op[id(op)]
        index = tuple(self._indices[id(axis.node)] for axis in slot.axes)
        for field_name, accumulator in slot.sums.items():
            if field_name == "iq":
                accumulator[index] += (sample.i, sample.q)
            elif field_name == "raw":
                accumulator[index] += sample.raw
            elif field_name == "state":
                accumulator[index] += sample.state
            # unknown tokens stay zero — they were validated against the platform's
            # measure.returns.* capabilities before execution
        slot.counts[index] += 1

    def _environment(self) -> dict[str, float]:
        """The bound loop variables (by id) plus the platform parameters (by ``alias.parameter``)."""
        env: dict[str, float] = {}
        for var in self._program.variables:
            value = var.value
            if isinstance(value, (int, float)):
                env[var.id] = float(value)
        env.update(self._parameters)
        return env

    # -- finalize --------------------------------------------------------------

    def _finalize(self) -> QProgramResult:
        result = QProgramResult()
        for slot in self._slots:
            dims = [axis.dim for axis in slot.axes]
            coords: dict[str, object] = {}
            for axis in slot.axes:
                for name, values in axis.coords.items():
                    coords[name] = (axis.dim, values) if name != axis.dim else values
            fields: dict[str, xr.DataArray] = {}
            for field_name in slot.op.returns:
                fields[field_name] = self._field_array(slot, field_name, dims, coords)
            primary = fields.get("iq", fields[slot.op.returns[0]])
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
        counts = slot.counts
        if field_name == "iq":
            mean = _safe_mean(slot.sums[field_name], counts[..., np.newaxis])
            return xr.DataArray(mean, dims=(*dims, "IQ"), coords={**coords, "IQ": ["I", "Q"]})
        if field_name == "raw":
            mean = _safe_mean(slot.sums[field_name], counts[..., np.newaxis, np.newaxis])
            return xr.DataArray(
                mean,
                dims=(*dims, "time", "IQ"),
                coords={**coords, "time": np.arange(self._raw_samples), "IQ": ["I", "Q"]},
            )
        mean = _safe_mean(slot.sums[field_name], counts)
        return xr.DataArray(mean, dims=dims, coords=coords)


def _safe_mean(total: np.ndarray, count: np.ndarray) -> np.ndarray:
    """``total / count`` with NaN where ``count`` is zero (unexecuted conditional arms)."""
    count_b = np.broadcast_to(count, total.shape)
    return np.divide(total, count_b, out=np.full_like(total, np.nan), where=count_b != 0)


def _evaluate_op_expressions(value: object) -> None:
    """Force-evaluate every :class:`Expression` reachable from an op's public attributes.

    Pins the reference semantics that all referenced variables must be bound at execution time —
    an unassigned variable raises :class:`~qprogram.UnassignedVariableError` here rather than
    silently producing nonsense downstream.
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


_BUS_LESS_OPS = frozenset({"op.set_parameter", "op.get_parameter", "op.set_crosstalk"})


def _swept_parameter_forces_software(
    node: Operation | Block,
    ctx: ValidationContext,
) -> Iterable[Diagnostic | DomainConstraint]:
    """A swept variable feeding ``set_parameter`` forces its binding loop out of real-time hw."""
    if isinstance(node, SetParameter) and isinstance(node.value, Variable):
        loop = ctx.binding_loop_of(node.value)
        if loop is not None:
            yield DomainConstraint(
                node=loop,
                exclude=frozenset({"hw"}),
                reason=f"parameter '{node.parameter}' is swept via set_parameter (software dispatch per iteration)",
            )


def reference_capabilities() -> PlatformCapabilities:
    """The reference platform's permissive capability descriptor.

    Every token in the live :data:`~qprogram.protocol.CAPABILITY_REGISTRY` is supported — core
    *and* vendor tokens (the reference executor runs vendor operations generically, so importing
    a vendor extension makes its programs executable here). Computed fresh on each call so
    late-registered vendor tokens are picked up. The platform slot supports everything in
    software while its **hw half excludes the orchestration ops** (``set_parameter`` /
    ``get_parameter`` / ``set_crosstalk``) — mirroring real platforms, so plans,
    ``forced-software`` warnings, and :func:`~qprogram.explain` are meaningful against the
    reference platform too.
    """
    from qprogram.protocol import CAPABILITY_REGISTRY  # noqa: PLC0415 — live, mutable registry

    tokens = frozenset(CAPABILITY_REGISTRY)

    def cc(profile: str, capability_tokens: frozenset[str]) -> CompilerCapabilities:
        return CompilerCapabilities(
            profile=profile,
            version=(0, 1, 0),
            capabilities=capability_tokens,
            limits={},
            predicates=(_swept_parameter_forces_software,),
            vendor_versions={},
        )

    bus_slot = BusCapabilities(
        hw=cc("qprogram-reference-bus", tokens - _BUS_LESS_OPS),
        sw=cc("qprogram-reference-bus", tokens - _BUS_LESS_OPS),
    )
    platform_slot = BusCapabilities(
        hw=cc("qprogram-reference-platform", tokens - _BUS_LESS_OPS),
        sw=cc("qprogram-reference-platform", tokens),
    )
    return PlatformCapabilities(bus={}, platform=platform_slot, default_bus_profile=bus_slot)


class ReferencePlatform(PlatformProtocol):
    """The in-tree software platform: validates, interprets, and returns real result xarrays.

    Follows the documented convention exactly: :meth:`execute` raises
    :class:`~qprogram.UnsupportedOperationError` on any error diagnostic, surfaces warnings via
    :mod:`warnings` (category :class:`ExecutionWarning`), and passes info through silently.
    Fragment calls are expanded before execution. This is the reference semantics vendor
    compilers are tested against.

    Args:
        schema: Optional :class:`~qprogram.BusSchema` reported by :meth:`get_bus_schema`.
        model: Measurement model; defaults to a fresh :class:`MockMeasurementModel` (all-zero
            response, no noise, ground state).
        parameters: Initial platform parameter store, keyed ``"alias.parameter"``. The same dict
            is read by ``get_parameter``, written by ``set_parameter``, and exposed to the model.
    """

    def __init__(
        self,
        schema: BusSchema | None = None,
        model: MeasurementModel | None = None,
        parameters: dict[str, float] | None = None,
    ) -> None:
        self._schema = schema
        self._model: MeasurementModel = model if model is not None else MockMeasurementModel()
        self.parameters: dict[str, float] = dict(parameters or {})

    def get_bus_schema(self) -> BusSchema:
        """Return the configured schema.

        Raises:
            ValueError: When the platform was built without one.
        """
        if self._schema is None:
            msg = "this ReferencePlatform was created without a BusSchema"
            raise ValueError(msg)
        return self._schema

    def get_buses(self) -> list[str]:
        """Return the schema's bus names, or an empty list without a schema."""
        if self._schema is None:
            return []
        return [
            self._schema.naming.pattern.format(element=element, index="*", kind=kind)
            for element, element_schema in self._schema.elements.items()
            for kind in element_schema.buses
        ]

    def get_parameters(self, bus: str) -> list[str]:
        """Return parameter names whose alias matches ``bus``."""
        return [key.split(".", 1)[1] for key in self.parameters if key.split(".", 1)[0] == bus]

    def get_global_parameters(self) -> list[str]:
        """Return every known ``alias.parameter`` key."""
        return sorted(self.parameters)

    @property
    def capabilities(self) -> PlatformCapabilities:
        """Return :func:`reference_capabilities`."""
        return reference_capabilities()

    def execute(self, qprogram: QProgram, **kwargs: object) -> QProgramResult:  # noqa: ARG002
        """Validate and run ``qprogram``, returning its :class:`~qprogram.QProgramResult`.

        Raises:
            UnsupportedOperationError: When validation produces any ``severity="error"``
                diagnostic (all of them are listed in the message).
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
        return _Interpreter(qprogram, self._model, self.parameters).run()


def run(
    program: QProgram,
    *,
    model: MeasurementModel | None = None,
    schema: BusSchema | None = None,
    parameters: dict[str, float] | None = None,
) -> QProgramResult:
    """Execute ``program`` on a one-off :class:`ReferencePlatform` — the quickest path to results.

    Args:
        program: Program to run.
        model: Measurement model (defaults to a deterministic, all-zero
            :class:`MockMeasurementModel`).
        schema: Optional bus schema for the platform.
        parameters: Initial parameter store.

    Returns:
        The :class:`~qprogram.QProgramResult`.
    """
    return ReferencePlatform(schema=schema, model=model, parameters=parameters).execute(program)


__all__ = [
    "ExecutionWarning",
    "MeasurementModel",
    "MeasurementSample",
    "MockMeasurementModel",
    "ReferencePlatform",
    "reference_capabilities",
    "run",
]
