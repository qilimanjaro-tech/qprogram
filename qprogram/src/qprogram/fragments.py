"""Reusable, parameterized sub-programs (fragments).

A :class:`Fragment` is a named, parameterized program template: it carries the full fluent builder
surface of :class:`~qprogram.QProgram` (operations, control flow, vendor namespaces) plus a list of
:class:`Parameter` placeholders. A host program instantiates it with :meth:`QProgram.call`, which
appends a first-class :class:`~qprogram.operations.Call` node — definitions and call sites survive
in the AST and round-trip through ``.qp`` (``fragment <name>(<params>):`` sections and bare
``<name>(<args>)`` statements).

``program.expand()`` is the canonical lowering: every call is replaced inline by a copy of the
fragment body with parameters substituted (values, expressions, buses, waveforms), fragment-local
variables hygienically renamed onto the host, and measurement names uniquified. Compilers and
validators consume expanded, fragment-free programs.

Two equal-billing definition styles::

    @fragment  # static: the signature IS the parameter list
    def x_pulse(f, drive, amp):
        f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))


    xp = Fragment("x_pulse")  # programmatic: explicit parameter() calls
    drive = xp.parameter("drive")
    amp = xp.parameter("amp")
    xp.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))
"""

from __future__ import annotations

import copy
import inspect
from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.buses import BusRef
from qprogram.errors import ValidationError
from qprogram.operations.call import Call
from qprogram.operations.operation import MeasurementOperation, Operation
from qprogram.qprogram import QProgram, _validate_acquires, _validate_waveform_channel
from qprogram.variable import (
    _ID_RE,
    BinaryOp,
    Comparison,
    Constant,
    Expression,
    LogicalBinaryOp,
    LogicalNot,
    MathFunc,
    MeasurementRef,
    UnaryOp,
    Variable,
    Where,
)
from qprogram.waveforms.waveform import IQWaveform, Waveform

if TYPE_CHECKING:
    from collections.abc import Callable

from qprogram._reserved import RESERVED_KEYWORDS

# Value kinds accepted as call-site argument bindings. ``str`` covers raw-string buses, BusRefs
# (a str subclass), and waveform aliases; ``Expression`` covers numbers wrapped at use sites,
# host variables, and arithmetic over them.
_ALLOWED_ARGUMENT_TYPES = (int, float, str, Expression, Waveform, IQWaveform)


class Parameter(Variable):
    """A fragment parameter — a placeholder substituted with the bound argument at expansion.

    Subclasses :class:`~qprogram.Variable`, so a parameter participates in expressions
    (``amp * 2``), serializes as a bare identifier, and follows the same id rules. Parameters are
    untyped: the *binding* determines the kind (number/expression, bus, or waveform), checked at
    expansion with a clear error when a binding is used in an incompatible position.
    """

    def __repr__(self) -> str:
        return f"Parameter('{self.id}')"


