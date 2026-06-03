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
import math
from typing import TYPE_CHECKING, Any

import numpy as np

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.blocks.for_loop import ForLoop
from qprogram.blocks.loop import Loop
from qprogram.crosstalk_matrix import CrosstalkMatrix
from qprogram.errors import ValidationError
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
    get_operation_spec_by_class,
    register_block,
    register_operation,
    register_sweep_generator,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from qprogram.operations.operation import MeasurementOperation, Operation
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


def _bind_signature_tokens(
    cls: type[Operation],
    tokens: list[str],
    ctx: Any,
) -> dict[str, Any]:
    """Bind operation-line tokens to ``cls.__init__``'s parameters.

    A token is a kwarg iff it contains ``=`` outside any parenthesised group and outside leading
    quotes. Positional tokens bind by index to the constructor signature; the result dict is used
    for an all-keyword construction so positional ordering can't drift.

    Raises:
        ParseError: If there are more positional tokens than the constructor has parameters —
            silently dropping the excess would load a *different* program without any error.
    """
    sig = inspect.signature(cls.__init__)
    params = list(sig.parameters.values())[1:]  # skip self
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
            positional.append(tok_stripped)
    if len(positional) > len(params):
        extra = positional[len(params) :]
        msg = (
            f"too many arguments for {cls.__name__!r}: {len(positional)} positional tokens but "
            f"the operation takes at most {len(params)}; unexpected: {extra!r}. If you meant an "
            f"arithmetic expression, parenthesise it: `(100 - t)`."
        )
        raise ctx.parse_error(msg)
    final: dict[str, Any] = {}
    for i, tok_stripped in enumerate(positional):
        final[params[i].name] = ctx.parse_value(tok_stripped)
    final.update(kwargs)
    return final


def _construct_operation(cls: type[Operation], final: dict[str, Any], ctx: Any) -> Operation:
    """Instantiate ``cls(**final)``, converting constructor ``TypeError`` into a line-tagged error.

    Raises:
        ParseError: When the bound arguments don't fit the constructor (unknown kwarg, missing
            required parameter) — surfacing the raw ``TypeError`` would lose the line number.
    """
    try:
        return cls(**final)
    except TypeError as e:
        msg = f"cannot construct {cls.__name__!r} from the given arguments: {e}"
        raise ctx.parse_error(msg) from e


def default_parse_operation(spec: OperationSpec, tokens: list[str], ctx: Any) -> Operation:
    """Signature-driven parser used by most operations.

    Args:
        spec: The :class:`OperationSpec` describing the target class.
        tokens: Tokens of the operation body (the leading keyword has been consumed).
        ctx: Parser instance — exposes ``parse_value``, ``get_or_create_handle``, etc.

    Returns:
        A freshly-constructed :class:`Operation` instance.

    Raises:
        ParseError: On excess positional tokens or arguments the constructor rejects.
    """
    final = _bind_signature_tokens(spec.cls, tokens, ctx)
    return _construct_operation(spec.cls, final, ctx)


def measurement_op_serialize(op: MeasurementOperation, ctx: Any) -> str:
    """Signature-driven serializer for :class:`MeasurementOperation` subclasses.

    Mirrors :func:`default_serialize_operation` but skips the ``handle`` constructor parameter and
    emits the measurement name as a ``name="..."`` kwarg instead — the wire format reads as intent
    (``measure "ro" "r" "w" name="q0/readout/m0"``) rather than as a bare positional string. The
    parse side (:func:`make_measurement_op_parse`) resolves ``name=`` back to the canonical
    handle instance.
    """
    spec = get_operation_spec_by_class(type(op))
    qualified = spec.qualified_name if spec is not None else type(op).__name__
    sig = inspect.signature(type(op).__init__)
    params = list(sig.parameters.values())[1:]  # skip self
    pos_parts: list[str] = []
    kw_parts: list[str] = []
    for p in params:
        if p.name == "handle" or not hasattr(op, p.name):
            continue
        value = getattr(op, p.name)
        if p.default is inspect.Parameter.empty:
            pos_parts.append(ctx.serialize_value(value))
        elif value != p.default:
            kw_parts.append(f"{p.name}={ctx.serialize_value(value)}")
    name_part = f"name={ctx.serialize_value(op.handle.name)}"
    return " ".join([qualified, *pos_parts, name_part, *kw_parts])


