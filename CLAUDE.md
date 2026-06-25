# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative specifications (read these first)

Two design specifications live under `.specs/` and are the **source of truth** for what this codebase is building. Read them before making any non-trivial change, and consult them whenever a question of intended behavior comes up:

- `.specs/qprogram-dsl.md` — QProgram DSL specification (Python API, expressions, waveforms, operations, control flow, vendor extension protocol, results, platform protocol).
- `.specs/qp-file-format.md` — `.qp` text serialization format (grammar, headers, `require` versioning rules, variable declarations, vendor dot-notation).

These specs are **drafts**. They originated as Notion mirrors, but the local copies are currently **ahead of Notion** (reconciled 2026-06-02 to match the implementation; the upstream Notion pages still need the same update). The code and the local specs are in sync as of that date. If you spot a mismatch between code and `.specs/`, flag it before "fixing" either side: ask whether the code or the spec should change. Do not silently bring the code in line with the spec, or vice versa, without confirming. Per the project's DSL-change checklist, every DSL change must update code, tests, docs, `.specs/`, and the upstream Notion pages together.

If the user asks about behavior, syntax, vendor protocol, or design rationale, consult `.specs/` before answering — don't infer from code alone.

## Repository layout

A three-package demo showing how to decouple a vendor-agnostic core DSL (`qprogram/`) from vendor-specific extensions (`qprogram-qblox/`, `qprogram-qdac/`). Each package is its own uv project with its own `pyproject.toml`, `uv.lock`, and `.venv` — there is **no top-level workspace**. The vendor packages depend on `qprogram` as an editable path source (see their `pyproject.toml` `[tool.uv.sources]`). `editors/vscode-qp/` holds the no-build VS Code extension (plain JS — not a Python package).

All three packages have substantial test suites (`qprogram/tests/` ~1,460 tests including hypothesis-based round-trip property tests in `tests/test_round_trip_property.py`; each vendor package ~95–100). `qprogram/tests/_dummy_vendor.py` is a complete in-tree vendor extension used as a fixture (`activate()`/`deactivate()` keep the global registries clean between tests).

## Common commands

Run all commands from inside the relevant package directory (`qprogram/` or `qprogram-qblox/`):

```bash
uv sync                          # install + create .venv (run once, or after dep changes)
uv run python -c "..."           # run anything inside the package's venv
uv run ruff check .              # lint
uv run ruff format .             # auto-format
uv run ty check src              # type-check (qprogram only — ty is in qprogram dev deps)
uv run pytest                    # run the package's test suite
uv run pytest path/to/test.py::test_name  # single test
uv run jupyter lab               # demo notebooks live in each package's .tmp/ (not committed)
```

The vendor packages' dev groups carry pytest but not ruff/ty. To lint/typecheck them, use `qprogram`'s venv: `cd ../qprogram && uv run ruff check ../qprogram-qblox ../qprogram-qdac`. In `qprogram`, `matplotlib` is an optional extra (`viz`) used only by `Waveform.plot()`; it is present in the dev group so the test suite can exercise plotting.

Ruff is configured with `select = ["ALL"]` and a curated ignore list — the rule set is intentionally strict, expect the formatter and linter to disagree with most external code.

## Architecture

### Core idea: AST that builds itself

`QProgram` (`qprogram/qprogram.py`) is a fluent builder that constructs an AST. Every `program.play(...)`, `program.measure(...)`, etc. appends a typed `Operation` subclass (`operations/`) to the current `Block` (`blocks/`). Control-flow context managers (`for_loop`, `loop`, `average`, `block`) push/pop blocks via `_block_stack`, so nested `with`-statements build nested AST nodes.

The `Block`/`Operation` split is the core taxonomy: blocks are containers (`Block`, `Loop`, `ForLoop`, `Average`, `Parallel`, `Conditional`) and operations are leaves (`Play`, `Measure`, `Wait`, `Sync`, `SetFrequency`, ...). `Parallel` is created exclusively by `LoopContext.__or__` — `with for_loop(...) | for_loop(...) as p:`. `Conditional` is created by `program.if_(...) / elif_(...) / else_()` — the builder maintains a `_pending_conditional` state so sequential `with` blocks chain correctly; any other append at the chain's parent level closes it (and the next `elif_` / `else_` raises loudly).

