# Architecture

This page is for people who want to change QProgram, not just use it.

## Repository layout

```
qprogram/
├── LICENSE                         # Apache-2.0
├── README.md
├── pyproject.toml                  # package metadata, dep groups, ruff / ty / pytest config
├── zensical.toml                   # docs site config (this site)
├── uv.lock
├── .github/workflows/              # CI: tests, code quality, docs
├── docs/                           # markdown sources for this site
├── tests/                          # 1579 tests, one module per source area
│   ├── conftest.py                 # shared schema / program / waveform fixtures
│   └── _dummy_vendor.py            # a complete in-tree vendor extension, used as a fixture
└── src/qprogram/
    ├── __init__.py                 # public re-export surface (parser entry points lazy-imported)
    ├── qprogram.py                 # QProgram builder + control-flow context managers
    ├── buses.py                    # BusSchema, BusRef, BusNaming, typed presets
    ├── variable.py                 # the symbolic Expression AST
    ├── fragments.py                # Fragment / Parameter: named, parameterized sub-programs
    ├── waveform_library.py         # per-bus waveform-name resolution, the .wfl format
    ├── result.py                   # MeasurementHandle, MeasurementResult, QProgramResult
    ├── errors.py                   # exception hierarchy
    ├── _reserved.py                # RESERVED_KEYWORDS
    ├── _structural.py              # ast_eq / ast_hash helpers
    ├── vendor.py                   # VendorNamespace base
    ├── platform.py                 # PlatformProtocol: capabilities, validate, plan, explain, execute
    ├── protocol.py                 # capability descriptors, Diagnostic, Profile, token registry
    ├── profiles.py                 # QPROGRAM_BASE_V1, the core platform-level profile
    ├── validation.py               # the two-pass validator + domain classifier
    ├── paths.py                    # structural node paths: node_path / resolve_path / format_path
    ├── explain.py                  # renders an execution plan as an annotated tree
    ├── optimization.py             # optimize(): plan-improving program rewrites
    ├── executor.py                 # ReferencePlatform + simulate(): the reference interpreter
    ├── lsp.py                      # check_text(), the check|explain|serve CLI, the language server
    ├── operations/                 # one module per leaf op, plus the Operation base
    ├── blocks/                     # Block, Sweep, Average, Parallel, Conditional
    ├── sweeps/                     # SweepSource contract, built-in sources, combinators
    ├── waveforms/                  # Waveform / IQWaveform bases and the built-in shapes
    ├── grammar/
    │   └── qp.lark                 # the normative machine-readable grammar
    └── serialization/
        ├── writer.py               # dumps / save
        ├── parser.py               # loads / load
        ├── registry.py             # registry-driven dispatch
        ├── _specs.py               # per-op serialize/parse callbacks
        └── _format.py              # the format version constant
```

`qprogram` is the whole language: the AST, the `.qp` format, the capability
protocol, and the reference executor. A vendor extension is a separate
package in its own repository that depends on `qprogram` and registers
itself on import — see [Building a vendor extension](vendor-extensions.md).
`tests/_dummy_vendor.py` is a complete vendor extension living in the test
suite: it runs the same registration steps an installed extension runs on
import. It ships no `pyproject.toml`, so entry-point discovery is covered
separately, with a stub entry point, in `tests/test_vendor_discovery.py`.

## The AST builder

`QProgram` (`qprogram.qprogram.QProgram`) is a fluent builder. Every method
on it falls into one of three categories.

- **Operation appenders** (`play`, `measure`, `set_frequency`, ...) append
  a single `Operation` node to the currently active block.
- **Context managers** (`sweep`, `average`, `block`, and the
  `if_` / `elif_` / `else_` chain) push a new `Block` onto a stack on
  `__enter__` and pop it on `__exit__`. Inside the `with`, the new block is
  the active one.
- **Transformers** (`rebind`, `with_waveforms`, `expand`) deep-copy the
  program and rewrite the copy — `rebind` re-resolves bus references
  structurally through a schema; `with_waveforms` resolves string waveform
  names per bus against a `WaveformLibrary`; `expand` lowers every fragment
  `Call` into a plain `Block`.

The active block is `self._block_stack[-1]`. Operations append into it; block
context managers push and pop.

```python
class QProgram:
    def __init__(self, ...):
        self._body = Block()
        self._block_stack: deque[Block] = deque([self._body])

    @property
    def _active_block(self) -> Block:
        return self._block_stack[-1]

    def play(self, bus, waveform):
        self._validate_bus(bus)
        _validate_waveform_channel(bus, waveform)
        self._append_to_active(Play(bus=bus, waveform=waveform))
```