def make_measurement_op_parse(cls: type[Operation]) -> Callable[[list[str], Any], Operation]:
    """Build a parse callback for a :class:`MeasurementOperation` subclass.

    The returned callback mirrors :func:`default_parse_operation` but resolves the measurement
    name to the canonical :class:`~qprogram.MeasurementHandle` via ``ctx.get_or_create_handle``.
    This is what lets every measurement op and every :class:`MeasurementRef` referring to the
    same name share one Python instance after a ``.qp`` load. Three accepted spellings:

    - ``name="..."`` kwarg — the canonical form the writer emits.
    - a quoted handle name bound to the ``handle`` positional slot — legacy files.
    - neither — the parser auto-allocates a name with the same convention the builder uses.

    Vendor measurement ops register with this factory the same way ``measure`` does::

        register_vendor_operation(
            "myvendor",
            "acquire",
            Acquire,
            serialize=measurement_op_serialize,
            parse=make_measurement_op_parse(Acquire),
        )

    Args:
        cls: The :class:`MeasurementOperation` subclass to build the parser for.

    Returns:
        A signature-driven parse callback ready for :func:`register_vendor_operation`.
    """

    def parse(tokens: list[str], ctx: Any) -> Operation:
        final = _bind_signature_tokens(cls, tokens, ctx)
        name = final.pop("name", None)
        if name is not None:
            if not isinstance(name, str):
                msg = f"measurement name= must be a quoted string, got {name!r}"
                raise ctx.parse_error(msg)
            final["handle"] = ctx.get_or_create_handle(name)
        elif "handle" in final and isinstance(final["handle"], str):
            # Legacy positional form: the handle name as a bare quoted token.
            final["handle"] = ctx.get_or_create_handle(final["handle"])
        elif "handle" not in final:
            # Hand-written file without a name — allocate one exactly like the builder would.
            final["handle"] = ctx.allocate_measurement_handle(final.get("bus", ""))
        return _construct_operation(cls, final, ctx)

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


def set_crosstalk_serialize(op: SetCrosstalk, ctx: Any) -> str:
    """Serialise the full crosstalk matrix as dict-literal kwargs.

    Wire form: ``set_crosstalk matrix={"flux_q0": {"flux_q0": 1.0, ...}, ...}`` plus optional
    ``offsets={...}`` and ``resistances={...}`` sections (each omitted when empty). An entirely
    empty matrix serialises as the bare keyword. ``None`` resistances emit as ``null``.
    """
    xtalk = op.crosstalk
    parts: list[str] = ["set_crosstalk"]
    if xtalk.matrix:
        parts.append(f"matrix={ctx.serialize_value(xtalk.matrix)}")
    if xtalk.flux_offsets:
        parts.append(f"offsets={ctx.serialize_value(xtalk.flux_offsets)}")
    if xtalk.resistances:
        parts.append(f"resistances={ctx.serialize_value(xtalk.resistances)}")
    return " ".join(parts)


def set_crosstalk_parse(tokens: list[str], ctx: Any) -> SetCrosstalk:
    """Inverse of :func:`set_crosstalk_serialize` — rebuild the full :class:`CrosstalkMatrix`.

    Raises:
        ParseError: On positional tokens, unknown kwargs, or section values that aren't
            dict literals of the expected shape.
    """
    xtalk = CrosstalkMatrix()
    for tok in tokens:
        tok_stripped = tok.strip()
        if not tok_stripped:
            continue
        if not _looks_like_kwarg(tok_stripped):
            msg = (
                f"set_crosstalk takes only matrix= / offsets= / resistances= sections; "
                f"unexpected token {tok_stripped!r}"
            )
            raise ctx.parse_error(msg)
        key, _, val = tok_stripped.partition("=")
        key = key.strip()
        parsed = ctx.parse_value(val.strip())
        if not isinstance(parsed, dict):
            msg = f"set_crosstalk {key}= must be a dict literal, got {parsed!r}"
            raise ctx.parse_error(msg)
        if key == "matrix":
            for src, row in parsed.items():
                if not isinstance(row, dict):
                    msg = f"set_crosstalk matrix= rows must be dicts, got {row!r} for {src!r}"
                    raise ctx.parse_error(msg)
                xtalk.matrix[src] = {tgt: float(coeff) for tgt, coeff in row.items()}
        elif key == "offsets":
            xtalk.flux_offsets.update({bus: float(v) for bus, v in parsed.items()})
        elif key == "resistances":
            xtalk.resistances.update({bus: (None if v is None else float(v)) for bus, v in parsed.items()})
        else:
            msg = f"set_crosstalk has no {key!r} section; allowed: matrix, offsets, resistances"
            raise ctx.parse_error(msg)
    return SetCrosstalk(crosstalk=xtalk)


