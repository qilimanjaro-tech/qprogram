# Building a vendor extension

This guide builds a new vendor extension from scratch. The example vendor is
`fake_inst`, with two operations: a real-time `fake_inst.beep(bus, duration)`
and a software-only `fake_inst.set_threshold(bus, value)`. Use
`qprogram-qblox` as the working reference; it follows the same template.

## Package layout

A vendor extension package looks like this:

```
qprogram-fakeinst/
├── pyproject.toml
├── src/qprogram_fakeinst/
│   ├── __init__.py        # registration + pre-combined QProgram
│   ├── operations.py      # Operation subclasses (AST nodes)
│   ├── namespace.py       # FakeInstNamespace (typed methods)
│   └── mixin.py           # FakeInstMixin (typed @property)
└── tests/
    ├── conftest.py
    ├── test_operations.py
    ├── test_namespace.py
    ├── test_mixin.py
    ├── test_registration.py
    └── test_serialization.py
```

Four source files, in increasing order of glue:

1. `operations.py` defines the AST node classes.
2. `namespace.py` defines the typed methods.
3. `mixin.py` defines the typed `@property`.
4. `__init__.py` registers everything and ships a pre-combined `QProgram`.

## Step 1: Define the operation classes

```python
# operations.py
from __future__ import annotations

from typing import ClassVar

from qprogram.operations.operation import Operation
from qprogram.variable import Expression


class Beep(Operation):
    """Real-time beep on a single bus."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, duration: int | Expression) -> None:
        self.bus = bus
        self.duration = duration


class SetThreshold(Operation):
    """Software-only threshold setter (no sequencer footprint)."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, value: float | Expression) -> None:
        self.bus = bus
        self.value = value
```

Rules of thumb.

- Each operation is a plain `Operation` subclass.
- `BUS_ATTRS` is `("bus",)` by default; declare it explicitly when the op has
  more than one bus attribute.
- `WAVEFORM_ATTRS` is `()` by default; declare it for waveform-bearing ops.
- The base `Operation` class supplies `variables()`, `buses()`,
  `waveforms()`, `walk()`, structural equality, and structural hashing. You
  rarely override anything.

If the operation produces a measurement, subclass `MeasurementOperation`
instead and accept a `name: str` argument; the namespace will use
`_append_measurement` to handle naming and return a `MeasurementHandle`.

## Step 2: Define the typed namespace

```python
# namespace.py
from __future__ import annotations

from qprogram.vendor import VendorNamespace
from qprogram.variable import Expression

from qprogram_fakeinst.operations import Beep, SetThreshold


class FakeInstNamespace(VendorNamespace):
    """Typed methods for the fake_inst vendor."""

    def beep(self, bus: str, duration: int | Expression) -> None:
        """Beep on a bus for the given duration in ns."""
        self._append(Beep(bus=bus, duration=duration))

    def set_threshold(self, bus: str, value: float | Expression) -> None:
        """Set the discrimination threshold (software-only)."""
        self._append(SetThreshold(bus=bus, value=value))
```

`VendorNamespace._append` does two things: it validates each BusRef attribute
against the program's schema, and it appends the operation to the active
block. Use `_append_measurement` instead for measurement operations.

## Step 3: Define the mixin

```python
# mixin.py
from __future__ import annotations

from qprogram_fakeinst.namespace import FakeInstNamespace


class FakeInstMixin:
    @property
    def fake_inst(self) -> FakeInstNamespace:
        try:
            return object.__getattribute__(self, "_fake_inst_ns")
        except AttributeError:
            pass
        ns = FakeInstNamespace(self)
        object.__setattr__(self, "_fake_inst_ns", ns)
        return ns
```

The mixin exists purely for IDE autocomplete; the runtime `__getattr__` on
the base `QProgram` would do the same lookup anyway. Cache the namespace on
the instance the first time it is accessed.

## Step 4: The `__init__.py` glue

```python
# __init__.py
from importlib.metadata import PackageNotFoundError, version

from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization.registry import (
    register_vendor_operation, register_vendor_version,
)

from qprogram_fakeinst.mixin import FakeInstMixin
from qprogram_fakeinst.namespace import FakeInstNamespace
from qprogram_fakeinst.operations import Beep, SetThreshold

try:
    __version__ = version("qprogram-fakeinst")
except PackageNotFoundError:
    __version__ = "0.0.0"

# 1. Runtime namespace.
_BaseQProgram.register_vendor("fake_inst", FakeInstNamespace)

# 2. Protocol version (from pyproject.toml).
register_vendor_version("fake_inst", __version__)

# 3. Operations with the .qp serializer.
register_vendor_operation("fake_inst", "beep", Beep)
register_vendor_operation("fake_inst", "set_threshold", SetThreshold)


# 4. Pre-combined typed QProgram.
class QProgram(FakeInstMixin, _BaseQProgram):
    pass


__all__ = [
    "Beep",
    "FakeInstMixin",
    "FakeInstNamespace",
    "QProgram",
    "SetThreshold",
]
```

