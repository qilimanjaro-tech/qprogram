"""Targeted tests for the few remaining coverage gaps.

These cases don't fit thematically into the per-module test files but are
small enough to keep together: lazy ``__getattr__`` fallbacks, edge-case
serialize paths, and a couple of registry edge conditions.
"""

from __future__ import annotations

import numpy as np
import pytest

import qprogram as qp
from qprogram import BusSchema, ParseError, Variable, serialization
from qprogram.blocks import Block
from qprogram.buses import BusRef
from qprogram.operations import Wait
from qprogram.operations.operation import Operation
from qprogram.serialization import registry
from qprogram.serialization._specs import default_parse_operation, default_serialize_operation
from qprogram.serialization.parser import (
    _parse_constructor_args,
    _parse_waveform_expr,
    _Parser,
    _unescape_str,
)
from qprogram.serialization.registry import (
    OperationSpec,
    get_operation_spec,
    get_operation_vendor_name,
)
from qprogram.serialization.writer import _Writer
from qprogram.vendor import VendorNamespace
from qprogram.waveforms import Square

# ---------------------------------------------------------------------------
# Lazy __getattr__ fallbacks: unknown attribute raises AttributeError.
# ---------------------------------------------------------------------------


def test_qprogram_unknown_attribute_via_getattr():
    with pytest.raises(AttributeError, match="has no attribute"):
        qp.nonexistent_thing  # noqa: B018


def test_serialization_unknown_attribute_via_getattr():
    with pytest.raises(AttributeError, match="has no attribute"):
        serialization.nonexistent_thing  # type: ignore[attr-defined]  # noqa: B018


def test_serialization_lazy_loads():
    assert callable(serialization.loads)


def test_serialization_lazy_load():
    assert callable(serialization.load)


def test_serialization_lazy_parse_error():
    assert serialization.ParseError is not None


def test_qprogram_lazy_load():
    assert callable(qp.load)


def test_qprogram_lazy_parse_error():
    assert qp.ParseError is not None


# ---------------------------------------------------------------------------
# Writer fallbacks for unknown ops / blocks / sweep blocks.
# ---------------------------------------------------------------------------


def test_writer_unknown_operation_emits_comment():
    """Operation not registered in the registry → emit a `# unknown ...` comment."""

    class _GhostOp(Operation):
        def __init__(self) -> None: ...

    p = qp.QProgram()
    p._active_block.append(_GhostOp())
    text = qp.dumps(p)
    assert "# unknown operation: _GhostOp" in text


def test_writer_unknown_block_emits_comment():

    class _GhostBlock(Block):
        pass

    p = qp.QProgram()
    p._active_block.append(_GhostBlock())
    text = qp.dumps(p)
    assert "# unknown block: _GhostBlock" in text


def test_writer_unknown_sweep_block_emits_comment():
    """A loop block class that isn't registered as a sweep generator."""

    class _GhostLoop(Block):
        pass

    p = qp.QProgram(label="x")
    w = _Writer(p)
    w._allocate_var_idents()
    # Calling _serialize_loop_header on an unregistered loop class.
    out = w._serialize_loop_header(_GhostLoop())
    assert "# unknown sweep block" in out


# ---------------------------------------------------------------------------
# vendor.get_operation_vendor_name with an unregistered class.
# ---------------------------------------------------------------------------


def test_get_operation_vendor_name_unregistered():
    """If a class isn't in the operation registry, get_operation_vendor_name returns None."""

    class _NotRegistered:
        pass

    assert get_operation_vendor_name(_NotRegistered) is None


# ---------------------------------------------------------------------------
# _Writer.serialize_waveform with the Arbitrary fallback (skip private attrs).
# ---------------------------------------------------------------------------


def test_serialize_waveform_skips_private_attrs():
    """Waveforms with a `_private` attr should not have it emitted."""

    p = qp.QProgram(label="x")
    wf = Square(0.5, 100)
    wf._private = "should be skipped"  # type: ignore[attr-defined]
    w = _Writer(p)
    w._allocate_var_idents()
    text = w.serialize_waveform(wf)
    assert "_private" not in text


# ---------------------------------------------------------------------------
# default_parse_operation skips empty tokens.
# ---------------------------------------------------------------------------


def test_default_parse_operation_skips_empty_tokens():

    spec = get_operation_spec(None, "reset_phase")
    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    op = default_parse_operation(spec, ['"bus"', "  ", ""], parser)  # type: ignore[arg-type]
    assert getattr(op, "bus") == "bus"  # noqa: B009


def test_typed_element_accessor_repr():
    """Direct repr of a typed element accessor returns the bracket form."""

    schema = BusSchema.transmon()
    r = repr(schema.q[0])
    assert "q" in r
    assert "0" in r