### Symbolic expressions for parametric values

Anywhere a numeric parameter is accepted, an `Expression` is also accepted (`variable.py`). The system is a tiny AST: `Variable` (structural equality **by string id**, holds a value or `UNASSIGNED`), `Constant`, `BinaryOp`, `UnaryOp`, `Comparison`, `LogicalBinaryOp`, `LogicalNot`, `MathFunc`, `Where`, plus `MeasurementRef` (a leaf referring to a measurement result field, built via `handle.state`). Operators on `Expression` build the tree; `evaluate()` reads `Variable.value` / `MeasurementHandle._values[field]` and propagates `UNASSIGNED`. The runtime executor sets variable values per loop iteration and measurement field values per measurement event — *not* a parameter-passing scheme. Use `evaluate_or_raise()` when a concrete number is required. `handle.state` returns a throwaway `_HandleFieldAccess` proxy whose `==` / `!=` build a `Comparison(MeasurementRef, Constant)` — the proxy exists so `MeasurementRef` itself keeps a normal structural `__eq__` for AST equality.

`Variable` ids must match `[A-Za-z_][A-Za-z0-9_]*`, must be unique within a program (`program.variable` enforces this), and are emitted verbatim as `.qp` identifiers — id-equality is what lets a whole program survive `deepcopy` and `loads(dumps(...))` while comparing equal to the original. All other expression nodes use plain structural equality.

### Fragments (program composition, `fragments.py` + `operations/call.py`)

`Fragment(QProgram)` is a named, parameterized sub-program — the whole builder surface works inside its body (vendor namespaces included), plus `parameter()` placeholders (`Parameter(Variable)`, untyped: the call-site binding determines value/bus/waveform kind). Two equal-billing definition styles: the `@fragment` decorator (signature = parameter list; the body runs **once** at decoration to record the AST) and the explicit `Fragment(...)`/`parameter()` API. `program.call(frag, *args, **kwargs)` binds with the Python calling convention and appends a **first-class `Call` node**; the fragment (and transitively its callees, deps first) is registered in `program._fragments`. Definitions and calls round-trip through `.qp` (`fragment <name>(<params>):` sections before `body:`; bare `name(args)` call statements — a statement shape only fragments use). `program.expand()` is the canonical lowering: deepcopy, then each `Call` becomes a plain `Block` with parameters substituted (raw in value positions, `Constant`-wrapped inside expression trees, kind-checked in bus/waveform positions incl. channel/acquires re-validation), fragment-local vars hygienically renamed (`{frag}_{id}` + numeric suffix), measurement names uniquified via handle rename (keeps `MeasurementRef`s consistent), nested calls recursed with cycle detection. `validate()` auto-expands when `Call`s are present; `Call.required_capabilities()` is empty by design — no platform token needed. The writer emits fragments in dependency order computed at write time (`_topo_fragments`); the parser enforces define-before-use and parses fragment bodies via a scope swap of `(self._program, self._variables, self._handles)` onto the Fragment instance. `dumps(fragment)` is rejected — serialize the host program.

### Vendor extensions (the decoupling pattern this repo demonstrates)

`qprogram` itself has zero knowledge of any vendor. Vendor extensions hook in via three orthogonal mechanisms:

1. **Runtime namespace** (`vendor.py`, `qprogram.py:__getattr__`). `QProgram._vendor_registry` maps vendor names to `VendorNamespace` subclasses. Calling `QProgram.register_vendor("qblox", QbloxNamespace)` makes `program.qblox.acquire(...)` work at runtime on *any* `QProgram` instance — including the base class — by lazily instantiating the namespace on first attribute access and caching it on the instance.

2. **Typed mixin** (`qprogram_qblox/mixin.py`). `QbloxMixin` exposes `.qblox` as a `@property` returning `QbloxNamespace`. This is purely for IDE autocomplete — at runtime the dynamic `__getattr__` would do the same thing. The pre-combined `qprogram_qblox.QProgram = type(QbloxMixin, BaseQProgram)` saves users the multiple-inheritance step. Multiple vendor mixins compose by listing them in MRO.

