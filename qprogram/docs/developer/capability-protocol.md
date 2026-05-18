# Capability protocol — internals

This page is the developer companion to
[Capabilities, diagnostics, and profiles](../guide/capabilities.md). The
user guide tells you how to use the protocol; this one tells you how it is
built and what to touch when you extend it.

## Design choices

The protocol has four design choices worth flagging up front, because they
make later sections easier to read:

1. **Three orthogonal axes.** Capabilities (flat boolean flags), limits
   (numeric thresholds), and predicates (AST-shape checks) have different
   *shapes of check*. Vulkan made the same split — features, limits, and
   extensions — for the same reason; collapsing them produces awkward
   overloads.
2. **Distributed declaration.** Each `Operation` / `Block` returns the
   tokens *it* needs through `required_capabilities()`, instance-aware. A
   single validator walks the AST and unions per-node sets. Borrowed from
   MLIR's SPIR-V dialect: each op declares its own availability
   requirements; a single conversion target checks them.
3. **One descriptor surface.** `CompilerCapabilities` is the object the
   validator consumes *and* the object users introspect. There is no
   parallel "what we advertise" vs "what we enforce" pair to drift apart.
4. **Profiles compose only by extension.** A profile may inherit from
   exactly one other profile, child overrides parent. No arbitrary
   intersection. QIR's profile design taught the field that
   non-hierarchical bundles fragment immediately.

If you want the full prior-art write-up, see `.specs/qprogram-dsl.md` §9.

## Module layout

Capability code lives in two files plus the existing `platform.py`:

```
qprogram/src/qprogram/
├── protocol.py         # data types + registries
├── validation.py       # the validator
└── platform.py         # PlatformProtocol + .capabilities + .validate
```

`protocol.py` defines:

- `Diagnostic` — frozen dataclass returned by the validator.
- `Profile` — a named bundle of capabilities, limits, predicates, vendor versions.
- `CompilerCapabilities` — what the platform exposes; built from a profile.
- `ValidationContext` — read-only data-flow facts the validator passes to predicates.
- `Predicate` — `Protocol` for `(node, ctx) -> Iterable[Diagnostic]`.
- `CAPABILITY_REGISTRY` — set of every dotted token any in-tree op may emit.
- `PROFILE_REGISTRY` — name → `Profile` mapping populated by `register_profile`.
- `WAVEFORM_TOKEN` — class → token mapping for waveform refinement.
- Helpers: `register_capability_tokens`, `validate_tokens`,
  `register_profile`, `resolve_profile`, `register_waveform_token`,
  `waveform_token`, `expression_tokens`.

`validation.py` exports a single function:

```python
def validate(qprogram: QProgram, caps: CompilerCapabilities) -> list[Diagnostic]: ...
```

`platform.py` grows `.capabilities` (abstract property) and `.validate(qp)`
(concrete default that delegates to `qprogram.validation.validate`).

## Distributed declaration in practice

Every concrete `Operation` and `Block` subclass overrides
`required_capabilities()` to return the tokens *it* needs. The method is
**non-recursive**: each node returns its own tokens only. The validator
walks via `body.walk()` and unions per-node sets — recursing inside
`required_capabilities()` would double-count when the walker re-visited
the child.

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

Key points:

- The lazy `from qprogram.protocol import ...` inside the method breaks an
  import cycle (`protocol.py` references `Waveform` types in its type
  annotations under `TYPE_CHECKING`, but `waveforms` import is needed at
  runtime to populate the dispatch table).
- The method inspects the *instance's* data. `Play(Square(...))` returns
  different tokens than `Play(IQDrag(...))` than `Play("alias")`.
- Channel kind (`waveform.single` / `waveform.iq`) comes from `isinstance`
  on the waveform base classes. Per-class refinement (`waveform.square`,
  `waveform.iq_drag`) comes from `waveform_token()`, which looks up the
  class in `WAVEFORM_TOKEN`.