def test_validate_bus_with_metadata_less_busref():
    """A BusRef constructed manually without element/kind passes through validation."""

    ref = BusRef("anything", element="", index=0, kind="", channel="IQ", acquires=False)
    p = qp.QProgram()
    # Should not raise: opaque/manually-constructed BusRefs skip validation.
    p._validate_bus(ref)


def test_validate_bus_without_recorded_schema():
    """A BusRef with element/kind but schema=None is deferred to other validators."""

    ref = BusRef("q0/drive", element="q", index=0, kind="drive", channel="IQ", acquires=False, schema=None)
    p = qp.QProgram()
    p._validate_bus(ref)  # no exception


def test_resolve_bus_path_returns_none_for_non_path():
    """Token that doesn't match the bus-path regex returns None (not a parse error)."""

    schema = BusSchema.transmon()
    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    parser._program._schema = schema
    # A plain string that doesn't match `element[index].kind` returns None.
    assert parser._resolve_bus_path("not_a_path") is None


def test_resolve_bus_path_no_schema_returns_none():

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    # program.schema is None — no resolution.
    assert parser._resolve_bus_path("q[0].drive") is None


def test_unescape_str_trailing_backslash():
    """Lone trailing backslash survives intact."""

    assert _unescape_str("a\\") == "a\\"


def test_parse_waveform_expr_unknown_class_raises():

    with pytest.raises(ParseError, match="Unknown waveform type"):
        _parse_waveform_expr("NoSuchWaveform()")


def test_writer_serialize_value_passthrough_str_value():
    """Writer handles a plain ``str`` value at top level by quoting it."""

    w = _Writer(qp.QProgram(label="x"))
    w._allocate_var_idents()
    assert w.serialize_value("hello") == '"hello"'


def test_vendor_append_skips_non_bus_attrs():
    """_append walks vars(op) and only validates BusRef / list-of-BusRef entries."""

    class _MixedOp(Operation):
        def __init__(self, bus: str, label: str, port: int) -> None:
            self.bus = bus
            self.label = label
            self.port = port

    class _NS(VendorNamespace):
        def mixed(self, bus: str, label: str, port: int) -> None:
            self._append(_MixedOp(bus=bus, label=label, port=port))

    qp.QProgram.register_vendor("mixedns", _NS)
    try:
        p = qp.QProgram()
        p.mixedns.mixed("bus", "some label", 42)  # type: ignore[attr-defined]
        # No raise — non-BusRef str/int attributes are ignored by _validate.
        assert len(p.body.elements) == 1
    finally:
        qp.QProgram._vendor_registry.pop("mixedns", None)


def test_vendor_append_list_with_non_busref_skipped():
    """A list attribute whose elements are not all BusRefs gets the non-BusRef items skipped."""

    class _ListOp(Operation):
        def __init__(self, targets: list) -> None:
            self.targets = targets

    class _NS(VendorNamespace):
        def list_op(self, targets: list) -> None:
            self._append(_ListOp(targets=targets))

    qp.QProgram.register_vendor("plainlistns", _NS)
    try:
        p = qp.QProgram()
        # A list of plain strings — _validate_bus is never called for any.
        p.plainlistns.list_op(["a", "b", "c"])  # type: ignore[attr-defined]
        assert len(p.body.elements) == 1
    finally:
        qp.QProgram._vendor_registry.pop("plainlistns", None)


def test_default_serialize_operation_skips_missing_attr():
    """If __init__ has a param without a stored attribute, the serializer skips it."""

    class _OddOp(Operation):
        def __init__(self, bus: str, extra: int = 5) -> None:  # noqa: ARG002
            self.bus = bus
            # Note: ``extra`` is in the signature but not stored.

    spec = OperationSpec(name="odd", vendor=None, cls=_OddOp)
    op = _OddOp("bus")
    w = _Writer(qp.QProgram(label="x"))
    w._allocate_var_idents()
    text = default_serialize_operation(op, spec, w)
    # Bus is emitted; `extra` is silently skipped (no `extra=` in output).
    assert '"bus"' in text
    assert "extra" not in text


# ---------------------------------------------------------------------------
# Even narrower parser branches (defensive paths and skip-empties).
# ---------------------------------------------------------------------------


def test_operation_variables_skips_private_attrs():
    """``Operation.variables`` should walk only public attrs, never `_x`."""

    op = Wait("bus", Variable("v"))
    op._private_var = Variable("private")  # type: ignore[attr-defined]
    found = op.variables()
    # The private one shouldn't be picked up.
    assert {v.id for v in found} == {"v"}