3. **Serialization registry** (`serialization/registry.py`). Three module-level dicts: waveforms by class name, vendor operations by `(vendor, name)`, vendor versions by name. Vendor extension's `__init__.py` calls `register_vendor_operation(vendor, op_name, cls)` for each operation it adds, and `register_vendor_version(vendor, x.y.z)` once.

`qprogram_qblox/__init__.py` does all three at import time. Importing the package is the activation step — `import qprogram_qblox` registers the namespace, version, and operations as side effects.

**Auto-activation via entry points.** Each vendor package also declares a `[project.entry-points."qprogram.vendors"]` mapping (`<vendor> = "<self-registering module>"`). When `loads()`/`load()` hits a `require <vendor>` line for a vendor that isn't imported yet, `registry.try_activate_vendor()` looks up that entry-point group and imports the module on demand (running the side effects above), so a `.qp` file loads without the caller importing the extension first. Discovery only runs for unregistered vendors; `loads(..., auto_activate=False)` opts out; a found-but-broken extension raises `VendorActivationError` (wrapped into `ParseError` during parse). The scan is memoized (`registry.clear_vendor_discovery_cache()` resets it for tests).

### Capability protocol (`protocol.py`, `profiles.py`, `validation.py`)

Platforms declare which DSL features they support per (bus, domain) slot, and validate programs against that declaration. The top-level descriptor is `PlatformCapabilities`:

```
PlatformCapabilities
├── bus: Mapping[(element_kind, bus_kind), BusCapabilities]   # per-bus profiles
├── platform: BusCapabilities                                  # block.*, expr.*, bus-less op.*
└── default_bus_profile: BusCapabilities                       # raw-string-bus fallback
```

`BusCapabilities(hw, sw)` is the two-domain split — real-time hardware vs software-dispatched orchestration. Either half may be `None`. Each non-None half is a `CompilerCapabilities` with the same three orthogonal axes as before (capabilities, limits, predicates).

**Routing.** Each AST node is checked against the slot it logically belongs to: bus-touching ops route via `caps.for_bus(node.bus)` (schema-backed BusRef → `(element, kind)` lookup, else `default_bus_profile`; raw string → `default_bus_profile`); bus-less ops and all blocks route to `caps.platform`; multi-bus ops (`Sync` with explicit targets) intersect across every touched bus, and broadcast ops (`Sync(targets=None)`, marked by `Operation.BROADCASTS_WHEN_NO_BUS`) intersect across **every bus in the program** (`ctx.program_buses`). Within the routed slot, `expr.*` tokens always check against `caps.platform` regardless of where the op itself routes (they describe Python AST node kinds, not bus features). Token namespace (and where each lives): `op.*` (bus or platform depending on whether the op touches a bus), `block.*` (platform), `sweep.*` (platform — emitted by loop blocks), `waveform.*` (bus), `expr.*` (always checked against platform), `measure.returns.*` (bus — emitted by `Measure`), `vendor.<name>.<op>` (bus or platform).

**HW / SW classification.** Required tokens are domain-agnostic — the same set is checked against both halves of the routed slot. Domain-specific behavior comes from predicates:
- `Diagnostic` outputs → hard error in the slot's domain (surfaces only when no domain works; deduplicated when the same profile fills both halves).
- `DomainConstraint(node, exclude, reason)` outputs → soft restriction. **Constraints must target a `Block`** — the binding loop of the swept variable (`ctx.binding_loop_of(var)`), never the op; an op-targeted constraint is reported as `bad-domain-constraint`.

