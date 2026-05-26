# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative specifications (read these first)

Two design specifications live under `.specs/` and are the **source of truth** for what this codebase is building. Read them before making any non-trivial change, and consult them whenever a question of intended behavior comes up:

- `.specs/qprogram-dsl.md` — QProgram DSL specification (Python API, expressions, waveforms, operations, control flow, vendor extension protocol, results, platform protocol).
- `.specs/qp-file-format.md` — `.qp` text serialization format (grammar, headers, `require` versioning rules, variable declarations, vendor dot-notation).

These specs are **drafts**, mirrored from Notion. The current code may diverge from them — when it does, **the spec is the intended target**, the code is what exists today. Flag any mismatch you spot before "fixing" either side: ask whether the code or the spec should change. Do not silently bring the code in line with the spec, or vice versa, without confirming.

If the user asks about behavior, syntax, vendor protocol, or design rationale, consult `.specs/` before answering — don't infer from code alone.

## Repository layout

A two-package demo showing how to decouple a vendor-agnostic core DSL (`qprogram/`) from vendor-specific extensions (`qprogram-qblox/`). Each package is its own uv project with its own `pyproject.toml`, `uv.lock`, and `.venv` — there is **no top-level workspace**. `qprogram-qblox` depends on `qprogram` as an editable path source (see `qprogram-qblox/pyproject.toml` `[tool.uv.sources]`).

There are currently **no tests** in either package, despite `pytest`/`pytest-cov` being declared as dev deps in `qprogram/pyproject.toml`.

## Common commands

Run all commands from inside the relevant package directory (`qprogram/` or `qprogram-qblox/`):

```bash
uv sync                          # install + create .venv (run once, or after dep changes)
uv run python -c "..."           # run anything inside the package's venv
uv run ruff check .              # lint
uv run ruff format .             # auto-format
uv run ty check src              # type-check (qprogram only — ty is in qprogram dev deps)
uv run pytest                    # tests (none exist yet; runs no-op)
uv run pytest path/to/test.py::test_name  # single test
uv run jupyter lab               # demo notebooks live in each package's .tmp/ (not committed)
```

`qprogram-qblox` has no dev-dep group, so its venv only contains runtime deps. To lint/typecheck `qprogram-qblox`, use `qprogram`'s venv: `cd ../qprogram && uv run ruff check ../qprogram-qblox`.

Ruff is configured with `select = ["ALL"]` and a curated ignore list — the rule set is intentionally strict, expect the formatter and linter to disagree with most external code.

## Architecture

### Core idea: AST that builds itself

`QProgram` (`qprogram/qprogram.py`) is a fluent builder that constructs an AST. Every `program.play(...)`, `program.measure(...)`, etc. appends a typed `Operation` subclass (`operations/`) to the current `Block` (`blocks/`). Control-flow context managers (`for_loop`, `loop`, `average`, `block`) push/pop blocks via `_block_stack`, so nested `with`-statements build nested AST nodes.

The `Block`/`Operation` split is the core taxonomy: blocks are containers (`Block`, `Loop`, `ForLoop`, `Average`, `Parallel`, `Conditional`) and operations are leaves (`Play`, `Measure`, `Wait`, `Sync`, `SetFrequency`, ...). `Parallel` is created exclusively by `LoopContext.__or__` — `with for_loop(...) | for_loop(...) as p:`. `Conditional` is created by `program.if_(...) / elif_(...) / else_()` — the builder maintains a `_pending_conditional` state so sequential `with` blocks chain correctly; any other append at the chain's parent level closes it (and the next `elif_` / `else_` raises loudly).

### Symbolic expressions for parametric values

Anywhere a numeric parameter is accepted, an `Expression` is also accepted (`variable.py`). The system is a tiny AST: `Variable` (identity-based equality, holds a value or `UNASSIGNED`), `Constant`, `BinaryOp`, `UnaryOp`, plus `MeasurementRef` (a leaf referring to a measurement result field, built via `handle.state`). Operators on `Expression` build the tree; `evaluate()` reads `Variable.value` / `MeasurementHandle._values[field]` and propagates `UNASSIGNED`. The runtime executor sets variable values per loop iteration and measurement field values per measurement event — *not* a parameter-passing scheme. Use `evaluate_or_raise()` when a concrete number is required. `handle.state` returns a throwaway `_HandleFieldAccess` proxy whose `==` / `!=` build a `Comparison(MeasurementRef, Constant)` — the proxy exists so `MeasurementRef` itself keeps a normal structural `__eq__` for AST equality.

`Variable` uses identity-based equality and an auto-incremented `_id`; other expression nodes use structural equality. This matters in the `.qp` writer, which keys its variable-identifier table on `Variable.id` so two variables with the same label still get distinct names.

### Vendor extensions (the decoupling pattern this repo demonstrates)

`qprogram` itself has zero knowledge of any vendor. Vendor extensions hook in via three orthogonal mechanisms:

