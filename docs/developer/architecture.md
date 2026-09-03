# Architecture

This page is for people changing QProgram rather than using it: what lives
where under `src/qprogram/`, which direction the imports run between those
modules, and the patterns that recur across the package. Most of the code below
is package source, each snippet headed by the file it comes from and keeping
that file's intra-package imports. One snippet is source from a vendor package,
headed by a `# qprogram-<vendor>/...` path comment: that is external code, so it
reaches QProgram symbols through `qp.` and uses its own dotted paths for its own
modules. The rest is ordinary code written against the installed package, which
reaches everything through `qp.`.

## Repository layout

```
qprogram/
├── LICENSE                         # Apache-2.0
├── README.md
├── CHANGELOG.md                    # assembled by towncrier from fragments in changelog/
├── pyproject.toml                  # metadata, deps, extras, ruff / ty / pytest / towncrier config
├── zensical.toml                   # docs site config and the explicit nav (this site)
├── uv.lock
├── .github/workflows/              # tests, code quality, docs, publish
├── docs/                           # markdown sources for this site
├── tests/                          # 1582 tests, roughly one module per source area
│   ├── conftest.py                 # shared schema / program / waveform fixtures
│   └── _dummy_vendor.py            # a complete in-tree vendor extension, used as a fixture
└── src/qprogram/
    ├── __init__.py                 # the public surface: 105 names, parser entry points lazy
    ├── py.typed                    # PEP 561 marker: the package ships its own annotations
    ├── qprogram.py                 # QProgram builder, control-flow contexts, vendor registry
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
    ├── plotting/                   # the figure model, the themes, and the renderer registry
    ├── operations/                 # one module per leaf op, plus the Operation base
    ├── blocks/                     # Block, Sweep, Average, Parallel, Conditional
    ├── sweeps/                     # SweepSource contract, built-in sources, combinators
    ├── waveforms/                  # Waveform / IQWaveform bases and 17 built-in shapes
    ├── grammar/
    │   ├── __init__.py             # grammar_text(), the reference Lark parser
    │   └── qp.lark                 # the normative machine-readable grammar
    └── serialization/
        ├── writer.py               # dumps / save
        ├── parser.py               # loads / load
        ├── registry.py             # registry-driven dispatch
        ├── _specs.py               # per-op serialize/parse callbacks
        └── _format.py              # the format version constant
```

`qprogram` is the whole language: the AST, the `.qp` format, the capability
protocol, the validator, and the reference executor. It depends on `numpy>=2.1`
and `xarray>=2026.4.0` at runtime and nothing else; `matplotlib` (the `viz`
extra) and `pygls` (the `lsp` extra) are optional, and `lark` is a development
dependency used only to cross-check the grammar. A vendor extension is a
separate package in its own repository that depends on `qprogram` and registers
itself on import. See [Building a vendor extension](vendor-extensions.md).

`tests/_dummy_vendor.py` is a complete vendor extension living in the test
suite. Its `activate()` runs the same registration calls an installed extension
runs on import, and `deactivate()` pops the same entries back out, so a fixture
can install and remove the vendor at well-defined points. It ships no
`pyproject.toml`, so entry-point discovery is covered separately, with a stub
entry point, in `tests/test_vendor_discovery.py`.

## What each module owns

The AST is the center of the package. `qprogram.py` holds the `QProgram`
builder, the private context-manager classes its control-flow methods return,
the class-level vendor-namespace registry, and the whole-program transforms
(`expand`, `rebind`, `with_waveforms`). `blocks/` holds the container nodes:
the `Block` base plus `Sweep`, `Average`, `Parallel`, and `Conditional`.
`operations/` holds one module per leaf node (12 classes, from `play` and
`measure` through `set_parameter` and the fragment `Call`), plus the
`Operation` base and the `MeasurementField` vocabulary in `operation.py`.