Block subclasses follow the same shape. `ForLoop.required_capabilities()`
returns `{"block.for_loop", "sweep.linear"}`; `Loop` returns
`{"block.loop", "sweep.arbitrary"}`.

### Expression tokens propagate from parametric arguments

When an op takes an expression-typed parameter, it unions in
`expression_tokens(value)`:

```python
class Wait(Operation):
    def __init__(self, bus, duration):
        self.bus = bus
        self.duration = duration

    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens
        return {"op.wait"} | expression_tokens(self.duration)
```

`expression_tokens` recursively walks the expression AST and returns one
token per node kind plus per-math-function name. A plain numeric literal
contributes nothing — the token represents the *shape* of the parameter,
not its concrete value.

## Token registry

`CAPABILITY_REGISTRY` is the canonical set of every dotted token any
in-tree op may emit. It exists for two reasons:

1. **Typo defence.** `Profile.__post_init__` calls `validate_tokens` on the
   profile's capability set; an unknown token raises `ValueError` at
   profile-construction time, not at validate-time.
2. **Discoverability.** A vendor author wanting to know what tokens exist
   can introspect `CAPABILITY_REGISTRY` rather than grep the source.

The registry is mutable. Vendor packages add their tokens at import time
via `register_capability_tokens(...)`. The function rejects empty strings,
leading/trailing dots, and doubled dots — a basic shape check, not a strict
schema. Each vendor knows its own namespace.

```python
register_capability_tokens(
    "vendor.qblox.acquire",
    "vendor.qblox.set_markers",
    ...
)
```

`register_capability_tokens` is idempotent: registering an existing token is
a no-op.

## Waveform-class dispatch

`WAVEFORM_TOKEN` maps a waveform class to its canonical refinement token:

```python
WAVEFORM_TOKEN: dict[type, str] = {
    Square: "waveform.square",
    IQDrag: "waveform.iq_drag",
    ...
}
```

Population is lazy via `_register_builtin_waveform_tokens` to avoid an
import cycle. Vendor packages register their own waveform classes the same
way:

```python
register_waveform_token(MyCustomPulse, "waveform.my_custom_pulse")
```

`register_waveform_token` automatically calls
`register_capability_tokens(token)` so a vendor never has to call both.

`waveform_token(value)` returns `None` for `str` aliases (the channel-kind
token `waveform.alias` is added separately by the op's
`required_capabilities`) and for unknown classes. Unknown classes still
emit channel-kind tokens (`waveform.single` or `waveform.iq`); they just
don't contribute a refinement token. This keeps validation forgiving for
prototype waveforms that haven't registered yet.

## The validator

`qprogram.validation.validate(qprogram, caps)` is a single pre-order walk:

1. **Build the `ValidationContext`** with a quick pre-walk that computes:
   - variable bindings (`var -> Block`)
   - sweep kinds (`var -> "linear" | "arbitrary"`)
   - max loop nesting depth
   - max parallel arity
   - measurement count
2. **Walk the AST** once via `body.walk()` and, for every visited node:
   - Compute `required = node.required_capabilities()`.
   - Emit one `missing-capability` diagnostic per token in
     `required - caps.capabilities`, sorted for stable output.
   - Run each predicate in `caps.predicates`, gather any diagnostics.
3. **Check limits** against the pre-computed aggregates.

The validator never raises. The list comes back, the caller decides what to
do. Inside a typical platform `execute()`:

```python
def execute(self, program, **kwargs):
    diagnostics = self.validate(program)
    if diagnostics:
        raise UnsupportedOperationError(
            "Program is not compatible with this platform:\n"
            + "\n".join(f"  {d}" for d in diagnostics)
        )
    # ... compile and run
```

### Per-node methods must not recurse

Both `Operation.required_capabilities()` and
`Block.required_capabilities()` return *only* the node's own tokens. The
validator walks. Recursing would double-count when the walker re-visited
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

