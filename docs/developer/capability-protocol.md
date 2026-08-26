# Capability protocol internals

This page is the developer companion to
[Capabilities, diagnostics, and profiles](../guide/capabilities.md). The guide
covers using the protocol from the outside; this page covers how the pieces are
built and what to touch when you extend them.

Three kinds of snippet appear below. A snippet whose first line is a
`# src/qprogram/...` path comment is package source, and keeps the real
intra-package imports it has in the tree, because `import qprogram` from inside
the package would close an import cycle. A snippet headed with a
`# qprogram-myvendor/...` path comment is source inside a vendor package, which
is ordinary external code: it reaches QProgram symbols through `qp.` and its own
modules through its own dotted paths. Every other example is user code and
reaches the library through `import qprogram as qp`.

## Design choices

The capability surface is split per bus and per domain.
`PlatformCapabilities` has exactly three fields: `bus`, a
`Mapping[BusSelector, BusCapabilities]` keyed by the `(element_kind, bus_kind)`
tuple that `BusSelector` names; `platform`, the slot for control flow,
expressions, and bus-less operations; and `default_bus_profile`, the fallback
for raw-string buses and for keys absent from `bus`. Each of those slots is a
`BusCapabilities(rt, host)`, and either half may be `None`:
`BusCapabilities.supported_domains()` reports which halves exist. A flux bus
driven by a slow DAC has `rt=None`, a sequencer-only bus has `host=None`. The
alternative, one flat capability set per platform, cannot express that a
platform plays a DRAG pulse on its drive bus but not on its flux bus, which is
the ordinary case on real racks.

Inside a slot the three axes stay separate because they are checked in
different ways. Capabilities are a `frozenset[str]` of flat tokens answered by
set membership, limits are a `Mapping[str, float]` compared against a number
the validator measured, and predicates are callables that look at the node and
its data-flow context. Vulkan made the same split into features, limits, and
extensions; collapsing them produces one axis with three kinds of entry and a
check that has to switch on which kind it holds. The predicate axis is MLIR's
dynamically-legal operations: legality is decided per node from what the node
holds, not per operation class.

Requirements are declared by the nodes rather than centrally. Each `Operation`
and `Block` subclass returns the tokens it needs from
`required_capabilities()`, computed from its own instance state, and the
validator walks the AST and unions per-node sets. This is the arrangement
MLIR's SPIR-V dialect uses: each operation declares its own availability
requirements, and one conversion target checks them. The cost is that which
tokens a node asks for is decided in the operation's module rather than in a
table, so `_BASE_TOKENS` tells a reader that a token exists but not what makes
a node require it.

The two domains share one token space. `required_capabilities()` is
domain-agnostic, and the same set is checked against the `rt` and the `host`
half of the routed slot; nothing in the DSL declares "this is a real-time
token". Domain-specific behavior comes from predicates instead, which emit a
`DomainConstraint` when a combination rules out a domain but leaves another
working, and a `Diagnostic` when it rules out all of them. The block classifier
turns those into per-node domain sets and reports the consequence once, as a
`forced-host` warning on the highest block that lost `"rt"`.

`PlatformCapabilities` is both what the validator consumes and what users
introspect; there is no separate advertised-versus-enforced surface to keep in
step. `qprogram.validation.validate` returns `(diagnostics, plan)`, and
`PlatformProtocol.validate`, `.plan`, and `.explain` are views over that one
call.

## Module layout

Capability code lives in three files plus `platform.py`, with two more that
present its output:

```
src/qprogram/
├── protocol.py         # data types + registries
├── profiles.py         # qprogram-base-v1 (core platform-level profile)
├── validation.py       # the validator + classifier
├── platform.py         # PlatformProtocol + .capabilities + .validate + .plan + .explain
├── paths.py            # structural node paths, stamped onto every node-bearing diagnostic
└── explain.py          # renders a plan as an annotated tree
```

`protocol.py` defines the data types and the registries:

| Name | What it is |
|---|---|
| `Domain` | `Literal["rt", "host"]`. |
| `BusSelector` | `tuple[str, str]`, an `(element_kind, bus_kind)` key into `PlatformCapabilities.bus`. |
| `SweepKind` | `Literal["linear", "arbitrary", "averaged"]`, the vocabulary `ValidationContext.sweep_kind_of` answers in. |
| `BusCapabilities(rt, host)` | Two stacked `CompilerCapabilities`, either may be `None`. `.get(domain)` and `.supported_domains()` read them. |
| `PlatformCapabilities(bus, platform, default_bus_profile)` | The top-level descriptor, with `.for_bus(bus)` for slot lookup. |
| `CompilerCapabilities` | What fills one (bus, domain) slot: `profile`, `version`, `capabilities`, `limits`, `predicates`, `vendor_versions`, plus `.supports(token)` and the `from_profile` constructor. |
| `Profile` | A named, versioned bundle of capabilities, limits, predicates, and vendor versions. Domain-agnostic. |
| `Diagnostic` | Frozen dataclass returned by the validator: `severity`, `code`, `message`, `node`, `path`, `capability`, `limit`, `domain`. |
| `DomainConstraint(node, exclude, reason)` | Soft predicate output that narrows domain support. |
| `Predicate` | Runtime-checkable `Protocol` for `(node, ctx) -> Iterable[Diagnostic \| DomainConstraint]`. |
| `PredicateFn` | The same signature as a plain `Callable` alias, for authors who would rather not import a `Protocol`. |
| `ValidationContext` | Read-only data-flow facts the validator passes to predicates. |
| `ExecutionPlan` | `Mapping[Operation \| Block, frozenset[Domain]]`. |
| `CAPABILITY_REGISTRY` | Mutable set of every dotted token any in-tree or registered vendor node may emit. |
| `PROFILE_REGISTRY` | `dict[str, Profile]`, populated by `register_profile`. |
| `WAVEFORM_TOKEN` | `dict[type, str]`, waveform class to refinement token. |
| `MEASUREMENT_FIELD_TOKEN_PREFIX` | `"measure.fields."`, the namespace that defines which `fields=` names exist. |