Four vocabularies feed those nodes. `variable.py` is the symbolic expression
AST: `Expression` and its `Variable`, `Constant`, `BinaryOp`, `UnaryOp`,
`Comparison`, `LogicalBinaryOp`, `LogicalNot`, `MathFunc`, `Where`, and
`MeasurementRef` nodes. `waveforms/` holds the `Waveform` and `IQWaveform`
bases and 17 shapes, one per module. `sweeps/` holds the `SweepSource`
contract, the five parameter-only sources in `builtin.py` (`Range`, `Values`,
`Linspace`, `Logspace`, `File`) and the three that wrap another source in
`combinators.py` (`Repeat`, `Rotate`, `Concat`). `buses.py` holds `BusSchema`,
`BusNaming`, and `BusRef`, which subclasses `str` so a typed reference is a
plain string everywhere downstream.

The rest of the AST layer is supporting structure. `fragments.py` holds
`Fragment`, `Parameter`, and the `expand_program` lowering that inlines every
call site. `result.py` holds `MeasurementHandle`, `MeasurementResult`, and
`QProgramResult`. `plotting/` is what `QProgramResult.plot` runs: `build.py`
turns a result array into the `Figure` description in `model.py`, and a
renderer registered in `renderers.py` draws it. Only `matplotlib_renderer.py`
imports a plotting library, and it is imported on first use, which is what
keeps `matplotlib` optional. `waveform_library.py` resolves a waveform alias
per bus and owns the `.wfl` text format, which is deliberately not part of a
`.qp` file: calibration state travels alongside a program, not inside it.
`errors.py` defines the whole exception hierarchy under `QProgramError`,
including the platform-side classes that core QProgram never raises but every
backend shares. `_reserved.py` holds `RESERVED_KEYWORDS`, and `_structural.py`
the two equality helpers described below.

Analysis sits above the AST. `protocol.py` defines what a platform declares:
`PlatformCapabilities` (per-bus profiles plus one platform-wide profile),
`BusCapabilities(rt, host)`, `CompilerCapabilities` for a single slot,
`Domain`, `DomainConstraint`, `Diagnostic`, `Profile`, `ValidationContext`, and
the two registries behind them, `CAPABILITY_REGISTRY` (67 core tokens) and
`PROFILE_REGISTRY`. `profiles.py` registers `QPROGRAM_BASE_V1`, the
platform-level base of block, sweep, and expression tokens, as a side effect of
`import qprogram`. `validation.py` is the two-pass validator: a per-node
capability check and a bottom-up domain classification, returning a list of
`Diagnostic`s and an `ExecutionPlan`. `paths.py` gives every node a structural
address that survives a `.qp` round-trip, which is how a diagnostic maps back
to a line. `explain.py` renders a plan as an annotated tree, and
`optimization.py` applies the one rewrite the validator's
`reorderable-averaging` hint suggests, sharing the match decision with the
validator so the hint and the rewrite cannot disagree.

Execution and tooling sit at the top. `platform.py` defines
`PlatformProtocol`, the seam a backend implements; its `validate`, `plan`, and
`explain` have working defaults, so a concrete platform supplies its resources,
its `PlatformCapabilities`, and `execute`. `executor.py` is `ReferencePlatform`
and `simulate()`, the in-tree interpreter that defines the reference semantics
vendor compilers are tested against. `serialization/` is the `.qp` writer,
parser, registries, and per-operation callbacks. `grammar/` ships `qp.lark`,
the normative grammar, and builds a reference Lark parser from it for the CI
cross-check. `lsp.py` exposes the real toolchain to editors through
`check_text()` and a `check | explain | serve` command line.

## Which way the imports run

The floor of the package is seven modules that anything else may import without
ordering concerns: `_reserved.py`, `_structural.py`, `errors.py`, `buses.py`,
`protocol.py`, `platform.py`, and `serialization/_format.py`. None of them
imports from `qprogram` at module scope, which is what makes them safe to reach
from anywhere: `protocol.py` is what every operation, block, and sweep source
has to reach for its capability tokens, and `platform.py` is what a vendor
package subclasses. Two more modules have no module-scope package import
either, but they sit at the far end of the tree rather than under it: nothing in
`src/qprogram/` imports `lsp.py` or `grammar/__init__.py`, and each defers an
optional dependency into a function body alongside its package imports, `pygls`
and `lsprotocol` from the `lsp` extra in `lsp.py` and the dev-only `lark` in
`grammar/__init__.py`.

