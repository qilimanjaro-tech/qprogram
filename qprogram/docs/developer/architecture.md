# Architecture

This page is for people who want to change QProgram, not just use it.

## Repository layout

```
qprogram/
├── pyproject.toml              # package + docs dep group
├── mkdocs.yml                  # docs site config (this site)
├── docs/                       # markdown sources
├── src/qprogram/
│   ├── __init__.py
│   ├── qprogram.py             # QProgram class, context managers
│   ├── buses.py                # BusSchema, BusRef, BusNaming, presets
│   ├── variable.py             # Expression AST
│   ├── _reserved.py            # RESERVED_KEYWORDS
│   ├── errors.py               # exception hierarchy
│   ├── _structural.py          # ast_eq / ast_hash helpers
│   ├── vendor.py               # VendorNamespace base
│   ├── platform.py             # PlatformProtocol + .capabilities + .validate
│   ├── protocol.py             # CompilerCapabilities, Diagnostic, Profile, token registry
│   ├── validation.py           # the validator
│   ├── result.py               # MeasurementHandle, QProgramResult
│   ├── crosstalk_matrix.py
│   ├── operations/
│   ├── blocks/
│   ├── waveforms/
│   └── serialization/
│       ├── writer.py
│       ├── parser.py
│       ├── registry.py         # registry-driven dispatch
│       └── _specs.py           # per-op serialize/parse callbacks
└── tests/                      # ~1000 unit tests
```

Alongside `qprogram/` in the parent repository is `.specs/`, which holds
the authoritative design specs (`qprogram-dsl.md`,
`qp-file-format.md`), and `qprogram-qblox/`, a proof-of-concept vendor
extension that the developer guide references as the working template.
That extension lives in its own `uv` project with its own `pyproject.toml`,
depending on `qprogram` as an editable path source.

## The AST builder

`QProgram` (`qprogram.qprogram.QProgram`) is a fluent builder. Every method
on it falls into one of three categories.

- **Operation appenders** (`play`, `measure`, `set_frequency`, ...) append
  a single `Operation` node to the currently active block.
- **Context managers** (`for_loop`, `loop`, `average`, `block`) push a new
  `Block` onto a stack on `__enter__` and pop it on `__exit__`. Inside the
  `with`, the new block is the active one.
- **Transformers** (`with_bus_mapping`, `with_waveforms`) deep-copy the
  program and rewrite the AST in-place.

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
        self._active_block.append(Play(bus=bus, waveform=waveform))
```

## Two taxonomies

The AST has two kinds of node.

**Operations** are the leaves. Each one is a typed dataclass-ish class with
explicit attributes:

```python
class Play(Operation):
    BUS_ATTRS = ("bus",)
    WAVEFORM_ATTRS = ("waveform",)
    def __init__(self, bus, waveform):
        self.bus = bus
        self.waveform = waveform
```

**Blocks** are containers. The five built-ins are `Block` (generic),
`ForLoop`, `Loop`, `Average`, and `Parallel`. Every block carries an
`elements` list:

```python
class Block:
    def __init__(self):
        self.elements = []
    def append(self, child): ...
    def walk(self): ...                  # yields all descendant ops
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
`UnaryOp`, `Comparison`, `LogicalBinaryOp`, `LogicalNot`, `MathFunc`, and
`Where`.

Two equality rules matter:

- `Variable` compares **by id**. Two `Variable("freq")` instances are equal
  even though they are distinct Python objects. This makes program-level
  structural equality survive `deepcopy` and `loads`/`dumps`.
- Every other node uses **structural** equality: `Constant(5) == Constant(5)`,
  same operator + structurally equal operands for `BinaryOp`, and so on.

The shared helpers `ast_eq` and `ast_hash` (`qprogram._structural`)
implement structural comparison for arbitrary nested data including
`Variable`, `numpy.ndarray`, tuples, lists, dicts, and nested combinations.

## Three vendor-extension hooks

A vendor extension plugs in three ways, each orthogonal.

### 1. Runtime namespace

`QProgram._vendor_registry` is a class-level dict mapping vendor names to
`VendorNamespace` subclasses. `QProgram.__getattr__` looks up the name and
lazily instantiates and caches a namespace on the instance.

```python
QProgram.register_vendor("qblox", QbloxNamespace)
program.qblox          # works on any QProgram, even the base class
```

### 2. Typed mixin

For IDE autocomplete, each vendor package ships a mixin with one
`@property`:

```python
class QbloxMixin:
    @property
    def qblox(self) -> QbloxNamespace: ...
```

`qprogram_qblox.QProgram = type("QProgram", (QbloxMixin, BaseQProgram), {})`
pre-combines the mixin so end users do not have to. Multiple vendors
compose with multiple inheritance.

