# Adding a core operation

This guide adds a new operation to the core `qprogram` package. The example
operation is `SetPower(bus, power)`, a hypothetical real-time power
setter. The same recipe works for any core operation.

If you are adding a vendor-specific operation, follow
[Building a vendor extension](vendor-extensions.md) instead. Vendor operations
have a much smaller surface to touch (no core changes at all).

## Recipe at a glance

A new core op touches seven places. In order:

1. Op class file under `operations/<name>.py`, with a
   `required_capabilities()` method.
2. Export from `operations/__init__.py`.
3. Method on `QProgram` in `qprogram.py` that appends an instance.
4. Registration line in `serialization/_specs.py:_register_core_specs()`.
5. New capability token in `protocol._BASE_TOKENS`; added to whichever
   profile is the right home for it.
6. Tests in `test_operations.py`, `test_qprogram.py`,
   `test_required_capabilities.py`, and `test_round_trip.py`.
7. Docs: the guide page and the `.qp` format reference.

## Step 1: Create the operation class

New file: `src/qprogram/operations/set_power.py`.

```python
from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation
from qprogram.variable import Expression


class SetPower(Operation):
    """Set the output power on a bus in dBm."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, power: float | Expression) -> None:
        self.bus = bus
        self.power = power

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens

        return {"op.set_power"} | expression_tokens(self.power)
```

A few rules.

- The class lives in its own file under `operations/`.
- Attribute order matters when the operation appears with positional
  arguments in `.qp` files: it matches `__init__` argument order.
- `BUS_ATTRS` lists the attributes that hold bus references. The default is
  `("bus",)`, so this declaration is redundant; declare it explicitly when
  the attribute has another name (`Sync.targets`) or when the op touches
  more than one bus.
- `WAVEFORM_ATTRS` is the same idea for waveform attributes. Default `()`.
- The base `Operation` class provides `variables()`, `buses()`,
  `waveforms()`, and `walk()` via these class attributes, plus structural
  equality and hashing. You almost never override them.
- `required_capabilities()` declares the capability tokens *this op
  instance* needs. The base class returns an empty set; concrete ops add
  their identity token (`op.<name>`) plus any refinement tokens computed
  from instance state — expression tokens for numeric arguments, waveform
  channel-kind and per-class tokens for waveform arguments,
  `measure.fields.*` tokens for measurement ops, and so on. The method is
  **non-recursive**: each op returns only its own tokens, and the validator
  walks via `body.walk()` to gather children. See
  [Capability protocol internals](capability-protocol.md) for the full story.

## Step 2: Export from `operations/__init__.py`

Add the class to the package init:

```python
from qprogram.operations.set_power import SetPower

__all__ = [
    ...,
    "SetPower",
]
```

## Step 3: Add a method on `QProgram`

In `src/qprogram/qprogram.py`:

```python
from qprogram.operations.set_power import SetPower


class QProgram:
    ...

    def set_power(self, bus: str, power: float | Expression) -> None:
        """Set the output power in dBm."""
        self._validate_bus(bus)
        self._append_to_active(SetPower(bus=bus, power=power))
```

`_validate_bus` enforces the one-schema-per-program rule for `BusRef`s. If
your operation has multiple bus attributes, call it once per bus.
`_append_to_active` is the appender: it adds the node to the active block
and closes any pending `if_` / `elif_` chain at that level.

## Step 4: Register it with the serializer

Most new operations need **no** writer or parser changes thanks to the
generic dispatch. The default serializer reflects on `__init__` to emit
positional and keyword arguments; the default parser uses
`inspect.signature(cls.__init__)` to bind them back.

You do need to register the operation, though. Add one line to
`src/qprogram/serialization/_specs.py`:

```python
from qprogram.operations.set_power import SetPower


def _register_core_specs() -> None:
    ...
    register_operation("set_power", SetPower)
    ...
```

