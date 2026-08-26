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
"""Default and special-case serialize/parse callbacks for the ``.qp`` format.

Bridges the registry's spec metadata to the concrete operation, block, and sweep classes. Exports
signature-driven default callbacks for the majority of operations, the special-case callbacks
(a measurement's ``name=`` kwarg, ``sync``'s variadic bus list, ``get_parameter``'s ``->`` arrow,
``average``'s shot count), and the registration entry point that puts every core operation, block,
and sweep source in the registries.

The ``ctx`` parameter on every callback is the writer or parser instance, narrowed to the slice of
it the callbacks may reach for: `SerializeContext` on the write side, `ParseContext` on the parse
side.
"""

from __future__ import annotations

import inspect
import math
from typing import TYPE_CHECKING, Any, Protocol, cast

from qprogram.blocks.average import Average
from qprogram.blocks.block import Block
from qprogram.errors import ValidationError
from qprogram.operations.get_parameter import GetParameter
from qprogram.operations.measure import Measure
from qprogram.operations.play import Play
from qprogram.operations.reset_phase import ResetPhase
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
    register_sweep_source,
)
from qprogram.sweeps.builtin import File, Linspace, Logspace, Range, Values
from qprogram.sweeps.combinators import Concat, Repeat, Rotate

if TYPE_CHECKING:
    from collections.abc import Callable

    from qprogram.operations.operation import MeasurementOperation, Operation
    from qprogram.result import MeasurementHandle
    from qprogram.serialization.parser import ParseError
    from qprogram.variable import Variable


# ---------------------------------------------------------------------------
# Callback contexts
# ---------------------------------------------------------------------------


class SerializeContext(Protocol):
    """The writer surface a serialize callback may use.

    The writer passes itself as the ``ctx`` argument. Callbacks see it through this protocol rather
    than through the concrete class, so a callback cannot reach into writer internals that are free
    to change between releases.
    """

    def serialize_value(self, val: object) -> str:
        """Render one argument value as a ``.qp`` token."""
        ...

    def serialize_bus(self, bus: object) -> str:
        """Render a bus as a bus path, or as a quoted string when it carries no schema."""
        ...

    def var_ident(self, var: Variable) -> str:
        """Return the identifier a variable is written under."""
        ...


class ParseContext(Protocol):
    """The parser surface a parse callback may use.

    The counterpart of `SerializeContext`: the parser passes itself as the ``ctx`` argument, and
    callbacks see only the token, error, variable, and handle accessors.
    """

    def parse_value(self, token: str) -> object:
        """Turn one ``.qp`` token back into the Python value it spells."""
        ...

    def parse_error(self, message: str) -> ParseError:
        """Build a line-tagged error for the statement being parsed. The caller raises it."""
        ...

    def get_or_declare_variable(self, name: str) -> Variable:
        """Return the program variable of that identifier, declaring it if the name is new."""
        ...

    def get_or_create_handle(self, name: str) -> MeasurementHandle:
        """Return the canonical measurement handle of that name, creating it if the name is new."""
        ...

    def allocate_measurement_handle(self, bus: object) -> MeasurementHandle:
        """Allocate a handle for an unnamed measurement, following the builder's naming convention."""
        ...


# ---------------------------------------------------------------------------
# Default operation callbacks
# ---------------------------------------------------------------------------


def default_serialize_operation(op: Operation, spec: OperationSpec, ctx: SerializeContext) -> str:
    """Signature-driven serializer used by most operations.

    Walks ``__init__``'s parameters: required ones (no default) emit positionally in declaration
    order; optional ones emit as ``name=value`` only when the stored value differs from the
    parameter default. Parameters with no corresponding attribute on the instance are silently
    skipped, allowing ``__init__`` to accept a kwarg the body doesn't bother to store.

    Args:
        op (Operation): Operation instance to serialize.
        spec (OperationSpec): The spec describing ``op``; supplies the qualified keyword and the
            class whose signature is walked.
        ctx (SerializeContext): Writer instance — exposes ``serialize_bus``, ``serialize_value``, etc.

    Returns:
        The full statement line: the qualified keyword, then positional arguments, then keyword
        arguments.
    """
    sig = inspect.signature(spec.cls.__init__)
    # skip self
    params = list(sig.parameters.values())[1:]
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
    ctx: ParseContext,
) -> dict[str, Any]:
    """Bind operation-line tokens to ``cls.__init__``'s parameters.

    A token is a kwarg iff it contains ``=`` outside any parenthesized group and outside leading
    quotes. Positional tokens bind by index to the constructor signature; the result dict is used
    for an all-keyword construction so positional ordering can't drift.

    Args:
        cls (type[Operation]): Class whose ``__init__`` the tokens bind to.
        tokens (list[str]): Tokens of the statement body, the keyword already consumed.
        ctx (ParseContext): Parser instance — supplies ``parse_value`` and ``parse_error``.

    Returns:
        The keyword arguments to construct ``cls`` with.

    Raises:
        ParseError: If there are more positional tokens than the constructor has parameters —
            silently dropping the excess would load a *different* program without any error.
    """
    sig = inspect.signature(cls.__init__)
    # skip self
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
            positional.append(tok_stripped)
    if len(positional) > len(params):
        extra = positional[len(params) :]
        msg = (
            f"too many arguments for {cls.__name__!r}: {len(positional)} positional tokens but "
            f"the operation takes at most {len(params)}; unexpected: {extra!r}. If you meant an "
            f"arithmetic expression, parenthesize it: `(100 - t)`."
        )
        raise ctx.parse_error(msg)
    final: dict[str, Any] = {}
    for i, tok_stripped in enumerate(positional):
        final[params[i].name] = ctx.parse_value(tok_stripped)
    final.update(kwargs)
    return final


