# Capabilities, diagnostics, and profiles

A QProgram describes *what* you want to happen. A platform is what actually runs
it. Different platforms support different subsets of the language: one might cap
loop nesting at four levels, another might accept arbitrary numpy sweeps at
waveform parameters but not at `Wait.duration`, another might not implement a
particular vendor operation. Within a single platform, a logical bus may be wired
to a different physical instrument than its siblings (drive on a fast waveform
generator, flux on a slow DAC), and an operation may run as a real-time hardware
loop on one path or as host-side (software-timed) per-shot orchestration on
another.

The capability protocol is how a platform declares which features it supports
per bus and per execution domain, and how QProgram catches programs that exceed
the declaration *before* anything reaches the sequencer. `qp.validate(program,
caps)` and `qp.explain(program, caps)` are functions of a program and a
descriptor and nothing else, so either runs on any program at any point, with no
platform handle and no connected instrument.

## The shape of a descriptor

`qp.PlatformCapabilities` is the whole declaration. It is a frozen dataclass
with three fields:

| Field                 | Type                                            | Holds                                                                                             |
|-----------------------|-------------------------------------------------|---------------------------------------------------------------------------------------------------|
| `bus`                 | `Mapping[tuple[str, str], BusCapabilities]`     | One slot per `(element_kind, bus_kind)` pair. `("q", "drive")` selects every transmon drive bus.   |
| `platform`            | `BusCapabilities`                               | The platform-wide slot: block tokens, expression tokens, and bus-less operations.                 |
| `default_bus_profile` | `BusCapabilities`                               | Fallback for raw-string buses, and for bus-touching ops whose `(element, kind)` key is not in `bus`. |

The per-bus grain exists because drive and readout buses commonly live on
different hardware than flux buses. The platform-wide grain covers what belongs
to no single bus: control-flow blocks, expression node kinds, and sweep shapes.

Each of those slots is a `qp.BusCapabilities`, which splits the slot into its two
execution domains. `rt` describes what runs on the real-time hardware sequencer;
`host` describes what runs as per-iteration dispatch from the lab server. Either
half may be `None` when the slot has no engine for that domain: a flux bus driven
only by a slow DAC has `rt=None`, a real-time-only bus has `host=None`. Read one
half with `slot.get("rt")`, and the non-`None` ones with
`slot.supported_domains()`, which returns a subset of `{"rt", "host"}`.

A slot half is a `qp.CompilerCapabilities`, the descriptor the validator actually
consumes. There is no separate "advertised" and "enforced" surface; this is both.

| Field             | Type                                       | Holds                                                                        |
|-------------------|--------------------------------------------|------------------------------------------------------------------------------|
| `profile`         | `str`                                      | Name of the profile it was materialized from. Appears in diagnostic messages. |
| `version`         | `tuple[int, int, int]`                      | Version of that profile, not of the merged result.                            |
| `capabilities`    | `frozenset[str]`                            | Merged capability tokens. `supports(token)` is a membership test on this set.  |
| `limits`          | `Mapping[str, float]`                       | Merged numeric thresholds.                                                    |
| `predicates`      | `tuple[Predicate, ...]`                     | Merged predicates, parent's first, run against every visited node.            |
| `vendor_versions` | `Mapping[str, tuple[int, int, int]]`        | Which vendor-extension versions the profile was written against. Informational; mirrors the `.qp` `require <vendor> <version>` line. |

`capabilities`, `limits`, and `predicates` are the three orthogonal capability
axes. Tokens are flags: either the slot advertises `op.play` or it does not.
Limits are numbers the validator measures the program against. Predicates are
callables that inspect an AST node with cross-node data-flow facts in hand,
which is what makes a constraint like "a wait may be swept, but only with evenly
spaced values" expressible at all.

A `qp.Profile` is a named, versioned bundle of the same three axes plus
`vendor_versions`, and `CompilerCapabilities.from_profile(name)` materializes
one. Profiles are domain-agnostic: a platform decides which profile fills each
slot half, and the same profile commonly fills both.

## Routing: which slot is checked?

The validator decides where each AST node's required tokens are checked from
what the node touches. Slot lookup for a bus goes through
`caps.for_bus(bus)`: a `BusRef` that carries schema metadata resolves to
`caps.bus[(bus.element, bus.kind)]` when that key is present, and a plain `str`
or a schema-less `BusRef` always falls through to `caps.default_bus_profile`.

A two-slot descriptor makes the difference visible. `schema.q[0].drive` is a
`BusRef` with `element == "q"` and `kind == "drive"`, so it finds the fast
waveform generator's slot; the flux bus finds a slot with no real-time half at
all; and the raw string `"drive_q0"` carries no schema to key on, so it falls
through to the default:

