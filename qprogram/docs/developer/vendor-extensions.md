# Building a vendor extension

This guide builds a new vendor extension from scratch. The example vendor is
`fake_inst`, with two operations: a real-time `fake_inst.beep(bus, duration)`
and a software-only `fake_inst.set_threshold(bus, value)`. The same template
applies to any vendor package; for an in-tree reference, the test suite's
`tests/_dummy_vendor.py` exercises every step.

## Package layout

A vendor extension package looks like this:

```
qprogram-fakeinst/
├── pyproject.toml
├── src/qprogram_fakeinst/
│   ├── __init__.py        # registration + pre-combined QProgram
│   ├── operations.py      # Operation subclasses (AST nodes)
│   ├── namespace.py       # FakeInstNamespace (typed methods)
│   ├── mixin.py           # FakeInstMixin (typed @property)
│   └── profiles.py        # capability tokens + Profile bundles
└── tests/
    ├── conftest.py
    ├── test_operations.py
    ├── test_namespace.py
    ├── test_mixin.py
    ├── test_registration.py
    ├── test_serialization.py
    └── test_profile.py
```

Five source files, in increasing order of glue:

1. `operations.py` defines the AST node classes.
2. `namespace.py` defines the typed methods.
3. `mixin.py` defines the typed `@property`.
4. `profiles.py` declares the vendor's capability tokens and ships one or more `Profile` bundles.
5. `__init__.py` registers everything and ships a pre-combined `QProgram`.

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

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens

        return {"vendor.fake_inst.beep"} | expression_tokens(self.duration)