## Two taxonomies

The AST has two kinds of node.

**Operations** are the leaves. Each one is a typed class with explicit
attributes:

```python
class Play(Operation):
    WAVEFORM_ATTRS = ("waveform",)

    def __init__(self, bus, waveform):
        self.bus = bus
        self.waveform = waveform
```

**Blocks** are containers. The five built-ins are `Block` (generic),
`Sweep`, `Average`, `Parallel`, and `Conditional`. Every block carries an
`elements` list:

```python
class Block:
    def __init__(self):
        self._elements = []

    @property
    def elements(self): ...
    def append(self, child): ...
    def walk(self): ...  # yields self, then every descendant in pre-order
    def variables(self): ...
    def buses(self): ...
    def waveforms(self): ...
```

Introspection (`Block.buses()`, `Block.walk()`, ...) is driven by the
`BUS_ATTRS` and `WAVEFORM_ATTRS` class attributes on each operation. Vendor
operations that declare these get free introspection support without any
core changes.

## Expressions

`qprogram.variable` contains a small AST for symbolic expressions. The base
class is `Expression`; concrete nodes are `Variable`, `Constant`, `BinaryOp`,
`UnaryOp`, `Comparison`, `LogicalBinaryOp`, `LogicalNot`, `MathFunc`,
`Where`, and `MeasurementRef`.

Two equality rules matter:

- `Variable` compares **by id**. Two `Variable("freq")` instances are equal
  even though they are distinct Python objects. This makes program-level
  structural equality survive `deepcopy` and `loads`/`dumps`.
- Every other node uses **structural** equality: `Constant(5) == Constant(5)`,
  same operator + structurally equal operands for `BinaryOp`, and so on.

The shared helpers `ast_eq` and `ast_hash` (`qprogram._structural`)
implement structural comparison for arbitrary nested data including
`Variable`, `numpy.ndarray`, tuples, lists, dicts, and nested combinations.

## Four vendor-extension hooks

A vendor extension plugs in four ways, each orthogonal.

### 1. Runtime namespace

`QProgram._vendor_registry` is a class-level dict mapping vendor names to
`VendorNamespace` subclasses. `QProgram.__getattr__` looks up the name and
lazily instantiates and caches a namespace on the instance.

```python
QProgram.register_vendor("myvendor", MyVendorNamespace)
program.myvendor  # works on any QProgram, even the base class
```

### 2. Typed mixin

For IDE autocomplete, each vendor package ships a mixin with one
`@property`:

```python
class MyVendorMixin:
    @property
    def myvendor(self) -> MyVendorNamespace: ...
```

`qprogram_myvendor.QProgram = type("QProgram", (MyVendorMixin, BaseQProgram), {})`
pre-combines the mixin so end users do not have to. Multiple vendors
compose with multiple inheritance.

The dynamic `__getattr__` works at runtime regardless of whether the mixin
is present. The mixin is purely a typing aid.

### 3. Serialization registry

`qprogram.serialization.registry` holds module-level dicts for:

- waveforms by class name,
- sweep sources by class name,
- operations by `(vendor, name)`,
- blocks by qualified keyword,
- vendor protocol versions by name.

Vendor packages call `register_vendor_operation(vendor, name, cls)` and
`register_vendor_version(vendor, version)` at import time. The writer
serializes each operation through a class lookup; the parser reverses the
same lookup by `(vendor, name)` to find the class. A
`[project.entry-points."qprogram.vendors"]` declaration lets `loads()`
import the extension on demand when a file `require`s it.

### 4. Capability protocol

`qprogram.protocol` defines `PlatformCapabilities` (per-bus + platform-wide profiles),
`BusCapabilities(rt, host)` (the two-domain split), `CompilerCapabilities` (a single slot's
contents), `Domain`, `DomainConstraint`, `Diagnostic`, `Profile`, `ValidationContext`, and
registries for capability tokens and profiles. Every `Operation` and `Block` subclass implements
`required_capabilities()` returning the dotted-string tokens it needs (instance-aware,
domain-agnostic). Core qprogram ships `qprogram-base-v1` in `src/qprogram/profiles.py` — a
platform-level base of block / sweep / expression tokens that vendors extend. A
vendor package registers tokens, a waveform-class dispatch table, and one or more `Profile`
bundles at import time, alongside the other registration steps. `qprogram.validation.validate(qp, caps)`
walks the AST in a two-pass routine (per-node check + bottom-up domain classification) and
returns `(diagnostics, plan)`. Full details in [Capability protocol internals](capability-protocol.md).

