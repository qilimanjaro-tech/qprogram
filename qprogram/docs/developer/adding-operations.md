# Adding a core operation

This guide adds a new operation to the core `qprogram` package. The example
operation is `SetPower(bus, power)`, a hypothetical hardware-real-time power
setter. The same recipe works for any core operation.

If you are adding a vendor-specific operation, follow
[Building a vendor extension](vendor-extensions.md) instead. Vendor operations
have a much smaller surface to touch (no core changes at all).

## Step 1: Create the operation class

New file: `qprogram/src/qprogram/operations/set_power.py`.

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
```

A few rules.

- The class lives in its own file under `operations/`.
- Attribute order matters when the operation appears with positional
  arguments in `.qp` files: it matches `__init__` argument order.
- `BUS_ATTRS` lists the attributes that hold bus names. The default is
  `("bus",)`, so this declaration is redundant; declare it explicitly when
  the op has multiple buses (`Sync.targets`, `ActiveReset.bus`/`control_bus`).
- `WAVEFORM_ATTRS` is the same idea for waveform attributes. Default `()`.
- The base `Operation` class provides `variables()`, `buses()`,
  `waveforms()`, and `walk()` via these class attributes. You almost never
  override them.

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

In `qprogram/src/qprogram/qprogram.py`:

```python
from qprogram.operations.set_power import SetPower

class QProgram:
    ...

    def set_power(self, bus: str, power: float | Expression) -> None:
        """Set the output power in dBm."""
        self._validate_bus(bus)
        self._active_block.append(SetPower(bus=bus, power=power))
```

`_validate_bus` enforces the one-schema-per-program rule for `BusRef`s. If
your operation has multiple bus attributes, call it once per bus.

## Step 4: Make the serializer aware (no code, just verify)

Most new operations need **no** writer or parser changes thanks to the
generic dispatch. The writer reflects on `__init__` to emit positional and
keyword arguments; the parser uses `inspect.signature(cls.__init__)` to
reconstruct them.

You do need to register the operation, though. Add one line to
`qprogram/src/qprogram/serialization/registry.py`:

```python
from qprogram.operations.set_power import SetPower

def _register_builtins() -> None:
    ...
    _register_core("set_power", SetPower)
    ...
```

That is it for serialization. The op now writes as
`set_power "drive_q0" 5.0` and reads back into a `SetPower` instance.

### When you do need a custom callback

A few operations have unusual shapes that the default reflection cannot
handle (`Sync.targets` is a variadic list of strings rather than positional
args; `GetParameter` uses the arrow `-> var` syntax). For those, supply
serialize/parse callbacks via `register_core_operation` with explicit
arguments. The existing special cases in `_specs.py` are the template.

## Step 5: Write tests

The convention is to add tests next to the relevant existing module.

For the operation class itself, add to
`qprogram/tests/test_operations.py`:

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

For the `QProgram` method, add to `qprogram/tests/test_qprogram.py`:

```python
def test_set_power_appends_to_active_block():
    p = QProgram()
    p.set_power("drive_q0", 5.0)
    assert isinstance(p.body.elements[0], SetPower)
```

For round-trip serialization, add to
`qprogram/tests/test_round_trip.py`:

```python
def test_round_trip_set_power():
    p = QProgram()
    p.set_power("drive_q0", 5.0)
    text = qp.dumps(p)
    reloaded = qp.loads(text)
    assert qp.dumps(reloaded) == text
```

The full test suite runs in well under a second; coverage is tracked in
`tox`/`pytest --cov`. See [Testing](testing.md).

## Step 6: Document it

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

- `qprogram/src/qprogram/operations/set_phase.py` (the class)
- `qprogram/src/qprogram/qprogram.py` (`def set_phase`)
- `qprogram/src/qprogram/serialization/registry.py` (the registration line)
- `qprogram/tests/test_operations.py` (the unit tests)

The boilerplate is small on purpose.