# ---------------------------------------------------------------------------
# Block header callbacks
# ---------------------------------------------------------------------------


def average_serialize_header(block: Average, ctx: Any) -> str:  # noqa: ARG001
    """Serialise as ``average <shots>``."""
    return f"average {block.shots}"


def average_parse_header(tokens: list[str], ctx: Any) -> Average:
    """Parse ``average <shots>`` — single positional integer.

    Raises:
        ParseError: If ``shots`` is missing, non-integer, or fails Average's own validation
            (``shots >= 1``).
    """
    if not tokens:
        msg = "average requires a shot count"
        raise ctx.parse_error(msg)
    try:
        shots = int(tokens[0])
    except ValueError as e:
        msg = f"average: invalid shots count {tokens[0]!r}"
        raise ctx.parse_error(msg) from e
    try:
        return Average(shots=shots)
    except ValidationError as e:
        raise ctx.parse_error(str(e)) from e


# ---------------------------------------------------------------------------
# Sweep generator callbacks
# ---------------------------------------------------------------------------


def range_parse(var: Variable, args_text: str, ctx: Any) -> ForLoop:
    """Parse ``range(start, stop[, step])`` into a :class:`ForLoop`.

    Construction-time validation failures (zero step, non-finite bounds, wrong step direction)
    are re-raised as line-tagged parse errors.
    """
    parts = [_parse_number(a.strip()) for a in args_text.split(",")]
    if len(parts) not in (2, 3):
        msg = "range() expects 2 or 3 arguments"
        raise ctx.parse_error(msg)
    step = parts[2] if len(parts) == 3 else 1
    try:
        return ForLoop(variable=var, start=parts[0], stop=parts[1], step=step)
    except ValidationError as e:
        raise ctx.parse_error(str(e)) from e


def range_write(loop: ForLoop, ctx: Any) -> str:
    """Serialise a :class:`ForLoop` as ``range(start, stop, step)``."""
    return (
        f"range({ctx.serialize_value(loop.start)}, {ctx.serialize_value(loop.stop)}, {ctx.serialize_value(loop.step)})"
    )


def values_parse(var: Variable, args_text: str, ctx: Any) -> Loop:
    """Parse a ``[v0, v1, ...]`` literal into a :class:`Loop`.

    ``args_text`` arrives with the brackets so the helper stays self-contained. Values must be
    numeric literals — symbolic expressions aren't allowed inside array literals at parse time.
    Construction-time validation failures (empty list) become line-tagged parse errors.
    """
    inner = args_text.strip().removeprefix("[").removesuffix("]")
    values = np.array([_parse_number(v.strip()) for v in inner.split(",") if v.strip()])
    try:
        return Loop(variable=var, values=values)
    except ValidationError as e:
        raise ctx.parse_error(str(e)) from e


def values_write(loop: Loop, ctx: Any) -> str:
    """Serialise a :class:`Loop` as a ``[v0, v1, ...]`` literal.

    Never truncated: the literal must reload to exactly the same sweep. (An earlier revision cut
    the list at 50 values with a ``...`` marker, which made every longer sweep's file unparseable.)
    """
    items = ", ".join(ctx.serialize_value(v) for v in loop.values)
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
    to floats — that would visibly change the rewritten ``.qp`` file. Non-finite values (``inf``,
    ``nan``) stay floats — calling ``int()`` on them would raise ``OverflowError`` / ``ValueError``.
    """
    s = s.strip()
    val = float(s)
    if math.isfinite(val) and val == int(val) and "." not in s and "e" not in s.lower():
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
    register_operation(
        "measure",
        Measure,
        serialize=measurement_op_serialize,
        parse=make_measurement_op_parse(Measure),
    )
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
