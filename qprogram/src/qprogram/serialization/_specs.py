"""Default and special-case serialize/parse callbacks for the ``.qp`` format.

This module is the bridge between the registry (which only stores spec
metadata) and the concrete operation/block/sweep classes. It provides:

- :func:`default_serialize_operation` and :func:`default_parse_operation` —
  signature-driven callbacks that work for the majority of operations.
  Required parameters of ``__init__`` are serialized positionally, optional
  parameters whose current value differs from their default are serialized as
  ``key=value`` kwargs.

- Special-case callbacks for operations whose surface syntax doesn't fit the
  default pattern: :func:`sync_*` (variadic, no positional/kwarg shape),
  :func:`get_parameter_*` (``-> ident`` assignment arrow),
  :func:`set_crosstalk_*` (the crosstalk matrix is not yet serialized;
  current behaviour is a literal stub).

- Header callbacks for blocks whose header carries data (``average <n>``).

- Sweep generator callbacks for ``range(...)``, the ``[...]`` literal
  (registered under the name ``values``), and ``file("path")``.

All registrations live in :func:`_register_core_specs`, which is invoked
once from ``qprogram.serialization.__init__`` so that importing the
serialization package activates the entire dispatch table.

The ``ctx: Any`` parameter on every callback is a duck-typed handle to the
writer or parser; the methods used are listed informally in each call site.
A formal :class:`typing.Protocol` is plausible future work but would expand
the API surface without changing the runtime contract.
"""
# Ruff: callback ``ctx`` arguments are intentionally type-erased (the writer
# and parser pass themselves; callbacks rely on a small duck-typed surface).
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
    from qprogram.operations.operation import Operation
    from qprogram.variable import Variable


# ---------------------------------------------------------------------------
# Default operation callbacks
# ---------------------------------------------------------------------------


def default_serialize_operation(op: Operation, spec: OperationSpec, ctx: Any) -> str:
    """Signature-driven serializer.

    Walks ``__init__``'s parameters. Required parameters (no default) are
    emitted positionally in declaration order. Optional parameters are
    emitted as ``name=value`` kwargs only when their current attribute value
    differs from the parameter default. Parameters with no corresponding
    attribute on the instance are silently skipped — this lets ``__init__``
    accept a kwarg that the body doesn't bother to store (rare; mainly a
    safety net against existing inconsistencies).
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
    """Signature-driven parser.

    Splits tokens into positional and kwargs (a token is a kwarg iff it
    contains ``=`` outside of any parenthesised group and outside of leading
    quotes). Positional tokens are bound by index to the corresponding
    parameter name from the constructor signature; the result is passed as
    ``**final`` so the operation is always constructed via keyword arguments
    and ordering issues are avoided.
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


def _looks_like_kwarg(tok: str) -> bool:
    """Return ``True`` if a single token is a ``key=value`` pair.

    A token counts as a kwarg if it contains an ``=`` that is *not* inside a
    quoted string and *not* inside parentheses. Concretely: split on the first
    ``=`` and require the prefix to be an unquoted identifier-shaped chunk
    with no ``(``. This rules out ``Gaussian(amplitude=0.5)`` (paren in
    prefix) and ``"key=value"`` (starts with a quote).
    """
    if "=" not in tok or tok.startswith('"'):
        return False
    prefix, _, _ = tok.partition("=")
    return "(" not in prefix


# ---------------------------------------------------------------------------
# Special-case operation callbacks
# ---------------------------------------------------------------------------


def sync_serialize(op: Sync, ctx: Any) -> str:
    """``sync`` (no buses) or ``sync <bus> [<bus> ...]`` (variadic positional)."""
    if op.buses:
        return "sync " + " ".join(ctx.serialize_bus(b) for b in op.buses)
    return "sync"


def sync_parse(tokens: list[str], ctx: Any) -> Sync:
    """All remaining tokens are bus references; empty list means sync-all."""
    if not tokens:
        return Sync(buses=None)
    return Sync(buses=[ctx.parse_value(tok) for tok in tokens])