1. **Runtime namespace** (`vendor.py`, `qprogram.py:__getattr__`). `QProgram._vendor_registry` maps vendor names to `VendorNamespace` subclasses. Calling `QProgram.register_vendor("qblox", QbloxNamespace)` makes `program.qblox.acquire(...)` work at runtime on *any* `QProgram` instance — including the base class — by lazily instantiating the namespace on first attribute access and caching it on the instance.

2. **Typed mixin** (`qprogram_qblox/mixin.py`). `QbloxMixin` exposes `.qblox` as a `@property` returning `QbloxNamespace`. This is purely for IDE autocomplete — at runtime the dynamic `__getattr__` would do the same thing. The pre-combined `qprogram_qblox.QProgram = type(QbloxMixin, BaseQProgram)` saves users the multiple-inheritance step. Multiple vendor mixins compose by listing them in MRO.

3. **Serialization registry** (`serialization/registry.py`). Three module-level dicts: waveforms by class name, vendor operations by `(vendor, name)`, vendor versions by name. Vendor extension's `__init__.py` calls `register_vendor_operation(vendor, op_name, cls)` for each operation it adds, and `register_vendor_version(vendor, x.y.z)` once.

`qprogram_qblox/__init__.py` does all three at import time. Importing the package is the activation step — `import qprogram_qblox` registers the namespace, version, and operations as side effects.

### Capability protocol (`protocol.py`, `profiles.py`, `validation.py`)

Platforms declare which DSL features they support per (bus, domain) slot, and validate programs against that declaration. The top-level descriptor is `PlatformCapabilities`:

```
PlatformCapabilities
├── bus: Mapping[(element_kind, bus_kind), BusCapabilities]   # per-bus profiles
├── platform: BusCapabilities                                  # block.*, expr.*, bus-less op.*
└── default_bus_profile: BusCapabilities                       # raw-string-bus fallback
```

`BusCapabilities(hw, sw)` is the two-domain split — real-time hardware vs software-dispatched orchestration. Either half may be `None`. Each non-None half is a `CompilerCapabilities` with the same three orthogonal axes as before (capabilities, limits, predicates).

**Routing.** Each AST node is checked against the slot it logically belongs to: bus-touching ops route via `caps.for_bus(node.bus)` (BusRef → `(element, kind)` lookup, else `default_bus_profile`; raw string → `default_bus_profile`); bus-less ops and all blocks route to `caps.platform`; multi-bus ops (`Sync`) intersect across every touched bus. Within the routed slot, `expr.*` tokens always check against `caps.platform` regardless of where the op itself routes (they describe Python AST node kinds, not bus features). Token namespace (and where each lives): `op.*` (bus or platform depending on whether the op touches a bus), `block.*` (platform), `sweep.*` (platform — emitted by loop blocks), `waveform.*` (bus), `expr.*` (always checked against platform), `measure.returns.*` (bus — emitted by `Measure`), `vendor.<name>.<op>` (bus or platform).

**HW / SW classification.** Required tokens are domain-agnostic — the same set is checked against both halves of the routed slot. Domain-specific behavior comes from predicates:
- `Diagnostic` outputs → hard error in the slot's domain (surfaces only when no domain works).
- `DomainConstraint(node, exclude, reason)` outputs → soft restriction (silently narrows the support set).

The validator runs a two-pass walk: per-node check, then bottom-up classification where each block's `support` is the intersection of children's. Outputs `(list[Diagnostic], ExecutionPlan)`. `ExecutionPlan = Mapping[Node, frozenset[Domain]]`. When a block's support shrinks from `{hw, sw}` to `{sw}`, one `severity="info"` `"forced-software"` diagnostic surfaces on the highest such block (its parent isn't forced sw). Empty support → `"empty-domain"` (or the contributing predicate `Diagnostic`s, if any).

**Per-node methods are non-recursive** — the validator walks via `body.walk()` and unions per-node sets; recursing inside `required_capabilities()` would double-count.

Vendors register **profile bundles** via `register_profile(Profile(name=..., capabilities=..., limits=..., predicates=..., extends=...))` as a side effect of importing the vendor package. Profiles are domain-agnostic; a platform decides which profile fills each (bus, domain) slot. Core qprogram ships `qprogram-base-v1` in `qprogram/profiles.py` (registered on `import qprogram`) — the canonical platform-level base of block/sweep/expression/bus-less-op tokens. Vendor platforms typically use it for the platform slot via `extends="qprogram-base-v1"` or `from_profile("qprogram-base-v1", ...)`.

`PlatformProtocol` exposes `.capabilities: PlatformCapabilities`, `.validate(qp) -> list[Diagnostic]`, and `.plan(qp) -> ExecutionPlan` — both default to delegating into `qprogram.validation.validate`. The validator does not raise — callers (typically `execute()`) decide how to react; the convention is to raise `UnsupportedOperationError` on any `severity="error"` diagnostic and pass `severity="info"` through as advisory.