Above that floor the direction is one way, from the AST outward. The
vocabularies (`variable`, `waveforms/waveform`, `sweeps/source`) import only
the floor. `operations/operation.py` imports the vocabularies; the concrete
operations import the base; `blocks/` imports the `Block` base and, in `Sweep`,
the sweep-source contract, naming `Operation` only under `TYPE_CHECKING`; and
`qprogram.py` imports blocks, operations, buses, sweeps, waveforms, and the
waveform library. Analysis imports the AST (`validation` reads blocks and
operations, `explain` reads `validation` and the writer), execution imports
analysis (`executor` imports `platform` and `validation`), and serialization
imports the AST plus its own registries. Nothing in the AST layer imports the
analysis, execution, or serialization layers at module scope.

Where an edge genuinely has to run the other way it is deferred into a function
body instead of the module scope. The main cases:

| In | Deferred import | Resolved when |
|---|---|---|
| `operations/*.py` | `qprogram.protocol` | `required_capabilities()` needs a waveform or expression token |
| `qprogram.py` | `qprogram.fragments` | `call()` binds arguments, or `expand()` lowers a call |
| `qprogram.py` | `qprogram.serialization.registry` | a fluent `from_*` sweep builder is resolved |
| `waveforms/waveform.py` | `qprogram.waveforms.chained` | two envelopes are joined with `|` |
| `waveform_library.py` | `qprogram.serialization.writer` / `.parser` | a `.wfl` file is written or read |
| `platform.py` | `qprogram.validation`, `qprogram.explain` | a default `validate` / `plan` / `explain` runs |
| `protocol.py` | `qprogram.buses`, `.variable`, `.waveforms`, `.paths` | a routing, token, or path helper is called |

The reasons differ but the shape does not. `protocol.py` needs `paths.py` to
stamp a diagnostic, and `paths.py` imports `qprogram.py`, so an eager import
there would pull the builder into the descriptor module. The sweep builder's
`from_*` lookup initializes the entire `qprogram.serialization` package, which
nothing in the builder needs until a reader actually writes `from_range(...)`.
Every deferred import in the package carries the annotation ruff wants
(`# ruff: ignore[import-outside-top-level]`), so a deferred import is always
visible as a decision rather than an accident.

### The parser's lazily resolved names

`loads`, `load`, and `ParseError` are the only names in `__all__` that
`qprogram/__init__.py` does not import at module scope. They are resolved on
first attribute access instead:

```python
# src/qprogram/__init__.py
def __getattr__(name: str):
    if name in {"loads", "load", "ParseError"}:
        from qprogram.serialization.parser import ParseError, load, loads

        return {"loads": loads, "load": load, "ParseError": ParseError}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
```

`qprogram/serialization/__init__.py` carries the same three names behind the
same `__getattr__`. The parser is the one module that sits above almost
everything: it constructs `QProgram`, `Fragment`, `BusSchema`, `BusRef`,
`MeasurementHandle`, and `Variable` instances, and it reaches back into
`qprogram.serialization` for the spec callbacks. Importing it from either
`__init__.py` closes a cycle in the module graph, through a package that is
still executing its own import block.

The effect a caller can observe is that `import qprogram` does not load the
parser at all:

```python
import sys

import qprogram as qp

"qprogram.serialization.parser" in sys.modules  # False
qp.loads  # resolves the name, which imports the parser now
"qprogram.serialization.parser" in sys.modules  # True
```

`qprogram.lsp` and `qprogram.grammar` stay out of that set too, for the same
reason from the other end: neither is imported by `__init__.py`, so the
optional `pygls` and `lark` dependencies are only reached by a caller that
asks for them.