class SetThreshold(Operation):
    """Software-only threshold setter (no sequencer footprint)."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, value: float | Expression) -> None:
        self.bus = bus
        self.value = value

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens

        return {"vendor.fake_inst.set_threshold"} | expression_tokens(self.value)
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

## Step 4: Declare vendor capabilities and ship a profile

A vendor extension ships a capability **profile**: a named bundle listing
which DSL features the backend supports, any numeric limits the hardware
imposes, and any predicates that check for context-sensitive constraints.
Users (and the validator) consume the profile to ask "will this program
run on this platform?".

```python
# profiles.py
from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.operations.wait import Wait
from qprogram.protocol import (
    Diagnostic,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)
from qprogram.variable import Variable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation


# Register the vendor's capability tokens *before* the Profile is
# constructed — `Profile.__post_init__` rejects unknown tokens.
register_capability_tokens(
    "vendor.fake_inst.beep",
    "vendor.fake_inst.set_threshold",
)


def _reject_zero_duration_beep(
    node: "Operation | Block",
    ctx: ValidationContext,  # noqa: ARG001
) -> "Iterable[Diagnostic]":
    """Demo predicate: fake_inst.beep with duration=0 is meaningless."""
    if isinstance(node, Beep) and isinstance(node.duration, int) and node.duration == 0:
        yield Diagnostic(
            severity="error",
            code="fake_inst.zero-beep",
            message="Beep duration must be > 0 ns",
            node=node,
        )


FAKE_INST_DEFAULT_V1 = Profile(
    name="fake_inst-default-v1",
    version=(0, 1, 0),
    extends=None,
    capabilities=frozenset(
        {
            # core ops the backend supports — name everything it can run
            "op.play", "op.measure", "op.wait", "op.sync",
            "op.set_frequency", "op.set_phase",
            "block.block", "block.average", "block.for_loop", "block.loop",
            "waveform.single", "waveform.iq", "waveform.alias",
            "waveform.square", "waveform.gaussian", "waveform.iq_drag", "waveform.iq_pair",
            "sweep.linear", "sweep.arbitrary",
            "expr.constant", "expr.variable", "expr.binary_op", "expr.unary_op",
            "measure.returns.iq",
            # vendor ops
            "vendor.fake_inst.beep",
            "vendor.fake_inst.set_threshold",
        }
    ),
    limits={
        "max_loop_nesting": 4,
        "min_wait_duration_ns": 4,
    },
    predicates=(_reject_zero_duration_beep,),
    vendor_versions={"fake_inst": (0, 1, 0)},
)


def _register() -> None:
    """Idempotently register the profile on the global registry."""
    register_profile(FAKE_INST_DEFAULT_V1)
```

A few notes:

- **List every capability the backend supports**, including core ones. The
  profile is what defines "what this platform will accept" — leaving out a
  core token means programs using it will fail validation against this
  profile. The validator catches typos at profile-construction time, so
  you can't accidentally claim support for a non-existent token.
- **Per-class waveform tokens** (`waveform.square`, `waveform.iq_drag`,
  ...) refine the channel-kind tokens. Include the per-class tokens for
  every waveform your compiler knows how to lower. Programs using a
  waveform whose token is missing from the profile produce one
  `missing-capability` diagnostic per use site.
- **Limits** are numeric thresholds. The validator understands
  `max_loop_nesting`, `max_parallel_loops`, `max_measurements`, and
  `min_wait_duration_ns` out of the box; vendors may declare additional
  keys for forward compatibility, which the current validator silently
  ignores.
- **Predicates** are callables `(node, ctx) -> Iterable[Diagnostic]`. Use
  them for context-sensitive checks that depend on more than one node —
  the canonical example is "this op's variable argument must be bound by
  a linear loop". See [Capability protocol internals](capability-protocol.md)
  for the full predicate / `ValidationContext` reference.

If you want a tiered family of profiles (`-base-v1`, `-adaptive-v1`,
...), use `extends="<parent-name>"`. Capabilities and predicates
accumulate; limits inherit and may be overridden. Recommended: start with
a single `<vendor>-default-v1` and split only when a real device demands
it. Premature profile proliferation is the most-cited mistake in QIR's
post-mortems.

## Step 5: The `__init__.py` glue

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
from qprogram_fakeinst.profiles import (
    FAKE_INST_DEFAULT_V1,
    _register as _register_fake_inst_profile,
)

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

# 4. Capability profile. (Tokens were registered by `profiles.py` at import.)
_register_fake_inst_profile()


# 5. Pre-combined typed QProgram.
class QProgram(FakeInstMixin, _BaseQProgram):
    pass


__all__ = [
    "Beep",
    "FAKE_INST_DEFAULT_V1",
    "FakeInstMixin",
    "FakeInstNamespace",
    "QProgram",
    "SetThreshold",
]
```

Five registration steps happen on import. Capability-token registration is
a side effect of importing `profiles.py`, which happens at the top of
`__init__.py`; profile registration is a separate call so the order is
explicit.

The vendor name (`"fake_inst"`) must not be one of the [reserved
keywords](../reference/reserved.md) or the sentinel `"core"`.

The protocol version is the version of the operation set this extension
exposes. Reading it from `importlib.metadata` keeps a single source of truth
in `pyproject.toml`.

## Step 6: `pyproject.toml`

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

## Step 7: Tests

A vendor package's `tests/` folder typically mirrors this layout:

- `test_operations.py` covers each Operation class: construction,
  introspection (`buses()`, `waveforms()`, `variables()`), structural
  equality, and `required_capabilities()` (instance-aware).
- `test_namespace.py` covers each method on the namespace: it appends the
  right op, validates buses, and uses the right naming scheme for
  measurement ops.
- `test_mixin.py` covers the mixin: returns a `FakeInstNamespace`, caches
  per instance, composes with multiple vendors.
- `test_registration.py` confirms the registration calls succeed.
- `test_serialization.py` exercises every operation through dumps and
  loads, including the `require` line.
- `test_profile.py` confirms the profile is registered, that
  representative programs validate clean, and that each predicate fires on
  the cases it should.

Aim for full coverage across these modules; copy the spirit.

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
from qprogram_othervendor import OtherVendorMixin
from qprogram_fakeinst import FakeInstMixin


class QProgram(OtherVendorMixin, FakeInstMixin, BaseQProgram):
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
