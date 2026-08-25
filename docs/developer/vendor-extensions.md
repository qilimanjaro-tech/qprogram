# Building a vendor extension

This guide builds a new vendor extension from scratch. The example vendor is
`fake_inst`, with two operations: a real-time `fake_inst.beep(bus, duration)`
and a host-side-only `fake_inst.set_threshold(bus, value)`. The same template
applies to any vendor package.

For a working reference, the test suite's `tests/_dummy_vendor.py` implements
Steps 1 through 5 in a single module: operations (one of them a measurement
op), a namespace, a mixin, a pre-combined `QProgram`, capability tokens, and a
profile. Two things on this page it does not cover. It is not an installed
distribution, so it declares no `qprogram.vendors` entry point;
`tests/test_vendor_discovery.py` exercises Step 6 against a stub one instead.
And it registers no vendor block; `tests/test_registry.py` and
`tests/test_writer.py` cover that.

## Package layout

A vendor extension is its own package, in its own repository, depending on
`qprogram`:

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
4. `profiles.py` declares the vendor's capability tokens and ships one or more
   `Profile` bundles.
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
    """Host-side-only threshold setter (no sequencer footprint)."""

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
- `BUS_ATTRS` is `("bus",)` by default; declare it explicitly when the bus
  attribute has another name or the op touches more than one bus.
- `WAVEFORM_ATTRS` is `()` by default; declare it for waveform-bearing ops.
- The base `Operation` class supplies `variables()`, `buses()`,
  `waveforms()`, `walk()`, structural equality, and structural hashing. You
  rarely override anything.

If the operation produces a measurement, subclass `MeasurementOperation`
instead. Such a class takes a `handle: MeasurementHandle` argument and sets
`self.fields` through `normalize_fields(...)`; the namespace calls
`_append_measurement`, which allocates the name, builds the op, appends it,
and returns the handle to the user.

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
        """Set the discrimination threshold (host-side-only)."""
        self._append(SetThreshold(bus=bus, value=value))
```

`VendorNamespace._append` does two things: it runs every `BusRef` attribute
(and lists thereof) through the program's `_validate_bus`, so a vendor op
cannot smuggle in a bus from a different schema, and it appends the
operation to the active block. Use `_append_measurement` instead for
measurement operations.

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

from qprogram.protocol import (
    Diagnostic,
    Profile,
    ValidationContext,
    register_capability_tokens,
    register_profile,
)

from qprogram_fakeinst.operations import Beep

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qprogram.blocks.block import Block
    from qprogram.operations.operation import Operation


# Register the vendor's capability tokens *before* the Profile is
# constructed: `Profile.__post_init__` rejects unknown tokens.
register_capability_tokens(
    "vendor.fake_inst.beep",
    "vendor.fake_inst.set_threshold",
)


def _reject_zero_duration_beep(
    node: "Operation | Block",
    ctx: ValidationContext,  # noqa: ARG001
) -> "Iterable[Diagnostic]":
    """Reject a fake_inst.beep whose duration is zero."""
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
            # bus-touching ops the backend can run (block.* / expr.* / sweep.*
            # live on the platform-level slot, see notes below).
            "op.play",
            "op.measure",
            "op.wait",
            "op.sync",
            "op.set_frequency",
            "op.set_phase",
            "waveform.single",
            "waveform.iq",
            "waveform.alias",
            "waveform.square",
            "waveform.gaussian",
            "waveform.iq_drag",
            "waveform.iq_pair",
            "measure.fields.iq",
            # vendor ops
            "vendor.fake_inst.beep",
            "vendor.fake_inst.set_threshold",
        }
    ),
    limits={
        "min_wait_duration_ns": 4,  # per-Wait limits live on the bus slot
    },
    predicates=(_reject_zero_duration_beep,),
    vendor_versions={"fake_inst": (0, 1, 0)},
)


def _register() -> None:
    """Idempotently register the profile on the global registry."""
    register_profile(FAKE_INST_DEFAULT_V1)
```

A few notes:

- **List the bus-side capabilities the backend supports.** Bus-touching ops
  (`op.play`, `op.measure`, `op.wait`, ...), waveforms, and `measure.fields.*`
  all live on the bus profile because the corresponding nodes route there. Block
  / expression / sweep tokens come from core `qprogram-base-v1`, the
  platform-level slot of `PlatformCapabilities`, which the platform materializes
  from that profile alongside its bus profiles; see
  [Building `CompilerCapabilities` from a profile](capability-protocol.md#building-compilercapabilities-from-a-profile).
  (Core `op.set_parameter` / `op.get_parameter` are also bus-touching,
  host-side-only ops; their tokens are *not* in `qprogram-base-v1`, so a
  platform opts them into a bus slot's `host` half explicitly if it supports
  them.) Leaving out a bus-touching token your backend can actually run means
  programs using it will fail validation against this profile.
- **Platform-level limits** like `max_loop_nesting`, `max_parallel_loops`,
  `max_measurements` live on the platform slot, not on the bus profile. Apply
  them via `limit_overrides=` when materializing the platform-level slot from
  `qprogram-base-v1`.
- **Per-class waveform tokens** (`waveform.square`, `waveform.iq_drag`,
  ...) refine the channel-kind tokens. Include the per-class tokens for
  every waveform your compiler knows how to lower. Programs using a
  waveform whose token is missing from the profile produce one
  `missing-capability` diagnostic per use site.
- **Limits** are numeric thresholds. The validator understands
  `max_loop_nesting`, `max_parallel_loops`, `max_measurements`, and
  `min_wait_duration_ns`; vendors may declare additional keys for forward
  compatibility, which the validator silently ignores.
- **Predicates** are callables
  `(node, ctx) -> Iterable[Diagnostic | DomainConstraint]`. Use them for
  context-sensitive checks that depend on more than one node. The canonical
  example is "this op's variable argument must be bound by a linear loop". See
  [Capability protocol internals](capability-protocol.md) for the full predicate
  / `ValidationContext` reference.

If you want a tiered family of profiles (`-base-v1`, `-adaptive-v1`,
...), use `extends="<parent-name>"`. Capabilities and predicates
accumulate; limits inherit and may be overridden. Start with
a single `<vendor>-default-v1` and split only when a real device demands
it, because a proliferation of near-identical profiles is the classic way this
kind of protocol becomes unusable.

## Step 5: The `__init__.py` glue

```python
# __init__.py
from importlib.metadata import PackageNotFoundError, version

from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization.registry import (
    register_vendor_operation,
    register_vendor_version,
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

Four registration steps run on import (five calls, since each operation is
registered on its own line) plus the capability-token registration that
happens as a side effect of importing `profiles.py` at the top of
`__init__.py`; profile registration is a separate call so the order is
explicit. The last piece, the `qprogram.vendors` entry point in
[`pyproject.toml`](#step-6-pyprojecttoml), is what lets `loads()` trigger this
whole import on demand, so a `.qp` file requiring the vendor loads without an
explicit `import`.

The vendor name (`"fake_inst"`) must not be one of the [reserved
keywords](../reference/reserved.md), the sentinel `"core"`, or the name of
any `QProgram` attribute. The last would make the namespace unreachable,
since vendor lookup happens in `__getattr__`, after normal attribute
resolution.

The protocol version is the version of the operation set this extension
exposes. Reading it from `importlib.metadata` keeps a single source of truth
in `pyproject.toml`.

## Step 6: `pyproject.toml`

The `[project.entry-points."qprogram.vendors"]` table is what makes the
extension **auto-discoverable**: when a `.qp` file declares
`require fake_inst <ver>` and the package is installed but not yet imported,
`qprogram.loads(...)` imports the module named here on demand (its import-time
side effects then run the registration steps above). The entry-point *name* is
the vendor namespace; the *value* is the module that self-registers.

```toml
[project]
name = "qprogram-fakeinst"
version = "0.1.0"
description = "Fake-instrument vendor extension for QProgram"
requires-python = ">=3.11"
dependencies = ["qprogram>=0.1.0"]

[project.entry-points."qprogram.vendors"]
fake_inst = "qprogram_fakeinst"

[dependency-groups]
dev = [
    "pytest>=9.0",
    "pytest-cov>=7.0",
    "pytest-mock>=3.15",
    "ruff>=0.16",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
minversion = "9.0"
addopts = ["-ra", "--strict-markers", "--strict-config"]
testpaths = ["tests"]
filterwarnings = ["error"]
xfail_strict = true

[tool.coverage.run]
source = ["qprogram_fakeinst"]
branch = true
```

The dependency on `qprogram` is a normal version constraint against the
published package. To develop against a local checkout of the core instead,
point `uv` at it for the duration:

```toml
[tool.uv.sources]
qprogram = { path = "/path/to/qprogram", editable = true }
```

That table only redirects local resolution with `uv`; the dependency the
package publishes stays the `qprogram>=0.1.0` constraint above, so an
installing user always resolves the core from the index.

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
serializes to `fake_inst.beep "drive_q0" 100`. Required arguments are
positional in constructor order; optional ones appear as `key=value` only
when they differ from their default.

The parser reverses the lookup. It reads the `require fake_inst 0.1` line
at the top of the file, validates compatibility against the installed
version, and then resolves each `fake_inst.<op>` in the body via
`("fake_inst", op)`.

No writer or parser changes are needed. Both sides drive themselves from
the registry.

## Contributing a control-flow block

An extension is not limited to operations. It can add a **block**, a
container with its own header keyword, by subclassing `Block` and
registering it with `register_vendor_block`:

```python
from typing import ClassVar

from qprogram.blocks import Block
from qprogram.serialization import register_vendor_block


class Forever(Block):
    """Repeat the body until the host stops it."""

    REPEATS: ClassVar[bool] = True  # occupies a repetition level

    def required_capabilities(self) -> set[str]:
        return {"vendor.fake_inst.forever"}


register_vendor_block("fake_inst", "forever", Forever)
```

The wire form is `fake_inst.forever:` followed by an indented suite. Four
things to get right:

1. **`REPEATS`.** Set it `True` if the block re-runs its body. That is what
   makes it count toward `max_loop_nesting`: the validator reads the marker
   rather than testing concrete classes, so extensions participate in the
   limit check for free. Leave it `False` (the default) for a block that
   merely groups.
2. **Capability slot.** Blocks route to the **platform** slot, not a per-bus
   one, so the token goes on your platform-slot profile. If the block should
   be real-time capable, it must be in that slot's `rt` half too. A token
   present only in `host` makes the block host-only, which will force
   everything inside it host-side.
3. **Opening it.** Add a namespace method returning a context manager that
   appends the block and pushes it onto the program's block stack (mirroring
   core's `block()` / `average()`). `VendorNamespace._append` covers
   operations only; there is no block equivalent, so the context manager
   touches `program._append_to_active` / `program._block_stack` directly.
4. **Registration, not `register_block`.** The vendor wrapper records the
   vendor on the spec, which is what puts a `require fake_inst 0.1` line in
   any file containing the block, even one with no vendor *operations*.
   Without it the file would not auto-activate your package on load.

A vendor block is deliberately not a `Sweep`: it binds no variable,
reports no `num_iterations()`, cannot compose under `|`, and adds no result
dimension. Those are sweep properties, not repetition properties.

## Versioning: choosing major / minor

The protocol version (`require fake_inst 0.1`) describes the **operation
set**, not the package release. Bump the minor when you add operations or
add backwards-compatible kwargs. Bump the major when you remove or rename
operations, or when you change semantics in a way that would break older
files.

The parser enforces:

- Same major as installed.
- Installed minor greater than or equal to the file's.

In practice, an existing file keeps parsing as long as you only add to the
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

A platform library that already depends on several vendor extensions
usually provides this combined class so end users do not write the
inheritance themselves.

The dynamic `program.fake_inst.*` resolution works regardless; the mixin
exists for IDE autocomplete.

## Pitfalls to avoid

- **Importing `qprogram_fakeinst` is the activation step**, and the entry
  point is what lets `loads()` perform that import for you. A `.qp` file
  using `fake_inst.*` loads on a machine where the package is *installed*
  even if nothing imported it. On a machine where it is not installed, or
  under `loads(..., auto_activate=False)`, the parser rejects the file.
  Say which package to install in your README.
- **Do not re-export `qprogram.QProgram` unchanged.** The pre-combined
  class on the vendor's `__init__` should subclass the mixin so users get
  static typing.
- **Avoid `from qprogram import *`.** Pick the symbols you need; star
  imports tend to trigger circular imports during registration.
- **Keep bus attributes plain.** For `rebind` to re-resolve your vendor's
  buses, an operation's bus attributes must be plain `str` / `BusRef`
  values, and `BUS_ATTRS` is what tells the rebinder which ones to rewrite.