## The builder and the block stack

`QProgram` is a fluent builder over a stack of open blocks. `__init__` creates
the root `Block` and puts it on the stack; `_active_block` is the top of that
stack; every operation appender goes through `_append_to_active`.

```python
# src/qprogram/qprogram.py, abridged
class QProgram:
    def __init__(self, label="", description=None, schema=None):
        self._body = Block()
        self._block_stack: deque[Block] = deque([self._body])

    @property
    def _active_block(self) -> Block:
        return self._block_stack[-1]

    def play(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self._validate_bus(bus)
        _validate_waveform_channel(bus, waveform)
        self._append_to_active(Play(bus=bus, waveform=waveform))
```

Past the read-only properties (`body`, `schema`, `buses`, `variables`,
`fragments`, `source_map`), the `measurement_handles()` accessor, and the two
declaration helpers (`variable`, `register_vendor`), every method on the builder
is one of three kinds. Operation appenders (`play`, `measure`, `wait`, `sync`,
`set_frequency`, `set_phase`, `set_gain`, `set_offset`, `reset_phase`,
`set_parameter`, `get_parameter`, `call`) construct one `Operation` and append
it to the active block, after checking what can only be checked at the call
site: that the bus belongs to this program's schema, and that a concrete
waveform's channel count matches the bus's.

Control-flow methods (`sweep`, `average`, `block`, and the `if_` / `elif_` /
`else_` chain) return a context manager rather than a node. Its `__enter__`
appends the new `Block` to the active block and then pushes it onto the stack;
its `__exit__` pops it. Nesting `with` statements therefore nests blocks, and
the depth of the Python indentation is the depth of the tree. The
context-manager classes are private, and none of them is meant to be
constructed directly.

Two details of the stack are worth knowing before changing it. The `if_` chain
is not a stack discipline: `elif_` and `else_` mutate the `Conditional` that
`if_` left in `_pending_conditional` and push a new arm body themselves, which
is why they bypass `_append_to_active`. That pending chain is closed by the
first append that lands at the conditional's own parent level, since anything
other than an `elif_` or `else_` there would make the chain ambiguous. And
`_LoopContext.__or__`, `repeat`, and `rotate` are all pure: each returns a
fresh context and touches the program only in `__enter__`, which is what lets
`functools.reduce(operator.or_, ...)` fold a list of sweeps into one `Parallel`
block.

Transformers (`expand`, `rebind`, `with_waveforms`) never mutate. Each deep
copies the program and rewrites the copy: `expand` replaces every fragment
`Call` with the fragment body inlined, `rebind` re-resolves schema-backed bus
references through a schema factory so the result is still a typed `BusRef`,
and `with_waveforms` resolves string waveform aliases per bus against a
`WaveformLibrary`.

## Operations and blocks

The AST has exactly two kinds of node, and they share one introspection
contract. Operations are the leaves: a typed class whose `__init__` parameters
are its attributes, with nothing hidden.

```python
# src/qprogram/operations/play.py
class Play(Operation):
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
        self.bus = bus
        self.waveform = waveform
```

Blocks are the containers. `Block` itself is an ordered list of children;
`Sweep`, `Average`, `Parallel`, and `Conditional` subclass it to add structure
the validator, writer, and executor understand.

```python
# src/qprogram/blocks/block.py, abridged
class Block:
    REPEATS: ClassVar[bool] = False

    def __init__(self) -> None:
        self._elements: list[Block | Operation] = []

    def append(self, element: Block | Operation) -> None: ...
    def walk(self) -> Iterator[Block | Operation]: ...  # self, then descendants, pre-order
    def variables(self) -> set[Variable]: ...
    def buses(self) -> set[str]: ...
    def waveforms(self) -> set[Waveform | IQWaveform | str]: ...
    def required_capabilities(self) -> set[str]: ...
```

