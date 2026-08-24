# Capability protocol — internals

This page is the developer companion to
[Capabilities, diagnostics, and profiles](../guide/capabilities.md). The user guide tells you how
to use the protocol; this one tells you how it is built and what to touch when you extend it.

## Design choices

The protocol has five design choices worth flagging up front:

1. **Per-bus, per-domain capability surface.** `PlatformCapabilities` carries a map of
   `(element, bus_kind) → BusCapabilities`, plus a platform-wide slot for non-bus features and a
   `default_bus_profile` fallback for raw-string buses. `BusCapabilities(rt, host)` is the
   two-domain split — real-time hardware vs host-side (software-timed) orchestration. Either half may be
   `None` when the bus or platform has no engine in that domain.
2. **Three orthogonal axes inside each slot.** Capabilities (flat boolean flags), limits (numeric
   thresholds), and predicates (AST-shape checks) have different *shapes of check*. Vulkan made
   the same split — features, limits, and extensions — for the same reason; collapsing them
   produces awkward overloads.
3. **Distributed declaration.** Each `Operation` / `Block` returns the tokens *it* needs through
   `required_capabilities()`, instance-aware and domain-agnostic. A single validator walks the
   AST and unions per-node sets. Borrowed from MLIR's SPIR-V dialect: each op declares its own
   availability requirements; a single conversion target checks them.
4. **Rt vs host decided by classification, not by separate token spaces.** Required tokens are the
   same in both domains. Domain-specific differences come from predicates emitting
   `DomainConstraint` (soft restriction) or `Diagnostic` (hard error). The classifier derives
   each block's domain from its **op-children consensus** (block-children act as units, with an
   implicit host propagation upward), applies `DomainConstraint`s to their target blocks, and
   emits one `severity="warning"` `"forced-host"` diagnostic on the highest block whose support
   lost `"rt"` and ended at `{host}`.
5. **One descriptor surface, two views.** `PlatformCapabilities` is the object the validator
   consumes *and* the object users introspect. `validate()` returns `(diagnostics, plan)`;
   `PlatformProtocol.validate()` and `PlatformProtocol.plan()` are convenience views over the
   same underlying call.

The prior art, in one line each: MLIR's SPIR-V dialect (per-op declaration plus one centralized
check), MLIR's dynamically-legal ops (operand-sensitive predicates), QIR profiles (named,
hierarchical bundles), and Vulkan (the features / limits / extensions split).

## Module layout

Capability code lives in three files plus `platform.py`, with two more that present its output:

```
src/qprogram/
├── protocol.py         # data types + registries
├── profiles.py         # qprogram-base-v1 (core platform-level profile)
├── validation.py       # the validator + classifier
├── platform.py         # PlatformProtocol + .capabilities + .validate + .plan + .explain
├── paths.py            # structural node paths, stamped onto every node-bearing diagnostic
└── explain.py          # renders a plan as an annotated tree
```

`protocol.py` defines:

- `Domain` — `Literal["rt", "host"]`.
- `BusSelector` — `(element_kind, bus_kind)` tuple.
- `BusCapabilities(rt, host)` — two stacked `CompilerCapabilities`, either may be `None`.
- `PlatformCapabilities(bus, platform, default_bus_profile)` with `.for_bus(bus)` helper.
- `Diagnostic` — frozen dataclass returned by the validator. `severity` is
  `Literal["error", "warning", "info"]`; `path` carries the offending node's structural address.
- `DomainConstraint(node, exclude, reason)` — soft predicate output that narrows domain support.
- `Profile` — a named bundle of capabilities, limits, predicates, vendor versions. Domain-agnostic.
- `CompilerCapabilities` — what fills a single (bus, domain) slot; built from a profile.
- `ExecutionPlan` — `Mapping[Operation | Block, frozenset[Domain]]`.
- `ValidationContext` — read-only data-flow facts the validator passes to predicates.
- `Predicate` — `Protocol` for `(node, ctx) -> Iterable[Diagnostic | DomainConstraint]`.
- `CAPABILITY_REGISTRY` — set of every dotted token any in-tree op may emit.
- `PROFILE_REGISTRY` — name → `Profile` mapping populated by `register_profile`.
- `WAVEFORM_TOKEN` — class → token mapping for waveform refinement.
- Helpers: `register_capability_tokens`, `validate_tokens`, `register_profile`,
  `resolve_profile`, `register_waveform_token`, `waveform_token`, `expression_tokens`,
  `measurement_field_token`, `known_measurement_fields`.