The base classes' default implementations follow this rule and subclasses
should too.

## Predicates and `ValidationContext`

A `Predicate` is any callable with the signature
`(node, ctx) -> Iterable[Diagnostic]`. Predicates run on every visited node
and see the same shared `ValidationContext`. Yield zero or more diagnostics
per call.

The context is built once per `validate()` and is read-only. The current
surface:

| Method                                  | Returns                                                                 |
|-----------------------------------------|-------------------------------------------------------------------------|
| `sweep_kind_of(var)`                    | `"linear"`, `"arbitrary"`, `"averaged"`, or `None`                       |
| `binding_loop_of(var)`                  | The loop block that binds the variable, or `None`                        |
| `max_loop_nesting` (property)           | Deepest nested-loop count                                                |
| `max_parallel_arity` (property)         | Largest `len(parallel.loops)`                                            |
| `measurement_count` (property)          | Total `MeasurementOperation` count                                       |

A predicate that fires on the user-motivating case:

```python
# qprogram-qblox/src/qprogram_qblox/profiles.py
def _reject_arbitrary_sweep_at_wait_duration(node, ctx):
    if not isinstance(node, Wait):
        return
    if not isinstance(node.duration, Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield Diagnostic(
            severity="error",
            code="qblox.arbitrary-wait-sweep",
            message=(
                f"Variable {node.duration.id!r} is swept with arbitrary "
                f"values and used at Wait.duration ..."
            ),
            node=node,
        )
```

To add a new context query (say, `parallel_siblings_of(var)`), add a method
to `ValidationContext`, populate the underlying data in
`validation._build_context`, and document it here. The interface is
deliberately small.

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

`__post_init__` calls `validate_tokens(self.capabilities)` so an unknown
token in the bundle is an error at construction time, not at validate
time.

### Registering a profile