```python
import qprogram as qp


def bus_half(profile: str) -> qp.CompilerCapabilities:
    return qp.CompilerCapabilities(
        profile=profile,
        version=(0, 1, 0),
        capabilities=frozenset({"op.play", "waveform.single", "waveform.square"}),
        limits={},
        predicates=(),
        vendor_versions={},
    )


awg = bus_half("fast-awg-v1")
dac = bus_half("slow-dac-v1")
base = qp.CompilerCapabilities.from_profile("qprogram-base-v1")
schema = qp.BusSchema.flux_tunable_transmon()

caps = qp.PlatformCapabilities(
    bus={
        ("q", "drive"): qp.BusCapabilities(rt=awg, host=awg),
        ("q", "flux"): qp.BusCapabilities(rt=None, host=dac),
    },
    platform=qp.BusCapabilities(rt=base, host=base),
    default_bus_profile=qp.BusCapabilities(rt=awg, host=awg),
)

sorted(caps.for_bus(schema.q[0].drive).supported_domains())  # ['host', 'rt']
sorted(caps.for_bus(schema.q[0].flux).supported_domains())  # ['host']
caps.for_bus("drive_q0") is caps.default_bus_profile  # True
```

The node-to-slot table:

| Node                                                                    | Routes to                                                |
|-------------------------------------------------------------------------|----------------------------------------------------------|
| Bus-touching op with a schema-bound `BusRef` (e.g. `schema.q[0].drive`)  | `caps.bus[(bus.element, bus.kind)]`, or `caps.default_bus_profile` if no entry |
| Bus-touching op with a raw-string bus                                    | `caps.default_bus_profile`                               |
| Multi-bus op (e.g. `Sync(targets=[a, b])`)                              | Each bus's slot, intersected across them                 |
| Broadcast op with no bus (`Sync(targets=None)`)                          | **Every** bus in the program, intersected across them, since it means "sync everything". `caps.default_bus_profile` only when the program touches no bus at all |
| Bus-less op (empty `BUS_ATTRS`, e.g. a vendor alias-addressed op)        | `caps.platform`                                          |
| Any block (`sweep`, `average`, `parallel`, `conditional`, plain `block`) | `caps.platform`                                          |

Which attributes hold buses is a class fact, `Operation.BUS_ATTRS`, defaulting
to `("bus",)`. `Sync` holds a list under `targets` instead, and sets
`BROADCASTS_WHEN_NO_BUS = True` so that an empty target list means every bus
rather than none.

Within the routed slot the node's required tokens split by namespace: tokens
under `expr.*` check against `caps.platform` regardless of routing, because they
describe which expression node kinds the platform's compiler accepts rather than
anything a particular instrument can do. Every other token checks against the
primary routed slot.

## A first validation

`qp.reference_capabilities()` builds the in-tree software platform's descriptor:
every token in the live registry, with `set_parameter` and `get_parameter`
present only in each bus slot's `host` half. That makes it a real descriptor to
try things against, so the examples below run as written.

```python
import qprogram as qp

caps = qp.reference_capabilities()

program = qp.QProgram(label="lo_sweep")
lo = program.variable("lo")
with program.average(1000), program.sweep(lo, qp.Range(5e9, 6e9, 1e6)):
    program.set_parameter("drive_q0", "lo_frequency", lo)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")

diagnostics, plan = qp.validate(program, caps)
```

`qp.validate` returns a `(diagnostics, plan)` pair and never raises. A platform's
`execute()` typically calls it first and raises
`UnsupportedOperationError` if any `severity="error"` diagnostic is present;
`severity="warning"` diagnostics are surfaced without raising, and
`severity="info"` diagnostics are passed through as advisory output.
`qp.ReferencePlatform` follows that convention exactly, re-emitting warnings
through `warnings.warn` as `ExecutionWarning`.

If you have a live platform handle, the same work lives on it. All three are
`PlatformProtocol` defaults; `validate` and `plan` delegate to `qp.validate` and
discard the half they do not return, so ask for both at once when you need both:

```python
platform.validate(program)  # list[Diagnostic]
platform.plan(program)  # ExecutionPlan
platform.explain(program)  # str
```

## What the validator does

Two walks, in order. A program containing fragment `Call` nodes is expanded
first, so both walks see the substituted fragment bodies rather than the call
sites.

The first walk builds a `qp.ValidationContext`: which loop binds each variable,
each bound variable's sweep kind, the deepest repetition nesting, the largest
`Parallel` arity, the measurement count, the requested fields per measurement
name, and the set of buses the program references. These are the facts no single
node can answer for itself, gathered once so that predicates do not each
re-walk the tree.

The second walk is a single recursive post-order pass. Per node it resolves the
routed slots, checks the required tokens against each domain half, runs that
half's predicates, and records two domain sets: `available`, the domains the slot
would allow, and `support`, what is left after `DomainConstraint`s apply.
Post-order matters because a block is classified from children whose support is
already known.

For an operation, `support` equals `available`; a `DomainConstraint` never
subtracts from an operation. For a block, `support` is its own slot's allowance
intersected with the consensus of its immediate op-children, minus whatever
constraints target it. When that subtraction leaves nothing, a block whose
op-children consensus is exactly `{rt}` and whose own slot carries `host` falls
back to `{host}` instead of to nothing: the operations stay real-time and only
the loop's iteration mechanism moves host-side. Block-children are not part of
that consensus. A host-side-only block-child instead acts as an implicit
`exclude={"rt"}` on its parent, since a real-time parent cannot host a host-side
sub-block. An `average` is the one block whose consensus is narrowed further: it
accumulates measurement results, so only op-children with `AFFECTS_AVERAGING`
set decide its domain, and the rest still validate and run without pulling the
average host-side. An `average` holding no such child falls back to all of its
op-children, so the narrowing can never widen a domain.

