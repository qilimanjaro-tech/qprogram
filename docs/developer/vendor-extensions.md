# Building a vendor extension

This guide builds a new vendor extension from scratch. The example vendor is
`fake_inst`, with two operations: a real-time `fake_inst.beep(bus, duration)`
and a host-side-only `fake_inst.set_threshold(bus, value)`. The same template
applies to any vendor package.

Every Python snippet below is a source file inside the vendor package, apart
from the end-user script under
[Combining with other vendors](#combining-with-other-vendors). A vendor
package is ordinary external code, so it reaches QProgram through
`import qprogram as qp` and refers to its own modules by their real dotted
paths. The in-tree extension guides ([Adding an operation](adding-operations.md),
[Adding a waveform](adding-waveforms.md)) look different because a module inside
`src/qprogram/` cannot `import qprogram` without closing an import cycle.

For a working reference, the test suite's `tests/_dummy_vendor.py` implements
steps 1 through 5 in a single module: operations (one of them a measurement
op), a namespace, a mixin, a pre-combined `QProgram`, capability tokens, and a
profile. Two things on this page it does not cover. It is not an installed
distribution, so it declares no `qprogram.vendors` entry point;
`tests/test_vendor_discovery.py` exercises step 6 against a stub one instead.
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

## Step 1: the operation classes

```python
# qprogram-fakeinst/src/qprogram_fakeinst/operations.py
from __future__ import annotations

from typing import ClassVar

import qprogram as qp


class Beep(qp.operations.Operation):
    """Real-time beep on a single bus."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, duration: int | qp.Expression) -> None:
        self.bus = bus
        self.duration = duration

    def required_capabilities(self) -> set[str]:
        return {"vendor.fake_inst.beep"} | qp.protocol.expression_tokens(
            self.duration,
        )


class SetThreshold(qp.operations.Operation):
    """Host-side-only threshold setter (no sequencer footprint)."""

    BUS_ATTRS: ClassVar[tuple[str, ...]] = ("bus",)

    def __init__(self, bus: str, value: float | qp.Expression) -> None:
        self.bus = bus
        self.value = value

    def required_capabilities(self) -> set[str]:
        return {"vendor.fake_inst.set_threshold"} | qp.protocol.expression_tokens(
            self.value,
        )
```

Four class attributes control how the validator sees an operation, and all four
have defaults that suit the common case:

| Attribute | Default | What it controls |
|---|---|---|
| `BUS_ATTRS` | `("bus",)` | Which `__init__` parameters hold bus references. `buses()` reads these, and `rebind` rewrites them. Declare it when the bus lives under another name or the op touches several buses. |
| `WAVEFORM_ATTRS` | `()` | Which parameters carry waveform values, for `waveforms()`. `None` values from optional parameters are skipped. |
| `BROADCASTS_WHEN_NO_BUS` | `False` | When `True` and `BUS_ATTRS` resolve to no buses, the op is routed across every bus in the program rather than the default-bus slot. Core `Sync(targets=None)` is the case this exists for. |
| `AFFECTS_AVERAGING` | `False` | Whether the op gates the execution domain of an enclosing `average` block. Only ops producing the results an average accumulates should set it; `MeasurementOperation` sets it `True`. |

The base `Operation` class supplies `variables()`, `buses()`, `waveforms()`,
`walk()`, and structural equality and hashing, so a subclass normally overrides
nothing but `required_capabilities()`. Because equality and hashing are
structural, an instance must not be mutated after it has been used as a `set`
member or `dict` key; `QProgram.rebind` respects this by rewriting a fresh
`deepcopy`.

The constructor signature is part of the wire format, not an implementation
detail. The default serializer walks `inspect.signature(cls.__init__)`: a
parameter with no default is emitted positionally in declaration order, a
parameter with a default is emitted as `name=value` only when the stored value
differs from that default, and a parameter with no matching attribute on the
instance is skipped. The parser binds the tokens back by the same names and
constructs the class with keyword arguments only. Three consequences follow.
Each parameter name must match the attribute that holds its value, or the value
is dropped on write. Reordering required parameters silently changes what older
files mean. Renaming a parameter breaks every file that used it as a keyword,
which is a major version bump.

Only the value types the writer knows how to render can appear in an operation
attribute: expressions, waveforms, bus references, measurement handles, sweep
sources, strings, booleans, `None`, numbers including numpy scalars, lists and
tuples and 1-D arrays, and string-keyed dicts. Anything else raises
`SerializationError` rather than being coerced into a token the parser would
mis-type on reload.

If the operation produces a measurement, subclass `MeasurementOperation`
(reachable as `qp.operations.operation.MeasurementOperation`) instead. Such a
class must set `self.handle` to the `MeasurementHandle` it is given and
`self.fields` to the result of `normalize_fields(...)`, which canonically sorts
and deduplicates the requested field names and rejects a name that has no
`measure.fields.<name>` token registered. The base
`MeasurementOperation.required_capabilities()` returns one
`measure.fields.<name>` token per requested field, so a subclass unions that
with its own identity token via `super()`. The namespace method calls
`_append_measurement` rather than `_append`, and registration needs the
measurement-aware serializer and parser described in step 5.

## Step 2: the typed namespace

```python
# qprogram-fakeinst/src/qprogram_fakeinst/namespace.py
from __future__ import annotations

import qprogram as qp

from qprogram_fakeinst.operations import Beep, SetThreshold


class FakeInstNamespace(qp.VendorNamespace):
    """Typed methods for the fake_inst vendor."""

    def beep(self, bus: str, duration: int | qp.Expression) -> None:
        """Beep on a bus for the given duration in ns."""
        self._append(Beep(bus=bus, duration=duration))

    def set_threshold(self, bus: str, value: float | qp.Expression) -> None:
        """Set the discrimination threshold (host-side-only)."""
        self._append(SetThreshold(bus=bus, value=value))
```

`VendorNamespace._append` does two things. It walks
`vars(operation).values()`, runs every `BusRef` it finds through the program's
`_validate_bus`, and does the same for `BusRef` items one level inside a list,
so a vendor op cannot smuggle in a bus from a different `BusSchema`. Then it
appends the operation to the program's active block. The walk is shallow: plain
strings are never validated, and a `BusRef` hidden inside a dict or a tuple is
not reached, so an operation that needs a bus checked should hold it as a plain
attribute or a list.

`_append_measurement(op_cls, *, bus, name=None, **kwargs)` is the measurement
counterpart. It allocates the handle name from the program's per-bus counter,
the same counter `QProgram.measure` uses, so vendor and core measurements on
one bus never collide; constructs `op_cls(bus=bus, handle=handle, **kwargs)`;
appends the result through `_append`; and returns the handle. The measurement
operation's `__init__` therefore has to accept `bus` and `handle` as keywords.
A `name` that is empty, not a string, or already taken raises `ValidationError`
at the call site.

## Step 3: the mixin

```python
# qprogram-fakeinst/src/qprogram_fakeinst/mixin.py
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

The mixin exists for static typing and IDE autocomplete. Without it,
`program.fake_inst` still resolves, because `QProgram.__getattr__` looks the
name up in the vendor registry and caches the namespace on the instance under
the vendor name itself. With the mixin, the property is found by normal
attribute lookup and `__getattr__` never runs, which is why the cache goes into
a separate `_fake_inst_ns` slot.

That interaction decides where registration happens. `register_vendor` refuses
a name that `hasattr` finds on the class it is called on, so calling it on the
pre-combined subclass, whose mixin already defines the property, fails with
`vendor name 'fake_inst' collides with a QProgram attribute; the namespace
would be unreachable because normal attribute lookup wins over vendor
dispatch`. Register on the base `QProgram`; the registry is a class-level dict,
so the entry is visible from every subclass.

## Step 4: capability tokens and a profile

A vendor extension ships a capability profile: a named bundle listing which DSL
features the backend supports, the numeric limits the hardware imposes, and the
predicates that check context-sensitive constraints. The validator consumes the
profile to answer whether a given program can run on this platform.

```python
# qprogram-fakeinst/src/qprogram_fakeinst/profiles.py
from __future__ import annotations

from typing import TYPE_CHECKING

import qprogram as qp

from qprogram_fakeinst.operations import Beep

if TYPE_CHECKING:
    from collections.abc import Iterable


# Register the vendor's capability tokens *before* the Profile is
# constructed: `Profile.__post_init__` rejects unknown tokens.
qp.register_capability_tokens(
    "vendor.fake_inst.beep",
    "vendor.fake_inst.set_threshold",
)


def _reject_zero_duration_beep(
    node: qp.operations.Operation | qp.blocks.Block,
    ctx: qp.ValidationContext,  # noqa: ARG001
) -> Iterable[qp.Diagnostic]:
    """Reject a fake_inst.beep whose duration is zero."""
    if isinstance(node, Beep) and isinstance(node.duration, int) and node.duration == 0:
        yield qp.Diagnostic(
            severity="error",
            code="fake_inst.zero-beep",
            message="Beep duration must be > 0 ns",
            node=node,
        )


FAKE_INST_DEFAULT_V1 = qp.Profile(
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
    qp.register_profile(FAKE_INST_DEFAULT_V1)
```

`Profile` is a frozen dataclass with four required fields, `name`, `version`
as a `(major, minor, patch)` tuple, `extends`, and `capabilities`, plus three
fields that default to empty: `limits`, `predicates`, and
`vendor_versions`. Its `__post_init__` validates every capability token against
the global registry, so a typo fails at construction with
`Unknown capability token(s): ['vendor.fake_inst.bep']. Register via
qprogram.protocol.register_capability_tokens before use.` rather than surfacing
as a mysterious validation result later. `register_profile` is idempotent for
the same object, which matters for an import-time side effect that may run
twice, and raises `Profile 'fake_inst-default-v1' is already registered with
different content` when a second, different profile claims the name.

Bus-touching ops, waveform tokens, and `measure.fields.*` all belong on a bus
profile, because the nodes that carry them route to a `(bus, domain)` slot.
Block, expression, and sweep tokens belong on the platform-level slot, which a
platform materializes from core `qprogram-base-v1`; see
[Building `CompilerCapabilities` from a profile](capability-protocol.md#building-compilercapabilities-from-a-profile).
Core `op.set_parameter` and `op.get_parameter` are bus-touching but host-side
only, and their tokens are not in `qprogram-base-v1`, so a platform that
supports them opts them into a bus slot's `host` half explicitly.

Per-class waveform tokens (`waveform.square`, `waveform.iq_drag`, ...) refine
the channel-kind tokens `waveform.single` and `waveform.iq`. List a token for
every waveform class the compiler can lower. Omitting a token your backend can
actually run makes any program using it fail validation with one
`missing-capability` diagnostic per node, reading
`'Play' requires capability 'waveform.iq_drag' which is not supported by
'fake_inst-default-v1' (rt)`.

The validator understands four limit keys: `max_loop_nesting`,
`max_parallel_loops`, and `max_measurements` are read from the platform slot,
and `min_wait_duration_ns` from the bus slot the `Wait` routes to. Other keys
are ignored, which lets a profile declare a limit an older validator has no
check for. Platform-level limits are applied through `limit_overrides=` when
the platform materializes its platform slot, not by listing them on a bus
profile where nothing reads them.

A predicate is a callable `(node, ctx) -> Iterable[Diagnostic |
DomainConstraint]`, run against every visited node. Use one for a check that
depends on more than the node in front of it; the canonical example is "this
op's variable argument must be bound by a linear loop", which `ctx` can answer
and the node alone cannot. A predicate carried by both halves of a slot runs
once per `(domain, bus)` pair, so twice for a single-bus node and twice more
for each extra bus a multi-bus op touches. The validator discards duplicate
outputs, which is why a predicate has to be cheap and free of side effects.
[Capability protocol internals](capability-protocol.md) has the full predicate
and `ValidationContext` reference.

For a tiered family of profiles (`-base-v1`, `-adaptive-v1`, ...) set
`extends="<parent-name>"`. Capabilities and predicates accumulate parent to
child; limits inherit and may be overridden. Start with a single
`<vendor>-default-v1` and split only when a real device demands it, because a
proliferation of near-identical profiles is the classic way this kind of
protocol becomes unusable.

## Step 5: the `__init__.py` glue

```python
# qprogram-fakeinst/src/qprogram_fakeinst/__init__.py
from importlib.metadata import PackageNotFoundError, version

import qprogram as qp

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

# 1. Runtime namespace, registered on the base class.
qp.QProgram.register_vendor("fake_inst", FakeInstNamespace)

# 2. Protocol version (from pyproject.toml).
qp.register_vendor_version("fake_inst", __version__)

# 3. Operations with the .qp serializer.
qp.register_vendor_operation("fake_inst", "beep", Beep)
qp.register_vendor_operation("fake_inst", "set_threshold", SetThreshold)

# 4. Capability profile. (Tokens were registered by `profiles.py` at import.)
_register_fake_inst_profile()


# 5. Pre-combined typed QProgram.
class QProgram(FakeInstMixin, qp.QProgram):
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

Four registration steps run on import, five calls since each operation is
registered on its own line, plus the capability-token registration that happens
as a side effect of importing `profiles.py`. Profile registration is a separate
call so the order stays explicit. The last piece, the `qprogram.vendors` entry
point in [`pyproject.toml`](#step-6-pyprojecttoml), is what lets `qp.loads()`
trigger this whole import on demand, so a `.qp` file requiring the vendor loads
without an explicit `import`.

`register_vendor(name, namespace_cls)` rejects three kinds of name. A reserved
one, meaning any of the [reserved keywords](../reference/reserved.md) or the
`"core"` sentinel, raises `vendor name 'core' is reserved (see
qprogram.RESERVED_KEYWORDS plus the 'core' sentinel); pick a different
namespace for this vendor extension`. A name that collides with a `QProgram`
attribute, whether a method such as `play`, a public instance attribute
(`label`, `description`), or a mixin property already on the class, raises the
collision message from step 3. A name already held by a different namespace
class raises rather than replacing it, since silently taking over another
vendor's namespace would be a supply-chain hazard. Re-registering the same
class under the same name is a no-op.

`register_vendor_version(vendor, version)` takes a semver string with at least
integer `major.minor`; `"0.1"` and `"0.1.0"` are both accepted and the patch
component is informational. A version with fewer components raises `vendor
version '1' must have at least major.minor components`, and a non-integer
component raises `vendor version '0.x' has non-integer major/minor
components`. Reading the value from `importlib.metadata` keeps a single source
of truth in `pyproject.toml`, but note what the fallback does: when the package
is not installed as a distribution, `__version__` becomes `"0.0.0"` and the
extension advertises major 0, minor 0, so any file written as
`require fake_inst 0.1` is rejected as "minor version too old". Registering the
version is also what marks the vendor as active, which is the check
`try_activate_vendor` makes.

`register_vendor_operation(vendor, name, cls, *, serialize=None, parse=None)`
keys on `(vendor, name)`. Re-registering the same class refreshes its
callbacks; a different class under a taken pair raises `operation
'fake_inst.beep' is already registered to pkg.Beep; refusing to replace it with
other.Beep`. A measurement operation passes the two callbacks from
`qprogram.serialization._specs`, `measurement_op_serialize` and
`make_measurement_op_parse(cls)`, so the parser reconstructs the one canonical
`MeasurementHandle` instance that every `MeasurementRef` naming it shares.

`Profile.vendor_versions` records `(major, minor, patch)` tuples and is
informational. Only the string registered with `register_vendor_version`
decides whether a `.qp` file loads.

## Step 6: `pyproject.toml`

The `[project.entry-points."qprogram.vendors"]` table is what makes the
extension discoverable without an import. When a `.qp` file declares
`require fake_inst <ver>` and the package is installed but not yet imported,
`qp.loads(...)` imports the module named here on demand, and its import-time
side effects run the registration steps above. The entry-point *name* is the
vendor namespace; the *value* is the module that self-registers. The group name
is exactly `qprogram.vendors`, and nothing else is scanned.

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

`try_activate_vendor(vendor)` is what performs the activation. It returns
`True` immediately when a protocol version is already registered, without
scanning anything. Otherwise it looks the vendor up in the entry-point map and
returns `False` when no installed distribution claims it, leaving the caller to
decide whether that is an error. When an entry point is found it calls
`ep.load()` and raises `VendorActivationError` in two cases: the import itself
failing, reported as `vendor extension for 'fake_inst' is installed (entry
point 'qprogram_fakeinst') but failed to import: ...`, and an import that
succeeds without registering a version, reported as `... imported from entry
point 'qprogram_fakeinst' but did not register a protocol version; the package
must call register_vendor_version('fake_inst', '<x.y.z>') on import`. The
second is the failure mode of a package that ships the entry point but forgets
step 5.

The entry-point scan is memoized for the life of the process, so a
distribution installed after the first lookup stays invisible until
`qp.serialization.registry.clear_vendor_discovery_cache()` is called; tests
that inject entry points need that call in teardown. If two distributions
declare the same vendor name, the first one discovered wins, which makes the
outcome deterministic rather than correct.

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

## Step 7: tests

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
- `test_serialization.py` exercises every operation through `dumps` and
  `loads`, including the `require` line. The default serializer walks
  `__init__`'s parameters and reads each value off the instance under the
  parameter's own name, skipping any parameter the instance has no attribute
  for, so a parameter renamed without renaming the attribute drops out of the
  written line and the reload fails with `cannot construct 'Beep' from the
  given arguments`.
- `test_profile.py` confirms the profile is registered, that representative
  programs validate clean, and that each predicate fires on the cases it
  should and stays quiet on the cases it should not.

Registration mutates process-global registries, so tests need an activate and
deactivate pair behind a fixture rather than an import side effect;
`tests/_dummy_vendor.py` in this repository shows the shape, popping each
registry entry it added.

## How serialization works for vendor ops

The writer looks up each operation instance in the registry, by exact class
rather than by inheritance, to find its `(vendor, name)` pair, and emits
`<vendor>.<name>` followed by the arguments the constructor signature dictates.
`Beep` registered under `("fake_inst", "beep")` writes as
`fake_inst.beep "drive_q0" 100`. A class that was never registered raises
`Cannot serialize operation class 'Beep': it is not registered with the .qp
serializer.` before anything is written.

The `require` lines come from the same lookup. The writer collects the vendors
referenced anywhere in the program body and in every fragment body, so an
operation reachable only through a `Call` still gets its line, then emits one
`require <vendor> <major.minor>` per vendor with the patch component truncated,
since compatibility is defined at major.minor. A vendor used in the program
with no registered version raises `Cannot serialize: vendor 'fake_inst' is used
in the program but no version is registered.`

A complete file for a two-operation program looks like this:

```
#!QProgram 1.0

require fake_inst 0.1

body:
  fake_inst.beep "drive_q0" 100
  fake_inst.set_threshold "readout_q0" 0.5
```

The parser reverses the lookup. It reads the `require` lines that immediately
follow the header, checks each against the installed extension, and then
resolves every `fake_inst.<op>` in the body through `("fake_inst", op)`. A
`require` line further down the file is not treated as a declaration, because
the scan skips blank lines and stops at the first other line that is not one.
No writer or parser changes are needed for a new vendor operation; both sides
drive themselves from the registry.

## Adding a control-flow block

An extension is not limited to operations. It can add a block, a container with
its own header keyword, by subclassing `Block` and registering it with
`register_vendor_block`, in a sixth module:

```python
# qprogram-fakeinst/src/qprogram_fakeinst/blocks.py
from typing import ClassVar

import qprogram as qp


class Forever(qp.blocks.Block):
    """Repeat the body until the host stops it."""

    REPEATS: ClassVar[bool] = True  # occupies a repetition level

    def required_capabilities(self) -> set[str]:
        return {"vendor.fake_inst.forever"}


qp.register_vendor_block("fake_inst", "forever", Forever)
```

The wire form is `fake_inst.forever:` followed by an indented suite:

```
body:
  fake_inst.forever:
    wait "drive_q0" 100
```

The block registry keys on the qualified keyword, so a vendor block can reuse a
core keyword (`fake_inst.block` and core `block` coexist) and can never collide
with one. Four things to get right:

1. **`REPEATS`.** Set it `True` if the block re-runs its body. That is what
   makes it count toward `max_loop_nesting`: the validator reads the marker
   rather than testing for the concrete core loop classes, so a vendor block
   counts toward the limit without a change to the core. Leave it `False`, the
   default, for a block that merely groups.
2. **Capability slot.** Blocks route to the platform slot, not a per-bus one,
   so the token goes on your platform-slot profile. A block whose token is in
   neither half of that slot draws an `empty-domain` diagnostic saying the
   platform slot supports none of the domains for the block's required tokens.
   A token present only in `host` makes the block host-only, so an op-child
   that can run only in real time leaves it with no executable domain:
   `own slot supports ['host'] but op-children consensus is ['rt']`.
3. **Opening it.** Add a namespace method returning a context manager that
   appends the block and pushes it onto the program's block stack, mirroring
   core's `block()` and `average()`: `program._append_to_active(blk)` on entry,
   `program._block_stack.append(blk)`, and a matching `pop()` on exit.
   `VendorNamespace._append` covers operations only; there is no block
   equivalent.
4. **`register_vendor_block`, not `register_block`.** The vendor wrapper
   records the vendor on the spec, which is what puts a `require fake_inst 0.1`
   line in any file containing the block, even one with no vendor
   *operations*. Without it the file would not auto-activate your package on
   load.

A vendor block is deliberately not a `Sweep`: it binds no variable, reports no
`num_iterations()`, cannot compose under `|`, and adds no result dimension.
Those are sweep properties, not repetition properties.

## Versioning: major and minor

The protocol version (`require fake_inst 0.1`) describes the operation set, not
the package release. Bump the minor when you add operations or add
backwards-compatible keyword arguments. Bump the major when you remove or
rename an operation, rename or reorder a constructor parameter, or change
semantics in a way that would break older files.

The parser enforces exactly two conditions, on major.minor with the patch
component ignored: the majors must match, and the installed minor must be at
least the file's. The two failures read:

```
Line 3: file requires fake_inst 1.0 (major 1); installed fake_inst is 0.1.0 (major 0) — major versions must match
Line 3: file requires fake_inst 0.9 or compatible; installed fake_inst is 0.1.0 — minor version too old
```

An existing file therefore keeps parsing as long as you only add to the
operation set on the same major, and a newer extension on that major always
reads older files.

When the vendor is not registered at all, the message depends on whether
auto-activation is on. The default suggests installing the package that
declares the entry point; under `qp.loads(..., auto_activate=False)` it says
auto-activation is disabled and names the import to add. Both are `ParseError`
carrying the line number of the `require` line.

## Combining with other vendors

End users combine multiple vendors with multiple inheritance:

```python
import qprogram as qp
from qprogram_fakeinst import FakeInstMixin
from qprogram_othervendor import OtherVendorMixin


class QProgram(OtherVendorMixin, FakeInstMixin, qp.QProgram):
    pass
```

A platform library that already depends on several vendor extensions usually
provides this combined class so end users do not write the inheritance
themselves. The dynamic `program.fake_inst.*` resolution works either way; the
mixin exists for IDE autocomplete.

## Failure modes

The registries are global and populated at import time, which puts most of the
mistakes in a vendor package on the path between installed and usable.

| Mistake | What you see |
|---|---|
| No `qprogram.vendors` entry point | A `.qp` file using `fake_inst.*` loads only in a process that already imported the package. Elsewhere: `no matching extension is registered in this environment` |
| Entry point present, no `register_vendor_version` call | `VendorActivationError: ... imported from entry point 'qprogram_fakeinst' but did not register a protocol version` |
| `register_vendor` called on the pre-combined class | `ValueError: vendor name 'fake_inst' collides with a QProgram attribute` |
| Package not installed as a distribution | `__version__` falls back to `"0.0.0"`, so every `require fake_inst 0.1` fails as "minor version too old" |
| Operation class not registered | `SerializationError: Cannot serialize operation class 'Beep': it is not registered with the .qp serializer.` |
| Constructor parameter renamed without a major bump | Older files fail to parse, or bind the value to the wrong parameter |
| Constructor parameter name differs from the attribute | The value is silently omitted from the written file |
| Capability token missing from the profile | One `missing-capability` diagnostic per node using it |
| `REPEATS` left `False` on a repeating block | The block does not count toward `max_loop_nesting`, so a program that exceeds the hardware's loop depth validates clean |
| `register_block` used instead of `register_vendor_block` | No `require` line for a file whose only vendor content is the block, so it does not auto-activate the package |

Two more, which the registries cannot catch for you. Do not re-export
`qp.QProgram` unchanged as your package's `QProgram`: the pre-combined class
should subclass the mixin, or users lose the static typing that is the mixin's
only purpose. And keep bus attributes plain: `QProgram.rebind` re-resolves an
operation's buses by rewriting the attributes named in `BUS_ATTRS`, and it can
only do that when they hold `str` or `BusRef` values, or a list of those,
directly.

Name the distribution to install in your README. The parser's error names the
vendor namespace, `fake_inst`, which is not necessarily the package name a
reader has to type into `pip install`.

## Related pages

[The `.qp` format](../reference/qp-format.md) is the grammar the writer and
parser implement on both sides of the registry lookup above.
