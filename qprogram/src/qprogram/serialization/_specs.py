"""Default and special-case serialize/parse callbacks for the ``.qp`` format.

Bridges the registry's spec metadata to the concrete operation, block, and sweep classes. Exports
signature-driven default callbacks for the majority of operations plus the few special-case callbacks
(``sync`` variadic, ``get_parameter`` arrow, ``set_crosstalk`` stub) and the built-in sweep
generators (``range``, ``values``, ``file``).

The ``ctx`` parameter on every callback is the writer or parser instance, duck-typed to a small
surface; a formal :class:`typing.Protocol` is plausible future work but doesn't change the runtime
contract.
"""
# Ruff: callback ``ctx`` arguments are intentionally ``Any`` — the writer and parser pass themselves
# and callbacks rely on a small duck-typed surface.
# ruff: noqa: ANN401

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import numpy as np

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.crosstalk_matrix import CrosstalkMatrix
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
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
from qprogram.serialization.registry import (
    OperationSpec,
    register_block,
    register_operation,
    register_sweep_generator,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


# ---------------------------------------------------------------------------
# Default operation callbacks
# ---------------------------------------------------------------------------


def default_serialize_operation(op: Operation, spec: OperationSpec, ctx: Any) -> str:
    """Signature-driven serializer used by most operations.

    Walks ``__init__``'s parameters: required ones (no default) emit positionally in declaration
    order; optional ones emit as ``name=value`` only when the stored value differs from the
    parameter default. Parameters with no corresponding attribute on the instance are silently
    skipped, allowing ``__init__`` to accept a kwarg the body doesn't bother to store.

    Args:
        op: Operation instance to serialise.
        spec: The :class:`OperationSpec` describing ``op``.
        ctx: Writer instance — exposes ``serialize_bus``, ``serialize_value``, etc.

    Returns:
        The serialised body of the line (the keyword prefix is added by the caller).
    """
    sig = inspect.signature(spec.cls.__init__)
    params = list(sig.parameters.values())[1:]  # skip self
    pos_parts: list[str] = []
    kw_parts: list[str] = []
    for p in params:
        if not hasattr(op, p.name):
            continue
        value = getattr(op, p.name)
        if p.default is inspect.Parameter.empty:
            pos_parts.append(ctx.serialize_value(value))
        elif value != p.default:
            kw_parts.append(f"{p.name}={ctx.serialize_value(value)}")
    return " ".join([spec.qualified_name, *pos_parts, *kw_parts])


def default_parse_operation(spec: OperationSpec, tokens: list[str], ctx: Any) -> Operation:
    """Signature-driven parser used by most operations.

    A token is a kwarg iff it contains ``=`` outside any parenthesised group and outside leading
    quotes. Positional tokens bind by index to the constructor signature; the operation is always
    constructed via ``**kwargs`` so positional ordering can't drift.

    Args:
        spec: The :class:`OperationSpec` describing the target class.
        tokens: Tokens of the operation body (the leading keyword has been consumed).
        ctx: Parser instance — exposes ``parse_value``, ``get_or_create_handle``, etc.

    Returns:
        A freshly-constructed :class:`Operation` instance.
    """
    sig = inspect.signature(spec.cls.__init__)
    params = list(sig.parameters.values())[1:]
    positional: list[Any] = []
    kwargs: dict[str, Any] = {}
    for tok in tokens:
        tok_stripped = tok.strip()
        if not tok_stripped:
            continue
        if _looks_like_kwarg(tok_stripped):
            key, _, val = tok_stripped.partition("=")
            kwargs[key.strip()] = ctx.parse_value(val.strip())
        else:
            positional.append(ctx.parse_value(tok_stripped))
    final: dict[str, Any] = {}
    for i, value in enumerate(positional):
        if i < len(params):
            final[params[i].name] = value
    final.update(kwargs)
    return spec.cls(**final)


def make_measurement_op_parse(cls: type[Operation]) -> Callable[[list[str], Any], Operation]:
    """Build a parse callback for a :class:`MeasurementOperation` subclass.

    The returned callback mirrors :func:`default_parse_operation` but resolves the ``handle``
    parameter from its name token to the canonical :class:`~qprogram.MeasurementHandle` via
    ``ctx.get_or_create_handle``. This is what lets every measurement op and every
    :class:`MeasurementRef` referring to the same name share one Python instance after a ``.qp``
    load.

    Vendor measurement ops register with this factory the same way ``measure`` does::

        register_vendor_operation(
            "myvendor",
            "acquire",
            Acquire,
            parse=make_measurement_op_parse(Acquire),
        )

    Args:
        cls: The :class:`MeasurementOperation` subclass to build the parser for.

    Returns:
        A signature-driven parse callback ready for :func:`register_vendor_operation`.
    """

    def parse(tokens: list[str], ctx: Any) -> Operation:
        sig = inspect.signature(cls.__init__)
        params = list(sig.parameters.values())[1:]
        positional: list[Any] = []
        kwargs: dict[str, Any] = {}
        for tok in tokens:
            tok_stripped = tok.strip()
            if not tok_stripped:
                continue
            if _looks_like_kwarg(tok_stripped):
                key, _, val = tok_stripped.partition("=")
                kwargs[key.strip()] = ctx.parse_value(val.strip())
            else:
                positional.append(ctx.parse_value(tok_stripped))
        final: dict[str, Any] = {}
        for i, value in enumerate(positional):
            if i < len(params):
                final[params[i].name] = value
        final.update(kwargs)
        if "handle" in final and isinstance(final["handle"], str):
            final["handle"] = ctx.get_or_create_handle(final["handle"])
        return cls(**final)

    return parse


def _looks_like_kwarg(tok: str) -> bool:
    """Return ``True`` if ``tok`` looks like a ``key=value`` pair.

    A kwarg is a token containing ``=`` outside any quoted string and outside any parentheses — i.e.
    the prefix up to the first ``=`` is an unquoted identifier with no ``(``. This rules out
    ``Gaussian(amplitude=0.5)`` (paren in prefix) and ``"key=value"`` (leading quote).
    """
    if "=" not in tok or tok.startswith('"'):
        return False
    prefix, _, _ = tok.partition("=")
    return "(" not in prefix


# ---------------------------------------------------------------------------
# Special-case operation callbacks
# ---------------------------------------------------------------------------


def sync_serialize(op: Sync, ctx: Any) -> str:
    """Serialise as ``sync`` (no buses) or ``sync <bus> [<bus> ...]``."""
    if op.targets:
        return "sync " + " ".join(ctx.serialize_bus(b) for b in op.targets)
    return "sync"


def sync_parse(tokens: list[str], ctx: Any) -> Sync:
    """Parse ``sync`` body: all tokens are buses, empty means sync-all."""
    if not tokens:
        return Sync(targets=None)
    return Sync(targets=[ctx.parse_value(tok) for tok in tokens])


def get_parameter_serialize(op: GetParameter, ctx: Any) -> str:
    """Serialise as ``get_parameter "alias" "param" [channel_id=N] -> <ident>``.

    The result variable appears after the ``->`` arrow rather than as a positional or kwarg —
    matches the visual convention that ``get_parameter`` *produces* a value.
    """
    extras = f" channel_id={op.channel_id}" if op.channel_id is not None else ""
    ident = ctx.var_ident(op.variable)
    alias = ctx.serialize_value(op.alias)
    parameter = ctx.serialize_value(op.parameter)
    return f"get_parameter {alias} {parameter}{extras} -> {ident}"


def get_parameter_parse(tokens: list[str], ctx: Any) -> GetParameter:
    """Inverse of :func:`get_parameter_serialize`.

    Splits on the ``->`` token; right side is the target variable identifier (auto-declared if new),
    left side is the standard ``alias parameter [kwargs]`` layout.
    """
    arrow_idx = next((i for i, t in enumerate(tokens) if t == "->"), None)
    if arrow_idx is None or arrow_idx + 1 >= len(tokens):
        msg = "get_parameter requires '-> <var>' assignment"
        raise ctx.parse_error(msg)
    var_name = tokens[arrow_idx + 1]
    body = tokens[:arrow_idx]
    if len(body) < 2:
        msg = "get_parameter requires alias and parameter name"
        raise ctx.parse_error(msg)
    alias = ctx.parse_value(body[0])
    parameter = ctx.parse_value(body[1])
    kw: dict[str, Any] = {}
    for tok in body[2:]:
        if _looks_like_kwarg(tok):
            key, _, val = tok.partition("=")
            kw[key.strip()] = ctx.parse_value(val.strip())
    var = ctx.get_or_declare_variable(var_name)
    return GetParameter(
        variable=var,
        alias=alias,
        parameter=parameter,
        channel_id=kw.get("channel_id"),
    )


def set_crosstalk_serialize(op: SetCrosstalk, ctx: Any) -> str:  # noqa: ARG001
    """Serialise as the stub ``set_crosstalk crosstalk`` — matrix content is not serialised yet."""
    return "set_crosstalk crosstalk"


def set_crosstalk_parse(tokens: list[str], ctx: Any) -> SetCrosstalk:  # noqa: ARG001
    """Parse to a :class:`SetCrosstalk` wrapping an empty matrix — matches the writer stub."""
    return SetCrosstalk(crosstalk=CrosstalkMatrix())


# ---------------------------------------------------------------------------
# Block header callbacks
# ---------------------------------------------------------------------------


def average_serialize_header(block: Average, ctx: Any) -> str:  # noqa: ARG001
    """Serialise as ``average <shots>``."""
    return f"average {block.shots}"


def average_parse_header(tokens: list[str], ctx: Any) -> Average:
    """Parse ``average <shots>`` — single positional integer.

    Raises:
        ParseError: If ``shots`` is missing or non-integer.
    """
    if not tokens:
        msg = "average requires a shot count"
        raise ctx.parse_error(msg)
    try:
        shots = int(tokens[0])
    except ValueError as e:
        msg = f"average: invalid shots count {tokens[0]!r}"
        raise ctx.parse_error(msg) from e
    return Average(shots=shots)


# ---------------------------------------------------------------------------
# Sweep generator callbacks
# ---------------------------------------------------------------------------


def range_parse(var: Variable, args_text: str, ctx: Any) -> ForLoop:
    """Parse ``range(start, stop[, step])`` into a :class:`ForLoop`."""
    parts = [_parse_number(a.strip()) for a in args_text.split(",")]
    if len(parts) == 2:
        return ForLoop(variable=var, start=parts[0], stop=parts[1], step=1)
    if len(parts) == 3:
        return ForLoop(variable=var, start=parts[0], stop=parts[1], step=parts[2])
    msg = "range() expects 2 or 3 arguments"
    raise ctx.parse_error(msg)


def range_write(loop: ForLoop, ctx: Any) -> str:
    """Serialise a :class:`ForLoop` as ``range(start, stop, step)``."""
    return (
        f"range({ctx.serialize_value(loop.start)}, {ctx.serialize_value(loop.stop)}, {ctx.serialize_value(loop.step)})"
    )


def values_parse(var: Variable, args_text: str, ctx: Any) -> Loop:  # noqa: ARG001
    """Parse a ``[v0, v1, ...]`` literal into a :class:`Loop`.

    ``args_text`` arrives with the brackets so the helper stays self-contained. Values must be
    numeric literals — symbolic expressions aren't allowed inside array literals at parse time.
    """
    inner = args_text.strip().removeprefix("[").removesuffix("]")
    values = np.array([_parse_number(v.strip()) for v in inner.split(",") if v.strip()])
    return Loop(variable=var, values=values)


def values_write(loop: Loop, ctx: Any) -> str:
    """Serialise a :class:`Loop` as a ``[v0, v1, ...]`` literal; truncate after 50 values."""
    items = ", ".join(ctx.serialize_value(v) for v in loop.values[:50])
    if len(loop.values) > 50:
        items += ", ..."
    return f"[{items}]"


def file_parse(var: Variable, args_text: str, ctx: Any) -> Loop:  # noqa: ARG001
    """Parse ``file("path.npy")`` by loading via :func:`numpy.load` into a :class:`Loop`.

    Parse-only — values inline onto the AST and the writer round-trips through ``values``. If a use
    case for retaining the path lands, store it on :class:`Loop` and register a write callback.
    """
    path = args_text.strip().strip('"')
    values = np.load(path)
    return Loop(variable=var, values=values)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_number(s: str) -> int | float:
    """Parse a numeric literal preserving ``int`` vs ``float``.

    Why this preserves the distinction: round-tripping must not silently promote integer sweep bounds
    to floats — that would visibly change the rewritten ``.qp`` file.
    """
    s = s.strip()
    val = float(s)
    if val == int(val) and "." not in s and "e" not in s.lower():
        return int(val)
    return val


# ---------------------------------------------------------------------------
# Registration entry point
# ---------------------------------------------------------------------------


def _register_core_specs() -> None:
    """Register all core operations, blocks, and sweep generators.

    Invoked once from :mod:`qprogram.serialization.__init__` at import time. Idempotent —
    re-registering overwrites prior entries.
    """
    # Default callbacks handle the regular shapes; explicit callbacks for the special-form ops.
    register_operation("play", Play)
    register_operation("measure", Measure, parse=make_measurement_op_parse(Measure))
    register_operation("wait", Wait)
    register_operation("sync", Sync, serialize=sync_serialize, parse=sync_parse)
    register_operation("set_frequency", SetFrequency)
    register_operation("set_phase", SetPhase)
    register_operation("reset_phase", ResetPhase)
    register_operation("set_gain", SetGain)
    register_operation("set_offset", SetOffset)
    register_operation("set_parameter", SetParameter)
    register_operation(
        "get_parameter",
        GetParameter,
        serialize=get_parameter_serialize,
        parse=get_parameter_parse,
    )
    register_operation(
        "set_crosstalk",
        SetCrosstalk,
        serialize=set_crosstalk_serialize,
        parse=set_crosstalk_parse,
    )

    # Blocks — the generic ``block:`` has no header data and uses defaults.
    register_block("block", Block)
    register_block(
        "average",
        Average,
        serialize_header=average_serialize_header,
        parse_header=average_parse_header,
    )

    # Sweep generators — ``file`` parses but writes back through ``values``,
    # so register it first (parse-only) and then ``values`` claims the write
    # side for Loop.
    register_sweep_generator("file", Loop, parse=file_parse)
    register_sweep_generator("range", ForLoop, parse=range_parse, write=range_write)
    register_sweep_generator("values", Loop, parse=values_parse, write=values_write)