def _construct_operation(cls: type[Operation], final: dict[str, Any], ctx: ParseContext) -> Operation:
    """Instantiate ``cls(**final)``, converting constructor failures into a line-tagged error.

    Two failure shapes are folded in. A ``TypeError`` means the bound arguments don't fit the
    signature (unknown kwarg, missing required parameter). A [`ValidationError`][qprogram.ValidationError] means an
    argument reached a constructor-side validator and was rejected on its merits (e.g. an unknown
    measurement field name in ``fields=[...]``); its message is already specific, so it is passed
    through verbatim under the line tag. Either way, letting the raw exception escape would lose
    the line number that ``source_map`` and the editor tooling depend on.

    Args:
        cls (type[Operation]): Class to instantiate.
        final (dict[str, Any]): Keyword arguments from `_bind_signature_tokens`.
        ctx (ParseContext): Parser instance — supplies ``parse_error``.

    Returns:
        The constructed operation.

    Raises:
        ParseError: On any constructor ``TypeError`` or [`ValidationError`][qprogram.ValidationError].
    """
    try:
        return cls(**final)
    except TypeError as e:
        msg = f"cannot construct {cls.__name__!r} from the given arguments: {e}"
        raise ctx.parse_error(msg) from e
    except ValidationError as e:
        raise ctx.parse_error(str(e)) from e


def default_parse_operation(spec: OperationSpec, tokens: list[str], ctx: ParseContext) -> Operation:
    """Signature-driven parser used by most operations.

    Args:
        spec (OperationSpec): The spec describing the target class.
        tokens (list[str]): Tokens of the operation body (the leading keyword has been consumed).
        ctx (ParseContext): Parser instance — exposes ``parse_value``, ``get_or_create_handle``, etc.

    Returns:
        A freshly-constructed [`Operation`][qprogram.operations.Operation] instance.

    Raises:
        ParseError: On excess positional tokens or arguments the constructor rejects.
    """
    final = _bind_signature_tokens(spec.cls, tokens, ctx)
    return _construct_operation(spec.cls, final, ctx)


def measurement_op_serialize(op: MeasurementOperation, ctx: SerializeContext) -> str:
    """Signature-driven serializer for `MeasurementOperation` subclasses.

    Mirrors `default_serialize_operation` but skips the ``handle`` constructor parameter and
    emits the measurement name as a ``name="..."`` kwarg instead — the wire format reads as intent
    (``measure "ro" "r" "w" name="q0/readout/m0"``) rather than as a bare positional string. The
    parse side (`make_measurement_op_parse`) resolves ``name=`` back to the canonical
    handle instance.

    Args:
        op (MeasurementOperation): Measurement operation to serialize.
        ctx (SerializeContext): Writer instance — exposes ``serialize_value``.

    Returns:
        The full statement line, with ``name=`` between the positional arguments and any other
        keyword arguments.
    """
    spec = get_operation_spec_by_class(type(op))
    qualified = spec.qualified_name if spec is not None else type(op).__name__
    sig = inspect.signature(type(op).__init__)
    # skip self
    params = list(sig.parameters.values())[1:]
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


