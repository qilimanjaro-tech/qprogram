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

The `Block`/`Operation` split is the core taxonomy: blocks are containers (`Block`, `Loop`, `ForLoop`, `Average`, `Parallel`) and operations are leaves (`Play`, `Measure`, `Wait`, `Sync`, `SetFrequency`, ...). `Parallel` is created exclusively by `LoopContext.__or__` — `with for_loop(...) | for_loop(...) as p:`.

### Symbolic expressions for parametric values

Anywhere a numeric parameter is accepted, an `Expression` is also accepted (`variable.py`). The system is a tiny AST: `Variable` (identity-based equality, holds a value or `UNASSIGNED`), `Constant`, `BinaryOp`, `UnaryOp`. Operators on `Expression` build the tree; `evaluate()` reads `Variable.value` and propagates `UNASSIGNED`. The runtime executor sets variable values per loop iteration — *not* a parameter-passing scheme. Use `evaluate_or_raise()` when a concrete number is required.

`Variable` uses identity-based equality and an auto-incremented `_id`; other expression nodes use structural equality. This matters in the `.qp` writer, which keys its variable-identifier table on `Variable.id` so two variables with the same label still get distinct names.

### Vendor extensions (the decoupling pattern this repo demonstrates)

`qprogram` itself has zero knowledge of any vendor. Vendor extensions hook in via three orthogonal mechanisms:

1. **Runtime namespace** (`vendor.py`, `qprogram.py:__getattr__`). `QProgram._vendor_registry` maps vendor names to `VendorNamespace` subclasses. Calling `QProgram.register_vendor("qblox", QbloxNamespace)` makes `program.qblox.acquire(...)` work at runtime on *any* `QProgram` instance — including the base class — by lazily instantiating the namespace on first attribute access and caching it on the instance.

2. **Typed mixin** (`qprogram_qblox/mixin.py`). `QbloxMixin` exposes `.qblox` as a `@property` returning `QbloxNamespace`. This is purely for IDE autocomplete — at runtime the dynamic `__getattr__` would do the same thing. The pre-combined `qprogram_qblox.QProgram = type(QbloxMixin, BaseQProgram)` saves users the multiple-inheritance step. Multiple vendor mixins compose by listing them in MRO.

3. **Serialization registry** (`serialization/registry.py`). Three module-level dicts: waveforms by class name, vendor operations by `(vendor, name)`, vendor versions by name. Vendor extension's `__init__.py` calls `register_vendor_operation(vendor, op_name, cls)` for each operation it adds, and `register_vendor_version(vendor, x.y.z)` once.

`qprogram_qblox/__init__.py` does all three at import time. Importing the package is the activation step — `import qprogram_qblox` registers the namespace, version, and operations as side effects.

### Capability protocol (`protocol.py`, `validation.py`)

Platforms declare which DSL features they support and validate programs against that declaration. The descriptor (`CompilerCapabilities`) has three orthogonal axes (Vulkan-style split, because flags/numbers/AST-shape-checks have different shapes of check):

1. **Capabilities** — flat set of dotted string tokens. Every `Operation`/`Block` subclass implements an *instance-aware* `required_capabilities() -> set[str]` that returns its identity token plus refinement tokens computed from instance state. A `Play(IQDrag(...))` returns `{op.play, waveform.iq, waveform.iq_drag}`; a `Play(Square(...))` returns `{op.play, waveform.single, waveform.square}`. **Per-node methods are non-recursive** — the validator walks the AST via `body.walk()` and unions per-node sets; recursing inside `required_capabilities()` would double-count. Token namespace: `op.*`, `block.*`, `waveform.*`, `sweep.*`, `expr.*`, `expr.math.*`, `measure.returns.*`, `vendor.<name>.*`.

2. **Limits** — numeric thresholds (`max_loop_nesting`, `max_parallel_loops`, `max_measurements`, `min_wait_duration_ns`). Each `Profile` declares defaults; a live device tightens via `CompilerCapabilities.from_profile(name, limit_overrides={...})`. Unknown keys are silently ignored — profiles can declare future limits the validator doesn't yet enforce.

3. **Predicates** — callables `(node, ValidationContext) -> Iterable[Diagnostic]`. The escape hatch for data-flow checks ("Wait.duration supports linear sweeps but not arbitrary ones"). `ValidationContext` is built once per `validate()` call by a pre-walk and exposes cross-op queries (`sweep_kind_of(var)`, `binding_loop_of(var)`, `max_loop_nesting`, ...). Predicates run on every visited node and may emit zero or more `Diagnostic` objects.

Vendors register **profile bundles** via `register_profile(Profile(name=..., capabilities=..., limits=..., predicates=..., extends=...))` as a side effect of importing the vendor package — same activation pattern as the existing three-step registration. Profiles can extend others by name; capabilities and predicates accumulate, limits inherit then override. Token registry validation runs at `Profile` construction time, so a typo in a vendor package's token list is an error at import, not at validate-time.

`PlatformProtocol` exposes `.capabilities: CompilerCapabilities` and `.validate(qp) -> list[Diagnostic]`. The validator is the single source of truth (same object users introspect, validator consumes). It does not raise — callers (typically `execute()`) decide how to react to non-empty diagnostic lists; the convention is to raise `UnsupportedOperationError`.

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

- New core operation: `qprogram/src/qprogram/operations/<name>.py` (subclass `Operation`), export from `operations/__init__.py`, add a method on `QProgram`, add a serializer branch in `_serialize_operation` (writer.py) and parser branch in `_parse_operation` (parser.py), implement `required_capabilities()` returning the op's identity token (`op.<name>`) plus any refinement tokens, register the token in `protocol.py:_BASE_TOKENS`, add it to vendor profiles that support it.
- New waveform: subclass `Waveform`/`IQWaveform`, add to `_register_builtins()` in `serialization/registry.py`, export from `waveforms/__init__.py`, register a class→token mapping in `protocol.py:_register_builtin_waveform_tokens()` (or via `register_waveform_token()` from a vendor package). Serializer auto-emits constructor args by walking `vars(wf)`; parser uses the registry by class name.
- New vendor operation: subclass `Operation` in the vendor package, add a typed method to its `VendorNamespace` subclass, call `register_vendor_operation(vendor, name, cls)` in the vendor package's `__init__.py`, implement `required_capabilities()` returning `{vendor.<name>.<op>}` plus refinement tokens, register the token via `register_capability_tokens(...)` in the vendor's `profiles.py`, include it in the vendor profile's capability set. No core changes needed.
- New vendor (whole namespace): create a separate package depending on `qprogram`, follow `qprogram-qblox` as the template (mirror its `__init__.py` four-step registration: vendor namespace, vendor version, operations, profile).
- New profile bundle: create a `Profile` in the vendor package's `profiles.py`, register via `register_profile()` from `qprogram-<vendor>/__init__.py`. Use `extends=` to inherit from a parent profile.