class Fragment(QProgram):
    """A named, parameterized sub-program — define once, :meth:`~qprogram.QProgram.call` many times.

    Inherits the entire :class:`~qprogram.QProgram` builder (operations, control flow, vendor
    namespaces), so a fragment body is built exactly like a program body. Fragments may call other,
    previously-defined fragments; cycles are rejected at registration/expansion.

    Args:
        name: Fragment identifier — must match ``[A-Za-z_][A-Za-z0-9_]*`` and not be a reserved
            keyword. Used verbatim as the ``.qp`` definition/call name.
        label: Optional human-readable label (not serialized in format 1.0).
        description: Optional longer description (not serialized in format 1.0).

    Raises:
        ValidationError: If ``name`` is not a valid identifier or is reserved.
    """

    def __init__(self, name: str, label: str = "", description: str | None = None) -> None:
        if not isinstance(name, str) or not _ID_RE.match(name):
            msg = f"fragment name {name!r} is invalid: must match [A-Za-z_][A-Za-z0-9_]* (no spaces or punctuation)"
            raise ValidationError(msg)
        if name in RESERVED_KEYWORDS:
            msg = f"fragment name {name!r} is a reserved keyword (see qprogram.RESERVED_KEYWORDS)"
            raise ValidationError(msg)
        super().__init__(label=label, description=description)
        self._name = name
        self._params: list[Parameter] = []

    @property
    def name(self) -> str:
        """Return the fragment's identifier."""
        return self._name

    @property
    def params(self) -> tuple[Parameter, ...]:
        """Return the declared parameters in declaration order."""
        return tuple(self._params)

    def parameter(self, id: str, *, label: str | None = None) -> Parameter:  # noqa: A002
        """Declare a new :class:`Parameter` on this fragment.

        Args:
            id: Identifier matching ``[A-Za-z_][A-Za-z0-9_]*``; unique among the fragment's
                parameters *and* local variables (they share the identifier namespace inside the
                body).
            label: Optional human-readable name.

        Returns:
            The new :class:`Parameter`.

        Raises:
            ValidationError: If ``id`` collides with an existing parameter or local variable.
        """
        if any(p.id == id for p in self._params):
            msg = f"Parameter {id!r} is already declared on fragment {self._name!r}"
            raise ValidationError(msg)
        if any(v.id == id for v in self._variables):
            msg = f"Parameter {id!r} collides with a local variable of fragment {self._name!r}"
            raise ValidationError(msg)
        param = Parameter(id, label=label)
        self._params.append(param)
        return param

    def variable(
        self,
        id: str,  # noqa: A002
        *,
        label: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> Variable:
        """Declare a fragment-local :class:`~qprogram.Variable`.

        Local variables are renamed onto the host program at expansion (``{fragment}_{id}``, with a
        numeric suffix on collision) so repeated calls never clash.

        Raises:
            ValidationError: If ``id`` collides with a parameter or an existing local variable.
        """
        if any(p.id == id for p in self._params):
            msg = f"Variable {id!r} collides with a parameter of fragment {self._name!r}"
            raise ValidationError(msg)
        return super().variable(id, label=label, units=units, description=description)

    def __eq__(self, other: object) -> bool:
        """Structural equality: same name, parameter ids, local variables, and body."""
        if type(self) is not type(other):
            return False
        assert isinstance(other, Fragment)  # narrowed by the type check above  # noqa: S101
        return (
            self._name == other._name
            and [p.id for p in self._params] == [p.id for p in other._params]
            and self._variables == other._variables
            and self._body == other._body
        )

    # Weak but consistent with __eq__ (equal fragments share a name). Body mutation after
    # hashing is already forbidden by the Block hashing contract.
    def __hash__(self) -> int:
        return hash(("Fragment", self._name))

    def __repr__(self) -> str:
        params = ", ".join(p.id for p in self._params)
        return f"Fragment({self._name!r}, params=({params}))"


def fragment(func: Callable[..., None]) -> Fragment:
    """Build a :class:`Fragment` from a function — the signature *is* the parameter list.

    The first parameter receives the fragment builder; each remaining parameter becomes a
    :class:`Parameter` (in order); the fragment name is the function's ``__name__``. The body runs
    **once**, at decoration time, to record the AST — Python-level control flow inside it is
    evaluated at definition, not per call.

    Example::

        @fragment
        def x_pulse(f, drive, amp):
            f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))


        program.call(x_pulse, "drive_q0", 0.5)

    Args:
        func: The definition function. ``*args`` / ``**kwargs`` / defaults / keyword-only
            parameters are rejected — the ``.qp`` grammar has no representation for them.

    Returns:
        The recorded :class:`Fragment` (the decorated name *is* the fragment object).

    Raises:
        ValidationError: On an unsupported signature.
    """
    func_name = getattr(func, "__name__", None)
    if not isinstance(func_name, str):
        msg = "@fragment requires a named function (the object has no __name__ to use as the fragment name)"
        raise ValidationError(msg)
    sig = inspect.signature(func)
    sig_params = list(sig.parameters.values())
    if not sig_params:
        msg = f"@fragment function {func_name!r} must take the fragment builder as its first parameter"
        raise ValidationError(msg)
    positional_kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    frag = Fragment(func_name)
    handles: list[Parameter] = []
    for p in sig_params[1:]:
        if p.kind not in positional_kinds:
            msg = (
                f"@fragment {func_name!r}: parameter {p.name!r} is "
                f"{p.kind.description}; only plain positional parameters are supported"
            )
            raise ValidationError(msg)
        if p.default is not inspect.Parameter.empty:
            msg = f"@fragment {func_name!r}: parameter {p.name!r} has a default value; defaults are not supported"
            raise ValidationError(msg)
        handles.append(frag.parameter(p.name))
    func(frag, *handles)
    return frag


def bind_arguments(frag: Fragment, args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
    """Bind call-site arguments to a fragment's parameters (Python calling convention).

    Args:
        frag: The fragment being called.
        args: Positional arguments, matched to parameters in declaration order.
        kwargs: Keyword arguments, matched by parameter id.

    Returns:
        A fully-bound ``{param_id: value}`` dict covering every parameter exactly once.

    Raises:
        ValidationError: On too many positionals, an unknown keyword, a parameter bound twice,
            a missing parameter, or an argument of an unsupported type.
    """
    params = frag.params
    param_ids = [p.id for p in params]
    if len(args) > len(params):
        msg = (
            f"fragment {frag.name!r} takes {len(params)} argument(s) "
            f"({', '.join(param_ids) or 'none'}) but {len(args)} positional argument(s) were given"
        )
        raise ValidationError(msg)
    bound: dict[str, object] = {}
    for param, value in zip(params, args, strict=False):
        bound[param.id] = value
    for key, value in kwargs.items():
        if key not in param_ids:
            msg = f"fragment {frag.name!r} has no parameter {key!r}; parameters are ({', '.join(param_ids) or 'none'})"
            raise ValidationError(msg)
        if key in bound:
            msg = f"fragment {frag.name!r} got multiple values for parameter {key!r}"
            raise ValidationError(msg)
        bound[key] = value
    missing = [pid for pid in param_ids if pid not in bound]
    if missing:
        msg = f"fragment {frag.name!r} missing argument(s) for parameter(s): {', '.join(missing)}"
        raise ValidationError(msg)
    for pid, value in bound.items():
        if isinstance(value, bool) or not isinstance(value, _ALLOWED_ARGUMENT_TYPES):
            msg = (
                f"fragment {frag.name!r} parameter {pid!r}: unsupported argument type "
                f"{type(value).__name__}; expected a number, expression/variable, bus "
                f"(string or BusRef), or waveform"
            )
            raise ValidationError(msg)
    return bound


# ---------------------------------------------------------------------------
# Expansion — the canonical lowering to a fragment-free program
# ---------------------------------------------------------------------------


def expand_program(program: QProgram) -> QProgram:
    """Return a deep copy of ``program`` with every :class:`Call` inlined.

    Each call site is replaced by a plain :class:`Block` containing a copy of the fragment body
    with parameters substituted by the bound arguments. Fragment-local variables become fresh host
    variables (``{fragment}_{id}``, numeric suffix on collision); measurement names that would
    collide get a ``_2`` / ``_3`` suffix — the shared :class:`~qprogram.MeasurementHandle` is
    renamed, so :class:`~qprogram.MeasurementRef` conditionals inside the fragment stay consistent.
    Nested calls expand recursively; expansion is deterministic (document order).

    Raises:
        ValidationError: On a fragment call cycle, or when a binding is used in an incompatible
            position (e.g. a waveform inside an arithmetic expression).
    """
    expanded = copy.deepcopy(program)
    used_names = {op.name for op in expanded.body.walk() if isinstance(op, MeasurementOperation)}
    _expand_in_block(expanded.body, expanded, stack=(), used_names=used_names)
    expanded._fragments = {}  # noqa: SLF001
    expanded._qp_source_map = {}  # noqa: SLF001 — expansion restructures the tree; recorded paths are stale
    return expanded


def _expand_in_block(block: Block, host: QProgram, stack: tuple[str, ...], used_names: set[str]) -> None:
    """Replace every :class:`Call` element under ``block`` (recursively) with its expansion."""
    if isinstance(block, Conditional):
        # Conditional keeps its arm bodies on .arms / .else_body, not in _elements.
        for _, arm_body in block.arms:
            _expand_in_block(arm_body, host, stack, used_names)
        if block.else_body is not None:
            _expand_in_block(block.else_body, host, stack, used_names)
        return
    new_elements: list[Block | Operation] = []
    for element in block.elements:
        if isinstance(element, Call):
            new_elements.append(_expand_call(element, host, stack, used_names))
        elif isinstance(element, Block):
            _expand_in_block(element, host, stack, used_names)
            new_elements.append(element)
        else:
            new_elements.append(element)
    block.elements[:] = new_elements


def _expand_call(call: Call, host: QProgram, stack: tuple[str, ...], used_names: set[str]) -> Block:
    """Expand one call site into a plain :class:`Block` holding the substituted fragment body."""
    frag = call.fragment
    if frag.name in stack:
        cycle = " -> ".join((*stack, frag.name))
        msg = f"fragment call cycle: {cycle}"
        raise ValidationError(msg)
    body = copy.deepcopy(frag.body)
    mapping: dict[str, object] = dict(call.arguments)
    for var in frag.variables:
        fresh = host.variable(
            _fresh_variable_id(host, f"{frag.name}_{var.id}"),
            label=var.label,
            units=var.units,
            description=var.description,
        )
        mapping[var.id] = fresh
    _substitute_in_body(body, mapping, frag)
    _uniquify_measurements(body, used_names)
    _expand_in_block(body, host, (*stack, frag.name), used_names)
    return body


def _fresh_variable_id(host: QProgram, base: str) -> str:
    existing = {v.id for v in host.variables}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _uniquify_measurements(body: Block, used_names: set[str]) -> None:
    """Suffix colliding measurement names; renaming the handle keeps MeasurementRefs consistent."""
    for node in body.walk():
        if isinstance(node, MeasurementOperation):
            base = node.handle.name
            if base in used_names:
                n = 2
                while f"{base}_{n}" in used_names:
                    n += 1
                node.handle.name = f"{base}_{n}"
            used_names.add(node.handle.name)


def _substitute_in_body(body: Block, mapping: dict[str, object], frag: Fragment) -> None:
    """Substitute parameters/local variables across every node of a copied fragment body."""
    for node in body.walk():
        if isinstance(node, Operation):
            _substitute_node_attrs(node, mapping, frag)
            _revalidate_op(node, frag)
        elif isinstance(node, Block):
            _substitute_node_attrs(node, mapping, frag)
            if isinstance(node, (ForLoop, Loop)) and not isinstance(node.variable, Variable):
                msg = (
                    f"fragment {frag.name!r}: a loop variable must be bound to a variable, "
                    f"got {type(node.variable).__name__}"
                )
                raise ValidationError(msg)


def _substitute_node_attrs(node: Block | Operation, mapping: dict[str, object], frag: Fragment) -> None:
    for attr, value in vars(node).items():
        if attr.startswith("_"):
            continue  # children are covered by walk(); private state is not user data
        setattr(node, attr, _subst_value(value, mapping, frag))


def _subst_value(value: object, mapping: dict[str, object], frag: Fragment) -> object:
    """Substitute parameters in a general (non-expression) position — bindings stay raw."""
    if isinstance(value, Variable):
        return mapping.get(value.id, value)
    if isinstance(value, Expression):
        return _subst_expr(value, mapping, frag)
    if isinstance(value, (Waveform, IQWaveform)):
        for attr, wf_value in vars(value).items():
            if not attr.startswith("_"):
                setattr(value, attr, _subst_value(wf_value, mapping, frag))
        return value
    if isinstance(value, list):
        return [_subst_value(v, mapping, frag) for v in value]
    if isinstance(value, tuple):
        return tuple(_subst_value(v, mapping, frag) for v in value)
    if isinstance(value, dict):
        return {k: _subst_value(v, mapping, frag) for k, v in value.items()}
    return value


def _subst_expr(expr: Expression, mapping: dict[str, object], frag: Fragment) -> Expression:
    """Substitute parameters inside an expression tree — numeric bindings wrap as Constant."""
    if isinstance(expr, Variable):
        bound = mapping.get(expr.id)
        if bound is None:
            return expr
        if isinstance(bound, Expression):
            return bound
        if isinstance(bound, (int, float)) and not isinstance(bound, bool):
            return Constant(bound)
        msg = (
            f"fragment {frag.name!r}: parameter {expr.id!r} is used in an expression "
            f"but bound to a {type(bound).__name__}; bind a number, variable, or expression"
        )
        raise ValidationError(msg)
    if isinstance(expr, (Constant, MeasurementRef)):
        return expr
    if isinstance(expr, (BinaryOp, Comparison, LogicalBinaryOp)):
        expr.left = _subst_expr(expr.left, mapping, frag)
        expr.right = _subst_expr(expr.right, mapping, frag)
        return expr
    if isinstance(expr, (UnaryOp, LogicalNot)):
        expr.operand = _subst_expr(expr.operand, mapping, frag)
        return expr
    if isinstance(expr, MathFunc):
        expr.operands = tuple(_subst_expr(o, mapping, frag) for o in expr.operands)
        return expr
    if isinstance(expr, Where):
        expr.condition = _subst_expr(expr.condition, mapping, frag)
        expr.then = _subst_expr(expr.then, mapping, frag)
        expr.else_ = _subst_expr(expr.else_, mapping, frag)
        return expr
    return expr  # unknown Expression subclass — leave untouched


def _revalidate_op(op: Operation, frag: Fragment) -> None:
    """Post-substitution kind checks: bus positions hold buses, waveform positions hold waveforms.

    Also re-runs the channel/acquires validation the builder applies — fragment bodies skip it
    when the position holds a :class:`Parameter`, so the bound value is checked here instead.
    """
    for attr in type(op).BUS_ATTRS:
        value = getattr(op, attr, None)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, str):
                msg = (
                    f"fragment {frag.name!r}: {type(op).__name__}.{attr} must be a bus "
                    f"(string or BusRef) after expansion, got {type(item).__name__}"
                )
                raise ValidationError(msg)
    for attr in type(op).WAVEFORM_ATTRS:
        value = getattr(op, attr, None)
        if value is None:
            continue
        if not isinstance(value, (Waveform, IQWaveform, str)):
            msg = (
                f"fragment {frag.name!r}: {type(op).__name__}.{attr} must be a waveform "
                f"or alias after expansion, got {type(value).__name__}"
            )
            raise ValidationError(msg)
    bus = getattr(op, "bus", None)
    if isinstance(bus, BusRef):
        for attr in type(op).WAVEFORM_ATTRS:
            wf = getattr(op, attr, None)
            if wf is not None:
                _validate_waveform_channel(bus, wf)
        if isinstance(op, MeasurementOperation):
            _validate_acquires(bus)