def make_measurement_op_parse(cls: type[Operation]) -> Callable[[list[str], ParseContext], Operation]:
    """Build a parse callback for a `MeasurementOperation` subclass.

    The returned callback mirrors `default_parse_operation` but resolves the measurement
    name to the canonical [`MeasurementHandle`][qprogram.MeasurementHandle] via ``ctx.get_or_create_handle``.
    This is what lets every measurement op and every [`MeasurementRef`][qprogram.MeasurementRef] referring to the
    same name share one Python instance after a ``.qp`` load. Three accepted spellings:

    - ``name="..."`` kwarg — the canonical form the writer emits.
    - a quoted handle name bound to the ``handle`` positional slot.
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
        cls (type[Operation]): The measurement-operation subclass to build the parser for.

    Returns:
        A signature-driven parse callback ready for `register_vendor_operation`.
    """

    def parse(tokens: list[str], ctx: ParseContext) -> Operation:
        final = _bind_signature_tokens(cls, tokens, ctx)
        if "returns" in final:
            # ``returns=`` earns its own diagnostic rather than a generic unexpected-kwarg error:
            # both the keyword and the value shape differ from ``fields=[...]``, and neither
            # difference is guessable from "unexpected keyword argument".
            msg = (
                "`returns=` was replaced by `fields=`, and the value is now a bracket list of "
                'field names rather than a comma-joined string: write `fields=["state", "iq"]` '
                'instead of `returns="state,iq"`.'
            )
            raise ctx.parse_error(msg)
        name = final.pop("name", None)
        if name is not None:
            if not isinstance(name, str):
                msg = f"measurement name= must be a quoted string, got {name!r}"
                raise ctx.parse_error(msg)
            final["handle"] = ctx.get_or_create_handle(name)
        elif "handle" in final and isinstance(final["handle"], str):
            # A bare quoted token in the handle slot names the handle.
            final["handle"] = ctx.get_or_create_handle(final["handle"])
        elif "handle" not in final:
            # Hand-written file without a name — allocate one exactly like the builder would.
            final["handle"] = ctx.allocate_measurement_handle(final.get("bus", ""))
        return _construct_operation(cls, final, ctx)

    return parse


def _looks_like_kwarg(tok: str) -> bool:
    """Return ``True`` if ``tok`` looks like a ``key=value`` pair.

    The test is deliberately shallow: the token must contain ``=``, must not open with a quote, and
    must carry no ``(`` before the first ``=``. That is enough to separate a keyword argument from
    the two positional shapes it could otherwise be confused with — a constructor call such as
    ``Gaussian(amplitude=0.5)`` (paren in the prefix) and a quoted string such as ``"key=value"``
    (leading quote). Nothing beyond those three conditions is checked: the prefix is not verified to
    be an identifier, and a quote anywhere but the first character is ignored.

    Args:
        tok (str): A single statement token, already stripped.

    Returns:
        ``True`` for a ``key=value`` token, ``False`` for a positional one.
    """
    if "=" not in tok or tok.startswith('"'):
        return False
    prefix, _, _ = tok.partition("=")
    return "(" not in prefix


# ---------------------------------------------------------------------------
# Special-case operation callbacks
# ---------------------------------------------------------------------------


def sync_serialize(op: Sync, ctx: SerializeContext) -> str:
    """Serialize as ``sync`` (no buses) or ``sync <bus> [<bus> ...]``.

    Args:
        op (Sync): The sync operation.
        ctx (SerializeContext): Writer instance — exposes ``serialize_bus``.

    Returns:
        The statement line. The bare keyword means synchronize every bus in the program.
    """
    if op.targets:
        return "sync " + " ".join(ctx.serialize_bus(b) for b in op.targets)
    return "sync"


def sync_parse(tokens: list[str], ctx: ParseContext) -> Sync:
    """Parse a ``sync`` body, in which every token is a bus.

    Args:
        tokens (list[str]): Bus tokens after the keyword. Empty means synchronize every bus.
        ctx (ParseContext): Parser instance — exposes ``parse_value``.

    Returns:
        The reconstructed operation, with ``targets=None`` for the bare keyword.
    """
    if not tokens:
        return Sync(targets=None)
    # A bus token decodes to a plain ``str`` (a bus path) or to a quoted string; `parse_value`
    # widens both to ``object``, and ``_upgrade_busrefs`` promotes them to BusRefs afterwards.
    return Sync(targets=cast("list[str]", [ctx.parse_value(tok) for tok in tokens]))