`Operation` implements all of those but `append`, so a caller can write
`for node in program.body.walk():` and treat what comes back uniformly.
`Operation.walk()` yields just the leaf, and `variables()` walks every public
attribute, descending into expressions, waveform parameters, and lists.
`buses()` and `waveforms()` take the attribute names to read off two class
attributes, which is what makes them free for a vendor operation: `BUS_ATTRS`
(default `("bus",)`) names the attributes holding bus references, and
`WAVEFORM_ATTRS` (default empty) names the ones holding waveforms. `Sync` sets
`BUS_ATTRS = ("targets",)` because it holds a list; `Call` sets it empty,
because buses reach a call site only as bound argument values.

Three more class attributes carry information the analysis layer needs and
would otherwise have to recover with an `isinstance` ladder.
`Block.REPEATS` is true on `Sweep`, `Parallel`, and `Average`, and is how the
validator computes `max_loop_nesting`; a `Parallel` counts as one level in
total, because its loop headers live on `loops` rather than among its children.
`Operation.AFFECTS_AVERAGING` is true on `MeasurementOperation` only, and marks
the ops whose presence decides whether an `Average` can run in real time.
`Operation.BROADCASTS_WHEN_NO_BUS` is true on `Sync`, and tells the validator to
route an op with no resolved bus across every bus in the program instead of the
default slot. A vendor class that sets any of these is counted correctly with no
core change.

`required_capabilities()` is non-recursive on both kinds of node. The validator
visits every node and checks each one's own token set against the slot that node
routes to, so recursing here would double-count. See
[Capability protocol internals](capability-protocol.md).

## Expressions

`variable.py` is a small AST of its own, rooted at `Expression`. Every numeric
parameter of an operation or a waveform accepts an `Expression` in place of a
plain number, and `Expression.evaluate()` takes no arguments: each `Variable`
carries its own current value, written by the runtime once per loop iteration,
and the `UNASSIGNED` sentinel propagates upward while any variable is unbound.

The operator overloads follow the NumPy and SymPy convention rather than
Python's keywords: `&`, `|`, and `~` build logical nodes, because `and`, `or`,
and `not` cannot be overloaded. `&` and `|` bind tighter than the comparison
operators, so a compound condition needs parentheses:
`(freq < 5e9) & (gain > 0.5)`. The ordering comparisons (`<`, `<=`, `>`, `>=`)
build `Comparison` nodes, but `==` and `!=` deliberately do not: `Variable.__eq__`
has to return a bool so that variables can live in the sets that
`Expression.variables()` returns and be used as dictionary keys. Equality
comparisons are written with the named helpers `qp.eq` and `qp.ne` instead.

## Structural equality

`_structural.py` holds the two helpers the whole AST shares, `ast_eq(a, b)` and
`ast_hash(value)`. Both recurse through the container shapes that actually
appear inside AST attributes, `ndarray`, `list`, and `dict`, and defer to the
value's own `==` or `hash` for everything else.

Four places define the same pair of methods over them: `Operation`, `Block`,
`SweepSource`, and the `_StructuralEqMixin` that `Waveform` and `IQWaveform`
inherit.

```python
# src/qprogram/operations/operation.py, abridged
class Operation:
    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return ast_eq(vars(self), vars(other))

    def __hash__(self) -> int:
        items = tuple(sorted((k, ast_hash(v)) for k, v in vars(self).items()))
        return hash((type(self).__name__, items))
```

Routing every node through `vars(self)` means a new operation, block, waveform,
or sweep source needs no `__eq__` or `__hash__` of its own, and no edit to
either when it gains an attribute, and it treats the awkward attribute types the
way every other node does: a nested `IQPair` recurses, a `Variable` attribute
compares by id, an `ndarray` of samples compares by contents. That is what makes
whole-program comparison work across a `deepcopy` and across a `.qp` round-trip,
since `Variable` compares equal whenever the `id` matches and
`QProgram.variable` rejects a duplicate id, making id-equality identity within
one program.