Plus the helpers `register_capability_tokens`, `validate_tokens`,
`register_profile`, `resolve_profile`, `register_waveform_token`,
`waveform_token`, `expression_tokens`, `measurement_field_token`, and
`known_measurement_fields`. The names re-exported at package top level are
listed in `qprogram.__all__`; the rest are reachable as `qp.protocol.<name>`.

`profiles.py` defines `QPROGRAM_BASE_V1`, the core platform-level profile, and
registers it as a side effect of `import qprogram`. It carries the five
`block.*` tokens, the nine `expr.*` node tokens and the nine `expr.math.*`
tokens, the two `sweep.<kind>` tokens, and one `sweep.<source>` token per
built-in source. It carries no bus tokens and no limits, and declares no
predicates.

`validation.py` exports one function:

```python
# src/qprogram/validation.py
def validate(
    qprogram: QProgram,
    caps: PlatformCapabilities,
) -> tuple[list[Diagnostic], ExecutionPlan]: ...
```

`platform.py` exposes `.capabilities` (an abstract property returning
`PlatformCapabilities`), `.validate(qp) -> list[Diagnostic]` and
`.plan(qp) -> ExecutionPlan`, both delegating to `qprogram.validation.validate`
and discarding the half they do not return, plus `.explain(qp) -> str`, which
delegates to `qprogram.explain`.

## Distributed declaration in practice

Every concrete `Operation` and `Block` subclass overrides
`required_capabilities()` to return the tokens it needs. The defaults are
permissive: `Operation`'s returns an empty set and `Block`'s returns
`{"block.block"}`, so a subclass that does not override adds no token of its
own: such an operation is accepted anywhere, and such a block is checked for
`block.block` alone.

A typical core operation reads its own instance state:

```python
# src/qprogram/operations/play.py
class Play(Operation):
    WAVEFORM_ATTRS: ClassVar[tuple[str, ...]] = ("waveform",)

    def __init__(self, bus: str, waveform: Waveform | IQWaveform | str) -> None:
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

So `Play("drive_q0", Square(0.5, 100))` needs
`{"op.play", "waveform.single", "waveform.square"}`, the same play with an
`IQDrag` needs `{"op.play", "waveform.iq", "waveform.iq_drag"}`, and the string
alias form needs `{"op.play", "waveform.alias"}`, because an alias is resolved
later by `QProgram.with_waveforms` and its class is not known yet.

Blocks follow the same shape. `Sweep.required_capabilities()` returns
`{"block.sweep"}` unioned with `SweepSource.tokens()`, which is the source's own
`TOKEN` plus `sweep.<KIND>`, so `Sweep(v, Range(0, 1, 0.1))` needs
`{"block.sweep", "sweep.range", "sweep.linear"}` and `Sweep(v, Values(...))`
needs `{"block.sweep", "sweep.values", "sweep.arbitrary"}`. A combinator unions
its wrapped source's tokens into its own, so a platform that does not declare
`sweep.logspace` also refuses `Rotate(Logspace(...))`. `Parallel` returns only
`{"block.parallel"}`: its loop headers are classified as block-children in their
own right, so their `sweep.*` tokens are checked there rather than repeated on
the parent.

### Expression tokens propagate from parametric arguments

An operation with an expression-typed parameter unions in
`expression_tokens(value)`:

```python
# src/qprogram/operations/wait.py
class Wait(Operation):
    def required_capabilities(self) -> set[str]:
        from qprogram.protocol import expression_tokens

        return {"op.wait"} | expression_tokens(self.duration)
```

`expression_tokens` walks the expression tree and returns one token per node
kind, plus `expr.math.<name>` per math function. `Wait("drive_q0", 100)` needs
only `{"op.wait"}`, because a plain `int` is not an `Expression` node and the
token describes the shape of the parameter rather than its value.
`qp.protocol.expression_tokens(qp.sqrt(d) + 2)` returns
`{"expr.binary_op", "expr.constant", "expr.math.sqrt", "expr.variable"}`. An
`Expression` subclass the function does not recognize contributes an empty set
rather than raising, so a vendor expression node does not break token
collection on an older validator.

`expr.*` tokens are checked against `caps.platform` wherever the operation
itself routes, since they describe which expression node kinds the platform's
compiler accepts rather than anything bus-specific. The split happens inside
the validator on the token prefix; an operation author calls
`expression_tokens` and unions the result in.

### Per-node methods must not recurse

Both `Operation.required_capabilities()` and `Block.required_capabilities()`
return only the node's own tokens. The validator does the walking, so a block
that unions its children's sets makes every descendant token be checked once
per ancestor as well as at the child, against the ancestor's slot rather than
the child's:

```python
# WRONG - do not do this
class MyBlock(qp.blocks.Block):
    def required_capabilities(self):
        caps = {"block.mine"}
        for child in self.elements:
            caps |= child.required_capabilities()  # walker also visits each child
        return caps