The dynamic `__getattr__` works at runtime regardless of whether the mixin
is present. The mixin is purely a typing aid.

### 3. Serialization registry

`qprogram.serialization.registry` exposes three module-level dicts:

- waveforms by class name,
- operations by `(vendor, name)`,
- vendor protocol versions by name.

Vendor packages call `register_vendor_operation(vendor, name, cls)` and
`register_vendor_version(vendor, version)` at import time. The writer
serialises each operation through `(vendor, name)` lookup; the parser
reverses the same lookup to find the class.

### 4. Capability protocol

`qprogram.protocol` defines `CompilerCapabilities`, `Diagnostic`,
`Profile`, `ValidationContext`, and registries for capability tokens and
profiles. Every `Operation` and `Block` subclass implements
`required_capabilities()` returning the dotted-string tokens it needs
(instance-aware: refines based on the op's data). A vendor package
registers tokens, a waveform-class dispatch table, and one or more
`Profile` bundles at import time, alongside the existing three registration
steps. `qprogram.validation.validate(qp, caps)` walks the AST once and
emits diagnostics for missing tokens, exceeded limits, and predicate
failures. Full details in [Capability protocol internals](capability-protocol.md).

## The `.qp` writer

`qprogram/src/qprogram/serialization/writer.py` walks the AST and emits
lines.

- Operations dispatch via the registry. The default serializer reflects on
  `__init__` to emit positional args followed by `key=value` kwargs;
  special-cased ops (`sync`, `get_parameter`, `set_crosstalk`, ...) supply
  their own callbacks in `_specs.py`.
- Variable identifiers are allocated up-front in `_allocate_var_idents`,
  sanitised against the reserved keyword set, and de-duplicated. Since
  `QProgram.variable` enforces unique ids at construction, the writer never
  has to invent disambiguation suffixes for in-API programs; the safety net
  is there for AST manipulation outside the public API.
- Schemas (when present) serialise to the inline form regardless of how the
  Python schema was constructed.

## The `.qp` parser

`qprogram/src/qprogram/serialization/parser.py` is a recursive-descent
parser in pure Python. It is small (under 1000 lines) and has no external
dependencies.

Key behaviours:

- It is **lazy-imported** from `qprogram/__init__.py` via module
  `__getattr__`. The parser imports `QProgram`, which would create a cycle
  if loaded eagerly.
- It validates `require` declarations against the installed extension
  before parsing the body.
- It rebuilds a dynamic `BusSchema` from the inline schema declaration
  rather than reaching for a preset class. The element/bus structure
  survives; the Python class identity does not.
- It is intentionally lenient on pre-1.0 trailing tokens (extra args past
  the signature are dropped), but strict on everything that would break
  semantics.

## Where to add things

A summary that maps the moving parts to file locations.

| Adding ...                              | Where it goes                                                                  |
|------------------------------------------|--------------------------------------------------------------------------------|
| A new core operation                     | `operations/<name>.py` (subclass `Operation`), export in `operations/__init__.py`, add a `QProgram.<verb>` method, implement `required_capabilities()`. The serializer auto-handles it; see [Adding operations](adding-operations.md). |
| A new waveform                           | `waveforms/<name>.py` (subclass `Waveform` or `IQWaveform`), register in `serialization/registry._register_builtins`, register a class→token mapping in `protocol._register_builtin_waveform_tokens`. See [Adding waveforms](adding-waveforms.md). |
| A new vendor operation                   | Inside the vendor package: new `Operation` subclass with `required_capabilities()`, a typed method on the `VendorNamespace`, one `register_vendor_operation` call, the vendor token in the profile's capability set. No core changes. |
| A new vendor (whole namespace)            | A new package depending on `qprogram`. Copy `qprogram-qblox` as the template. Ships a profile bundle. |
| A new capability token                    | Edit `protocol._BASE_TOKENS` (core) or call `register_capability_tokens` from a vendor package. See [Capability protocol internals](capability-protocol.md). |
| A new profile bundle                      | Create a `Profile` in the vendor package's `profiles.py`. Register via `register_profile(profile)` from the vendor `__init__.py`. |
| A new block kind                         | `blocks/<name>.py`. Will need a parser entry and a writer header callback; pre-1.0 these are not yet pluggable. |

## Why decouple at the package boundary

A platform library (QiliLab) often pulls in many vendor SDKs. By making
each vendor extension a separate package that registers itself on import,
we keep `qprogram` cheap to install for users who only care about the
language, and we keep each vendor's hard dependencies out of `qprogram`'s
dependency graph entirely. Users can install only the vendor packages they
actually need.

The boundary is enforced at import time: `import qprogram` does not
transitively import any vendor package. The other direction is fine and
intended: vendor packages import from `qprogram` freely.