## The `.qp` writer

`src/qprogram/serialization/writer.py` walks the AST and emits lines.

- Operations dispatch via the registry. The default serializer reflects on
  `__init__` to emit positional args followed by `key=value` kwargs;
  special-cased ops (`sync`, `get_parameter`, the measurement ops) supply
  their own callbacks in `_specs.py`.
- Variable identifiers are collected up-front in `_allocate_var_idents` and
  emitted verbatim. `Variable` validates its id against
  `[A-Za-z_][A-Za-z0-9_]*` at construction and `QProgram.variable` rejects
  duplicates, so the writer never has to invent an identifier or a
  disambiguation suffix.
- Schemas (when present) serialize to the inline form regardless of how the
  Python schema was constructed.
- Arrays are never truncated. `Values` points and `Arbitrary` samples are
  emitted in full, because the file has to reload to exactly the same
  program.

## The `.qp` parser

`src/qprogram/serialization/parser.py` is a recursive-descent parser in pure
Python, with no external dependencies.

Key behaviors:

- It is **lazy-imported** from `qprogram/__init__.py` via module
  `__getattr__`. The parser imports `QProgram`, which would create a cycle
  if loaded eagerly.
- It validates `require` declarations against the installed extension
  before parsing the body, and activates an installed-but-unimported vendor
  through its `qprogram.vendors` entry point.
- It rebuilds a dynamic `BusSchema` from the inline schema declaration
  rather than reaching for a preset class. The element/bus structure
  survives; the Python class identity does not.
- It is **strict in both directions**. An unknown operation, an unknown
  block keyword, an unrecognized top-level section, an excess positional
  token, or malformed metadata each raise `ParseError` rather than loading a
  program with content silently missing.

`src/qprogram/grammar/qp.lark` is the normative machine-readable grammar for
the same format. It is not the production parser; `tests/test_grammar.py`
cross-checks the two so they cannot drift.

## Where to add things

A summary that maps the moving parts to file locations.

| Adding ...                              | Where it goes                                                                  |
|------------------------------------------|--------------------------------------------------------------------------------|
| A new core operation                     | `operations/<name>.py` (subclass `Operation`), export in `operations/__init__.py`, add a `QProgram.<verb>` method, implement `required_capabilities()`, register in `serialization/_specs.py:_register_core_specs()`; see [Adding operations](adding-operations.md). |
| A new waveform                           | `waveforms/<name>.py` (subclass `Waveform` or `IQWaveform`), add to `serialization/registry._register_builtin_waveforms()`, register a class→token mapping in `protocol._register_builtin_waveform_tokens()`. See [Adding waveforms](adding-waveforms.md). |
| A new sweep source                       | `sweeps/builtin.py` (or `sweeps/combinators.py` if it wraps another source), declare `KIND` and `TOKEN`, one line in the `register_sweep_source` loop in `_specs.py`, the token in `protocol._BASE_TOKENS` and `profiles._SWEEP_SOURCES`. |
| A new core block kind                    | `blocks/<name>.py` (subclass `Block`), set `REPEATS` if it re-runs its body, then `register_block(name, cls, serialize_header=..., parse_header=...)` in `_specs.py`. |
| A new vendor operation                   | Inside the vendor package: new `Operation` subclass with `required_capabilities()`, a typed method on the `VendorNamespace`, one `register_vendor_operation` call, the vendor token in the profile's capability set. No core changes. |
| A new vendor (whole namespace)            | A new package depending on `qprogram`. Mirror the four-step activation pattern (namespace, version, operations, profile) plus the entry point; `tests/_dummy_vendor.py` is a working reference for the four registration steps. |
| A new capability token                    | Edit `protocol._BASE_TOKENS` (core) or call `register_capability_tokens` from a vendor package. See [Capability protocol internals](capability-protocol.md). |
| A new profile bundle                      | Create a `Profile` in the vendor package's `profiles.py`. Register via `register_profile(profile)` from the vendor `__init__.py`. |

## Why decouple at the package boundary

A platform library often pulls in many vendor SDKs. Making each vendor
extension a separate package that registers itself on import keeps
`qprogram` cheap to install for users who only care about the language, and
keeps each vendor's hard dependencies out of `qprogram`'s dependency graph
entirely. Users install only the vendor packages they actually need.

The boundary is enforced at import time: `import qprogram` does not
transitively import any vendor package. The other direction is fine and
intended: vendor packages import from `qprogram` freely.