`register_profile(profile)` adds the profile to `PROFILE_REGISTRY`. The
function is idempotent for *the same `Profile` object* (so re-importing a
module is safe), and raises `ValueError` if you try to register a different
profile under an already-used name (so a typo doesn't silently take over
someone else's profile name).

Vendor packages register at import time, alongside the existing
vendor-namespace / vendor-version / operations registration:

```python
# qprogram-qblox/src/qprogram_qblox/__init__.py
from qprogram_qblox.profiles import _register as _register_qblox_profile

_register_qblox_profile()
```

### Building `CompilerCapabilities` from a profile

`CompilerCapabilities.from_profile(name, *, limit_overrides=None, extra_predicates=())`
materialises a descriptor from a registered profile:

1. Resolve the profile via `resolve_profile(name)` (raises `KeyError` if
   missing).
2. Walk the `extends` chain root-first via `_profile_chain`, detecting
   cycles.
3. Union capabilities, accumulate predicates (parent → child), and merge
   limits (later overrides earlier, so the leaf wins).
4. Apply `limit_overrides` on top (device-level tightening).
5. Append `extra_predicates` to the merged tuple.

The chain walk is root-first specifically so the leaf profile's limits
overwrite ancestors'.

## How `PlatformProtocol` consumes the descriptor

`PlatformProtocol` grows two members:

```python
class PlatformProtocol(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> CompilerCapabilities: ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        from qprogram.validation import validate as _validate
        return _validate(qprogram, self.capabilities)
```

A concrete platform typically caches the result of
`CompilerCapabilities.from_profile("<vendor>-<tier>-v1", limit_overrides=...)`
on construction and returns it from `capabilities`. The default `validate`
delegates to `qprogram.validation.validate`; platforms with site-specific
predicates can override and append them.

The contract document calls for `execute()` to call `validate()` first,
but the base class does not enforce this — concrete platforms vary in
where they put the gate.

## Adding things

### A new capability token

Add it to `_BASE_TOKENS` in `protocol.py`. Choose the prefix that matches
its category. Tokens are flat strings; the dotted convention is for
readability.

If the token is vendor-specific, *do not* edit `_BASE_TOKENS`. Register it
from the vendor package via `register_capability_tokens(...)` instead. A
test in `tests/test_protocol.py::test_register_capability_tokens_rejects_malformed_tokens`
guards the shape rules.

### A new waveform token

Either edit `_register_builtin_waveform_tokens` in `protocol.py` (for core
waveforms), or call `register_waveform_token(cls, token)` from a vendor
package's `__init__.py` (for vendor waveforms). The function automatically
adds the token to `CAPABILITY_REGISTRY` so a profile that lists it
validates.

### A new core operation

Implement `required_capabilities(self) -> set[str]` returning your op's
identity token plus any refinement tokens computed from instance state.
Add the identity token to `_BASE_TOKENS`. Add it to the profiles that
support the op. See [Adding operations](adding-operations.md) for the
full operation walkthrough.

### A new vendor operation

The vendor-operation walkthrough at
[Building a vendor extension](vendor-extensions.md) covers the protocol
side: implement `required_capabilities()` returning `{vendor.<name>.<op>}`
plus refinement, register the token via
`register_capability_tokens(...)`, and include it in your profile's
capability set.

### A new profile

Create a `Profile` in the vendor package's `profiles.py`. Pick a name
following the `<vendor>-<tier>-v<major>` convention. Optionally `extends`
an existing profile. Register via `register_profile(profile)` from the
vendor package's `__init__.py`.

### A new ValidationContext query

Add a method to `ValidationContext`, populate the underlying data in
`validation._build_context`, and document it on this page and in the
guide. Keep the surface small — predicate authors look here first.

## Testing

The protocol has dedicated test modules:

| File                                                          | Scope                                                                |
|---------------------------------------------------------------|---------------------------------------------------------------------|
| `qprogram/tests/test_protocol.py`                              | Dataclass behaviour, registry, waveform dispatch, profile extends. |
| `qprogram/tests/test_required_capabilities.py`                 | Per-op / per-block assertions, instance-aware variants.             |
| `qprogram/tests/test_validation.py`                            | Validator end-to-end: missing caps, limits, predicates, data flow. |
| `qprogram-qblox/tests/test_profile.py`                         | Vendor profile integration: registration, happy path, predicate.    |

When you add a new operation, mirror an existing
`test_required_capabilities.py` block: cover at least one positive case
per refinement axis (e.g. for an op with a waveform attribute, test both
single-channel and IQ paths).

When you add a new profile, mirror `test_profile.py` from `qprogram-qblox`:
register the profile, build the descriptor, validate a representative
happy-path program, and exercise the predicate(s) you ship.

## Out of scope (today)

The current implementation deliberately leaves three things on the table.
If you find yourself wanting one, the design has explicit hooks:

- **Multi-vendor descriptor union.** Two vendors' profiles combined into
  one target. Trivial follow-up — union capabilities, take the most
  restrictive limit element-wise — but no real use case has landed yet.
- **`.qp` `require profile <name>` syntax extension.** Profiles stay
  platform-side for now; the file format does not carry profile names.
- **`"warning"` severity.** `Diagnostic.severity` is typed
  `Literal["error"]`. Widening to include `"warning"` is one annotation
  change; reserved for a future limit-violation-with-software-fallback
  story.

## See also

- [Capabilities, diagnostics, and profiles](../guide/capabilities.md) — the user guide.
- [Architecture](architecture.md) — where the capability protocol fits in the rest of the codebase.
- [Adding operations](adding-operations.md) — the operation walkthrough, with the `required_capabilities` step in context.
- [Building a vendor extension](vendor-extensions.md) — the vendor walkthrough, with the profile-bundle step.
- [API reference](../reference/api-qprogram.md#capability-protocol) — the auto-generated reference.
