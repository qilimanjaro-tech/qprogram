# Capability protocol — internals

This page is the developer companion to
[Capabilities, diagnostics, and profiles](../guide/capabilities.md). The user guide tells you how
to use the protocol; this one tells you how it is built and what to touch when you extend it.

## Design choices

The protocol has five design choices worth flagging up front:

1. **Per-bus, per-domain capability surface.** `PlatformCapabilities` carries a map of
   `(element, bus_kind) → BusCapabilities`, plus a platform-wide slot for non-bus features and a
   `default_bus_profile` fallback for raw-string buses. `BusCapabilities(hw, sw)` is the
   two-domain split — real-time hardware vs software-dispatched orchestration. Either half may be
   `None` when the bus or platform has no engine in that domain.
2. **Three orthogonal axes inside each slot.** Capabilities (flat boolean flags), limits (numeric
   thresholds), and predicates (AST-shape checks) have different *shapes of check*. Vulkan made
   the same split — features, limits, and extensions — for the same reason; collapsing them
   produces awkward overloads.
3. **Distributed declaration.** Each `Operation` / `Block` returns the tokens *it* needs through
   `required_capabilities()`, instance-aware and domain-agnostic. A single validator walks the
   AST and unions per-node sets. Borrowed from MLIR's SPIR-V dialect: each op declares its own
   availability requirements; a single conversion target checks them.
4. **Hw vs sw decided by classification, not by separate token spaces.** Required tokens are the
   same in both domains. Domain-specific differences come from predicates emitting
   `DomainConstraint` (soft restriction) or `Diagnostic` (hard error). The classifier intersects
   domain support bottom-up across blocks and emits one `severity="info"` `"forced-software"`
   diagnostic on the highest block whose support went from `{hw, sw}` to `{sw}`.
5. **One descriptor surface, two views.** `PlatformCapabilities` is the object the validator
   consumes *and* the object users introspect. `validate()` returns `(diagnostics, plan)`;
   `PlatformProtocol.validate()` and `PlatformProtocol.plan()` are convenience views over the
   same underlying call.

If you want the full prior-art write-up, see `.specs/qprogram-dsl.md` §9.

## Module layout

Capability code lives in three files plus the existing `platform.py`:

```
qprogram/src/qprogram/
├── protocol.py         # data types + registries
├── profiles.py         # qprogram-base-v1 (core platform-level profile)
├── validation.py       # the validator + classifier
└── platform.py         # PlatformProtocol + .capabilities + .validate + .plan
```

`protocol.py` defines:

- `Domain` — `Literal["hw", "sw"]`.
- `BusSelector` — `(element_kind, bus_kind)` tuple.
- `BusCapabilities(hw, sw)` — two stacked `CompilerCapabilities`, either may be `None`.
- `PlatformCapabilities(bus, platform, default_bus_profile)` with `.for_bus(bus)` helper.
- `Diagnostic` — frozen dataclass returned by the validator. `severity` is `Literal["error", "info"]`.
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
  `resolve_profile`, `register_waveform_token`, `waveform_token`, `expression_tokens`.

`profiles.py` defines:

- `QPROGRAM_BASE_V1` — the core platform-level profile (block / sweep / expression / bus-less-op
  tokens). Registered at import time.

`validation.py` exports a single function:

```python
def validate(qprogram: QProgram, caps: PlatformCapabilities) -> tuple[list[Diagnostic], ExecutionPlan]: ...
```

`platform.py` exposes `.capabilities` (abstract property returning `PlatformCapabilities`),
`.validate(qp) -> list[Diagnostic]`, and `.plan(qp) -> ExecutionPlan` — both default to delegating
into `qprogram.validation.validate`.

## Distributed declaration in practice

Every concrete `Operation` and `Block` subclass overrides `required_capabilities()` to return the
tokens *it* needs. The method is **non-recursive**: each node returns its own tokens only. The
validator walks the AST and unions per-node sets — recursing inside `required_capabilities()`
would double-count when the walker re-visited the child.

A typical core op looks like this:

```python
# qprogram/src/qprogram/operations/play.py
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

The method is **domain-agnostic**. The same token set is checked against both hw and sw slots.
Domain-specific behavior is expressed by predicates emitting `DomainConstraint`, not by varying
the declaration.

Block subclasses follow the same shape. `ForLoop.required_capabilities()` returns
`{"block.for_loop", "sweep.linear"}`; `Loop` returns `{"block.loop", "sweep.arbitrary"}`.

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

`qprogram.validation.validate(qprogram, caps)` runs in two cooperating walks merged into one
recursive post-order pass:

1. **Per-node check.** For each AST node:
   - Resolve the routed slot via `caps.for_bus(node.bus)` for bus-touching ops or `caps.platform`
     for blocks / bus-less ops. Multi-bus ops contribute a list of slots that are intersected.
   - Split `required_capabilities()` into expression tokens (always checked against
     `caps.platform`) and the rest (checked against the primary slot).
   - For each domain `d ∈ {"hw", "sw"}`: the domain is *available* iff the slot has a non-None
     CompilerCapabilities for d AND the token subsets are satisfied AND no predicate emitted a
     `Diagnostic` in d.
   - Collect `DomainConstraint`s from predicates; subtract `union(constraint.exclude)` from
     `available` to get `support`.
   - If `support` is empty, surface all per-domain diagnostics (the user sees the contributing
     reasons). If at least one domain works, the per-domain Diagnostics are suppressed.
2. **Block-level classification.** After recursing into children, each block's `final_support` is
   the intersection of children's `final_support` ∩ the block's own `support`. Empty →
   `"empty-domain"` error diagnostic. `{hw, sw} → {sw}` reduction → one `severity="info"`
   `"forced-software"` diagnostic on the highest block in the forced chain.

Then two more passes:

3. **Whole-program limits.** `max_loop_nesting`, `max_parallel_loops`, `max_measurements` check
   against `caps.platform`'s limits; `min_wait_duration_ns` checks per-Wait against the bus
   slot's limits.
4. **Universal Conditional checks.** `unknown-measurement` and `missing-classification` —
   profile-independent.

The validator never raises. The list comes back, the caller decides what to do.

### `DomainConstraint` vs `Diagnostic`

Predicates choose between two output types based on whether the case represents a hard error or
just a domain restriction:

```python
def reject_arbitrary_wait(node, ctx):
    """Hard error: the wait instruction can't handle arbitrary sweeps in any domain."""
    if isinstance(node, Wait) and isinstance(node.duration, Variable) and ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(severity="error", code="myvendor.arbitrary-wait-sweep", ..., node=node)


def drag_sigma_is_software_only(node, ctx):
    """Soft restriction: hw can't, sw still works via per-iteration dispatch."""
    if isinstance(node, Play) and isinstance(node.waveform, IQDrag) and isinstance(node.waveform.sigma, Variable):
        yield DomainConstraint(node=node, exclude=frozenset({"hw"}),
                               reason="IQDrag.sigma sweep is not real-time")
```

The validator collects both. Diagnostics queue per (node, domain); they surface only when
`support` is empty (otherwise the fallback worked and they're noise). DomainConstraints subtract
from support silently and the classifier reports the consequence via `"forced-software"`.

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
            caps |= child.required_capabilities()   # walker also visits each child
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
| `max_loop_nesting` (property)           | Deepest nested-loop count                                                |
| `max_parallel_arity` (property)         | Largest `len(parallel.loops)`                                            |
| `measurement_count` (property)          | Total `MeasurementOperation` count                                       |
| `measurement_returns(name)`             | The `returns` tuple of the named measurement, or `None`                   |
| `known_measurement_names()`             | Set of every measurement name in the program                              |

To add a new context query (say, `parallel_siblings_of(var)`), add a method to `ValidationContext`,
populate the underlying data in `validation._build_context`, and document it here. The interface
is deliberately small.

## Token registry

`CAPABILITY_REGISTRY` is the canonical set of every dotted token any in-tree op may emit. It
exists for two reasons:

1. **Typo defence.** `Profile.__post_init__` calls `validate_tokens` on the profile's capability
   set; an unknown token raises `ValueError` at profile-construction time, not at validate-time.
2. **Discoverability.** A vendor author wanting to know what tokens exist can introspect
   `CAPABILITY_REGISTRY` rather than grep the source.

The registry is mutable. Vendor packages add their tokens at import time via
`register_capability_tokens(...)`. The function rejects empty strings, leading/trailing dots, and
doubled dots — a basic shape check, not a strict schema. Each vendor knows its own namespace.

```python
register_capability_tokens(
    "vendor.myvendor.acquire",
    "vendor.myvendor.set_markers",
    ...
)
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

Core qprogram ships `qprogram-base-v1` in `qprogram/profiles.py`. Vendors typically use it for
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

`CompilerCapabilities.from_profile(name, *, limit_overrides=None, extra_predicates=())`
materialises a descriptor from a registered profile:

1. Resolve the profile via `resolve_profile(name)` (raises `KeyError` if missing).
2. Walk the `extends` chain root-first via `_profile_chain`, detecting cycles.
3. Union capabilities, accumulate predicates (parent → child), and merge limits (later overrides
   earlier, so the leaf wins).
4. Apply `limit_overrides` on top (device-level tightening).
5. Append `extra_predicates` to the merged tuple.

## Routing rules in detail

`PlatformCapabilities.for_bus(bus)`:

- `BusRef` with a non-None `schema` → look up `bus[(element, kind)]`; fall back to
  `default_bus_profile` if not present.
- Plain `str` or schema-less `BusRef` → `default_bus_profile`.

`validation._route(node, caps)`:

- Blocks → `[caps.platform]`.
- Ops with empty `BUS_ATTRS` (bus-less) → `[caps.platform]`.
- Bus-touching ops with extracted bus values → `[caps.for_bus(b) for b in bus_values]`. The
  caller intersects across the list when there's more than one (Sync).
- Bus-touching ops whose bus list resolves to empty (e.g. `Sync(targets=None)` meaning "every
  active bus") → `[caps.default_bus_profile]`.

Within the routed slot(s), the node's `required_capabilities()` splits on prefix:
`expr.*` tokens always check against `caps.platform`; everything else checks against the routed
slot(s).

## How `PlatformProtocol` consumes the descriptor

`PlatformProtocol` exposes three members:

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
```

A concrete platform typically caches its `PlatformCapabilities` on construction and returns it
from `capabilities`. The default `validate` and `plan` delegate to `qprogram.validation.validate`;
callers who want both — e.g. `execute()` implementations that gate on diagnostics and then
compile against the plan — should call `qprogram.validation.validate` directly to avoid the
duplicated walk.

The contract document calls for `execute()` to call `validate()` first and raise on any
`severity="error"` diagnostic, passing `severity="info"` through as advisory. The base class does
not enforce this — concrete platforms vary in where they put the gate.

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
in hw, but software dispatch is fine"). Yield a `Diagnostic` for hard errors ("this combination
doesn't run in any domain — even sw dispatch can't fix it"). Register the predicate on the
profile of the slot where it should fire (typically the bus profile for bus-touching ops).

## Testing

The protocol has dedicated test modules:

| File                                                          | Scope                                                                |
|---------------------------------------------------------------|---------------------------------------------------------------------|
| `qprogram/tests/test_protocol.py`                              | Dataclass behaviour, registry, waveform dispatch, profile extends, BusCapabilities, PlatformCapabilities routing. |
| `qprogram/tests/test_required_capabilities.py`                 | Per-op / per-block assertions, instance-aware variants.             |
| `qprogram/tests/test_validation.py`                            | Validator end-to-end: routing, missing caps, limits, predicates, hw/sw classification, forced-software info. |
| `qprogram-<vendor>/tests/test_profile.py`                      | Vendor profile integration: registration, happy path, predicate, DomainConstraint flow. |

When you add a new operation, mirror an existing `test_required_capabilities.py` block: cover at
least one positive case per refinement axis (e.g. for an op with a waveform attribute, test both
single-channel and IQ paths).

When you add a new profile, mirror a vendor `test_profile.py`: register the profile, build the
descriptor, validate a representative happy-path program, and exercise the predicate(s) you ship.

## Out of scope (today)

The current implementation deliberately leaves three things on the table. If you find yourself
wanting one, the design has explicit hooks:

- **Profile `removes=`.** Today `extends` only adds; a vendor with exotic constraints builds a
  profile from scratch instead of subtracting from a parent. Adding `removes=` is a localized
  change to `_profile_chain` in `protocol.py`.
- **`.qp` `require profile <name>` syntax extension.** Profiles stay platform-side for now; the
  file format does not carry profile names.
- **Multi-domain support beyond hw/sw.** `Domain = Literal["hw", "sw"]` covers the two domains
  the demo cares about. Adding more (e.g. `"cloud"` dispatch) is widening the literal plus
  adjusting the classifier intersection algorithm.

## See also

- [Capabilities, diagnostics, and profiles](../guide/capabilities.md) — the user guide.
- [Architecture](architecture.md) — where the capability protocol fits in the rest of the codebase.
- [Adding operations](adding-operations.md) — the operation walkthrough, with the `required_capabilities` step in context.
- [Building a vendor extension](vendor-extensions.md) — the vendor walkthrough, with the profile-bundle step.
- [API reference](../reference/api-qprogram.md#capability-protocol) — the auto-generated reference.