`profiles.py` defines:

- `QPROGRAM_BASE_V1` — the core platform-level profile (block / sweep / expression tokens).
  Registered at import time.

`validation.py` exports a single function:

```python
def validate(qprogram: QProgram, caps: PlatformCapabilities) -> tuple[list[Diagnostic], ExecutionPlan]: ...
```

`platform.py` exposes `.capabilities` (abstract property returning `PlatformCapabilities`),
`.validate(qp) -> list[Diagnostic]`, and `.plan(qp) -> ExecutionPlan` — both defaulting to
`qprogram.validation.validate` — plus `.explain(qp) -> str`, which defaults to `qprogram.explain`.

## Distributed declaration in practice

Every concrete `Operation` and `Block` subclass overrides `required_capabilities()` to return the
tokens *it* needs. The method is **non-recursive**: each node returns its own tokens only. The
validator walks the AST and unions per-node sets — recursing inside `required_capabilities()`
would double-count when the walker re-visited the child.

A typical core op looks like this:

```python
# src/qprogram/operations/play.py
class Play(Operation):
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus, waveform):
        self.bus = bus
        self.waveform = waveform

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import waveform_token

        caps = {"op.play"}
        if isinstance(self.waveform, str):
            caps.add("waveform.alias")
        else:
            caps.add("waveform.iq" if isinstance(self.waveform, IQWaveform) else "waveform.single")
            tok = waveform_token(self.waveform)
            if tok is not None:
                caps.add(tok)
        return caps
```

The method is **domain-agnostic**. The same token set is checked against both rt and host slots.
Domain-specific behavior is expressed by predicates emitting `DomainConstraint`, not by varying
the declaration.

Block subclasses follow the same shape. `Sweep.required_capabilities()` returns `block.sweep`
unioned with its source's own `tokens()` — the source's per-class token plus its `sweep.<kind>`.
`Sweep(v, Range(0, 1, 0.1))` needs `{"block.sweep", "sweep.range", "sweep.linear"}`;
`Sweep(v, Values([...]))` needs `{"block.sweep", "sweep.values", "sweep.arbitrary"}`. A combinator
unions its children's tokens, so a platform lacking `sweep.logspace` also refuses
`Rotate(Logspace(...))`.

### Expression tokens propagate from parametric arguments

When an op takes an expression-typed parameter, it unions in `expression_tokens(value)`:

```python
class Wait(Operation):
    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens

        return {"op.wait"} | expression_tokens(self.duration)
```

`expression_tokens` recursively walks the expression AST and returns one token per node kind plus
per-math-function name. Plain numeric literals contribute nothing — the token represents the
*shape* of the parameter, not its concrete value.

`expr.*` tokens always check against `caps.platform`, regardless of where the op itself routes —
they describe Python AST node kinds the compiler accepts, not anything bus-specific. The
validator handles this split internally; op authors just call `expression_tokens` and union it
in.

## The two-pass validator

`qprogram.validation.validate(qprogram, caps)` expands any fragment `Call` first, builds a
`ValidationContext` in a pre-walk, then runs two cooperating walks merged into one recursive
post-order pass:

1. **Per-node check.** For each AST node:
   - Resolve the routed slot via `caps.for_bus(node.bus)` for bus-touching ops or `caps.platform`
     for blocks / bus-less ops. Multi-bus ops contribute a list of slots that are intersected;
     broadcast ops with no explicit targets (`Sync(targets=None)`) intersect over **every bus in
     the program** (`ctx.program_buses`).
   - Split `required_capabilities()` into expression tokens (always checked against
     `caps.platform`) and the rest (checked against the primary slot).
   - For each domain `d ∈ {"rt", "host"}`: the domain is *available* iff the slot has a non-None
     CompilerCapabilities for d AND the token subsets are satisfied AND no predicate emitted a
     `Diagnostic` in d.
   - Collect `DomainConstraint`s from predicates — they must target a **Block** (the binding
     loop), are deduplicated, and are routed to their target's bucket. An op's own `support`
     equals its `available`: constraints restrict blocks, never ops.
   - If `available` is empty, surface the failure reasons — **one** `missing-capability` per
     token (naming both slots when missing in both domains) plus deduplicated predicate
     Diagnostics. If at least one domain works, the per-domain failures are suppressed.