def get_parameter_serialize(op: GetParameter, ctx: SerializeContext) -> str:
    """Serialize as ``get_parameter <bus> "param" -> <ident>``.

    The result variable appears after the ``->`` arrow rather than as a positional or kwarg —
    matches the visual convention that ``get_parameter`` *produces* a value. ``ctx.serialize_value``
    emits ``bus`` as a bus path when it is a schema-backed [`BusRef`][qprogram.BusRef] and as a quoted
    string otherwise.

    Args:
        op (GetParameter): The operation to serialize.
        ctx (SerializeContext): Writer instance — exposes ``var_ident`` and ``serialize_value``.

    Returns:
        The statement line.
    """
    ident = ctx.var_ident(op.variable)
    bus = ctx.serialize_value(op.bus)
    parameter = ctx.serialize_value(op.parameter)
    return f"get_parameter {bus} {parameter} -> {ident}"


def get_parameter_parse(tokens: list[str], ctx: ParseContext) -> GetParameter:
    """Inverse of `get_parameter_serialize`.

    Splits on the ``->`` token; right side is the target variable identifier (auto-declared if new),
    left side is the ``bus parameter`` layout. The bus token is promoted to a [`BusRef`][qprogram.BusRef]
    by the parser's ``_upgrade_busrefs`` pass after construction (``GetParameter.BUS_ATTRS == ("bus",)``).

    Args:
        tokens (list[str]): Tokens after the keyword, arrow included.
        ctx (ParseContext): Parser instance — exposes ``parse_value``, ``get_or_declare_variable`` and
            ``parse_error``.

    Returns:
        The reconstructed operation.

    Raises:
        ParseError: If the ``->`` arrow or its target is missing, or the bus and parameter name are
            not both present.
    """
    arrow_idx = next((i for i, t in enumerate(tokens) if t == "->"), None)
    if arrow_idx is None or arrow_idx + 1 >= len(tokens):
        msg = "get_parameter requires '-> <var>' assignment"
        raise ctx.parse_error(msg)
    var_name = tokens[arrow_idx + 1]
    body = tokens[:arrow_idx]
    if len(body) < 2:
        msg = "get_parameter requires bus and parameter name"
        raise ctx.parse_error(msg)
    # Both tokens decode to strings — see the note in `sync_parse`.
    bus = cast("str", ctx.parse_value(body[0]))
    parameter = cast("str", ctx.parse_value(body[1]))
    var = ctx.get_or_declare_variable(var_name)
    return GetParameter(
        variable=var,
        bus=bus,
        parameter=parameter,
    )


# ---------------------------------------------------------------------------
# Block header callbacks
# ---------------------------------------------------------------------------


def average_serialize_header(block: Average, ctx: SerializeContext) -> str:  # ruff: ignore[unused-function-argument]
    """Serialize as ``average <shots>``.

    Args:
        block (Average): The averaging block.
        ctx (SerializeContext): Writer instance. Unused — the shot count is a plain integer.

    Returns:
        The header text, without the trailing colon.
    """
    return f"average {block.shots}"


def average_parse_header(tokens: list[str], ctx: ParseContext) -> Average:
    """Parse ``average <shots>`` — single positional integer.

    Args:
        tokens (list[str]): Tokens after the keyword; the first is the shot count.
        ctx (ParseContext): Parser instance — exposes ``parse_error``.

    Returns:
        The reconstructed block, with an empty body for the caller to fill.

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
# Helpers
# ---------------------------------------------------------------------------


def _parse_number(s: str) -> int | float:
    """Parse a numeric literal preserving ``int`` vs ``float``.

    The distinction matters because round-tripping must not silently promote integer sweep bounds to
    floats — that would visibly change the rewritten ``.qp`` file. Non-finite values (``inf``,
    ``nan``) stay floats — calling ``int()`` on them would raise ``OverflowError`` / ``ValueError``.

    Args:
        s (str): The token to parse. Surrounding whitespace is ignored.

    Returns:
        An ``int`` when the literal is written without a decimal point or exponent and its value is
        integral, otherwise a ``float``.

    Raises:
        ValueError: If ``s`` is not a numeric literal. Callers treat this as "the token is something
            else" and fall back to their next candidate type.
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
    """Register all core operations, blocks, and sweep sources.

    Invoked once from `qprogram.serialization` at import time. Idempotent — re-registering the
    same classes refreshes their callbacks and leaves everything else untouched.
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

    # Blocks — the generic ``block:`` has no header data and uses defaults.
    register_block("block", Block)
    register_block(
        "average",
        Average,
        serialize_header=average_serialize_header,
        parse_header=average_parse_header,
    )

    # Sweep sources — registered by class name, exactly like waveforms. Both the parse and the write
    # side are signature-driven from ``__init__``, so none of these needs a callback and a new source
    # needs no change here beyond one line.
    for source_cls in (Range, Values, Linspace, Logspace, File, Repeat, Rotate, Concat):
        register_sweep_source(source_cls)