```

The concrete failure is a misattributed diagnostic. A block routes to
`caps.platform`, so a child's `op.play` folded into the parent's set is checked
against the platform slot, which does not carry bus tokens, and the reader gets
a `missing-capability` for `op.play` on the block. Nothing enforces the rule,
which is why it is stated here and in both base classes' docstrings.

## The two-pass validator

`qprogram.validation.validate(qprogram, caps)` starts by expanding fragments:
if any node in `qprogram.body.walk()` is a `Call`, the whole program is
replaced by `qprogram.expand()`, and everything after that point sees the
expansion. Diagnostics then carry nodes and paths from the expanded copy, so a
caller that wants plan entries for nodes it holds should call
`QProgram.expand()` itself and validate the result.

A pre-walk (`_build_context`) then collects the program-wide facts predicates
need, and the two cooperating checks run as one recursive post-order walk:

1. **Per-node check.** For each node, resolve the slots it routes to, split its
   required tokens on the `expr.` prefix, and work out which domains survive.
   Predicates registered on the halves that were not skipped run here, and their
   outputs are sorted into diagnostics and constraints.
2. **Block-level classification.** After a block's children have been
   classified, the block's domain is derived from its immediate op-children's
   consensus, intersected with what its own slot allows, minus the domains any
   `DomainConstraint` targeting it excludes.

Three passes follow, in order: whole-program limit checks; the two
profile-independent `Conditional` checks; then advisory emission, which appends
the `forced-host` warnings and the `reorderable-averaging` hints and finally
stamps every node-bearing diagnostic with its structural `path`.

The validator never raises. Both halves of the return value are always
produced, and the caller decides what an error means.

### Token checking inside a slot

`_check_node_self` iterates the two domains in the order `("rt", "host")`. A
domain is skipped before any token is examined when any of the node's routed
slots has `None` for that half, and, when the node contributes `expr.*` tokens,
also when `caps.platform.get(domain)` is `None`. Otherwise every routed slot is
checked: a token missing from any one of them fails the domain, which is how
the intersection across a multi-bus `Sync` is implemented. A domain that
finishes with no missing token and no predicate-emitted `Diagnostic` is
*available*.

Failure reasons are collected during the loop but only surface when the
available set comes out empty, so a node that works host-side does not also
report why real-time was refused. What surfaces is one `missing-capability` per
missing token, not one per domain, naming each profile and domain that lacked
it, followed by the deduplicated predicate diagnostics. The `domain` field is
populated only when a single domain was involved. So a token missing in both
halves of one profile reads:

```
[error] missing-capability: 'Play' requires capability 'waveform.square' which is not supported by 'awg-v1' (rt) / 'awg-v1' (host) (at body[0])
```

and a missing expression token says `expression capability` instead of
`capability` and names the platform profile:

```
[error] missing-capability: 'Wait' requires expression capability 'expr.variable' which is not supported by 'plat-v1' (rt) / 'plat-v1' (host) (at body[0][0])
```

The profile name in the message is `CompilerCapabilities.profile`, which is the
leaf profile's name, so a descriptor built from a profile that extends another
names only the leaf. When neither a token nor a predicate has anything to say
and the available set is still empty, the node gets an `empty-domain` error
instead: that is the case where both domains were skipped, because in each of
them a routed slot, or the platform slot for a node carrying `expr.*` tokens,
was `None`.

Predicates run once per (domain, routed slot) pair. A single-bus operation on a
slot with both halves filled calls each predicate twice; a `Sync` broadcasting
over three buses calls them six times. Duplicate outputs are discarded, a
`Diagnostic` by dataclass equality and a `DomainConstraint` by identity of the
target node plus equality of `exclude` and `reason`, so the usual case of one
profile filling both halves reports each finding once. Write predicates as
cheap, side-effect-free functions of `(node, ctx)`.

### Block classification

A block's op-children and block-children are partitioned first
(`_immediate_children`). A `Conditional` has no op-children at all: its arm
bodies and its `else_body` are block-children. A `Parallel`'s loop headers are
block-children alongside any block in its body, and its body's operations are
its op-children. Every other block splits its `.elements` on `isinstance`.

Both sets are recursed into before the block is judged. The natural domain is
then the intersection of the supports of the op-children that gate the block,
which is every op-child except in an `Average`, where it is only the
averaging-relevant ones: those with `Operation.AFFECTS_AVERAGING`, which
`MeasurementOperation` sets to `True` so that core `measure` and vendor
`acquire` opt in automatically. An averaging block accumulates measurement
results, so a host-side-only `set_offset` in its body does not pull the
averaging host-side, while a host-side-only measurement does. The relaxation
never widens: an `Average` with op-children but no averaging-relevant one falls
back to all of them.

Op-children whose own support is already empty are excluded from the consensus,
because folding an empty set in would manufacture a `mixed-domain` error on the
parent on top of the child's own diagnostic. The block's support still goes
empty, silently. Healthy op-children with disjoint singleton supports, on the
other hand, produce a `mixed-domain` error naming each child and its domains,
and the block's `available` and `support` are both set to the empty set.

What the consensus does not do is widen a block to host-side because host-side
would work. An all-real-time block stays `{rt}` until something takes `"rt"`
away, and two things can: a `DomainConstraint` targeting the block, and a
block-child whose support is exactly `{host}`, which propagates an implicit
`exclude={"rt"}` upward because a real-time parent cannot host a host-side
sub-block. When that leaves the block with nothing, the (e2) fallback applies:
a block whose natural domain was exactly `{rt}`, whose own token check passed
host-side, and which lost `"rt"` to an exclusion, drops to `{host}`. The block's
iteration mechanism becomes host-side dispatch, one real-time shot per
iteration, and its op-children keep their own real-time support. That is the
rule that lets a swept parameter change a loop's class without changing the
class of the operations inside it.

Two errors come out of the same step. If the support is empty, `empty-domain`
is reported with one of three messages, according to whether constraints
emptied it (the message lists their reasons), the block's own slot supported
nothing, or the slot and the op-children consensus were disjoint. If the
support is exactly `{rt}`, each block-child whose support lacks `"rt"` gets a
`host-in-rt` error. In practice that fires when the platform slot has no host
half at all, since with one the propagation above would have moved the parent to
host-side first.

### `DomainConstraint` versus `Diagnostic`

A predicate chooses between the two by asking whether any domain survives. The
in-tree example is `_swept_parameter_forces_host` in `executor.py`, which the
reference platform installs on every slot; the two below are the shapes a
vendor predicate takes.

```python
import qprogram as qp