2. **Block-level classification (post-order).** Each block's natural domain is the intersection
   of its **immediate op-children's** supports (rule (c)); block-children don't enter the
   consensus, but a host-only block-child implicitly excludes `"rt"` from the parent (rule (e1)'s
   constructive side). An `Average` is the one exception: only its *averaging-relevant*
   op-children — those with `Operation.AFFECTS_AVERAGING`, which `MeasurementOperation` sets — enter
   its consensus, since averaging is what accumulates measurement results. Disjoint healthy
   singletons → `"mixed-domain"` error (rule (d)); an
   op-child whose own support is already empty silently empties the block (its own diagnostics
   explain why). A `DomainConstraint` that strips `"rt"` from an all-rt block drops the *block*
   to host dispatch while its ops stay rt (rule (e2)); a constraint aimed at an op instead of a
   block is an authoring error, reported as `"bad-domain-constraint"`. A host block-child inside a
   parent whose slot has no host half at all → `"host-in-rt"` error. Empty support without a child
   explanation → `"empty-domain"`.

The returned `ExecutionPlan` is **identity-keyed**: every node *instance* has its own entry,
even when two nodes are structurally identical — `plan[node]` resolves by object identity, so a
compiler can trust one entry per AST position.

Then three more passes:

3. **Whole-program limits.** `max_loop_nesting`, `max_parallel_loops`, `max_measurements` check
   against `caps.platform`'s limits; `min_wait_duration_ns` checks per-Wait against the bus
   slot's limits.
4. **Universal Conditional checks.** `unknown-measurement` and `missing-classification` —
   profile-independent.
5. **Advisory emission and path stamping.** One `severity="warning"` `"forced-host"` diagnostic
   per highest block whose support fell from `{rt, host}` to `{host}`, attributed to its immediate
   cause; one `severity="info"` `"reorderable-averaging"` hint per `Average` that is host-side only
   because it encloses a host-side sweep, pointing at `qprogram.optimize`; then every node-bearing
   diagnostic is stamped with its structural `path`.

The validator never raises. The list comes back, the caller decides what to do.

### `DomainConstraint` vs `Diagnostic`

Predicates choose between two output types based on whether the case represents a hard error or
just a domain restriction:

```python
def reject_arbitrary_wait(node, ctx):
    """Hard error: the wait instruction can't handle arbitrary sweeps in any domain."""
    if isinstance(node, Wait) and isinstance(node.duration, Variable) and ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(severity="error", code="myvendor.arbitrary-wait-sweep", ..., node=node)


def drag_sigma_is_host_only(node, ctx):
    """Soft restriction: rt can't, host still works via per-iteration dispatch.

    The constraint targets the BINDING LOOP (a Block), not the Play op — the loop is what
    falls back to host-side; the Play stays a real-time shot per iteration. Targeting an op
    is an authoring error reported as `bad-domain-constraint`.
    """
    if isinstance(node, Play) and isinstance(node.waveform, IQDrag) and isinstance(node.waveform.sigma, Variable):
        binding_loop = ctx.binding_loop_of(node.waveform.sigma)
        if binding_loop is not None:
            yield DomainConstraint(node=binding_loop, exclude=frozenset({"rt"}),
                                   reason="IQDrag.sigma sweep is not real-time")
```