An op-child that can run nowhere empties its parent's support without a second
diagnostic, because the child's own diagnostic already explains it, and it is
also excluded from the consensus so that it cannot manufacture a misleading
`mixed-domain` error on the parent.

Four finishing steps run after the walks: the whole-program limit checks, the
profile-independent `Conditional` checks, one `forced-host` warning per highest
forced block, and the `reorderable-averaging` hints. Then every node-bearing
diagnostic is stamped with its structural path.

## Diagnostics

`qp.Diagnostic` is a frozen dataclass describing one issue.

| Field         | Meaning                                                            |
|---------------|--------------------------------------------------------------------|
| `severity`    | `"error"`: the program cannot execute; `"warning"`: it runs but degraded or surprising (the `forced-host` notice); `"info"`: purely advisory. |
| `code`        | Short machine-readable string, from the table below. Vendor predicates prefix their own codes with the vendor name. |
| `message`     | Human-readable explanation.                                        |
| `node`        | The offending `Operation` or `Block`, when one is available. Whole-program checks have none. |
| `path`        | Structural address of `node` under the program body, a tuple of integer and string segments: `(0, 1)` prints as `body[0][1]`, and `(1, "arm:0", 2)` as `body[1].arm:0[2]`. The four segment kinds are tabulated under [From a diagnostic to a `.qp` line](#from-a-diagnostic-to-a-qp-line). Stamped by `validate`; `None` when there is no node. |
| `capability`  | The missing token, on `missing-capability`.                          |
| `limit`       | A `(name, observed_value)` tuple when a numeric limit fired. The threshold itself stays in `CompilerCapabilities.limits`. |
| `domain`      | On `forced-host`, the domain the node ended up in. On `missing-capability`, the single domain the token was missing from, or `None` when it was missing from both. |

`__str__` renders as `[severity] code: message (at path)`, which is what the
examples on this page print.

The codes the validator emits, and the condition that produces each:

| Code                     | Severity | Condition                                                                                          |
|--------------------------|----------|----------------------------------------------------------------------------------------------------|
| `missing-capability`     | error    | A required token is absent from the routed slot and no domain could run the node. One diagnostic per missing token, naming every domain and profile it was missing from. |
| `empty-domain`           | error    | A node has no domain left. On an operation: no domain has an engine on all the slots the node routes to, so nothing was even checked. On a block: its own slot allows nothing, or the op-children consensus is disjoint from what its slot allows, or a `DomainConstraint` removed the last domain. |
| `mixed-domain`           | error    | A block's healthy domain-relevant op-children have disjoint singleton supports, for example one op that is real-time only beside one that is host-side only. |
| `host-in-rt`             | error    | A block whose final support is `{rt}` contains a block-child without `rt`. Real-time inside host-side is always allowed; the reverse is not. |
| `bad-domain-constraint`  | error    | A predicate yielded a `DomainConstraint` whose `node` is not a `Block`. The constraint is dropped. |
| `limit-exceeded`         | error    | One of the four numeric limits was breached. Carries `limit=(name, observed)`. |
| `unknown-measurement`    | error    | A `Conditional` arm condition references a measurement name no measurement in the program carries, usually a `MeasurementRef` built outside `measure(...)`. |
| `missing-classification` | error    | A condition references `handle.state` but that measurement's `fields` omits `state`. |
| `forced-host`            | warning  | A block's support fell to `{host}` while its `available` still contained `rt`, so real-time would have been viable without the constraints. One per highest block in each forced chain. |
| `reorderable-averaging`  | info     | An `average` is host-side-only only because it encloses a host-side sweep whose measurement sequence is real-time-capable, in the exact shape `qp.optimize` can rewrite. |

Those ten are the whole set. The `parse-error` code you may also come across
belongs to `check_text` in `qprogram.lsp`, which reports `.qp` text that fails to
parse before there is a program to validate at all.

Real messages, from a slot that advertises nothing:

```python
bare = qp.CompilerCapabilities(
    profile="no-op-v1",
    version=(0, 0, 1),
    capabilities=frozenset(),
    limits={},
    predicates=(),
    vendor_versions={},
)
empty_slot = qp.BusCapabilities(rt=bare, host=bare)
empty_caps = qp.PlatformCapabilities(bus={}, platform=empty_slot, default_bus_profile=empty_slot)

unsupported = qp.QProgram()
unsupported.play("drive_q0", "pi_pulse")

for diag in qp.validate(unsupported, empty_caps)[0]:
    print(diag)
```

```
[error] missing-capability: 'Play' requires capability 'op.play' which is not supported by 'no-op-v1' (rt) / 'no-op-v1' (host) (at body[0])
[error] missing-capability: 'Play' requires capability 'waveform.alias' which is not supported by 'no-op-v1' (rt) / 'no-op-v1' (host) (at body[0])
```

Set `host=None` on that slot and the same two diagnostics name only `(rt)` and
carry `domain="rt"`; drop both halves and the token check never runs, so the
diagnostic becomes `[error] empty-domain: 'Play' has no executable domain on
its routed slot. (at body[0])`

When at least one domain supports a node, the complaints from the other domain
are suppressed, because the fallback worked; diagnostics appear only when no
domain can run the node. What does surface is deduplicated twice over. There is
one diagnostic per missing token rather than one per domain, which is why the
two above each name both `(rt)` and `(host)`, and a predicate registered on both
halves of a slot contributes its equal diagnostic once.

## Capability tokens

Tokens are flat dotted strings. The prefix determines both what the token means
and which grain it lives on:

| Prefix                 | Examples                                                            | Lives on        |
|------------------------|---------------------------------------------------------------------|-----------------|
| `op.<name>`            | `op.play`, `op.measure`, `op.set_frequency`, `op.set_phase`, `op.set_gain`, `op.reset_phase`, `op.set_offset`, `op.wait`, `op.sync`, `op.set_parameter`, `op.get_parameter` | bus |
| `block.<name>`         | `block.block`, `block.sweep`, `block.average`, `block.parallel`, `block.conditional` | platform |
| `sweep.<kind>`         | `sweep.linear`, `sweep.arbitrary`: the bound source's `KIND`         | platform        |
| `sweep.<source>`       | `sweep.range`, `sweep.values`, `sweep.linspace`, `sweep.logspace`, `sweep.file`, `sweep.repeat`, `sweep.rotate`, `sweep.concat`: one per source class | platform |
| `waveform.<kind>`      | `waveform.single`, `waveform.iq`, `waveform.alias`                  | bus             |
| `waveform.<class>`     | `waveform.square`, `waveform.iq_drag`, `waveform.iq_pair`, and one per built-in waveform class | bus |
| `expr.<kind>`          | `expr.constant`, `expr.variable`, `expr.binary_op`, `expr.where`, `expr.math.sin` | always platform |
| `measure.fields.<f>`   | `measure.fields.iq`, `measure.fields.raw`, `measure.fields.state`   | bus             |
| `vendor.<name>.<op>`   | `vendor.myvendor.acquire`, `vendor.myvendor.active_reset`            | bus, or platform for bus-less vendor ops |

Every token any in-tree `required_capabilities()` may emit is listed in
`qp.protocol.CAPABILITY_REGISTRY`, and `Profile.__post_init__` validates its
token set against it. A typo in a vendor package therefore fails at profile
construction rather than being silently accepted and never matching:

```
ValueError: Unknown capability token(s): ['op.zap']. Register via qprogram.protocol.register_capability_tokens before use.
```

Vendors widen the registry with `qp.register_capability_tokens(*tokens)`, which
is idempotent and rejects only malformed shapes (empty, leading or trailing dot,
doubled dot). Each vendor owns its own `vendor.<name>.*` prefix. Registering
`measure.fields.<name>` also widens what `fields=` accepts at the `measure(...)`
call site, since `qp.protocol.known_measurement_fields()` is derived from the
registry.

Every `Operation` and `Block` declares the tokens *it* needs through
`required_capabilities()`. The set is **instance-aware** and
**domain-agnostic**: it depends on the node's data, not just its class, and is
the same set whether the validator checks `rt` or `host`.

```python
qp.operations.Play("drive_q0", qp.waveforms.Square(0.5, 100)).required_capabilities()
# {'op.play', 'waveform.single', 'waveform.square'}

qp.operations.Play("drive_q0", qp.waveforms.IQDrag(0.5, 40, 8, 0.1)).required_capabilities()
# {'op.play', 'waveform.iq', 'waveform.iq_drag'}

qp.operations.Play("drive_q0", "pi_pulse").required_capabilities()
# {'op.play', 'waveform.alias'}

qp.blocks.Sweep(qp.Variable("f"), qp.Range(0, 1, 0.1)).required_capabilities()
# {'block.sweep', 'sweep.linear', 'sweep.range'}

qp.blocks.Sweep(qp.Variable("f"), qp.Values([1, 2, 4])).required_capabilities()
# {'block.sweep', 'sweep.arbitrary', 'sweep.values'}
```

Each method is non-recursive: the validator visits every node and checks that
node's own set against the slot that node routes to, so a node that recursed
into its children would double-count them, and would check a child's tokens
against its parent's slot.

## Real-time vs host-side classification

The DSL makes no syntactic distinction between real-time and host-side
(software-timed) loops. The same `sweep` may run in either domain depending on
what is inside it. The validator's classifier picks, and the answer is in the
`ExecutionPlan`.

### `DomainConstraint`: predicates that narrow the domain instead of erroring

The canonical example: a sequencer can hold a real-time loop sweeping
`IQDrag.amplitude` (a register write per iteration) but cannot recompute
`IQDrag.sigma` between iterations, because the Gaussian envelope is precomputed
at upload. Sweeping `sigma` in a real-time loop has to fall back to
per-iteration host-side dispatch, and per-iteration dispatch *does* work.

Profiles express that with a `qp.DomainConstraint`, which carries three fields:
the `node` it applies to, the `exclude` frozenset of domains that node cannot
run in, and a `reason` string that surfaces in the eventual `forced-host`
message.

```python
def drag_sigma_is_host_only(node, ctx):
    if not isinstance(node, qp.operations.Play):
        return
    if not isinstance(node.waveform, qp.waveforms.IQDrag):
        return
    sigma = node.waveform.sigma
    if isinstance(sigma, qp.Variable) and ctx.sweep_kind_of(sigma) is not None:
        yield qp.DomainConstraint(
            node=ctx.binding_loop_of(sigma),
            exclude=frozenset({"rt"}),
            reason="IQDrag.sigma sweep is not real-time",
        )
```

A constraint's `node` **must be a `Block`**: the loop whose binding is the
problem, which is what `ctx.binding_loop_of(var)` returns. Targeting the
operation instead is reported as `bad-domain-constraint` and the constraint is
dropped, because it is the loop that has to move host-side, not the pulse. The
classifier subtracts the exclusion from the target block's support set, and every
ancestor inherits the result through the host-side propagation rule.

`Diagnostic` is reserved for hard outcomes, cases the compiler genuinely cannot
run anywhere; the validator surfaces one when no domain can run the node.
`DomainConstraint` is the soft outcome: the block *would* be supported, except in
the listed domains, and it stays silent whenever a domain fallback works. A
predicate may yield any mixture of the two from one call.

### `forced-host` warnings

When a block's support is reduced to `{host}` from a set that included `rt`, the
validator emits one `severity="warning"` `forced-host` diagnostic on the
*highest* block in that forced chain. Ancestors that are host-side-only purely
because of this child are not separately reported, which keeps the output
skimmable. The message names that block's **immediate** cause: its own
`DomainConstraint` reasons if any target it, otherwise the host-side-only
sub-block it contains, with that sub-block's reasons in parentheses. The reasons
for a sub-block are gathered from its whole subtree, so a constraint two levels
down still explains the warning.

The condition is `support == {host}` **and** `"rt" in available`. A block that
never had `rt` in the first place is natively host-side, not forced, and gets no
warning. In the `lo_sweep` program above, the `sweep` contains a `set_parameter`
that only the bus slot's `host` half advertises, so the sweep's op-children
consensus is `{host}` from the start and it is silent. The `average` around it
had both domains available (only measurements gate an `average`, and the measure
is real-time-capable), lost `rt` to the sweep, and so carries the warning:

```
[warning] forced-host: Block 'Average' falls back to host-side execution: contains host-side-only sub-block 'Sweep' (parameter 'lo_frequency' is swept via set_parameter (host-side dispatch per iteration)). (at body[0])
[info] reorderable-averaging: Block 'Average' runs host-side only because it encloses a host-side sweep; its measurement sequence supports real-time hardware. Moving the sweep outside the average (hoisting the host-side-only setup with it) would let the averaging run in real-time hardware — see qprogram.optimize(). (at body[0])
```

The `reorderable-averaging` hint fires only for the shape `qp.optimize` can
actually rewrite: an `average` whose sole child is one flat sweep whose body is a
leading contiguous run of host-side-only ops followed by real-time-capable ops
including at least one measurement. Hoisting a host-side op that sits *after* a
kept op would reorder it past that op and could change results, so such an
average is not reorderable. The validator cannot prove the reorder preserves
intent either, since interleaved and grouped shots differ on a drifting device,
which is why it suggests rather than rewrites.

## The execution plan

The second return value of `qp.validate` is an `ExecutionPlan`, a
`Mapping[Operation | Block, frozenset[Domain]]`. `frozenset({"rt"})` means a
real-time hardware path, `frozenset({"host"})` means host-side dispatch,
`frozenset({"rt", "host"})` means the platform may pick either at compile time,
and an empty frozenset means nothing can run the node, which always comes with an
error diagnostic explaining why.

The plan covers every visited node except the root body, in the post-order the
classifier walked. It is keyed by node **identity**, not by structural equality:
two `play "drive_q0" "pi_pulse"` operations compare equal yet get one entry each,
so a compiler can give the same pulse different treatment at different call
sites. A plain `dict` would have collapsed them and a three-operation program
would come back with two entries.

```python
for node, domains in plan.items():
    print(type(node).__name__, sorted(domains))
```

```
SetParameter ['host']
Play ['host', 'rt']
Measure ['host', 'rt']
Sweep ['host']
Average ['host']
```

Operations were classified from their slots, the sweep from its op-children, and
the average from the sweep it contains.

## Seeing the plan: `explain()`

`qp.explain(program, caps)`, or `platform.explain(program)`, renders the same
conclusion as a tree. Each body node appears as its `.qp` text, with the domain
set in an aligned column (`[rt|host]`, `[rt]`, `[host]`, or `[--]` for no
executable domain) and diagnostics annotated inline: `!!` for errors, `~` for
warnings, `i` for info. Node-less diagnostics such as the whole-program limits
land in a footer. The header carries the program label and the counts by
severity, and says so when a program with fragment calls was expanded first.

```python
print(qp.explain(program, caps))
```

```
plan for 'lo_sweep' — errors: 0 · warnings: 1 · info: 1
body
└─ average 1000:                                                               [host]     ~ forced-host: contains host-side-only sub-block 'Sweep' (parameter 'lo_frequency' is swept via set_parameter (host-side dispatch per iteration))  i reorderable-averaging: Block 'Average' runs host-side only because it encloses a host-side sweep; its measurement sequence supports real-time hardware. Moving the sweep outside the average (hoisting the host-side-only setup with it) would let the averaging run in real-time hardware — see qprogram.optimize().
   └─ for lo in Range(start=5000000000.0, stop=6000000000.0, step=1000000.0):  [host]
      ├─ set_parameter "drive_q0" "lo_frequency" lo                            [host]
      ├─ play "drive_q0" "pi_pulse"                                            [rt|host]
      └─ measure "readout_q0" "readout" "weights" name="m0"                    [rt|host]
```

Rows come from the `.qp` writer's own serializers, so a row reads the way the
program would be written to a file, and a node the writer cannot serialize falls
back to its `repr`. A `forced-host` annotation is shortened to its reason clause,
since the row it sits on already names the block. A `Conditional` renders as an
`if/elif/else chain` row with one row per arm.

### From a diagnostic to a `.qp` line

Every node-bearing diagnostic carries a structural `path`. Resolve it against the
program with `qp.resolve_path(program, diag.path)`, or map it to a line in the
serialized text. `loads()` records `program.source_map` as path to 1-based line,
and because the round-trip preserves structure, a path computed against the built
program looks up directly in the reloaded one:

```python
diag = diagnostics[0]
qp.format_path(diag.path)  # 'body[0]'
type(qp.resolve_path(program, diag.path)).__name__  # 'Average'

text = qp.dumps(program)
line = qp.loads(text).source_map[diag.path]
print(text.splitlines()[line - 1])  # '  average 1000:'
```

A path is a tuple of segments rooted at `program.body`, whose own path is `()`.
Four segment kinds occur, matching the child taxonomy the validator walks:

| Segment      | Addresses                                                     |
|--------------|---------------------------------------------------------------|
| `int i`      | The i-th element of a block's body, a `Parallel`'s body included |
| `"arm:<i>"`  | The body of a `Conditional`'s i-th arm                          |
| `"else"`     | A `Conditional`'s else body                                    |
| `"loop:<i>"` | A `Parallel`'s i-th composed loop header, itself a `Sweep`       |

`qp.format_path` renders integer segments in brackets and string segments after a
dot. A program with a `|` composition and an `else_` arm reaches all four:

```python
paths_program = qp.QProgram(label="paths")
amp = paths_program.variable("amp")
phase = paths_program.variable("phase")
with paths_program.sweep(amp, qp.Range(0.0, 1.0, 0.1)) | paths_program.sweep(phase, qp.Linspace(0.0, 6.0, 11)):
    m = paths_program.measure("readout_q0", "readout", "weights", fields=[qp.MeasurementField.STATE])
    with paths_program.if_(m.state == 1):
        paths_program.play("drive_q0", "pi_pulse")
    with paths_program.else_():
        paths_program.wait("drive_q0", 100)


def print_paths(node, prefix=()):
    for segment, child in qp.paths.iter_child_edges(node):
        path = (*prefix, segment)
        print(qp.format_path(path).ljust(22), type(child).__name__)
        print_paths(child, path)


print_paths(paths_program.body)
```

```
body[0]                Parallel
body[0].loop:0         Sweep
body[0].loop:1         Sweep
body[0][0]             Measure
body[0][1]             Conditional
body[0][1].arm:0       Block
body[0][1].arm:0[0]    Play
body[0][1].else        Block
body[0][1].else[0]     Wait
```

The `Parallel`'s two sweep headers are `loop:0` and `loop:1`, while its body
elements are plain integers, so a diagnostic on the `measure` carries `(0, 0)`.
Each arm body and the else body is a `Block` of its own, which is why a node
inside one takes two segments: the arm, then the index within it. A dangling
segment raises `KeyError` from `resolve_path`, naming the segment and the prefix
that did resolve.

## Numeric limits

A profile's `limits` dict carries numeric thresholds. The validator measures the
program and emits one `limit-exceeded` diagnostic per breach. It reads four keys:

| Limit                    | Lives on    | Compared against                                        |
|--------------------------|-------------|---------------------------------------------------------|
| `max_loop_nesting`       | platform    | Deepest repetition depth in the AST. A block counts when its `REPEATS` class attribute is `True`: `sweep`, `average`, and `parallel`, plus any vendor block that opts in. A `parallel` counts as one level however many loops it composes, because its headers advance in lockstep rather than nesting. |
| `max_parallel_loops`     | platform    | Largest number of loops composed by any `Parallel`.      |
| `max_measurements`       | platform    | Total `MeasurementOperation` count.                      |
| `min_wait_duration_ns`   | bus         | Each `Wait.duration` that is a plain `int`, against the touched bus's limits. A duration given as an `Expression` has no static value to compare and is left unchecked. |

The three platform-level limits are read from whichever half of `caps.platform`
is present, preferring `rt` when both are, since the real-time engine is
typically the more constrained one. The messages name the observed value and the
threshold:

```
[error] limit-exceeded: Program nests loops 2 deep; limit max_loop_nesting=1
[error] limit-exceeded: Program has a Parallel block with 2 concurrent loops; limit max_parallel_loops=1
[error] limit-exceeded: Program contains 2 measurements; limit max_measurements=1
[error] limit-exceeded: Wait duration 4 ns is shorter than min_wait_duration_ns=8 (at body[0][0][0])
```

A limit the platform does not declare is not checked, and a key outside those
four is ignored by the validator, so a profile may carry limits this validator
has no check for and a vendor compiler is free to read them itself.

### A live device can tighten limits

The profile's limits are *defaults*. A concrete device may know its specific
hardware is tighter and pass `limit_overrides` when materializing the descriptor:

```python
tight = qp.CompilerCapabilities.from_profile(
    "qprogram-base-v1",
    limit_overrides={"max_loop_nesting": 4},
)
tight.limits  # {'max_loop_nesting': 4}
```

Overrides are applied after the whole `extends` chain has merged, so they win
over every profile in it. Inside a platform implementation this is how
device-specific limits flow through without re-publishing the vendor profile.
`extra_predicates=(my_pred,)` does the same for a rack-level predicate that does
not belong in a vendor-shipped profile.

## Predicates and the validation context

Some constraints depend on how *several* AST nodes interact, not on any one node
in isolation. The canonical hard-error example: `Wait.duration` accepts a
`Variable`, but on some backends the wait instruction takes a fixed-step
register, so a variable bound by an arbitrary-valued source (`Values`,
`Logspace`, `File`) is illegal while the same variable bound by a `Range` is
fine. A flat token cannot express that; the answer depends on the binding loop,
which is a different node.

A predicate is any callable taking `(node, ctx)` and returning an iterable of
`Diagnostic` and `DomainConstraint` objects, in any order and any mixture.
`qp.Predicate` is the runtime-checkable protocol for that signature, and
`qp.PredicateFn` the plain callable alias, for authors who would rather not pull
`Protocol` into scope.

```python
def reject_arbitrary_wait(node, ctx):
    if not isinstance(node, qp.operations.Wait):
        return
    if not isinstance(node.duration, qp.Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield qp.Diagnostic(
            severity="error",
            code="myplat.arbitrary-wait-sweep",
            message="Wait.duration cannot be swept with arbitrary values",
            node=node,
        )
```

A node is judged against each domain half of each slot it routes to, so a
predicate carried by both halves of a profile runs once per (domain, bus) pair:
twice for a single-bus node, and twice more for every additional bus a multi-bus
operation such as `Sync` touches. Equal outputs are discarded, so the mistake
above is reported once rather than twice, but a predicate must be a cheap,
side-effect-free function of `(node, ctx)` for that to be true.

The `qp.ValidationContext` the predicate receives is a read-only view of the
program-wide facts gathered by the first walk:

| Query                           | Returns                                                                |
|---------------------------------|------------------------------------------------------------------------|
| `ctx.sweep_kind_of(var)`        | `"linear"`, `"arbitrary"`, `"averaged"`, or `None` when nothing binds `var`. `"linear"` means exactly `start + step * i`, which a sequencer can run from a loop register. No built-in source declares `"averaged"`. |
| `ctx.binding_loop_of(var)`      | The `Sweep` that binds `var`, standalone or as one of a `Parallel`'s composed headers, or `None`. This is the node a `DomainConstraint` about `var` must target. |
| `ctx.max_loop_nesting`          | Deepest repetition depth observed in the program.                       |
| `ctx.max_parallel_arity`        | Largest `len(parallel.loops)`.                                           |
| `ctx.measurement_count`         | Total number of measurement operations.                                 |
| `ctx.measurement_fields(name)`  | The `fields` tuple of the named measurement in canonical order, or `None` when no measurement carries that name. |
| `ctx.known_measurement_names()` | Every measurement name in the program, whether the author spelled it or the builder allocated it. |
| `ctx.program_buses`             | Every bus the program references. Elements may be `BusRef`s, which subclass `str`, so per-bus routing keeps its schema awareness. |

New queries are added there rather than passed around separately, so predicate
authors have one surface to read. Treat the context as immutable.

## Profile bundles

A `qp.Profile` is a named, versioned bundle of capabilities, limits, predicates,
and vendor versions, registered with `qp.register_profile`. Importing a vendor
package registers its profiles as a side effect, the same activation pattern the
serializer uses.

```python
import qprogram_myvendor  # an example vendor: registers "myvendor-default-v1"

profile = qp.resolve_profile("myvendor-default-v1")
profile.version
profile.extends  # the parent it inherits from, or None
profile.limits
```

`qp.resolve_profile(name)` raises `KeyError` for an unregistered name, and lists
what is available so a typo is easy to spot:

```
KeyError: "Unknown profile 'nope'. Available: qprogram-base-v1"
```

`register_profile` is idempotent for the *same* `Profile` object, which matters
for import-time side-effect modules that may load twice. Re-registering
different content under an existing name raises
`ValueError: Profile 'dup' is already registered with different content`.

Core QProgram ships one profile, `qprogram-base-v1`, exposed as
`qp.QPROGRAM_BASE_V1` and registered as a side effect of `import qprogram`. It is
a root profile (`extends=None`) at version `(0, 1, 0)` with no limits and no
predicates, carrying 33 tokens: the five `block.*` tokens, the eighteen `expr.*`
tokens, the two `sweep.<kind>` tokens, and one `sweep.<source>` token per
built-in source. Those are exactly the non-bus capabilities the DSL exposes,
which is why it fills a platform-level slot and nothing else. `op.set_parameter`
and `op.get_parameter` are bus-scoped ops, so their tokens belong on a bus
profile and are absent here.

Core declares every built-in sweep source, so a platform that inherits this
profile accepts them all. A platform that wants to *refuse* one, no native log
sweep for instance, declares its own platform profile rather than extending this
one. There is no `removes=` field.

```python
platform_slot = qp.CompilerCapabilities.from_profile("qprogram-base-v1")
platform_slot.supports("block.sweep")  # True
platform_slot.supports("op.play")  # False, that is a bus token
```

### Profiles can extend other profiles

A profile can inherit from another by name. `from_profile` walks the `extends`
chain root-first and merges: capabilities and predicates *accumulate* (parent to
child, with the parent's predicates ordered first), while limits and
`vendor_versions` *inherit and may be overridden* by the child. The resulting
`CompilerCapabilities.profile` and `.version` name the leaf, not the merge.

```python
strict = qp.Profile(
    name="myplat-strict-v1",
    version=(0, 1, 0),
    extends="myvendor-default-v1",  # inherits everything
    capabilities=frozenset(),  # add nothing new
    limits={"min_wait_duration_ns": 8},  # tighten one limit
    predicates=(reject_arbitrary_wait,),
)
qp.register_profile(strict)
```

This mirrors QIR's profile design and is the only composition mode the protocol
supports; arbitrary intersection of unrelated profiles is out of scope. A cycle
in the `extends` chain is detected when the chain is walked, not when the
profiles are registered, and raises a `ValueError` naming the profile the walk
revisited (`Profile inheritance cycle detected at 'myplat-strict-v1'`). A parent
that was never registered raises `KeyError` from the same walk.

## A descriptor built by hand

A platform normally builds its descriptor from registered profiles. Spelled out
by hand here so the example runs as written, with both predicates from this page
wired into the bus slot that checks them:

```python
import qprogram as qp

bus_slot = qp.CompilerCapabilities(
    profile="myplat-bus-v1",
    version=(0, 1, 0),
    capabilities=frozenset({"op.play", "op.wait", "waveform.iq", "waveform.iq_drag"}),
    limits={"min_wait_duration_ns": 4},
    predicates=(reject_arbitrary_wait, drag_sigma_is_host_only),
    vendor_versions={},
)
platform_slot = qp.CompilerCapabilities.from_profile("qprogram-base-v1")
caps = qp.PlatformCapabilities(
    bus={},
    platform=qp.BusCapabilities(rt=platform_slot, host=platform_slot),
    default_bus_profile=qp.BusCapabilities(rt=bus_slot, host=bus_slot),
)

program = qp.QProgram(label="two_sweeps")
d = program.variable("d")
sigma = program.variable("sigma")
with program.sweep(d, qp.Values([100, 200, 400])):  # arbitrary sweep
    program.wait("drive_q0", d)  # the hard predicate rejects this
with program.sweep(sigma, qp.Range(1.0, 10.0, 1.0)):  # linear sweep
    program.play("drive_q0", qp.waveforms.IQDrag(0.5, 40, sigma, 0.1))

print(qp.explain(program, caps))
```

```
plan for 'two_sweeps' — errors: 1 · warnings: 1 · info: 0
body
├─ for d in [100.0, 200.0, 400.0]:                                               [--]
│  └─ wait "drive_q0" d                                                          [--]       !! myplat.arbitrary-wait-sweep: Wait.duration cannot be swept with arbitrary values
└─ for sigma in Range(start=1.0, stop=10.0, step=1.0):                           [host]     ~ forced-host: IQDrag.sigma sweep is not real-time
   └─ play "drive_q0" IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1)  [rt|host]
```

The first sweep is unrunnable: the predicate's `Diagnostic` empties the wait's
domain set, and an op-child that can run nowhere empties its parent's too,
silently, because the child's own diagnostic already explains it. The second
sweep runs, host-side, and the `play` inside it stays real-time-capable. Only the
loop's iteration mechanism moved.

## Quick reference

| You want to ...                                              | Use                                                       |
|--------------------------------------------------------------|-----------------------------------------------------------|
| Validate a program against a platform                         | `qp.validate(program, platform.capabilities)`             |
| Ask a live platform handle instead                             | `platform.validate(program)`, `platform.plan(program)`    |
| Print the plan as a tree                                      | `qp.explain(program, caps)`, `platform.explain(program)`  |
| Try something against the in-tree software platform            | `qp.reference_capabilities()`                             |
| Ask "is this token supported in this slot?"                   | `caps.platform.rt.supports(token)`, `caps.for_bus(bus).rt.supports(token)` |
| Ask which domains a slot has an engine for                     | `caps.for_bus(bus).supported_domains()`                   |
| Materialize capabilities from a registered profile             | `qp.CompilerCapabilities.from_profile(name)`              |
| Tighten one limit for a specific device                       | `from_profile(name, limit_overrides={...})`               |
| Add a one-off predicate without editing the profile           | `from_profile(name, extra_predicates=(my_pred,))`         |
| Look a profile up, or find out what is registered              | `qp.resolve_profile(name)`                                |
| Inspect what a node needs                                      | `node.required_capabilities()`                            |
| Look up the canonical token for a waveform value                | `qp.protocol.waveform_token(wf)`                          |
| Find a diagnostic's node, or its `.qp` line                     | `qp.resolve_path(program, diag.path)`, `qp.loads(text).source_map[diag.path]` |

## See also

- [Building a vendor extension](../developer/vendor-extensions.md): how vendors
  ship their own profile.
- [Capability protocol internals](../developer/capability-protocol.md): the
  design, and how to add tokens, predicates, and profiles.
- [Errors](../reference/errors.md): the platform-side exception families a
  backend raises when validation reports problems.
- [API reference](../reference/api-qprogram.md#capability-protocol):
  auto-generated reference for the capability-protocol types.
