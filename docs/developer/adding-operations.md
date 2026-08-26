# Adding a core operation

This page adds an operation to the core `qprogram` package. The example is
`SetPower(bus, power)`, a hypothetical real-time power setter. Every core
operation follows the same path, so the steps double as a description of how
`set_phase` and `play` got where they are.

Snippets whose first line is a `# src/qprogram/...` path comment are package
source, and they keep their intra-package imports (`from
qprogram.operations.operation import Operation`): inside the package, `import
qprogram` would close an import cycle. The snippets without that comment are
user code, and follow the convention the rest of the documentation uses, a
single `import qprogram as qp` with everything else reached through `qp.`. The
snippets in [step 6](#step-6-write-tests) are a third case: they are bodies
lifted from files under `tests/`, which import the names they use directly
(`from qprogram import Variable`), so nothing in them is prefixed either.

A vendor-specific operation is a different job, with no core change at all.
[Building a vendor extension](vendor-extensions.md) covers that end to end.

## What a new operation touches

Seven edits, in dependency order:

1. `src/qprogram/operations/set_power.py`: the class, subclassing `Operation`
   and overriding `required_capabilities()`.
2. `src/qprogram/operations/__init__.py`: the import and the `__all__` entry
   that make the class `qp.operations.SetPower`.
3. `src/qprogram/qprogram.py`: the `QProgram.set_power` builder method.
4. `src/qprogram/serialization/_specs.py`: one `register_operation` line in
   `_register_core_specs()`.
5. `src/qprogram/protocol.py`: the `op.set_power` token in `_BASE_TOKENS`.
6. The test files that cover the operation, listed in
   [step 6](#step-6-write-tests).
7. The four documentation pages that enumerate the operations, listed in
   [step 7](#step-7-document-it).

Three places that look like they need an edit do not. The canonical grammar in
`src/qprogram/grammar/qp.lark` accepts any identifier as a statement keyword and
leaves the decision to the registries, so a new keyword parses under it
unchanged. The writer and the parser dispatch through the operation registry
rather than through a list of keywords. The editor integration in
`src/qprogram/lsp.py` parses and validates instead of carrying its own keyword
table.

## Step 1: Create the operation class

```python
# src/qprogram/operations/set_power.py
from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.operation import Operation

if TYPE_CHECKING:
    from qprogram.variable import Expression


class SetPower(Operation):
    """A new output power for a bus, in dBm.

    Args:
        bus (str): Bus whose output power to set.
        power (float | Expression): Power in dBm. Accepts an
            [`Expression`][qprogram.Expression] for sweeps.
    """

    def __init__(self, bus: str, power: float | Expression) -> None:
        self.bus = bus
        self.power = power

    def required_capabilities(self) -> set[str]:
        """Return ``op.set_power`` plus the tokens the ``power`` expression contributes."""
        from qprogram.protocol import expression_tokens  # ruff: ignore[import-outside-top-level]

        return {"op.set_power"} | expression_tokens(self.power)
```

An operation stores its constructor arguments on `self` under the parameter
names, and stores nothing else in public attributes. Both halves of
serialization read `inspect.signature(cls.__init__)`, so a public attribute that
is not a constructor parameter is never written to the file and does not survive
a reload. Give anything computed a leading underscore, or make it a property.

Four class attributes tune the introspection the base class performs. Each has a
default that suits an op with one bus attribute and no waveform, which is why
`SetPower` declares none of them.

| Class attribute | Default | Set by |
|---|---|---|
| `BUS_ATTRS` | `("bus",)` | `Sync` uses `("targets",)`, a list of bus names; `Call` uses `()`, because buses reach a call site only as bound argument values |
| `WAVEFORM_ATTRS` | `()` | `Play` uses `("waveform",)`; `Measure` uses `("waveform", "weights")` |
| `BROADCASTS_WHEN_NO_BUS` | `False` | `Sync`, whose empty target list means every bus in the program |
| `AFFECTS_AVERAGING` | `False` | `MeasurementOperation`, so `measure` and a vendor `acquire` opt in automatically |

From those lists the base class derives the four introspection methods, and
overriding them is rare. `buses()` reads `BUS_ATTRS` and collects plain strings,
`BusRef`s, and lists of either. `waveforms()` reads `WAVEFORM_ATTRS` and skips
`None`, which is what makes an optional waveform parameter work. `variables()`
ignores the attribute lists and walks every public attribute instead, descending
into `Expression` trees, waveform parameters, and nested lists and tuples, so a
symbolic parameter is reported wherever it is stored. `walk()` yields `self`,
since an operation is a leaf.

Equality and hashing are structural, over `vars(self)` through `ast_eq` and
`ast_hash`, which is what lets two independently built `SetPower` instances
compare equal and lets an operation be a dictionary key. The cost is that an
instance must not be mutated after it has been hashed. `QProgram.rebind`
rewrites operations on a fresh `deepcopy` for that reason.

`required_capabilities()` returns the tokens this instance needs, and only
those. The method is non-recursive: the validator walks the tree and unions the
per-node sets, so a node that recursed into its children would double-count.
An operation's own token is its identity (`op.set_power`), and everything else
is a refinement computed from instance state: `expression_tokens(value)` for a
numeric parameter, the channel kind and per-class token for a waveform
parameter, one `measure.fields.<name>` per requested field for a measurement.
`expression_tokens` is imported inside the method rather than at module level,
which is what every core op does, so `qprogram.operations` carries no
import-time dependency on `qprogram.protocol`.
[Capability protocol internals](capability-protocol.md) has the full mechanics.

## Step 2: Export from `operations/__init__.py`

```python
# src/qprogram/operations/__init__.py
from qprogram.operations.set_power import SetPower

__all__ = [
    ...,
    "SetPower",
]
```

Both the import block and `__all__` are alphabetical. This is the list
`qp.operations.SetPower` resolves through, and it is not everything the
subpackage defines: `MeasurementOperation` stays out, and the API reference
documents it under its full path,
`qprogram.operations.operation.MeasurementOperation`.

## Step 3: Add a method on `QProgram`

```python
# src/qprogram/qprogram.py
from qprogram.operations.set_power import SetPower


class QProgram:
    def set_power(self, bus: str, power: float | Expression) -> None:
        """Append a [`SetPower`][qprogram.operations.SetPower], setting the output power on ``bus``.

        Args:
            bus (str): Bus whose output power to set.
            power (float | Expression): Power in dBm. Accepts an
                [`Expression`][qprogram.Expression] for sweeps.

        Raises:
            ValidationError: If ``bus`` comes from another schema.
        """
        self._validate_bus(bus)
        self._append_to_active(SetPower(bus=bus, power=power))
```

Put the method in the `--- Core operations ---` section of the file, next to the
other bus-scoped setters; the API reference lists members in source order.

`_validate_bus` rejects a `BusRef` produced by a different `BusSchema` than the
one attached to the program, and adopts the ref's schema when the program has
none yet. Plain strings and refs without schema metadata pass through. Call it
once per bus attribute, the way `sync` does for each entry in its target list.
An operation that takes a waveform also calls the module-level
`_validate_waveform_channel(bus, waveform)`, which raises `ValidationError` when
a single-channel waveform lands on an IQ bus or the reverse.

`_append_to_active` appends to the innermost block still open on the block
stack. It also closes a pending `if_` chain when the append lands at the chain's
own level, because anything other than `elif_` or `else_` there makes the chain
ambiguous; appends inside an arm body sit at a deeper level and leave the chain
open.

The method name is not what names the operation on the wire. The registration in
step 4 does that, and matching the two is a convention every core op follows.

## Step 4: Register it with the serializer

Most operations need no writer or parser code. Add one line to
`_register_core_specs()`:

```python
# src/qprogram/serialization/_specs.py
from qprogram.operations.set_power import SetPower


def _register_core_specs() -> None:
    register_operation("set_power", SetPower)
```

The first argument is the keyword as it appears in a `.qp` file. Re-registering
the same class under the same name refreshes its callbacks and is allowed, since
import-time registration modules can run twice; registering a *different* class
under a taken name raises `ValueError` rather than changing how every existing
file parses that keyword.

`default_serialize_operation` walks the constructor parameters after `self`.
Parameters with no default are emitted positionally in declaration order, and
parameters with a default are emitted as `name=value` only when the stored value
differs from that default. A parameter with no matching attribute is skipped, so
`__init__` may accept a keyword it does not store. The result is
`set_power "drive_q0" 5.0`, and `set_power "drive_q0" pw` when the power is a
swept variable.

`default_parse_operation` inverts it. A token counts as a keyword argument when
it contains an `=` that is not inside leading quotes and has no `(` before it,
which is what keeps `Gaussian(amplitude=0.5)` and `"key=value"` positional. The
remaining tokens bind by index to the constructor parameters, and the operation
is then constructed entirely from keywords, so positional order cannot drift.
Two failures get their own messages, each prefixed by the parser with
`Line <n>:`:

```
set_power "drive_q0" 5.0 7.0
# ParseError: too many arguments for 'SetPower': 3 positional tokens but the
# operation takes at most 2; unexpected: ['7.0']. If you meant an arithmetic
# expression, parenthesize it: `(100 - t)`.

set_power "drive_q0" bogus=1
# ParseError: cannot construct 'SetPower' from the given arguments:
# SetPower.__init__() got an unexpected keyword argument 'bogus'
```

Excess positional tokens are an error rather than a truncation, because dropping
them would load a different program than the file describes. A `ValidationError`
raised by the constructor itself is passed through under the same line tag,
which is how an unknown measurement field in a `fields=[...]` list reports its
own message with a line number attached.

### When a custom callback is needed

Three core operations do not fit "keyword, then positional arguments, then
keyword arguments", and each shows what a callback is for. All three live in
`_specs.py`:

- `sync` has a variadic bus list rather than a fixed parameter list, so
  `sync_serialize` writes `sync` or `sync <bus> ...` and `sync_parse` reads
  every token as a bus.
- `get_parameter` writes its result variable after a `->` arrow, so
  `get_parameter_serialize` places the identifier itself and
  `get_parameter_parse` splits the token list on the arrow.
- `measure` carries a `MeasurementHandle` that the file names rather than
  spells, so `measurement_op_serialize` skips the `handle` parameter and emits
  `name="..."`, and `make_measurement_op_parse(cls)` resolves that name back to
  the canonical handle instance through `ctx.get_or_create_handle`, which is
  what makes every reference to one measurement the same Python object after a
  load.

```python
# src/qprogram/serialization/_specs.py
register_operation("sync", Sync, serialize=sync_serialize, parse=sync_parse)
```

One core operation is registered nowhere. `call` is written as
`<fragment_name>(<args>)` rather than as a keyword-led statement, so the writer
and the parser handle it directly. An operation whose statement shape differs
that much from the others needs writer and parser code of its own rather than a
spec callback.

## Step 5: Register the capability token

```python
# src/qprogram/protocol.py
_BASE_TOKENS: frozenset[str] = frozenset(
    {
        "op.set_power",
    },
)
```

`CAPABILITY_REGISTRY` is seeded from `_BASE_TOKENS` at import time, and
`Profile.__post_init__` validates every token a profile lists against it. A
token that is not registered therefore fails at profile construction, which for
a vendor package means at import:

```
ValueError: Unknown capability token(s): ['op.set_power']. Register via qprogram.protocol.register_capability_tokens before use.
```

Registering the token makes it spellable. Advertising it is a separate act, and
where it belongs follows from how the validator routes the node. An op whose
`BUS_ATTRS` resolve to one or more bus names is checked against
`caps.for_bus(bus)` for each of them, and the results are intersected; an op with
`BUS_ATTRS = ()` is checked against the platform slot; a broadcast op whose bus
list comes out empty is checked against every bus in the program. `SetPower`
holds one bus, so `op.set_power` belongs in a bus profile, alongside the other
`op.*` tokens. `QPROGRAM_BASE_V1`, the platform-level profile core ships, carries
only block, expression, and sweep tokens for that reason.
`tests/_dummy_vendor.py` shows the other side: its `_CORE_OPS` frozenset is the
set of core operations the dummy backend advertises per bus, unioned into the
`dummy-default-v1` profile.

`reference_capabilities()` grants every token in the live `CAPABILITY_REGISTRY`,
with `set_parameter` and `get_parameter` present only in each bus slot's `host`
half, so a new core operation runs on `qp.ReferencePlatform` as soon as its
token is registered, without touching the executor. A platform that has not
advertised it rejects the program with a `missing-capability` diagnostic naming
the profile and the domains it checked:

```
[error] missing-capability: 'SetPower' requires capability 'op.set_power' which is not supported by 'dummy-default-v1' (rt) / 'dummy-default-v1' (host) (at body[0])
```

## Step 6: Write tests

Tests go next to the ones for the operation the new one most resembles. For
`SetPower`, that is `set_phase`, and these are the files its tests live in.

`tests/test_operations.py` covers the class in isolation, in the shape of
`test_set_phase_construction` and `test_set_phase_variables`:

```python
def test_set_power_construction():
    op = SetPower("bus", 5.0)
    assert op.bus == "bus"
    assert op.power == 5.0
    assert op.buses() == {"bus"}


def test_set_power_variables():
    v = Variable("power")
    assert SetPower("bus", v).variables() == {v}
```

`tests/test_required_capabilities.py` pins the token set, in the shape of
`test_set_phase_picks_up_expr_tokens`. Test both the constant and the symbolic
argument: the refinement tokens are the part that is easy to get wrong.

```python
def test_set_power_token():
    assert SetPower(bus="drive_q0", power=5.0).required_capabilities() == {"op.set_power"}


def test_set_power_picks_up_expr_tokens():
    v = Variable("p")
    assert SetPower(bus="drive_q0", power=v).required_capabilities() == {
        "op.set_power",
        "expr.variable",
    }
```

`tests/test_qprogram.py` covers the builder method, using the `empty_program`
fixture from `tests/conftest.py`, in the shape of `test_set_phase_appends`:

```python
def test_set_power_appends(empty_program):
    empty_program.set_power("bus", 5.0)
    assert isinstance(empty_program.body.elements[0], SetPower)
```

`tests/test_round_trip.py` covers serialization. The existing
`test_round_trip_all_core_operations` builds one program holding every core
operation and calls the module's `_assert_byte_stable` helper, which asserts
that `dumps` after `loads` after `dumps` is identical text; adding one line to
it is usually enough. `tests/test_writer.py` is where a test goes when the
emitted text itself is the point, as `test_dumps_set_phase_int` asserts that an
integer argument is not promoted to a float.

`tests/test_specs.py` covers the signature-driven callbacks rather than any one
operation, so it needs a new test only for an operation with an unusual
signature. `test_default_parse_operation_positional`,
`test_default_parse_operation_kwarg`,
`test_default_parse_operation_extra_positional_raises`, and
`test_default_parse_operation_unknown_kwarg_raises` already cover the four
paths through the defaults.

`tests/test_round_trip_property.py` builds random programs with hypothesis and
asserts byte stability. Its `emit_ops` helper draws from a `sampled_from` list of
operation names and dispatches on the result, so an operation joins the property
tests by adding its name to that list and a branch that calls the builder.
Worth doing for anything with more than one interesting argument shape. See
[Testing](testing.md) for how the suite is organized.

## Step 7: Document it

Four pages enumerate the operations, and all four go stale otherwise.

[`docs/guide/operations.md`](../guide/operations.md) has the "Every core
operation" table, which gives the builder call, the `.qp` statement, and the
capability tokens, plus a short subsection per operation with an example.

[`docs/reference/qp-format.md`](../reference/qp-format.md) has the wire syntax
under "Operations". The prose there counts the core keywords, so the count moves
with the table.

[`docs/reference/api-qprogram.md`](../reference/api-qprogram.md) is generated
from docstrings, but its member lists are explicit: add the builder method to
the `members:` list under `::: qprogram.QProgram` and the class to the one under
`::: qprogram.operations`. A symbol absent from those lists does not appear on
the page at all.

[`docs/guide/capabilities.md`](../guide/capabilities.md) lists the `op.*` tokens
in its token-prefix table.

If the operation interacts with control flow, measurements, or sweeps in a way
that is not obvious from its signature, the matching guide page needs a
paragraph too. `grep -rn set_phase docs/` finds every page that enumerates the
operations.

## Where `set_phase` appears

The closest thing to a checklist is an existing operation. `set_phase` is one of
the plainest, and every row below is a place an operation shaped like it needs an
entry.

| File | What it holds |
|---|---|
| `src/qprogram/operations/set_phase.py` | the class and `required_capabilities()` |
| `src/qprogram/operations/__init__.py` | the import and the `__all__` entry |
| `src/qprogram/qprogram.py` | `QProgram.set_phase` |
| `src/qprogram/serialization/_specs.py` | `register_operation("set_phase", SetPhase)` |
| `src/qprogram/protocol.py` | `"op.set_phase"` in `_BASE_TOKENS` |
| `tests/test_operations.py` | `test_set_phase_construction`, `test_set_phase_variables` |
| `tests/test_qprogram.py` | `test_set_phase_appends` |
| `tests/test_required_capabilities.py` | `test_set_phase_picks_up_expr_tokens` |
| `tests/test_writer.py` | `test_dumps_set_phase_int` |
| `tests/test_round_trip.py` | `test_round_trip_all_core_operations` |
| `tests/test_validation.py` | `_BUS_TOKENS`, the bus profile the validator tests run against |
| `tests/_dummy_vendor.py` | `_CORE_OPS`, the tokens the in-tree vendor advertises |
| `docs/guide/operations.md` | the table row and the `set_phase(bus, phase)` subsection |
| `docs/guide/variables.md` | the list of operations that contribute expression tokens |
| `docs/guide/capabilities.md` | the `op.*` row of the token table |
| `docs/reference/qp-format.md` | the operations table and the worked example |
| `docs/reference/api-qprogram.md` | the `members:` entries for the method and the class |