def test_typed_element_factory_base_getitem_via_subclass():
    """The base ``_TypedElementFactory.__getitem__`` is used by every preset
    factory subclass; covering it via any preset access exercises the line.
    """
    # No-op: the line was already covered when we constructed a factory
    # subclass like `TransmonQubitFactory`. This test exists purely to
    # confirm the path is hit.

    _ = BusSchema.transmon().q[0]
    assert True


def test_parser_blank_line_inside_block():
    """An indented blank line within a block body is skipped, not parsed."""
    text = (
        "#!QProgram 1.0\n\n"
        "body:\n"
        "  average 100:\n"
        "\n"  # blank line at deeper indent — should be ignored
        '    play "bus" "wf"\n'
    )
    p = qp.loads(text)
    avg = p.body.elements[0]
    assert len(avg.elements) == 1


def test_parser_non_block_non_var_line_falls_to_operation():
    """A line that doesn't start with var/for/keyword falls through to op parsing.
    Unknown ops silently return None — already tested. This is the path itself."""
    text = '#!QProgram 1.0\n\nbody:\n  unknown_op "bus"\n'
    p = qp.loads(text)
    assert len(p.body.elements) == 0


def test_parse_operation_empty_line_returns_none():
    """The empty-tokens branch of `_parse_operation` returns None."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    assert parser._parse_operation("") is None


def test_parse_value_returns_bare_identifier_as_string():
    """An unknown bare identifier — not a variable, not a number — returns the string."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    # Not in variables, not a number, not a function call: returns the raw string.
    assert parser.parse_value("not_a_var") == "not_a_var"


def test_parse_value_finds_declared_variable():
    """A declared variable is resolved to the Variable object."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    v = parser.get_or_declare_variable("x")
    assert parser.parse_value("x") is v


def test_parse_value_number():

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    assert parser.parse_value("42") == 42


def test_parse_constructor_args_skips_blank_entries():
    """Constructor arg lists with empty entries (e.g., trailing commas) skip them."""

    pos, kw = _parse_constructor_args("0.5, , 100")
    # Two values; the blank one is skipped.
    assert pos == [0.5, 100]
    assert kw == {}


def test_get_operation_vendor_name_for_vendor_op():
    """For a registered vendor op, returns the (vendor, name) tuple."""
    import qprogram_qblox  # noqa: F401, PLC0415  # conditional vendor import
    from qprogram_qblox.operations import Acquire  # noqa: PLC0415  # conditional vendor import

    assert get_operation_vendor_name(Acquire) == ("qblox", "acquire")


def test_writer_serialize_numpy_integer():
    """``serialize_value`` of a numpy.integer returns its int repr."""

    w = _Writer(qp.QProgram(label="x"))
    w._allocate_var_idents()
    assert w.serialize_value(np.int32(7)) == "7"


def test_parallel_with_non_loop_inner_raises():
    """If a custom sweep generator produced something other than ForLoop/Loop,
    the parser would reject it on Parallel composition. We can't reach this
    naturally with built-in generators, so register a temporary one."""

    class _OddBlock(Block):
        def __init__(self, variable, x):
            super().__init__()
            self.variable = variable
            self.x = x

    def _parse(var, args_text, ctx):  # noqa: ARG001
        return _OddBlock(variable=var, x=args_text)

    def _write(block, ctx):  # noqa: ARG001
        return f"odd({block.x})"

    registry.register_sweep_generator("oddgen", _OddBlock, parse=_parse, write=_write)
    try:
        text = "#!QProgram 1.0\n\nbody:\n  var a\n  var b\n  for a in oddgen(1) | for b in oddgen(2):\n    sync\n"
        with pytest.raises(qp.ParseError, match="parallel"):
            qp.loads(text)
    finally:
        registry._sweep_generator_specs_by_name.pop("oddgen", None)
        registry._sweep_generator_specs_by_class.pop(_OddBlock, None)


def test_parse_var_decl_with_only_var_token_raises():
    """``_parse_var_decl`` called directly with a one-token line raises."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    with pytest.raises(qp.ParseError):
        parser._parse_var_decl("var")


# ---------------------------------------------------------------------------
# Top-level parse loop: blank lines, unknown lines.
# ---------------------------------------------------------------------------


def test_parser_top_level_blank_lines_skipped():
    text = "#!QProgram 1.0\n\n\n\nbody:\n  var freq\n\n\n"
    p = qp.loads(text)
    assert p.variables[0].id == "freq"