def reject_arbitrary_wait(node, ctx):
    """Hard error: this wait instruction cannot take arbitrary sweeps in any domain."""
    if (
        isinstance(node, qp.operations.Wait)
        and isinstance(node.duration, qp.Variable)
        and ctx.sweep_kind_of(node.duration) == "arbitrary"
    ):
        yield qp.Diagnostic(
            severity="error",
            code="myvendor.arbitrary-wait-sweep",
            message="wait duration cannot be driven by an arbitrary sweep",
            node=node,
        )


def drag_sigma_is_host_only(node, ctx):
    """Soft restriction: real-time cannot, host-side dispatch still can.

    The constraint targets the binding loop, which is a Block, not the Play: the loop
    is what falls back to host-side dispatch, while the Play stays a real-time shot
    per iteration. Targeting the op is an authoring error, reported as
    bad-domain-constraint.
    """
    if isinstance(node, qp.operations.Play) and isinstance(node.waveform, qp.waveforms.IQDrag):
        sigma = node.waveform.sigma
        if isinstance(sigma, qp.Variable):
            binding_loop = ctx.binding_loop_of(sigma)
            if binding_loop is not None:
                yield qp.DomainConstraint(
                    node=binding_loop,
                    exclude=frozenset({"rt"}),
                    reason="IQDrag.sigma sweep is not real-time",
                )