Four registration calls happen on import. The order matters only loosely:
all four are independent.

The vendor name (`"fake_inst"`) must not be one of the [reserved
keywords](../reference/reserved.md) or the sentinel `"core"`.

The protocol version is the version of the operation set this extension
exposes. Reading it from `importlib.metadata` keeps a single source of truth
in `pyproject.toml`.

## Step 5: `pyproject.toml`

```toml
[project]
name = "qprogram-fakeinst"
version = "0.1.0"
description = "Fake-instrument vendor extension for QProgram"
requires-python = ">=3.11"
dependencies = ["qprogram>=0.1.0"]

[dependency-groups]
dev = [
    "pytest>=9.0.3",
    "pytest-cov>=5.0.0",
    "pytest-mock>=3.15.1",
    "ruff>=0.15.12",
]

[tool.uv.sources]
qprogram = { path = "../qprogram", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
minversion = "8.0"
addopts = ["-ra", "--strict-markers", "--strict-config"]
testpaths = ["tests"]
filterwarnings = ["error"]
xfail_strict = true

[tool.coverage.run]
source = ["qprogram_fakeinst"]
branch = true
```

The path source assumes a sibling checkout; replace it with a pinned PyPI
version once published.

## Step 6: Tests

The `qprogram-qblox/tests/` folder is the canonical template. Mirror its
layout:

- `test_operations.py` covers each Operation class: construction,
  introspection (`buses()`, `waveforms()`, `variables()`), and structural
  equality.
- `test_namespace.py` covers each method on the namespace: it appends the
  right op, validates buses, and uses the right naming scheme for
  measurement ops.
- `test_mixin.py` covers the mixin: returns a `FakeInstNamespace`, caches
  per instance, composes with multiple vendors.
- `test_registration.py` confirms the three registration calls succeed.
- `test_serialization.py` exercises every operation through dumps and
  loads, including the `require` line.

`qprogram-qblox` reaches 100% coverage with 80 tests; copy the spirit.

## How serialization works for vendor ops

The writer looks up each operation instance in the registry to find its
`(vendor, name)` pair. `Beep` registered under `("fake_inst", "beep")`
serialises to `fake_inst.beep "drive_q0" 100`. Default arguments are
omitted; non-default keyword arguments appear as `key=value`.

The parser reverses the lookup. It reads the `require fake_inst 0.1` line
at the top of the file, validates compatibility against the installed
version, and then resolves each `fake_inst.<op>` in the body via
`("fake_inst", op)`.

No writer or parser changes are needed. Both sides drive themselves from
the registry.

## Versioning: choosing major / minor

The protocol version (`require fake_inst 0.1`) describes the **operation
set**, not the package release. Bump the minor when you add operations or
add backwards-compatible kwargs. Bump the major when you remove or rename
operations, or when you change semantics in a way that would break older
files.

The parser enforces:

- Same major as installed.
- Installed minor greater than or equal to the file's.

In practice, files saved today keep parsing as long as you only add to the
operation set on the same major.

## Combining with other vendors

End users combine multiple vendors with multiple inheritance:

```python
from qprogram import QProgram as BaseQProgram
from qprogram_qblox import QbloxMixin
from qprogram_fakeinst import FakeInstMixin


class QProgram(QbloxMixin, FakeInstMixin, BaseQProgram):
    pass
```

The platform library (QiliLab) usually provides this combined class so end
users do not have to write the inheritance themselves.

The dynamic `program.fake_inst.*` resolution works regardless; the mixin
exists for IDE autocomplete.

## Pitfalls to avoid

- **Importing `qprogram_fakeinst` is the activation step.** If the body of
  a `.qp` file uses `fake_inst.*` and nothing has imported the package, the
  parser will reject the file. Document this in your README.
- **Do not re-export `qprogram.QProgram` unchanged.** The pre-combined
  class on the vendor's `__init__` should subclass the mixin so users get
  static typing.
- **Avoid `from qprogram import *`.** Pick the symbols you need; star
  imports tend to trigger circular imports during registration.
- **Keep operations frozen on attribute set.** If you want
  `with_bus_mapping` to remap your vendor's buses, your operation's
  attributes must be plain string-typed; `BUS_ATTRS` tells the remapper
  which ones to rewrite.