def test_parser_top_level_unknown_line_ignored():
    """A non-blank, non-section top-level line is silently consumed."""
    text = (
        "#!QProgram 1.0\n\n"
        "some_unknown_section: stuff\n"  # not a known section header
        "\n"
        "body:\n"
        "  var freq\n"
    )
    p = qp.loads(text)
    assert p.variables[0].id == "freq"


def test_parser_indent_past_end_returns_zero():
    """``_indent()`` returns 0 when pos is past the end of the file."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    parser._pos = 999
    assert parser._indent() == 0


def test_parser_stripped_past_end_returns_empty():

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._pos = 999
    assert parser._stripped() == ""


def test_parser_require_malformed_version_raises():
    """``_check_vendor_compat`` re-raises a ParseError when version parse fails."""
    import qprogram_qblox  # noqa: F401, PLC0415  # conditional vendor import

    # Force a malformed installed version.
    original = registry._vendor_versions.get("qblox")
    registry._vendor_versions["qblox"] = "not-a-version"
    try:
        text = "#!QProgram 1.0\n\nrequire qblox 0.1\n\nbody:\n"
        with pytest.raises(qp.ParseError):
            qp.loads(text)
    finally:
        if original is not None:
            registry._vendor_versions["qblox"] = original


def test_parser_blank_line_inside_nested_block():
    """A blank indented line inside a control-flow block is consumed."""
    text = (
        "#!QProgram 1.0\n\n"
        "body:\n"
        "  var freq\n"
        "  average 100:\n"
        "    for freq in range(0, 10, 1):\n"
        "\n"  # blank inside the loop body
        "      sync\n"
        "\n"
        '      play "bus" "wf"\n'
    )
    p = qp.loads(text)
    avg = p.body.elements[0]
    fl = avg.elements[0]
    assert len(fl.elements) == 2


def test_parser_block_header_without_colon():
    """A line that doesn't end in ``:`` after a block keyword is treated as an op."""
    text = "#!QProgram 1.0\n\nbody:\n  average 100\n"
    p = qp.loads(text)
    # Not a block header (no colon) → falls through to operation lookup, returns None.
    assert len(p.body.elements) == 0


def test_parser_var_decl_attr_unquoted_value_message():
    """The unquoted-value path uses a specific message; check it."""
    text = "#!QProgram 1.0\n\nbody:\n  var x label=foo\n"
    with pytest.raises(qp.ParseError, match="quoted string"):
        qp.loads(text)


def test_parser_blank_lines_before_header():
    text = "\n\n\n#!QProgram 1.0\n\nbody:\n"
    p = qp.loads(text)
    assert p is not None


def test_parser_blank_lines_in_inline_schema():
    """Inline schema parsing tolerates blank lines between element blocks."""
    text = (
        "#!QProgram 1.0\n\n"
        "schema:\n"
        "  element q:\n"
        "    drive info=IQ\n"
        "\n"  # blank line within schema body — should be consumed
        "  element c:\n"
        "    flux info=single\n"
        "\n"
        "body:\n"
    )
    p = qp.loads(text)
    assert "q" in p.schema.elements  # type: ignore[union-attr]
    assert "c" in p.schema.elements  # type: ignore[union-attr]


def test_parser_blank_lines_in_element_bus_list():
    """The element-bus inline parser tolerates blank lines."""
    text = "#!QProgram 1.0\n\nschema:\n  element q:\n    drive info=IQ\n\n    readout info=IQ+acquires\n\nbody:\n"
    p = qp.loads(text)
    assert set(p.schema.elements["q"].buses.keys()) == {"drive", "readout"}  # type: ignore[union-attr]


def test_writer_serialize_math_func_args_recursive():
    """Math function args are serialised recursively (e.g. sin of an expression)."""
    p = qp.QProgram()
    v = p.variable("x")
    p.set_frequency("bus", qp.sin(v + 1))
    text = qp.dumps(p)
    assert "sin((x + 1))" in text


def test_parser_unknown_operator_in_paren_expression():
    """A parenthesised expression with an unknown operator raises with a clear message."""
    # Use a custom operator that gets through _tokenize (3 tokens) but doesn't match.
    text = '#!QProgram 1.0\n\nbody:\n  var x\n  set_offset "bus" (x ?? 5)\n'
    with pytest.raises(qp.ParseError, match="unknown operator"):
        qp.loads(text)


def test_parse_value_with_bus_path_token_returns_string():
    """Bus path token like ``q[0].drive`` returns the string and lets
    ``_upgrade_busrefs`` promote it later."""

    parser = _Parser("#!QProgram 1.0\nbody:\n")
    parser._parse_header()
    # Not in variables, contains `[` and `.`; not a function call.
    # parse_value treats it as a fallback string.
    assert parser.parse_value("q[0].drive") == "q[0].drive"