The two helpers are not exact mirrors.
`ast_eq` compares arrays with `np.array_equal`, which ignores dtype; `ast_hash`
hashes `(shape, tobytes())`, which does not. Two nodes that differ only in a
sample array's dtype therefore compare equal but hash apart, and land in
different buckets of a `dict` or `set`:

```python
import numpy as np

import qprogram as qp

ints = qp.waveforms.Arbitrary(samples=np.array([1, 2, 3]))
floats = qp.waveforms.Arbitrary(samples=np.array([1.0, 2.0, 3.0]))

ints == floats  # True: ast_eq compares contents
hash(ints) == hash(floats)  # False: ast_hash includes the dtype
```

Every one of these classes is a value object whose attributes must stop
changing once it has been hashed. `QProgram.rebind` and the other transformers
rewrite operations on a fresh `deepcopy` for that reason, never in place.

## The three vendor hooks

A vendor extension plugs into core QProgram in three places, each independent
of the others: the runtime namespace that makes its operations callable, the
serialization registries that make them survive a `.qp` round-trip, and the
capability protocol that says which of them a given platform supports. The
`activate()` function in `tests/_dummy_vendor.py` is the whole sequence in one
place: `register_vendor`, `register_vendor_version`, one
`register_vendor_operation` per operation, `register_capability_tokens`, and
`register_profile`.

### Runtime namespace

`QProgram._vendor_registry` is a class-level dict mapping a vendor name to a
`VendorNamespace` subclass. `QProgram.__getattr__` looks the name up and caches
an instantiated namespace on the program with `object.__setattr__`, so a
namespace costs nothing until it is first reached.

```python
# qprogram-myvendor/src/qprogram_myvendor/__init__.py
import qprogram as qp

from qprogram_myvendor.namespace import MyVendorNamespace

qp.QProgram.register_vendor("myvendor", MyVendorNamespace)
```

After that call `program.myvendor.<op>(...)` resolves on any `QProgram`
instance, including one built from the base class. `register_vendor` refuses
three kinds of name: a reserved keyword or the `"core"` sentinel, a name that
collides with a `QProgram` attribute (normal attribute lookup wins over
`__getattr__`, so the namespace would be unreachable), and a name already
registered to a different class. Re-registering the same class under the same
name is a no-op, since import-time side-effect modules can run twice.
`__getattr__` also refuses any underscore-prefixed name immediately, so
protocol probes such as `__deepcopy__` fail fast instead of reaching the
registry.

`VendorNamespace` gives a namespace method the two helpers it needs: `_append`
for a plain operation, which validates any `BusRef` attribute against the
program's schema before appending, and `_append_measurement` for one that
returns a `MeasurementHandle`, which shares the per-bus name counter with
`QProgram.measure` so vendor and core measurements on a bus cannot collide.

Each vendor package also ships a mixin with one typed `@property` and a
pre-combined `QProgram` class (`qprogram_myvendor.QProgram`), and several
vendors compose through multiple inheritance. That is a typing aid, not a
fourth hook: the dynamic `__getattr__` resolves the namespace at runtime
whether or not the mixin is present, and the mixin exists so that an editor can
complete `program.myvendor.` and a type checker can check the arguments.

### Serialization registries

`qprogram/serialization/registry.py` holds seven module-level dicts making up
five registries: operations by class and by `(vendor, name)`, blocks by keyword
and by class, sweep sources by class name, waveforms by class name, and vendor
protocol versions by vendor name. The writer looks an operation up by its class
and the parser reverses the lookup by `(vendor, name)`, so neither one contains
an `isinstance` ladder or a hard-coded keyword list.

A vendor calls `register_vendor_operation(vendor, name, cls)` and
`register_vendor_version(vendor, version)` at import time, plus
`register_vendor_block` for a control-flow block of its own. It also declares a
`[project.entry-points."qprogram.vendors"]` entry point, which is what lets
`loads()` import an installed-but-unimported extension when a file's `require`
line names it.