def get_parameter_serialize(op: GetParameter, ctx: Any) -> str:
    """``get_parameter "alias" "param" [channel_id=N] -> <ident>``.

    The result variable's identifier appears after the ``->`` arrow rather
    than as a positional or kwarg of the operation; this matches the visual
    convention that ``get_parameter`` produces a value the rest of the
    program can refer to.
    """
    extras = f" channel_id={op.channel_id}" if op.channel_id is not None else ""
    ident = ctx.var_ident(op.variable)
    alias = ctx.serialize_value(op.alias)
    parameter = ctx.serialize_value(op.parameter)
    return f"get_parameter {alias} {parameter}{extras} -> {ident}"


def get_parameter_parse(tokens: list[str], ctx: Any) -> GetParameter:
    """Inverse of :func:`get_parameter_serialize`.

    Splits on the ``->`` arrow token; the right side is the target variable
    identifier (auto-declared if not already known), the left side is the
    standard ``alias parameter [kwargs]`` layout.
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
    """Stub form ``set_crosstalk crosstalk`` — matrix content is not serialized yet."""
    return "set_crosstalk crosstalk"


def set_crosstalk_parse(tokens: list[str], ctx: Any) -> SetCrosstalk:  # noqa: ARG001
    """Construct an empty :class:`CrosstalkMatrix` — matches the writer stub."""
    return SetCrosstalk(crosstalk=CrosstalkMatrix())


# ---------------------------------------------------------------------------
# Block header callbacks
# ---------------------------------------------------------------------------


def average_serialize_header(block: Average, ctx: Any) -> str:  # noqa: ARG001
    """``average <shots>``."""
    return f"average {block.shots}"


def average_parse_header(tokens: list[str], ctx: Any) -> Average:
    """Single positional integer: the shot count."""
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
    """``range(start, stop[, step])`` — produces a :class:`ForLoop`."""
    parts = [_parse_number(a.strip()) for a in args_text.split(",")]
    if len(parts) == 2:
        return ForLoop(variable=var, start=parts[0], stop=parts[1], step=1)
    if len(parts) == 3:
        return ForLoop(variable=var, start=parts[0], stop=parts[1], step=parts[2])
    msg = "range() expects 2 or 3 arguments"
    raise ctx.parse_error(msg)


def range_write(loop: ForLoop, ctx: Any) -> str:
    return (
        f"range({ctx.serialize_value(loop.start)}, "
        f"{ctx.serialize_value(loop.stop)}, "
        f"{ctx.serialize_value(loop.step)})"
    )


def values_parse(var: Variable, args_text: str, ctx: Any) -> Loop:  # noqa: ARG001
    """``[v0, v1, ...]`` literal — produces a :class:`Loop`.

    ``args_text`` is passed in with the surrounding brackets so this stays
    self-contained; we strip them here. The values must be numeric — symbolic
    expressions are not allowed in the array literal at parse time.
    """
    inner = args_text.strip().removeprefix("[").removesuffix("]")
    values = np.array([_parse_number(v.strip()) for v in inner.split(",") if v.strip()])
    return Loop(variable=var, values=values)


def values_write(loop: Loop, ctx: Any) -> str:
    items = ", ".join(ctx.serialize_value(v) for v in loop.values[:50])
    if len(loop.values) > 50:
        items += ", ..."
    return f"[{items}]"


def file_parse(var: Variable, args_text: str, ctx: Any) -> Loop:  # noqa: ARG001
    """``file("path.npy")`` — loads via :func:`numpy.load` into a :class:`Loop`.

    Parse-only: the loaded values become inline data on the AST, and the
    writer round-trips through the ``values`` generator rather than retaining
    the file path. If file-path retention becomes important, store the path
    on the :class:`Loop` and register a write callback here.
    """
    path = args_text.strip().strip('"')
    values = np.load(path)
    return Loop(variable=var, values=values)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_number(s: str) -> int | float:
    """Parse a numeric literal, preserving int vs float distinction.

    Mirrors the original parser helper so that round-trips don't promote
    integer sweep bounds to floats (which would visibly change the file).
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

    Invoked once from :mod:`qprogram.serialization.__init__` on package
    import. Idempotent: re-registering overwrites prior entries.
    """
    # Core operations — default callbacks for the regular shapes,
    # explicit callbacks for the three special-form ops.
    register_operation("play", Play)
    register_operation("measure", Measure)
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