```

The two outputs travel differently. A `Diagnostic` marks its domain failed and
is held back unless every domain fails, then reaches the caller as written; the
validator does not rewrite the `code` or the `message`, so a vendor prefixes its
codes to keep them apart from the core ones. A `DomainConstraint` is routed to
its target block's bucket, subtracts from that block's support silently, and is
reported once by the classifier as the `forced-host` warning, with its `reason`
quoted in the message. A constraint whose `node` is not a `Block` is dropped and
reported as a `bad-domain-constraint` error naming the node whose predicate
emitted it, because there is no sound place to apply it: the (e2) fallback is
defined on the iteration mechanism of a loop, and an operation has none.

### Diagnostic codes

Everything the validator can emit, with what it attaches to. Vendor predicates
add their own codes, which by convention carry a vendor prefix.

| Code | Severity | Node | Emitted when |
|---|---|---|---|
| `missing-capability` | error | the node | A required token is absent and no domain survived. One per token, with `capability` set, and `domain` set when only one domain was involved. |
| `empty-domain` | error | the node or block | Both domains were skipped for want of a descriptor in a slot the node needs, or a block's support came out empty. For a block the message says which of the three cases applies. |
| `mixed-domain` | error | the block | Healthy op-children have disjoint singleton supports. |
| `bad-domain-constraint` | error | the node whose predicate emitted it | A `DomainConstraint` targets something that is not a `Block`. |
| `host-in-rt` | error | the block-child | A block with support `{rt}` contains a block-child whose support lacks `"rt"`. |
| `limit-exceeded` | error | the `Wait`, or none | A declared numeric limit is breached. `limit` carries `(name, observed_value)`; the threshold is in the message and in `CompilerCapabilities.limits`. |
| `unknown-measurement` | error | the `Conditional` | An arm condition references a measurement name the program does not define. |
| `missing-classification` | error | the `Conditional` | An arm condition reads `.state` from a measurement whose `fields` does not request state classification. |
| `forced-host` | warning | the highest forced block | A block's support fell to `{host}` while its `available` still contained `"rt"`. `domain` is `"host"`. |
| `reorderable-averaging` | info | the `Average` | `qprogram.optimize` could rewrite this average to run in real-time hardware. |

`Diagnostic.__str__` renders as `[severity] code: message (at path)`, with the
path omitted when there is none. The `path` is the structural address of the
node under the program body, stamped in the final pass by walking
`qprogram.paths.iter_child_edges` and rebuilding each frozen `Diagnostic` with
`dataclasses.replace`. Because the `.qp` round trip preserves structure, the
same path resolves against `qp.loads(qp.dumps(p))`, whose `source_map` maps it
to a 1-based line.

### Whole-program limits

Four limit keys are checked, and which slot they are read from differs.
`max_loop_nesting`, `max_parallel_loops`, and `max_measurements` come from
`caps.platform`; `min_wait_duration_ns` comes from the routed bus slot of each
`Wait`. A key the profile does not declare is not checked, and a key the
validator does not know is ignored, so a profile may declare a limit an older
validator has no check for.

Within a slot the limits are read from one half only: `_pick_limits` returns
`slot.rt.limits` when the slot has a real-time half, otherwise
`slot.host.limits`, otherwise an empty mapping. A limit declared only on the
host half of a slot that also has a real-time half is therefore never read. The
real-time engine is normally the tighter one, which is why it wins, but the
practical consequence is that a platform should put its limits on both halves.

The observed values come from the context. `max_loop_nesting` is compared
against `ctx.max_loop_nesting`, which counts repetition levels: a block adds a
level when it declares `Block.REPEATS`, so `Sweep`, `Average`, and `Parallel`
each add one, a `Parallel` adds one however many loops it composes, and a
`Conditional` arm or a plain grouping block adds none. `max_parallel_loops` is
compared against `ctx.max_parallel_arity`, the largest `len(parallel.loops)` in
the program, and note the two names differ. `max_measurements` is compared
against `ctx.measurement_count`.

The wait check applies only when `node.duration` is a plain `int`. A duration
given as an `Expression` has no static value to compare and is left unchecked,
which is why a swept wait is a predicate's business rather than a limit's.

### Conditional checks

Two checks run over every `Conditional` in the program regardless of profile,
because they are about the program being self-consistent rather than about what
a platform supports. Each arm condition is descended for `MeasurementRef`
leaves. A reference to a name that is not in `ctx.known_measurement_names()`
gives `unknown-measurement`, which usually means a `MeasurementRef` was built
outside `measure(...)`. A reference to `handle.state` whose source measurement
did not request `MeasurementField.STATE` in `fields=` gives
`missing-classification`. Both attach to the `Conditional`, not to the
reference.

### The execution plan is identity-keyed

AST nodes use structural equality and hashing, which is right for round-trip
comparison and wrong for the classifier's bookkeeping: a plain `dict` would
collapse two identical `play` operations into one entry and hand the compiler a
plan with nodes missing. The plan is an `_IdentityNodeMap`, a `MutableMapping`
keyed by `id()` that holds a reference to each key so ids cannot be recycled. It
satisfies the public `Mapping` contract: iteration yields the node objects and
`plan[node]` resolves by identity.

So `len(plan)` counts node instances: a three-operation program has three
entries even when two of them are equal. A structurally identical node that is
not in the program raises `KeyError` on lookup. The root `body` block has no
entry; the plan covers everything below it.

## `ValidationContext` queries

A `Predicate` is any callable with the signature
`(node, ctx) -> Iterable[Diagnostic | DomainConstraint]`; `Predicate` is a
runtime-checkable `Protocol` and `PredicateFn` is the same shape as a
`Callable` alias. The context is built once per `validate()` call by
`_build_context`, has a keyword-only constructor, and is read-only. Predicates
must treat it as immutable.

| Method | Returns |
|---|---|
| `sweep_kind_of(var)` | `"linear"`, `"arbitrary"`, `"averaged"`, or `None` when the variable is not loop-bound. No built-in source declares `"averaged"`. |
| `binding_loop_of(var)` | The `Sweep` that binds the variable, whether standalone or a `Parallel` header, or `None`. This is the node a `DomainConstraint` about the variable must target. |
| `max_loop_nesting` (property) | Deepest repetition-level count, counting blocks that declare `Block.REPEATS`. |
| `max_parallel_arity` (property) | Largest `len(parallel.loops)` in the program. |
| `measurement_count` (property) | Total number of `MeasurementOperation` instances. |
| `measurement_fields(name)` | The named measurement's `fields` tuple in canonical order, or `None` when no measurement carries that name. |
| `known_measurement_names()` | Every measurement name in the program, whether the author spelled it or the builder allocated it. |
| `program_buses` (property) | Every bus referenced anywhere in the program. Elements may be `BusRef` instances, which subclass `str`, so per-bus routing over them keeps its schema awareness. |

The fields mapping is keyed by name, so two measurements sharing a name keep
one entry between them while both still count toward `measurement_count`.

To add a query, add the method to `ValidationContext`, populate the underlying
data in the `visit` closure of `validation._build_context`, and document it
here. The surface is kept small so that predicate authors can read all of it.

## Token registry

`CAPABILITY_REGISTRY` starts as a copy of `_BASE_TOKENS`, the canonical set of
every dotted token an in-tree node may emit: eleven `op.*`, five `block.*`,
three waveform channel kinds and seventeen per-class waveform tokens, two
`sweep.<kind>` and eight `sweep.<source>`, nine `expr.*` node kinds and nine
`expr.math.*` functions, and three `measure.fields.*`. It exists for two
reasons.

The first is typo defense. `Profile.__post_init__` calls `validate_tokens` on
the profile's capability set, so an unknown token raises at
profile-construction time rather than being silently absent at validate time:

```
ValueError: Unknown capability token(s): ['op.playy']. Register via qprogram.protocol.register_capability_tokens before use.
```

The second is discoverability: reading `qp.protocol.CAPABILITY_REGISTRY` tells
a vendor author which tokens exist without grepping the source, and the read is
live, so it includes whatever the imported vendor packages have added.

The registry is mutable, and vendor packages add to it at import time:

```python
import qprogram as qp