The block registry covers the keyword-led headers, `block:` and
`average 1000:`, and a vendor block's `<vendor>.<keyword>:`. The three
structural blocks are not registered: a `Sweep` header is emitted as
`for <var> in <Source>(...)` with the source rendered from its own attributes,
and `Parallel` and `Conditional` have fixed grammar in both the writer and the
parser. Adding a sweep shape is therefore a new `SweepSource`, not a new block.
`qp.lark` in `grammar/` is the normative statement of all of this, and
`tests/test_grammar.py` parses the writer's output with it so the hand-written
parser and the grammar cannot drift.

### Capability protocol

Every `Operation` and `Block` subclass implements `required_capabilities()`,
returning the dotted-string tokens that instance needs. A vendor registers its
tokens with `register_capability_tokens`, maps its waveform classes to tokens
with `register_waveform_token`, and registers one or more `Profile` bundles of
capabilities, limits, and predicates with `register_profile`. `Profile` rejects
an unknown token in `__post_init__`, so the token registration has to happen
before the profile is constructed. A vendor profile either extends
`qprogram-base-v1` or fills its platform-level slot from it with
`CompilerCapabilities.from_profile("qprogram-base-v1", limit_overrides=...)`.
`validate(program, capabilities)` then walks the AST, checking each node's
tokens against the routed slot and classifying each block's execution domain,
and returns the diagnostics and the plan. Full details in
[Capability protocol internals](capability-protocol.md).

## Where to add things

| Adding ...                              | Where it goes                                                                  |
|------------------------------------------|--------------------------------------------------------------------------------|
| A new core operation                     | `operations/<name>.py` (subclass `Operation`), export in `operations/__init__.py`, add a `QProgram.<verb>` method, implement `required_capabilities()`, register in `serialization/_specs.py:_register_core_specs()`; see [Adding operations](adding-operations.md). |
| A new waveform                           | `waveforms/<name>.py` (subclass `Waveform` or `IQWaveform`), add to `serialization/registry._register_builtin_waveforms()`, register a class to token mapping in `protocol._register_builtin_waveform_tokens()`. See [Adding waveforms](adding-waveforms.md). |
| A new sweep source                       | `sweeps/builtin.py` (or `sweeps/combinators.py` if it wraps another source), declare `KIND` and `TOKEN`, one line in the `register_sweep_source` loop in `_specs.py`, the token in `protocol._BASE_TOKENS` and `profiles._SWEEP_SOURCES`. |
| A new core block kind                    | `blocks/<name>.py` (subclass `Block`), set `REPEATS` if it re-runs its body, then `register_block(name, cls, serialize_header=..., parse_header=...)` in `_specs.py`. |
| A new vendor operation                   | Inside the vendor package: new `Operation` subclass with `required_capabilities()`, a typed method on the `VendorNamespace`, one `register_vendor_operation` call, the vendor token in the profile's capability set. No core changes. |
| A new vendor (whole namespace)            | A new package depending on `qprogram`. Mirror the four-step activation pattern (namespace, version, operations, profile) plus the entry point; `tests/_dummy_vendor.py` is a working reference for the four registration steps. |
| A new capability token                    | Edit `protocol._BASE_TOKENS` (core) or call `register_capability_tokens` from a vendor package. See [Capability protocol internals](capability-protocol.md). |
| A new profile bundle                      | Create a `Profile` in the vendor package's `profiles.py`. Register via `register_profile(profile)` from the vendor `__init__.py`. |

## Why a vendor extension is a separate package

A platform library that supports many instruments tends to pull in many vendor
SDKs. Keeping each extension in its own package, registering itself on import,
keeps `qprogram` installable with two runtime dependencies for someone who only
wants the language, and keeps every vendor's hard dependencies out of
`qprogram`'s dependency graph. The cost is that a program's `.qp` file can
`require` a vendor the reader does not have installed, which is why the parser
checks the `require` lines against the registry before parsing the body and
raises rather than loading a program it cannot represent.

The boundary is enforced at import time: `import qprogram` does not
transitively import any vendor package. The other direction is intended, and
vendor packages import from `qprogram` freely.