That is it for serialization. The op now writes as
`set_power "drive_q0" 5.0` and reads back into a `SetPower` instance.

### When you do need a custom callback

A few operations have unusual shapes that the default reflection cannot
handle (`Sync.targets` is a variadic list of bus names rather than
positional args; `GetParameter` uses the arrow `-> var` syntax; measurement
ops carry their handle as a `name="..."` kwarg). For those, pass explicit
callbacks to `register_operation`:

```python
register_operation("sync", Sync, serialize=sync_serialize, parse=sync_parse)
```

The existing special cases in `_specs.py` are the template.

## Step 5: Register the capability token

If your operation introduces a new top-level token, add it to
`_BASE_TOKENS` in `src/qprogram/protocol.py`:

```python
_BASE_TOKENS: frozenset[str] = frozenset(
    {
        ...
        "op.set_power",
        ...
    }
)
```

Registering the token only makes it *spellable*. A platform still has to
advertise it, and where it goes depends on the op: bus-touching ops belong
on bus profiles, bus-less ops on the platform-level profile. For a
bus-touching op like this one, a vendor's bus profile adds it to its
capability set:

```python
_CORE_OPS = frozenset(
    {
        ...,
        "op.set_power",
    }
)
```

The validator rejects a program using an unadvertised op with a
`missing-capability` diagnostic; the token registry also rejects typos at
profile-construction time, so a forgotten registration shows up as an
import error rather than a silent miss at validate-time. See
[Capability protocol internals](capability-protocol.md) for the full
mechanics.

## Step 6: Write tests

The convention is to add tests next to the relevant existing module.

For the operation class itself, add to `tests/test_operations.py`:

```python
def test_set_power_basic():
    op = SetPower("drive_q0", 5.0)
    assert op.bus == "drive_q0"
    assert op.power == 5.0
    assert list(op.buses()) == ["drive_q0"]


def test_set_power_with_variable():
    v = Variable("p")
    op = SetPower("drive_q0", v)
    assert op.power is v
    assert list(op.variables()) == [v]
```

For `required_capabilities()`, add to
`tests/test_required_capabilities.py`:

```python
def test_set_power_token():
    assert SetPower("drive_q0", 5.0).required_capabilities() == {"op.set_power"}


def test_set_power_with_variable_adds_expr_variable():
    v = Variable("p")
    assert SetPower("drive_q0", v).required_capabilities() == {
        "op.set_power",
        "expr.variable",
    }
```

For the `QProgram` method, add to `tests/test_qprogram.py`:

```python
def test_set_power_appends_to_active_block():
    p = QProgram()
    p.set_power("drive_q0", 5.0)
    assert isinstance(p.body.elements[0], SetPower)
```

For round-trip serialization, add to `tests/test_round_trip.py`:

```python
def test_round_trip_set_power():
    p = QProgram()
    p.set_power("drive_q0", 5.0)
    text = qp.dumps(p)
    reloaded = qp.loads(text)
    assert qp.dumps(reloaded) == text
```

The hypothesis-driven properties in `tests/test_round_trip_property.py`
pick up a new operation once its strategy is extended, which is worth doing
for anything with more than one interesting argument shape. See
[Testing](testing.md).

## Step 7: Document it

Two places need a mention.

- [`docs/guide/operations.md`](../guide/operations.md) gets a short
  description with an example.
- [`docs/reference/qp-format.md`](../reference/qp-format.md) gets the wire
  syntax under the operations list.

If the operation has interesting interactions with control flow or
measurements, mention them on the relevant guide page too.

## Worked example: `set_phase`

For comparison, the existing `set_phase` operation is exactly this same
shape. Look at it for a real-world example:

- `src/qprogram/operations/set_phase.py` (the class)
- `src/qprogram/qprogram.py` (`def set_phase`)
- `src/qprogram/serialization/_specs.py` (the registration line)
- `tests/test_operations.py` (the unit tests)

The boilerplate is small on purpose.