qp.register_capability_tokens("vendor.myvendor.acquire", "vendor.myvendor.set_markers")
```

`register_capability_tokens` is idempotent, and rejects a token that is empty,
starts or ends with `.`, or contains `..`:

```
ValueError: Invalid capability token 'vendor..myvendor' (empty / leading-dot / trailing-dot / doubled dot)
```

That is a shape check, not a namespace policy: each vendor owns its own
`vendor.<name>.*` prefix, and nothing stops a package registering a token
outside it.

One namespace is load-bearing beyond validation. `measure.fields.<name>` tokens
are the single source of truth for which `fields=` names exist:
`known_measurement_fields()` derives the set from the live registry, and
`normalize_fields` uses it to reject a typo at the `measure(...)` call site. So
`qp.register_capability_tokens("measure.fields.demod")` widens the DSL's field
vocabulary as well as the token registry, and `measurement_field_token("demod")`
builds the token name for you.

## Waveform-class dispatch

`WAVEFORM_TOKEN` maps a waveform class to its refinement token:

```python
# src/qprogram/protocol.py
WAVEFORM_TOKEN: dict[type, str] = {}
```

The map starts empty and `_register_builtin_waveform_tokens` fills it with the
seventeen built-ins on the first `waveform_token()` call, importing
`qprogram.waveforms` inside the function to break the import cycle between the
two modules. Its guard is that the population is skipped whenever the map is
already non-empty, so the order of the first two writes matters: a package that
registers a waveform class before anything has classified a waveform suppresses
the built-in population, and `waveform_token(Square(...))` then returns `None`
and the play loses its `waveform.square` refinement.

Vendor packages register their own classes through the same map:

```python
import qprogram as qp

qp.register_waveform_token(MyCustomPulse, "waveform.my_custom_pulse")
```

`register_waveform_token` also calls `register_capability_tokens(token)`, so a
vendor never has to call both, and a profile that lists the token validates.

Dispatch is on the exact class: `waveform_token` returns
`WAVEFORM_TOKEN.get(type(wf))`, not the first matching base class, so a
subclass of `Square` gets no token until it registers its own. The function
returns `None` for a `str` alias, which is why `Play` adds `waveform.alias`
itself, and `None` for an unregistered class. An unregistered waveform still
contributes its channel kind, `waveform.single` or `waveform.iq`, because that
comes from an `isinstance` check in `required_capabilities` rather than from the
map; it just carries no per-class refinement. A prototype waveform is therefore
validated as a generic pulse rather than rejected.

## Profile bundles

`Profile` is a frozen dataclass:

```python
# src/qprogram/protocol.py
@dataclass(frozen=True)
class Profile:
    name: str
    version: tuple[int, int, int]
    extends: str | None
    capabilities: frozenset[str]
    limits: Mapping[str, float] = field(default_factory=dict)
    predicates: tuple[Predicate, ...] = ()
    vendor_versions: Mapping[str, tuple[int, int, int]] = field(default_factory=dict)
```

`name`, `version`, `extends`, and `capabilities` are required; the rest default
to empty. `vendor_versions` is informational, recording which vendor extension
versions the profile was written against, and mirrors the `.qp`
`require <vendor> <version>` line. `__post_init__` validates the capability set
against `CAPABILITY_REGISTRY`, so a vendor's `profiles.py` has to register its
tokens before it constructs the `Profile`, not after.

Profiles are domain-agnostic. Nothing in a `Profile` says `rt` or `host`; the
platform decides which profile fills each half of each slot, and the same
profile commonly fills both. Core ships `qprogram-base-v1` in
`src/qprogram/profiles.py`, which vendors use for the platform-level slot either
by naming it in `CompilerCapabilities.from_profile` or by declaring their own
profile with `extends="qprogram-base-v1"`. Naming a bundle and letting another
bundle extend it is the arrangement QIR profiles use, where each profile names a
subset of the instruction set a backend accepts and a larger profile builds on a
smaller one.

### Registering a profile

`register_profile(profile)` adds the profile to `PROFILE_REGISTRY` under its
`name`. It is idempotent for the same `Profile` object, so a module whose import
side effects run twice is safe, and raises
`ValueError: Profile 'x' is already registered with different content` when a
different profile claims a name already taken. `resolve_profile(name)` is the
read side, and raises `KeyError` with the currently registered names listed in
the message.

Vendor packages register at import time, alongside their vendor-namespace,
vendor-version, and operation registration:

```python
# qprogram-myvendor/src/qprogram_myvendor/__init__.py
from qprogram_myvendor.profiles import _register as _register_myvendor_profile