The validator collects both. Diagnostics queue per (node, domain) and are deduplicated — a
token missing in both domains is reported once, and a predicate registered on both halves of a
slot contributes its outputs once; they surface only when `support` is empty (otherwise the
fallback worked and they're noise). DomainConstraints subtract from their target block's support
silently and the classifier reports the consequence via `"forced-host"`.

### Per-node methods must not recurse

Both `Operation.required_capabilities()` and `Block.required_capabilities()` return *only* the
node's own tokens. The validator walks. Recursing would double-count when the walker re-visited
the child:

```python
# WRONG — do not do this
class MyBlock(Block):
    def required_capabilities(self):
        caps = {"block.mine"}
        for child in self.elements:
            caps |= child.required_capabilities()  # walker also visits each child
        return caps
```

The base classes' default implementations follow this rule and subclasses should too.

## `ValidationContext` queries

A `Predicate` is any callable with the signature
`(node, ctx) -> Iterable[Diagnostic | DomainConstraint]`. The context is built once per
`validate()` call and is read-only. The current surface:

| Method                                  | Returns                                                                 |
|-----------------------------------------|-------------------------------------------------------------------------|
| `sweep_kind_of(var)`                    | `"linear"`, `"arbitrary"`, `"averaged"`, or `None`                       |
| `binding_loop_of(var)`                  | The loop block that binds the variable, or `None`                        |
| `max_loop_nesting` (property)           | Deepest repetition depth (blocks opt in via `Block.REPEATS`)               |
| `max_parallel_arity` (property)         | Largest `len(parallel.loops)`                                            |
| `measurement_count` (property)          | Total `MeasurementOperation` count                                       |
| `measurement_fields(name)`              | The `fields` tuple of the named measurement, or `None`                    |
| `known_measurement_names()`             | Set of every measurement name in the program                              |
| `program_buses` (property)              | Every bus referenced anywhere in the program (routes broadcast ops)       |

To add a new context query (say, `parallel_siblings_of(var)`), add a method to `ValidationContext`,
populate the underlying data in `validation._build_context`, and document it here. The interface
is deliberately small.

## Token registry

`CAPABILITY_REGISTRY` is the canonical set of every dotted token any in-tree op may emit. It
exists for two reasons:

1. **Typo defense.** `Profile.__post_init__` calls `validate_tokens` on the profile's capability
   set; an unknown token raises `ValueError` at profile-construction time, not at validate-time.
2. **Discoverability.** A vendor author wanting to know what tokens exist can introspect
   `CAPABILITY_REGISTRY` rather than grep the source.

The registry is mutable. Vendor packages add their tokens at import time via
`register_capability_tokens(...)`. The function rejects empty strings, leading/trailing dots, and
doubled dots — a basic shape check, not a strict schema. Each vendor knows its own namespace.

```python
register_capability_tokens("vendor.myvendor.acquire", "vendor.myvendor.set_markers", ...)
```

`register_capability_tokens` is idempotent: registering an existing token is a no-op.

## Waveform-class dispatch

`WAVEFORM_TOKEN` maps a waveform class to its canonical refinement token:

```python
WAVEFORM_TOKEN: dict[type, str] = {
    Square: "waveform.square",
    IQDrag: "waveform.iq_drag",
    ...
}
```

Population is lazy via `_register_builtin_waveform_tokens` to avoid an import cycle. Vendor
packages register their own waveform classes the same way:

```python
register_waveform_token(MyCustomPulse, "waveform.my_custom_pulse")
```

`register_waveform_token` automatically calls `register_capability_tokens(token)` so a vendor
never has to call both.

`waveform_token(value)` returns `None` for `str` aliases (the channel-kind token `waveform.alias`
is added separately by the op's `required_capabilities`) and for unknown classes. Unknown classes
still emit channel-kind tokens (`waveform.single` or `waveform.iq`); they just don't contribute
a refinement token. This keeps validation forgiving for prototype waveforms that haven't
registered yet.

## Profile bundles

`Profile` is a frozen dataclass:

```python
@dataclass(frozen=True)
class Profile:
    name: str
    version: tuple[int, int, int]
    extends: str | None
    capabilities: frozenset[str]
    limits: Mapping[str, float]
    predicates: tuple[Predicate, ...]
    vendor_versions: Mapping[str, tuple[int, int, int]]
```

Profiles are **domain-agnostic** — the platform decides which profile fills each (bus, domain)
slot. `__post_init__` calls `validate_tokens(self.capabilities)` so an unknown token in the
bundle is an error at construction time, not at validate time.

Core qprogram ships `qprogram-base-v1` in `src/qprogram/profiles.py`. Vendors typically use it for
the platform-level slot (via `extends="qprogram-base-v1"` on their own profile, or by passing the
name directly to `CompilerCapabilities.from_profile`).

### Registering a profile

`register_profile(profile)` adds the profile to `PROFILE_REGISTRY`. The function is idempotent
for *the same `Profile` object* (so re-importing a module is safe), and raises `ValueError` if
you try to register a different profile under an already-used name.

Vendor packages register at import time, alongside the existing vendor-namespace /
vendor-version / operations registration:

```python
# qprogram-myvendor/src/qprogram_myvendor/__init__.py
from qprogram_myvendor.profiles import _register as _register_myvendor_profile

_register_myvendor_profile()
```

### Building `CompilerCapabilities` from a profile

`CompilerCapabilities.from_profile(profile_name, *, limit_overrides=None, extra_predicates=())`
materializes a descriptor from a registered profile:

1. Resolve the profile via `resolve_profile(name)` (raises `KeyError` if missing).
2. Walk the `extends` chain root-first via `_profile_chain`, detecting cycles.
3. Union capabilities, accumulate predicates (parent → child), and merge limits and vendor
   versions (later overrides earlier, so the leaf wins).
4. Apply `limit_overrides` on top (device-level tightening).
5. Append `extra_predicates` to the merged tuple.

## Routing rules in detail

`PlatformCapabilities.for_bus(bus)`:

- `BusRef` with a non-None `schema` → look up `bus[(element, kind)]`; fall back to
  `default_bus_profile` if not present.
- Plain `str` or schema-less `BusRef` → `default_bus_profile`.

`validation._route(node, caps, ctx)`:

- Blocks → `[caps.platform]`.
- Ops with empty `BUS_ATTRS` (bus-less) → `[caps.platform]`.
- Bus-touching ops with extracted bus values → `[caps.for_bus(b) for b in bus_values]`. The
  caller intersects across the list when there's more than one (Sync).
- Broadcast ops whose bus list resolves to empty (`Sync(targets=None)`, meaning "every active
  bus") → one slot per bus in `ctx.program_buses`, so the intersection matches the
  explicit-targets form. With no buses in the program at all — or for a non-broadcast op with an
  empty bus list — `[caps.default_bus_profile]`.

Within the routed slot(s), the node's `required_capabilities()` splits on prefix:
`expr.*` tokens always check against `caps.platform`; everything else checks against the routed
slot(s).

## How `PlatformProtocol` consumes the descriptor

`PlatformProtocol` carries the platform's schema, parameter, and `execute` surface too; the four
capability-facing members are:

```python
class PlatformProtocol(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities: ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        from qprogram.validation import validate as _validate

        diagnostics, _ = _validate(qprogram, self.capabilities)
        return diagnostics

    def plan(self, qprogram: QProgram) -> ExecutionPlan:
        from qprogram.validation import validate as _validate

        _, plan = _validate(qprogram, self.capabilities)
        return plan

    def explain(self, qprogram: QProgram) -> str:
        from qprogram.explain import explain as _explain

        return _explain(qprogram, self.capabilities)
```

A concrete platform typically caches its `PlatformCapabilities` on construction and returns it
from `capabilities`. The default `validate` and `plan` delegate to `qprogram.validation.validate`;
callers who want both — e.g. `execute()` implementations that gate on diagnostics and then
compile against the plan — should call `qprogram.validation.validate` directly to avoid the
duplicated walk.

The convention for `execute()` is to validate first, raise `UnsupportedOperationError` on any
`severity="error"` diagnostic, surface `severity="warning"` without raising, and pass
`severity="info"` through as advisory. `ReferencePlatform` in `qprogram.executor` does exactly
that and is the worked example. The base class does not enforce it — concrete platforms vary in
where they put the gate.

## Adding things

### A new capability token

Add it to `_BASE_TOKENS` in `protocol.py`. Choose the prefix that matches its category. Tokens
are flat strings; the dotted convention is for readability.

If the token is vendor-specific, *do not* edit `_BASE_TOKENS`. Register it from the vendor
package via `register_capability_tokens(...)` instead. A test in
`tests/test_protocol.py::test_register_capability_tokens_rejects_malformed_tokens` guards the
shape rules.

### A new waveform token

Either edit `_register_builtin_waveform_tokens` in `protocol.py` (for core waveforms), or call
`register_waveform_token(cls, token)` from a vendor package's `__init__.py` (for vendor
waveforms). The function automatically adds the token to `CAPABILITY_REGISTRY` so a profile that
lists it validates.

### A new core operation

Implement `required_capabilities(self) -> set[str]` returning your op's identity token plus any
refinement tokens computed from instance state. Add the identity token to `_BASE_TOKENS`. Decide
where it lives: bus-touching ops go on bus profiles, bus-less ops on the platform profile. See
[Adding operations](adding-operations.md) for the full operation walkthrough.

### A new vendor operation

The vendor-operation walkthrough at [Building a vendor extension](vendor-extensions.md) covers
the protocol side: implement `required_capabilities()` returning `{vendor.<name>.<op>}` plus
refinement, register the token via `register_capability_tokens(...)`, and include it in your
profile's capability set.

### A new profile

Create a `Profile` in the vendor package's `profiles.py`. Pick a name following the
`<vendor>-<tier>-v<major>` convention. Optionally `extends` an existing profile. Register via
`register_profile(profile)` from the vendor package's `__init__.py`.

### A new ValidationContext query

Add a method to `ValidationContext`, populate the underlying data in
`validation._build_context`, and document it on this page and in the guide. Keep the surface
small — predicate authors look here first.

### A new domain-constraint predicate

Write it as a callable returning `Iterable[Diagnostic | DomainConstraint]`. Yield a
`DomainConstraint(node, exclude, reason)` for soft restrictions ("this combination doesn't run
in rt, but host-side dispatch is fine"). Yield a `Diagnostic` for hard errors ("this combination
doesn't run in any domain — even host-side dispatch can't fix it"). Register the predicate on the
profile of the slot where it should fire (typically the bus profile for bus-touching ops).

## Testing

The protocol has dedicated test modules:

| File                                                          | Scope                                                                |
|---------------------------------------------------------------|---------------------------------------------------------------------|
| `tests/test_protocol.py`                                       | Dataclass behavior, registry, waveform dispatch, profile extends, BusCapabilities, PlatformCapabilities routing. |
| `tests/test_required_capabilities.py`                          | Per-op / per-block assertions, instance-aware variants.             |
| `tests/test_validation.py`                                     | Validator end-to-end: routing, missing caps, limits, predicates, rt/host classification, forced-host warnings. |
| `tests/test_explain.py`, `tests/test_paths.py`                 | Plan rendering and the structural paths diagnostics are stamped with. |
| A vendor package's `tests/test_profile.py`                      | Vendor profile integration: registration, happy path, predicate, DomainConstraint flow. |

When you add a new operation, mirror an existing `test_required_capabilities.py` block: cover at
least one positive case per refinement axis (e.g. for an op with a waveform attribute, test both
single-channel and IQ paths).

When you add a new profile, mirror a vendor `test_profile.py`: register the profile, build the
descriptor, validate a representative happy-path program, and exercise the predicate(s) you ship.

## Out of scope

Three things the protocol deliberately leaves out. If you find yourself wanting one, the design
has explicit hooks:

- **Profile `removes=`.** `extends` only adds; a vendor with exotic constraints builds a profile
  from scratch instead of subtracting from a parent. Adding `removes=` is a localized change to
  `_profile_chain` in `protocol.py`.
- **`.qp` `require profile <name>` syntax extension.** Profiles are platform-side; the file format
  does not carry profile names.
- **Multi-domain support beyond rt/host.** `Domain = Literal["rt", "host"]` names the language's
  two execution domains, real-time hardware and host-side orchestration. Adding a third (e.g.
  `"cloud"` dispatch) is widening the literal plus adjusting the classifier intersection
  algorithm.

## See also

- [Capabilities, diagnostics, and profiles](../guide/capabilities.md) — the user guide.
- [Architecture](architecture.md) — where the capability protocol fits in the rest of the codebase.
- [Adding operations](adding-operations.md) — the operation walkthrough, with the `required_capabilities` step in context.
- [Building a vendor extension](vendor-extensions.md) — the vendor walkthrough, with the profile-bundle step.
- [API reference](../reference/api-qprogram.md#capability-protocol) — the auto-generated reference.