Design lineage: MLIR's SPIR-V dialect (distributed declaration + centralized check + per-op interface methods), MLIR's `addDynamicallyLegalOp` (operand-sensitive predicates), QIR profiles (named, hierarchical bundles), Vulkan (features/limits/extensions split).

### `.qp` text format (custom serializer)

`serialization/writer.py` (`dumps`/`save`) and `serialization/parser.py` (`loads`/`load`) implement a custom plain-text format — not JSON/YAML. Format:

```
#!QProgram 1.0
require qblox 0.1                   # one per used vendor
metadata:
  label: "..."
  description: "..."
body:
  var freq "Frequency"              # variable declarations, identifier + label
  average 1000:
    for freq in range(...):
      set_frequency "q0/drive" freq
      qblox.acquire "readout_q0" "weights"
```

Key behaviors:

- **Vendor compatibility** is checked on parse via `_check_vendor_compat`: file's `major` must equal installed extension's `major`; file's `minor` must be ≤ installed `minor`. Compatibility is at major.minor; patch is informational. Patch is truncated when writing.
- **Variable identifier allocation** (`_allocate_var_idents`) sanitizes labels and disambiguates collisions with `_2`, `_3`, .... Always emits `var <ident> "<label>"` even when ident == label, for output uniformity.
- **Vendor operations** are serialized via the reverse-lookup dict `_operation_reverse[cls] = (vendor, name)` and reparsed by `inspect.signature`-based generic constructor (`_construct_generic`). New vendor operations get free serialization as long as their `__init__` signature is introspectable and they're registered.
- The parser is **lazy-imported** from `qprogram/__init__.py` and `qprogram/serialization/__init__.py` via module `__getattr__` — the parser imports `QProgram`, which would create a cycle if loaded eagerly. Don't break this without redoing the import graph.

### Bus references (`buses.py`)

`BusRef` subclasses `str` (with `__slots__`!) so it is a string everywhere — AST, serialization, compiler — but also carries `element`, `index`, `bus_type`, `info` metadata. `BusInfo` records channel type (`single`/`IQ`) and `acquires` (has ADC). The validators in `qprogram.py` (`_validate_waveform_channel`, `_validate_acquires`) only fire when the bus is a `BusRef` — raw strings opt out of validation entirely.

`BusSchema` exposes typed factories (`TransmonSchema`, `FluxoniumSchema`, ...) via class-method presets, plus a dynamic `add_element()` path that uses `__getattr__` and gives no static typing. New typed schemas should subclass `BusSchema` and add `@property` factories.

### What lives where (when adding things)

- New core operation: `qprogram/src/qprogram/operations/<name>.py` (subclass `Operation`), export from `operations/__init__.py`, add a method on `QProgram`, add a serializer branch in `_serialize_operation` (writer.py) and parser branch in `_parse_operation` (parser.py), implement `required_capabilities()` returning the op's identity token (`op.<name>`) plus any refinement tokens, register the token in `protocol.py:_BASE_TOKENS`. Add the token to whichever profile is the right home: bus-touching ops to vendor bus profiles; bus-less ops to `qprogram-base-v1` (`qprogram/profiles.py`).
- New waveform: subclass `Waveform`/`IQWaveform`, add to `_register_builtins()` in `serialization/registry.py`, export from `waveforms/__init__.py`, register a class→token mapping in `protocol.py:_register_builtin_waveform_tokens()` (or via `register_waveform_token()` from a vendor package). Serializer auto-emits constructor args by walking `vars(wf)`; parser uses the registry by class name. Waveform tokens always live on the bus profile (waveforms reach the hardware via a bus).
- New vendor operation: subclass `Operation` in the vendor package, add a typed method to its `VendorNamespace` subclass, call `register_vendor_operation(vendor, name, cls)` in the vendor package's `__init__.py`, implement `required_capabilities()` returning `{vendor.<name>.<op>}` plus refinement tokens, register the token via `register_capability_tokens(...)` in the vendor's `profiles.py`, include it in the vendor profile's capability set. No core changes needed.
- New vendor (whole namespace): create a separate package depending on `qprogram`, follow `qprogram-qblox` as the template (mirror its `__init__.py` four-step registration: vendor namespace, vendor version, operations, profile).
- New profile bundle: create a `Profile` in the vendor package's `profiles.py`, register via `register_profile()` from `qprogram-<vendor>/__init__.py`. Use `extends=` to inherit from a parent profile (e.g. `extends="qprogram-base-v1"` for a platform-level slot).
- New domain-constraint predicate: write a callable returning `Iterable[Diagnostic | DomainConstraint]`. Yield `DomainConstraint(node, exclude={"hw"}, reason="...")` for soft restrictions ("hw can't, but sw dispatch works") — the classifier will lift the enclosing block to sw. Yield `Diagnostic` for hard errors (no domain can run it).