_register_myvendor_profile()
```

### Building `CompilerCapabilities` from a profile

`CompilerCapabilities.from_profile` materializes a descriptor from a registered
profile:

```python
# src/qprogram/protocol.py
@classmethod
def from_profile(
    cls,
    profile_name: str,
    *,
    limit_overrides: Mapping[str, float] | None = None,
    extra_predicates: tuple[Predicate, ...] = (),
) -> CompilerCapabilities: ...
```

It resolves the name through `resolve_profile`, which raises `KeyError` if it is
not registered, then walks the `extends` chain with `_profile_chain`, which
returns it root-first and raises
`ValueError: Profile inheritance cycle detected at 'a'` if a profile is revisited.
Walking root-first is what makes a child override a parent. Along the chain
capabilities are unioned and predicates are accumulated in parent-to-child
order, while limits and `vendor_versions` are merged key by key so the leaf
wins. Then `limit_overrides` is applied on top, which is where a live device
tightens a limit its profile states loosely, and `extra_predicates` is appended
after the profile's own, which is where a rack-level constraint goes that does
not belong in the vendor-shipped profile.

The resulting `profile` and `version` fields name the leaf, not the chain, so a
diagnostic about a token inherited from `qprogram-base-v1` names the vendor
profile that pulled it in.

```python
import qprogram as qp

base = qp.CompilerCapabilities.from_profile("qprogram-base-v1")
tight = qp.CompilerCapabilities.from_profile(
    "qprogram-base-v1",
    limit_overrides={"max_loop_nesting": 4},
)
base.supports("block.sweep")  # True
base.supports("op.play")  # False, op tokens live on bus profiles
tight.limits["max_loop_nesting"]  # 4, the override value as given
```

## Routing rules in detail

`PlatformCapabilities.for_bus(bus)` resolves a bus to a slot. A `BusRef` whose
`schema` is not `None` looks up `bus[(ref.element, ref.kind)]` and falls back to
`default_bus_profile` when that key is absent. A plain `str`, or a `BusRef` with
no schema, always goes to `default_bus_profile`.

`validation._route(node, caps, ctx)` decides which slots a node touches:

- Blocks route to `[caps.platform]`.
- Operations whose `BUS_ATTRS` is empty, such as `Call`, route to
  `[caps.platform]`.
- Bus-touching operations route to one slot per bus value read off their
  `BUS_ATTRS`, collecting plain strings and the string elements of lists. The
  caller intersects across the list, which is what makes a multi-target `Sync`
  need its token on every bus it names.
- An operation whose bus list comes out empty and whose class sets
  `BROADCASTS_WHEN_NO_BUS = True`, which is `Sync(targets=None)`, routes to one
  slot per bus in `sorted(ctx.program_buses)`, so the broadcast form and the
  explicit-targets form intersect the same way. With no buses in the program at
  all, or for a non-broadcast operation with an empty bus list, the fallback is
  `[caps.default_bus_profile]`.

The list is never empty, so every node is checked against at least one slot.
Within the routed slots, `expr.*` tokens are checked against `caps.platform`
and everything else against the routed slots.

## How `PlatformProtocol` consumes the descriptor

`PlatformProtocol` also carries the platform's schema, parameter, and `execute`
surface; the four capability-facing members are:

```python
# src/qprogram/platform.py
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

`capabilities` is a property rather than a method because callers introspect it
like data. A concrete platform typically builds its `PlatformCapabilities` once
in `__init__` and returns the cached object, since `validate` reads it on every
call. An `execute()` that gates on diagnostics and then compiles against the
plan should call `qprogram.validation.validate` directly rather than
`self.validate` followed by `self.plan`, which would walk the program twice.

The convention for `execute()` is to validate first, raise
`UnsupportedOperationError` on any `severity="error"` diagnostic, surface
`severity="warning"` without raising, and pass `severity="info"` through as
advisory. `ReferencePlatform` in `qprogram.executor` does exactly that, warning
through the `ExecutionWarning` category, and is the worked example. The base
class does not enforce the convention, because platforms differ in where they
put the gate.

`qp.reference_capabilities()` builds that platform's descriptor from the live
token registry, with `set_parameter` and `get_parameter` present only in each
bus slot's `host` half and one predicate installed on every slot, so it is a
real descriptor to try a program against:

```python
import qprogram as qp

caps = qp.reference_capabilities()
p = qp.QProgram()
amp = p.variable("amp")
with p.average(100), p.sweep(amp, qp.Range(0.0, 1.0, 0.1)):
    p.set_parameter("drive_q0", "power", amp)
    p.measure("readout_q0", "readout", "weights")

diagnostics, plan = qp.validate(p, caps)
```

The sweep drives a `set_parameter`, so the core predicate constrains the
binding loop out of real-time, the enclosing average follows it host-side, and
the two advisory diagnostics come back:

```
[warning] forced-host: Block 'Average' falls back to host-side execution: contains host-side-only sub-block 'Sweep' (parameter 'power' is swept via set_parameter (host-side dispatch per iteration)). (at body[0])
[info] reorderable-averaging: Block 'Average' runs host-side only because it encloses a host-side sweep; its measurement sequence supports real-time hardware. Moving the sweep outside the average (hoisting the host-side-only setup with it) would let the averaging run in real-time hardware — see qprogram.optimize(). (at body[0])
```