The validator runs a two-pass walk (see `.specs/qprogram-dsl.md` §9.7 for the full rules): per-node check, then post-order block classification by **op-children consensus** — (c) a block's natural domain is the intersection of its immediate op-children's supports (block-children act as units, with implicit SW propagation upward); **an `Average` is the exception** — only its *averaging-relevant* op-children (`Operation.AFFECTS_AVERAGING`, set `True` on `MeasurementOperation`) enter its consensus, since averaging accumulates measurement results (the other ops still validate/execute but don't gate the average's domain; `validation._domain_relevant_ops`); (d) healthy op-children with disjoint singleton supports → `"mixed-domain"` error (an op-child whose own support is already empty silently empties the block instead — its own diagnostics explain why); (e1) an SW-only block-child inside a parent whose slot lacks an SW half → `"sw-in-hw"` error; (e2) a constraint stripping `"hw"` from an all-HW block drops the *block* to SW dispatch while its ops stay HW. Outputs `(list[Diagnostic], ExecutionPlan)`. `ExecutionPlan = Mapping[Node, frozenset[Domain]]` and is **identity-keyed** — one entry per node *instance*, looked up by `id()`, so structurally identical ops don't collapse. When a block's support loses `"hw"` and ends at `{sw}`, one `severity="warning"` `"forced-software"` diagnostic surfaces on the highest such block (its parent isn't forced sw); its reason is attributed to the **immediate** cause — the block's own `DomainConstraint` reasons, or (when forced merely by containing a software sub-block) the named sub-block plus that sub-block's reasons. An `Average` that is sw *only* because it encloses a software sweep (its measurements all support hw) also gets a `severity="info"` `"reorderable-averaging"` hint pointing at the free function `optimize(program, caps)` (in `optimization.py`, the rewrite analogue of `validate`), which turns the `average{ sweep{…} }` pattern into `sweep{ setup; average{…} }` so the averaging runs in hardware. Empty support → `"empty-domain"` (or the contributing predicate `Diagnostic`s, if any). `max_loop_nesting` counts repetition levels: `for_loop`/`loop`/`average` are one level each, `parallel` one total, `conditional`/`block` zero.

**Diagnostics UX** (`paths.py`, `explain.py`). Severities are `error` (cannot run) / `warning` (runs degraded — forced-software) / `info` (advisory). Every node-bearing `Diagnostic` is stamped with a structural `path` (rooted at `body` = `()`; int segments index `elements`, `"arm:<i>"`/`"else"` address Conditional arm bodies, `"loop:<i>"` Parallel loop headers) — helpers `node_path`/`resolve_path`/`format_path` are top-level exports. `loads()` records `program.source_map` (path → 1-based `.qp` line; cleared by `expand()`, empty for built programs, fragment internals unmapped), so `loads(dumps(p)).source_map[diag.path]` locates the offending line. `qp.explain(program, caps)` / `PlatformProtocol.explain()` renders the plan as a tree — nodes as `.qp` text via the writer's serializers (repr fallback), domain column (`[hw|sw]`/`[hw]`/`[sw]`/`[--]`), inline `!!`/`~`/`i` annotations, node-less diagnostics in a footer; fragments auto-expand.

**Per-node methods are non-recursive** — the validator walks via `body.walk()` and unions per-node sets; recursing inside `required_capabilities()` would double-count.

Vendors register **profile bundles** via `register_profile(Profile(name=..., capabilities=..., limits=..., predicates=..., extends=...))` as a side effect of importing the vendor package. Profiles are domain-agnostic; a platform decides which profile fills each (bus, domain) slot. Core qprogram ships `qprogram-base-v1` in `qprogram/profiles.py` (registered on `import qprogram`) — the canonical platform-level base of block/sweep/expression/bus-less-op tokens. Vendor platforms typically use it for the platform slot via `extends="qprogram-base-v1"` or `from_profile("qprogram-base-v1", ...)`.

`PlatformProtocol` exposes `.capabilities: PlatformCapabilities`, `.validate(qp) -> list[Diagnostic]`, `.plan(qp) -> ExecutionPlan`, and `.explain(qp) -> str` — all default to delegating into `qprogram.validation.validate` / `qprogram.explain`. The validator does not raise — callers (typically `execute()`) decide how to react; the convention is to raise `UnsupportedOperationError` on any `severity="error"` diagnostic, surface `severity="warning"` without raising, and pass `severity="info"` through as advisory.

### Reference software executor (`executor.py`)

`ReferencePlatform(PlatformProtocol)` + the `qp.run(program, model=..., parameters=...)` one-liner: a pure-Python interpreter that is the **reference semantics vendor compilers are tested against** and the executable definition of spec §8's result shapes. `execute()` expands fragments, validates (errors → `UnsupportedOperationError`; warnings → `warnings.warn(..., ExecutionWarning)`; the platform slot keeps `set_parameter`/`get_parameter` sw-only and ships a swept-`set_parameter` `DomainConstraint` predicate, so forced-software fires here too), then interprets: loops drive `Variable.set_value` (`ForLoop` values pinned as `start + step*arange(num_iterations())`), `Average` repeats and **averages out** (no shots dim; `state` → excited-state population), `Parallel` advances in lockstep, `Conditional` evaluates via the handle values each measurement writes (`handle._set_value("state", ...)` — feedback works by construction), every op's `Expression` attrs are `evaluate_or_raise`d (except `GetParameter.variable`, an output target), and non-measurement ops are otherwise no-ops (no timing/waveform physics). Measurement outcomes come from a pluggable `MeasurementModel` — the default `MockMeasurementModel(response, p_excited, noise, raw_samples, seed)` is seed-deterministic and receives an env of bound loop variables + platform parameters. Results: one record per measurement, dims = enclosing sweeps outermost-first (Parallel → one `"a|b"` dim with both coords), per-token arrays in `MeasurementResult.fields` (`iq` `(*s, IQ)`, `state` `(*s)`, `raw` `(*s, time, IQ)`), `data` = primary (`iq` when requested), `result.get(m, field=...)`; conditional-arm measurements are NaN where unexecuted. `reference_capabilities()` covers every token in the live `CAPABILITY_REGISTRY` (vendor ops execute generically).

Design lineage: MLIR's SPIR-V dialect (distributed declaration + centralized check + per-op interface methods), MLIR's `addDynamicallyLegalOp` (operand-sensitive predicates), QIR profiles (named, hierarchical bundles), Vulkan (features/limits/extensions split).

### `.qp` text format (custom serializer)

`serialization/writer.py` (`dumps`/`save`) and `serialization/parser.py` (`loads`/`load`) implement a custom plain-text format — not JSON/YAML. Format:

```
#!QProgram 1.0
require qblox 0.1                   # one per used vendor
metadata:
  label: "..."
  description: "..."
fragment x_pulse(drive, amp):       # zero or more, before body; called as bare `x_pulse(...)` statements
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)
body:
  var freq label="Frequency"        # declarations: id + optional label/units/description kwargs
  average 1000:
    for freq in range(...):
      set_frequency q[0].drive freq
      x_pulse(q[0].drive, 0.5)
      qblox.acquire "readout_q0" "weights" name="m0"
```

Key behaviors:

- **Strict both ways.** The parser raises `ParseError` for unknown operations (core or dotted-vendor), unknown block keywords, unknown top-level sections, excess positional tokens, and malformed metadata — a file never loads with content silently missing. The writer raises `SerializationError` (in `errors.py`) for unregistered node classes or unrepresentable values, and **never truncates** arrays (`Loop` values, `Arbitrary` samples are emitted in full). Files are always UTF-8. `FORMAT_VERSION` lives once in `serialization/_format.py`.
- **Vendor compatibility** is checked on parse via `_check_vendor_compat`: file's `major` must equal installed extension's `major`; file's `minor` must be ≤ installed `minor`. Compatibility is at major.minor; patch is informational. Patch is truncated when writing. `_collect_vendors` walks via `Block.walk()` so vendor ops inside `Conditional` arms still get their `require` line.
- **Variable identifiers** are the `Variable.id` verbatim (ids are unique per program, pattern-validated, and reserved-keyword-checked at construction).
- **Measurements** carry their name as a `name="..."` kwarg on the wire (`measurement_op_serialize` / `make_measurement_op_parse` in `_specs.py`); files without a name auto-allocate like the builder. Vendor measurement ops register both callbacks.
- **Vendor operations** are serialized via the registry's per-class `OperationSpec` lookup and reparsed by the `inspect.signature`-driven default parser. New vendor operations get free serialization as long as their `__init__` signature is introspectable and they're registered. Registration collisions (same `(vendor, name)`, different class) raise; reserved vendor names (`RESERVED_KEYWORDS ∪ {"core"}`) are rejected at all three registration sites (`QProgram.register_vendor`, `register_vendor_version`, `register_operation`), and `register_vendor` also rejects names shadowing `QProgram` attributes.
- **Quoting is the type distinction**: quoted tokens are plain strings (tracked through parsing via the `_QuotedStr` marker so a raw-string bus that *looks* like a path is never promoted to a `BusRef`); bare `element[index].kind` tokens are bus paths, promoted post-parse for attributes listed in the op's `BUS_ATTRS` only. Sequence kwargs are bracket literals (`outputs=[1, 2]`), string-keyed dicts are brace literals (`matrix={...}` — a generic dict-kwarg form, available to any op that carries one), `null` means `None`, and expressions are always parenthesised (`(100 - t)`).
- The parser is **lazy-imported** from `qprogram/__init__.py` and `qprogram/serialization/__init__.py` via module `__getattr__` — the parser imports `QProgram`, which would create a cycle if loaded eagerly. Don't break this without redoing the import graph.

### Canonical grammar + editor tooling (`grammar/`, `lsp.py`, `editors/vscode-qp/`)

`src/qprogram/grammar/qp.lark` is the **normative machine-readable grammar** (Lark dialect, LALR + 2-space `Indenter`; `qprogram.grammar.parser()` builds the reference parser — `lark` is a dev-only dep). It is NOT the production parser; `tests/test_grammar.py` CI-verifies the two never drift (writer corpus + hypothesis strategies must parse under it; ~13 syntactic negatives must fail under both). The grammar over-approximates semantic rules (registry membership, section order, duplicates) by design; it is exact about token shapes (call adjacency `name(`, BUS_PATH/DOTTED_NAME terminals, quoting, indentation). Soft structural keywords (`metadata`/`schema`/`body`/`require`/`element`/`naming`/`info`) remain valid identifiers via the `name` rule; hard keywords in use (`var`/`for`/`in`/`and`/`or`/`not`) are in `RESERVED_KEYWORDS`.

`qprogram/lsp.py` is the editor checker built on the real toolchain: pure `check_text()` (ParseError → line-tagged error; else `validate()` against `reference_capabilities()` with `Diagnostic.path` → `source_map` line mapping), CLI modes `python -m qprogram.lsp check|explain` (zero extra deps, JSON out / plan tree), and `serve` (LSP over stdio via the `qprogram[lsp]` extra, pygls 2.x). `editors/vscode-qp/` is a dependency-free plain-JS VS Code extension (no build step): TextMate grammar `syntaxes/qp.tmLanguage.json`, language config, snippets, and `extension.js` spawning the `check` mode on open/save/change-debounced plus a `qp: Explain execution plan` command.

### Bus references (`buses.py`)

`BusRef` subclasses `str` (with `__slots__`!) so it is a string everywhere — AST, serialization, compiler — but also carries `element`, `idx` (named to avoid shadowing `str.index`), `kind`, `channel` (`single`/`IQ`), `acquires` (has ADC), and `schema` (back-pointer to the producing `BusSchema`, used to reject refs from a foreign schema). The validators in `qprogram.py` (`_validate_waveform_channel`, `_validate_acquires`) only fire when the bus is a `BusRef` — raw strings opt out of validation entirely. A program built without a schema silently adopts the first schema-backed BusRef's schema.

`BusSchema` exposes typed factories (`TransmonSchema`, `FluxoniumSchema`, ...) via class-method presets, plus a dynamic `add_element()` path that uses `__getattr__` and gives no static typing. New typed schemas should subclass `BusSchema` and add `@property` factories. Schemas **compose**: `schema_a + schema_b` (or `BusSchema.combine(*schemas, naming=...)` for 3+ / explicit naming) returns a new dynamic `BusSchema` with the union of both schemas' elements — either operand may be a schema instance or class (class-level `+` works via the `_BusSchemaMeta` metaclass; both forms return an *instance*). Combining rejects conflicting element definitions or naming patterns, and the result is untyped (build refs from the combined schema). This is purely a Python-API construction convenience — the `.qp` inline-schema form is unchanged, so it doesn't touch the grammar/format.

### Portability: `rebind` + `WaveformLibrary` (`waveform_library.py`)

Two transforms port a program and bind its waveforms, both keyed on the schema's `(element, idx, kind)` coordinate (these replaced the legacy flat `with_bus_mapping`/`with_waveforms` dicts). **`program.rebind(*, schema=, elements=, naming=, strings=, allow_unported_strings=)`** re-resolves every `BusRef` *structurally* through a schema factory (shared `buses.resolve_ref`, also used by the parser; `naming_substituted_schema` handles naming-only ports), so the result stays a typed `BusRef` that serializes as a path — fixing the legacy `BusRef`→`str` round-trip demotion. It swaps `program._schema` in lockstep, raises `AttributeError` on an absent target kind, requires `strings=`/`allow_unported_strings=` for metadata-less raw buses, and re-derives bus-embedded auto measurement names (the new non-serialized `MeasurementHandle._auto_named` flag distinguishes them from user names; user names are never rewritten). **`program.with_waveforms(library_or_dict)`** resolves string waveform names **per bus** via `WaveformLibrary` (tiers: exact `(element, idx, kind, name)` → family `(element, kind, name)` → global `(name,)`; a bare dict = global tier = legacy behavior), re-running `_validate_waveform_channel` on each replacement. `WaveformLibrary` is a **standalone artifact** — deliberately *not* tied to `PlatformProtocol` (no `get_waveform_library`); resolution is a pre-execution step the caller applies (`program.with_waveforms(library)` / `library.apply(program)`) before `run`/`execute`, which take concrete programs and know nothing about libraries. The library is **not** in `.qp`; it has its own portable `.wfl` text format (`WaveformLibrary.dumps/save/loads/load`) that reuses the `.qp` waveform-constructor syntax — see `.specs/qp-file-format.md` §10.

### What lives where (when adding things)

- New core operation: `qprogram/src/qprogram/operations/<name>.py` (subclass `Operation`), export from `operations/__init__.py`, add a method on `QProgram`, register it in `serialization/_specs.py:_register_core_specs()` (the default signature-driven serialize/parse pair covers regular shapes; pass custom callbacks for special forms — measurement ops use `measurement_op_serialize` + `make_measurement_op_parse`), implement `required_capabilities()` returning the op's identity token (`op.<name>`) plus any refinement tokens, register the token in `protocol.py:_BASE_TOKENS`. Add the token to whichever profile is the right home: bus-touching ops to vendor bus profiles; bus-less ops to `qprogram-base-v1` (`qprogram/profiles.py`).
- New waveform: subclass `Waveform`/`IQWaveform`, add to `_register_builtin_waveforms()` in `serialization/registry.py`, export from `waveforms/__init__.py`, register a class→token mapping in `protocol.py:_register_builtin_waveform_tokens()` (or via `register_waveform_token()` from a vendor package). Serializer auto-emits constructor args by walking `vars(wf)`; parser uses the registry by class name (duplicate names with a different class are rejected). Waveform tokens always live on the bus profile (waveforms reach the hardware via a bus).
- New vendor operation: subclass `Operation` in the vendor package, add a typed method to its `VendorNamespace` subclass, call `register_vendor_operation(vendor, name, cls)` in the vendor package's `__init__.py` (measurement ops also pass `serialize=measurement_op_serialize, parse=make_measurement_op_parse(cls)`), implement `required_capabilities()` returning `{vendor.<name>.<op>}` plus refinement tokens, register the token via `register_capability_tokens(...)` in the vendor's `profiles.py`, include it in the vendor profile's capability set. No core changes needed.
- New vendor (whole namespace): create a separate package depending on `qprogram`, follow `qprogram-qblox` or `qprogram-qdac` as the template (mirror the `__init__.py` four-step registration: vendor namespace, vendor version, operations, profile). Vendor names must not be reserved keywords, `"core"`, or `QProgram` attribute names. Also declare a `[project.entry-points."qprogram.vendors"]` line (`<vendor> = "<package_module>"`) so `.qp` files requiring it auto-activate on load; after editing pyproject, `uv sync` so the entry point lands in the installed metadata.
- New profile bundle: create a `Profile` in the vendor package's `profiles.py`, register via `register_profile()` from `qprogram-<vendor>/__init__.py`. Use `extends=` to inherit from a parent profile (e.g. `extends="qprogram-base-v1"` for a platform-level slot).
- New domain-constraint predicate: write a callable returning `Iterable[Diagnostic | DomainConstraint]`. Yield `DomainConstraint(node=<the binding loop Block>, exclude={"hw"}, reason="...")` for soft restrictions ("hw can't, but sw dispatch works") — the constraint **must target a Block** (use `ctx.binding_loop_of(var)`); the classifier drops that loop to sw while the op stays hw. Yield `Diagnostic` for hard errors (no domain can run it).