The `Measure` keeps `{"rt", "host"}` in the plan while the `Sweep` and the
`Average` above it are `{"host"}`: the loop's iteration mechanism moved, not the
measurement. The hint fires only for the shape `qprogram.optimize` can actually
rewrite, which the validator and the rewrite agree on by sharing one predicate,
`validation.reorderable_average_split`: the average's sole child is a flat
`Sweep` whose body is a leading contiguous run of host-side-only operations
followed by real-time-capable ones including at least one that affects
averaging. A host-side-only operation after a kept one cannot be hoisted without
reordering it past that operation, so such an average is not reorderable and
gets no hint.

## Adding things

### A new capability token

Add it to `_BASE_TOKENS` in `protocol.py`, under the prefix that matches its
category. Tokens are flat strings and the dots carry no structure; nothing
parses a prefix except the `expr.` test in the validator and the
`measure.fields.` test in `known_measurement_fields`.

If the token is vendor-specific, do not edit `_BASE_TOKENS`. Register it from
the vendor package with `register_capability_tokens(...)` instead.
`tests/test_protocol.py::test_register_capability_tokens_rejects_malformed_tokens`
guards the shape rules.

### A new waveform token

Either add the class to `_register_builtin_waveform_tokens` in `protocol.py`
for a core waveform, or call `register_waveform_token(cls, token)` from a vendor
package's `__init__.py` for a vendor one. The function adds the token to
`CAPABILITY_REGISTRY` for you, so a profile that lists it validates.

### A new core operation

Implement `required_capabilities(self) -> set[str]` returning the operation's
identity token plus any refinement tokens computed from instance state, and add
the identity token to `_BASE_TOKENS`. Decide where it belongs: a bus-touching
operation's token goes on bus profiles, a bus-less operation's on the platform
profile. If the operation is one an `average` block accumulates, set
`AFFECTS_AVERAGING = True` on the class. See
[Adding operations](adding-operations.md) for the full walkthrough.

### A new vendor operation

The vendor walkthrough at
[Building a vendor extension](vendor-extensions.md) covers the protocol side:
implement `required_capabilities()` returning `{"vendor.<name>.<op>"}` plus any
refinement, register the token with `register_capability_tokens(...)`, and
include it in the profile's capability set.

### A new profile

Create a `Profile` in the vendor package's `profiles.py`, named following the
`<vendor>-<tier>-v<major>` convention, optionally extending an existing
profile, and register it with `register_profile(profile)` from the package's
`__init__.py`. Register the tokens it lists first, since the constructor
validates them.

### A new `ValidationContext` query

Add the method to `ValidationContext`, populate the underlying data in
`validation._build_context`, and document it on this page and in the guide.
Keep the surface small, because predicate authors read all of it.

### A new domain-constraint predicate

Write it as a callable returning `Iterable[Diagnostic | DomainConstraint]`.
Yield a `DomainConstraint(node, exclude, reason)` for a restriction that leaves
another domain working, targeting the block whose iteration mechanism has to
change, which is usually the binding loop from `ctx.binding_loop_of`. Yield a
`Diagnostic` when no domain can run the node, and give it a vendor-prefixed
code. Register it on the profile of the slot where it should fire, which for a
bus-touching operation is the bus profile, and remember that it will be called
once per (domain, slot) pair.

## Testing

The protocol has dedicated test modules:

| File | Scope |
|---|---|
| `tests/test_protocol.py` | Dataclass behavior, registries, waveform dispatch, profile `extends`, `BusCapabilities`, `PlatformCapabilities` routing. |
| `tests/test_required_capabilities.py` | Per-operation and per-block token assertions, including the instance-aware variants. |
| `tests/test_validation.py` | The validator end to end: routing, missing capabilities, limits, predicates, classification, forced-host warnings, averaging hints. |
| `tests/test_explain.py`, `tests/test_paths.py` | Plan rendering, and the structural paths diagnostics are stamped with. |
| A vendor package's `tests/test_profile.py` | Vendor profile integration: registration, happy path, predicate, `DomainConstraint` flow. |

`tests/test_validation.py` builds its descriptors from two token sets and a
`_slot` helper whose `rt=` and `host=` flags drop a half, which is the shortest
way to write a platform where one bus has no real-time engine. Copy that shape
rather than a real vendor profile when what you are testing is the validator.

When you add an operation, mirror an existing `test_required_capabilities.py`
block and cover at least one case per refinement axis: for an operation with a
waveform attribute, that means the single-channel path, the IQ path, and the
string alias.

When you add a profile, mirror a vendor `test_profile.py`: register the profile,
build the descriptor, validate a representative happy-path program, and
exercise each predicate you ship in both the firing and the non-firing case.

## Out of scope

Three things the protocol does not do, with where the change would go if it had
to:

- **Subtractive profiles.** `extends` only adds, so a vendor whose constraints
  are narrower than a parent's builds a profile from scratch rather than
  removing from one. A `removes=` field would be a localized change to
  `_profile_chain` in `protocol.py`.
- **Profile names in `.qp` files.** Profiles are platform-side; the file format
  carries vendor requirements through `require <vendor> <version>`, and nothing
  in it names a profile.
- **Domains beyond `rt` and `host`.** `Domain` is a `Literal` of exactly the
  language's two execution domains. Adding a third, host-side dispatch to a
  remote runner for instance, means widening the literal and revisiting the
  classifier, which currently reasons about a two-element set by intersection
  and one rt-to-host fallback.

## See also

- [Architecture](architecture.md): where the capability protocol sits in the
  rest of the codebase.
- [API reference](../reference/api-qprogram.md#capability-protocol): the
  generated reference.
