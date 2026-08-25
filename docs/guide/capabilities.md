# Capabilities, diagnostics, and profiles

A QProgram describes *what* you want to happen. A platform is what actually runs
it. Different platforms support different subsets of the language: one might cap
loop nesting at four levels, another might accept arbitrary numpy sweeps only at
waveform parameters but not at `Wait.duration`, another might not implement a
particular vendor operation. Within a single platform, a
logical bus may be wired to a different physical instrument than its siblings
(drive on a fast waveform generator, flux on a slow DAC), and an operation may
run as a real-time hardware loop on one path or as host-side (software-timed)
per-shot orchestration on another.

The **capability protocol** is how a platform declares which features it
supports per bus and per execution domain, and how QProgram catches programs
that exceed the declaration *before* anything reaches the sequencer. You can
also call it yourself, at any time, to ask "will this run, and how?".

## The big idea

A platform's capability surface has two grains: **per-bus** and
**platform-wide**. The per-bus grain captures the fact that drive and readout
buses may live on different hardware than flux buses. The platform-wide grain
captures features that don't belong to any single bus: control-flow blocks,
expression node kinds, and sweep shapes.

Both grains split into a **rt** and **host** half. ``rt`` describes what runs in
real-time on the hardware sequencer; ``host`` describes what runs as
per-iteration host-side dispatch on the lab server. The validator picks the
domain for each AST node and yields an `ExecutionPlan` mapping every block /
operation to its allowed domain set.

Inside each (bus, domain) slot live the three orthogonal capability axes:

| Axis                | What it is                                                          |
|---------------------|---------------------------------------------------------------------|
| **Capabilities**    | Set of dotted string *tokens* like `op.play`, `vendor.<name>.acquire`. Flags. |
| **Limits**          | Dict of numeric thresholds like `max_loop_nesting`, `min_wait_duration_ns`. |
| **Predicates**      | Callables that walk the AST and emit `Diagnostic` (hard error) or `DomainConstraint` (soft domain restriction). |

A **profile** is a named bundle of those three axes. Profiles are
domain-agnostic; a platform decides which profile fills each (bus, domain) slot.
Core qprogram ships `qprogram-base-v1`, a platform-level base bundle of block,
sweep, and expression tokens that vendors extend. It is registered as a side effect
of `import qprogram`.

## A first validation

```python
import qprogram as qp
import qprogram_myvendor  # a vendor package: registers myvendor-default-v1 on import

caps = qp.PlatformCapabilities(
    bus={
        ("q", "drive"): qp.BusCapabilities(
            rt=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
            host=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        ),
        ("q", "readout"): qp.BusCapabilities(
            rt=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
            host=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        ),
    },
    platform=qp.BusCapabilities(
        rt=qp.CompilerCapabilities.from_profile("qprogram-base-v1"),
        host=qp.CompilerCapabilities.from_profile("qprogram-base-v1"),
    ),
    default_bus_profile=qp.BusCapabilities(
        rt=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        host=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
    ),
)

program = qp.QProgram()
freq = program.variable("freq")
with program.average(1000), program.sweep(freq, qp.Range(5e9, 6e9, 1e6)):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")

diagnostics, plan = qp.validate(program, caps)
# diagnostics: [] when the program is supported with no fallback events
# plan: maps each AST node to its frozenset of allowed domains
```

The plan is a `Mapping`, but it is keyed by node **identity**, not by structural
equality: two `play "drive_q0" "pi_pulse"` operations compare equal yet get one
entry each, so a compiler can give the same pulse different treatment at
different call sites.

If you have a live platform handle, the same call lives on it:

```python
platform.validate(program)  # returns list[Diagnostic]
platform.plan(program)  # returns the ExecutionPlan
```

## Routing: which slot is checked?

The validator decides where each AST node's required tokens are checked based on
what the node touches:

| Node                                                       | Routes to                                                |
|------------------------------------------------------------|----------------------------------------------------------|
| Bus-touching op with a schema-bound `BusRef` (e.g. `schema.q[0].drive`) | `caps.bus[(bus.element, bus.kind)]`, or `caps.default_bus_profile` if no entry |
| Bus-touching op with a raw-string bus                       | `caps.default_bus_profile`                               |
| Multi-bus op (e.g. `Sync(targets=[a, b])`)                 | Each bus's slot, intersected across them                 |
| Broadcast op with no bus (`Sync(targets=None)`)             | **Every** bus in the program, intersected across them, since it means "sync everything". `caps.default_bus_profile` only when the program touches no bus at all |
| Bus-less op (empty `BUS_ATTRS`, e.g. a vendor alias-addressed op) | `caps.platform`                                    |
| Any block (`sweep`, `average`, `parallel`, `conditional`, plain `block`) | `caps.platform`                                          |

Within the routed slot, the node's required tokens split: tokens in the `expr.*`
namespace check against `caps.platform` (they describe Python AST node kinds the
compiler accepts, not anything bus-specific), while every other token checks
against the primary slot.

## Diagnostics

`Diagnostic` is a frozen dataclass that describes one issue. Its fields:

| Field         | Meaning                                                            |
|---------------|--------------------------------------------------------------------|
| `severity`    | `"error"`: the program cannot execute; `"warning"`: it runs but degraded/surprising (the `forced-host` notice); `"info"`: purely advisory. |
| `code`        | Short machine-readable string. The core set: `missing-capability`, `limit-exceeded`, `empty-domain`, `mixed-domain`, `host-in-rt`, `bad-domain-constraint`, `missing-classification`, `unknown-measurement`, `forced-host`, `reorderable-averaging`. Vendor predicates prefix their own codes with the vendor name. |
| `message`     | Human-readable explanation.                                        |
| `node`        | The offending `Operation` / `Block`, when one is available.        |
| `path`        | Structural address of `node` under the program body (`body[1][0].arm:0[2]` shape). Resolve with `qp.resolve_path`, or map to a `.qp` line via `loads(dumps(p)).source_map[path]`. |
| `capability`  | The missing token, when applicable.                                |
| `limit`       | A `(name, observed_value)` tuple, when a numeric limit fired.      |
| `domain`      | The domain the node ended up in, on `forced-host` warnings.        |

A diagnostic prints as `[error] missing-capability: …`:

```python
program = qp.QProgram()
program.play("drive_q0", "pi_pulse")

empty_slot = qp.BusCapabilities(
    rt=qp.CompilerCapabilities(
        profile="no-op-v1", version=(0, 0, 1), capabilities=frozenset(), limits={}, predicates=(), vendor_versions={}
    ),
    host=None,
)
empty_caps = qp.PlatformCapabilities(bus={}, platform=empty_slot, default_bus_profile=empty_slot)

diagnostics, _ = qp.validate(program, empty_caps)
for d in diagnostics:
    print(d)
# [error] missing-capability: 'Play' requires capability 'op.play' which is not supported by 'no-op-v1' (rt) (at body[0])
# [error] missing-capability: 'Play' requires capability 'waveform.alias' which is not supported by 'no-op-v1' (rt) (at body[0])
```

`validate` does **not** raise. It returns the diagnostic list and the execution
plan so you can decide how to react. A platform's `execute()` typically calls
`validate` first and raises `UnsupportedOperationError` if any
`severity="error"` diagnostic is present; `severity="warning"` diagnostics are
surfaced prominently without raising, and `severity="info"` diagnostics are
passed through as advisory output.

When at least one domain supports a node, the predicate diagnostics from the
other domain are *suppressed*, because the fallback worked. You only see
diagnostics when no domain can run a node.

## Capability tokens

Tokens are flat dotted strings. They group by prefix, and the prefix determines
whether the token lives on a bus profile, the platform profile, or (for
`expr.*`) is checked against platform regardless of routing:

| Prefix                 | Examples                                                            | Lives on        |
|------------------------|---------------------------------------------------------------------|-----------------|
| `op.<name>`            | `op.play`, `op.measure`, `op.set_frequency`, `op.wait`, `op.sync`, `op.set_parameter`, `op.get_parameter` | bus |
| `block.<name>`         | `block.block`, `block.sweep`, `block.average`, `block.parallel`, `block.conditional` | platform |
| `sweep.<kind>`         | `sweep.linear`, `sweep.arbitrary`: the bound source's `KIND`    | platform        |
| `sweep.<source>`       | `sweep.range`, `sweep.values`, `sweep.logspace`, `sweep.repeat`, ...: one per source class | platform |
| `waveform.<kind>`      | `waveform.single`, `waveform.iq`, `waveform.alias`                  | bus             |
| `waveform.<class>`     | `waveform.square`, `waveform.iq_drag`, `waveform.iq_pair`, ...      | bus             |
| `expr.<kind>`          | `expr.constant`, `expr.variable`, `expr.binary_op`, `expr.math.sin` | always platform |
| `measure.fields.<f>`   | `measure.fields.iq`, `measure.fields.raw`, `measure.fields.state`   | bus            |
| `vendor.<name>.<op>`   | `vendor.myvendor.acquire`, `vendor.myvendor.active_reset`, ...      | bus (or platform for bus-less vendor ops) |

Every `Operation` and `Block` declares the tokens *it* needs through
`required_capabilities()`. The set is **instance-aware** and
**domain-agnostic**: it depends on the op's data, not just its class, and is the
same set whether the validator checks rt or host.

```python
from qprogram.operations.play import Play
from qprogram.waveforms import IQDrag, Square

Play("drive_q0", Square(0.5, 100)).required_capabilities()
# {'op.play', 'waveform.single', 'waveform.square'}

Play("drive_q0", IQDrag(0.5, 40, 8, 0.1)).required_capabilities()
# {'op.play', 'waveform.iq', 'waveform.iq_drag'}

Play("drive_q0", "pi_pulse").required_capabilities()
# {'op.play', 'waveform.alias'}
```

The validator walks the AST and unions per-node sets. A token missing from every
domain of the routed slot produces one `missing-capability` diagnostic per
missing token.

## Real-time vs host-side classification

The DSL makes no syntactic distinction between real-time and host-side
(software-timed) loops. The same `sweep` may run in either domain depending on
what's inside. The validator's classifier picks, and the answer is in the
`ExecutionPlan`.

### `DomainConstraint`: predicates that narrow the domain instead of erroring

The canonical example: a sequencer can hold a real-time loop sweeping
`IQDrag.amplitude` (a register write per iteration) but cannot recompute
`IQDrag.sigma` between iterations, because the Gaussian envelope is precomputed
at upload. Sweeping `sigma` in a real-time loop must fall back to per-iteration
host-side dispatch, and per-iteration dispatch *does* work.

Profiles express this with a `DomainConstraint`:

```python
from qprogram.operations.play import Play
from qprogram.waveforms import IQDrag
from qprogram.variable import Variable


def drag_sigma_is_host_only(node, ctx):
    if not isinstance(node, Play) or not isinstance(node.waveform, IQDrag):
        return
    if isinstance(node.waveform.sigma, Variable) and ctx.sweep_kind_of(node.waveform.sigma) is not None:
        yield qp.DomainConstraint(
            node=ctx.binding_loop_of(node.waveform.sigma),
            exclude=frozenset({"rt"}),
            reason="IQDrag.sigma sweep is not real-time",
        )
```

A constraint's `node` **must be a `Block`**: the loop whose binding is the
problem, which is what `ctx.binding_loop_of(var)` returns. Targeting the
operation instead is reported as a `bad-domain-constraint` error, because it is
the loop that has to move host-side, not the pulse.

`DomainConstraint` is a soft outcome: the block *would* be supported, except in
the listed domains. The classifier subtracts the exclusion from that block's
support set, and every ancestor inherits the result via intersection.

`Diagnostic` is reserved for hard outcomes, cases the compiler genuinely cannot
run anywhere. Predicate authors choose:

- *Hard error?* Yield a `Diagnostic`. The validator surfaces it when no domain
  can run the node.
- *Just a domain restriction?* Yield a `DomainConstraint`. Silent if some domain
  fallback works.

### `"forced-host"` warnings

When a block's support is reduced from `{rt, host}` to `{host}` (because of a
DomainConstraint somewhere in its subtree), the validator emits one
`severity="warning"` `"forced-host"` diagnostic, on the *highest* block in that
forced chain. Ancestors that are host-side-only purely because of this child are
not separately reported; the highest-block rule keeps the diagnostic output
skimmable. The message names that block's **immediate** cause: its own
`DomainConstraint` reasons if any target it, otherwise the host-side-only
sub-block it contains, with that sub-block's reasons in parentheses.

```python
program = qp.QProgram(label="rabi")
sigma = program.variable("sigma")
with program.average(100), program.sweep(sigma, qp.Range(1.0, 10.0, 1.0)):
    program.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))

diagnostics, plan = qp.validate(program, caps)
for d in diagnostics:
    print(d)
# [warning] forced-host: Block 'Average' falls back to host-side execution:
#           contains host-side-only sub-block 'Sweep' (IQDrag.sigma sweep is not
#           real-time). (at body[0])
```

The plan shows the same conclusion structurally:

```python
for node, domains in plan.items():
    print(type(node).__name__, "→", set(domains))
# Play    → {'rt', 'host'}
# Sweep   → {'host'}
# Average → {'host'}
```

## Seeing the plan: `explain()`

`platform.explain(program)`, or the functional form
`qp.explain(program, caps)`, renders the plan as a tree: every body node as its
`.qp` text, the domain set
in an aligned column, and diagnostics annotated inline (`!!` errors, `~`
warnings, `i` info; node-less diagnostics in a footer). Programs with fragment
calls are expanded first.

```python
print(qp.explain(program, caps))
# plan for 'rabi' — errors: 0 · warnings: 1 · info: 0
# body
# └─ average 100:                                            [host]     ~ forced-host: contains host-side-only sub-block 'Sweep' (IQDrag.sigma sweep is not real-time)
#    └─ for sigma in Range(start=1.0, stop=10.0, step=1.0):   [host]
#       └─ play "drive_q0" IQDrag(...)                        [rt|host]
```

### From a diagnostic to a `.qp` line

Every node-bearing diagnostic carries a structural `path`. Resolve it against
the program with `qp.resolve_path(program, diag.path)`, or map it to a line in
the serialized text: `loads()` records `program.source_map` (path → 1-based
line), and since the round-trip preserves structure, a path computed against the
built program looks up directly in the reloaded one:

```python
text = qp.dumps(program)
line = qp.loads(text).source_map[diag.path]
print(text.splitlines()[line - 1])
```

## Numeric limits

A profile's `limits` dict carries numeric thresholds. The validator measures the
program (max loop nesting, total measurements, etc.) and emits a
`limit-exceeded` diagnostic when one fires.

| Limit                    | Lives on    | Compared against                                        |
|--------------------------|-------------|---------------------------------------------------------|
| `max_loop_nesting`       | platform    | Deepest repetition depth in the AST. A block counts when its `REPEATS` class attribute is `True`: `sweep`, `average`, and `parallel`, plus any vendor block that opts in. |
| `max_parallel_loops`     | platform    | Largest number of loops composed by any `Parallel`.      |
| `max_measurements`       | platform    | Total `MeasurementOperation` count.                      |
| `min_wait_duration_ns`   | bus         | Each constant-valued `Wait.duration` (against the touched bus's limits). |

Profiles may declare additional limit keys. The validator reads only the four
keys above and ignores every other key, so a profile may carry limits this
validator does not check; a vendor compiler is free to read them itself.

### A live device can tighten limits

The profile's limits are *defaults*. A concrete device may know its specific
hardware is tighter and pass `limit_overrides` when constructing the descriptor:

```python
tight = qp.CompilerCapabilities.from_profile(
    "myvendor-default-v1",
    limit_overrides={"min_wait_duration_ns": 8},
)
```

Inside a platform implementation, this is how device-specific limits flow
through without re-publishing the vendor profile.

## Predicates: the data-flow escape hatch

Some constraints depend on how *several* AST nodes interact, not on any one node
in isolation. The canonical hard-error example: `Wait.duration` accepts a
`Variable`, but on some backends the wait instruction takes a fixed-step
register, so a variable bound by an arbitrary-valued source (`Values`,
`Logspace`, `File`) is illegal, while the same variable bound by a `Range` is
fine. A flat token can't express this; the answer depends on the binding loop,
which is a different node.

```python
def reject_arbitrary_wait(node, ctx):
    from qprogram.operations.wait import Wait
    from qprogram.variable import Variable

    if not isinstance(node, Wait) or not isinstance(node.duration, Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield qp.Diagnostic(
            severity="error",
            code="myplat.arbitrary-wait-sweep",
            message="Wait.duration cannot be swept with arbitrary values",
            node=node,
        )
```

The predicate gets the AST node plus a `ValidationContext` that exposes cross-op
data-flow facts:

| Query                          | Returns                                                                |
|--------------------------------|------------------------------------------------------------------------|
| `ctx.sweep_kind_of(var)`       | `"linear"`, `"arbitrary"`, `"averaged"`, or `None`                      |
| `ctx.binding_loop_of(var)`     | The `Block` that binds `var`, or `None`                                 |
| `ctx.max_loop_nesting`         | Deepest nesting observed in the program                                 |
| `ctx.max_parallel_arity`       | Largest `len(parallel.loops)`                                            |
| `ctx.measurement_count`        | Total number of measurement operations                                  |
| `ctx.measurement_fields(name)`  | The `fields` tuple of the named measurement, or `None`                  |
| `ctx.known_measurement_names()` | Every measurement name in the program                                   |
| `ctx.program_buses`             | Every bus the program references                                        |

## Profile bundles

A `Profile` is a named, versioned bundle of capabilities, limits, predicates,
and vendor versions. Importing a vendor package registers its profile as a side
effect, the same activation pattern the serializer uses.

```python
import qprogram_myvendor  # registers "myvendor-default-v1"
from qprogram_myvendor.profiles import MYVENDOR_DEFAULT_V1

MYVENDOR_DEFAULT_V1.name  # "myvendor-default-v1"
MYVENDOR_DEFAULT_V1.version  # (0, 1, 0)
len(MYVENDOR_DEFAULT_V1.capabilities)  # 30 or so
MYVENDOR_DEFAULT_V1.limits
# {'min_wait_duration_ns': 4}
```

Core qprogram ships `qprogram-base-v1` containing every non-bus capability the
DSL declares: block-structure tokens, expression tokens, the two sweep-kind
tokens, and one token per built-in sweep source. (`set_parameter` /
`get_parameter` are bus ops, host-side only, so their tokens live on the bus
slot, not here.) Vendor platforms typically set their platform-level slot via
`CompilerCapabilities.from_profile("qprogram-base-v1", limit_overrides=...)` and
only declare what's different.

### Profiles can extend other profiles

A profile can inherit from another by name. Capabilities and predicates
*accumulate* (parent → child union); limits *inherit and may be overridden*
(child wins). This mirrors QIR's profile design and is the only composition mode
the protocol supports; arbitrary intersection of unrelated profiles is
intentionally out of scope.

```python
strict = qp.Profile(
    name="myplat-strict-v1",
    version=(0, 1, 0),
    extends="myvendor-default-v1",  # inherits everything
    capabilities=frozenset(),  # add nothing new
    limits={"min_wait_duration_ns": 8},  # tighten one limit
    predicates=(my_extra_predicate,),
)
qp.register_profile(strict)
```

Cycles in the `extends` chain are detected at resolution time and raise
`ValueError`. There is no `removes=` field: a vendor with exotic constraints
declares a profile from scratch rather than subtracting from a parent.

## Putting it together

A platform normally builds its descriptor from registered profiles; spelled out
here so the example runs as written, with the two predicates from this page
wired into the bus slot that checks them:

```python
import qprogram as qp

bus_slot = qp.CompilerCapabilities(
    profile="myplat-bus-v1",
    version=(0, 1, 0),
    capabilities=frozenset({"op.play", "op.wait", "waveform.iq", "waveform.iq_drag"}),
    limits={},
    predicates=(reject_arbitrary_wait, drag_sigma_is_host_only),
    vendor_versions={},
)
platform_slot = qp.CompilerCapabilities.from_profile("qprogram-base-v1")
caps = qp.PlatformCapabilities(
    bus={},
    platform=qp.BusCapabilities(rt=platform_slot, host=platform_slot),
    default_bus_profile=qp.BusCapabilities(rt=bus_slot, host=bus_slot),
)

# A program that hits both an operation issue and a domain constraint
p = qp.QProgram()
d = p.variable("d")
sigma = p.variable("sigma")
with p.sweep(d, qp.Values([100, 200, 400])):  # arbitrary sweep
    p.wait("drive_q0", d)  # hard predicate rejects
with p.sweep(sigma, qp.Range(1.0, 10.0, 1.0)):  # linear sweep
    p.play("drive_q0", qp.waveforms.IQDrag(0.5, 40, sigma, 0.1))  # DomainConstraint rt→host

diagnostics, plan = qp.validate(p, caps)
for diag in diagnostics:
    print(diag)
# [error] myplat.arbitrary-wait-sweep: Wait.duration cannot be swept with
# arbitrary values (at body[0][0])
# [warning] forced-host: Block 'Sweep' falls back to host-side execution:
# IQDrag.sigma sweep is not real-time. (at body[1])
```

## Quick reference

| You want to ...                                              | Use                                                       |
|--------------------------------------------------------------|-----------------------------------------------------------|
| Validate a program against a platform                         | `qp.validate(program, platform.capabilities)`             |
| Get the execution plan                                        | `platform.plan(program)`                                  |
| Ask "is this token supported in this slot?"                   | `caps.platform.rt.supports(token)`, or `caps.for_bus(bus).rt.supports(token)` |
| Materialize capabilities from a registered profile             | `qp.CompilerCapabilities.from_profile(name)`              |
| Tighten one limit for a specific device                       | `from_profile(name, limit_overrides={...})`               |
| Add a one-off predicate without editing the profile           | `from_profile(name, extra_predicates=(my_pred,))`         |
| Inspect what an op needs                                       | `op.required_capabilities()`                              |
| Look up the canonical token for a waveform class               | `qp.protocol.waveform_token(wf)`                          |

## See also

- [Building a vendor extension](../developer/vendor-extensions.md): how vendors
  ship their own profile.
- [Capability protocol internals](../developer/capability-protocol.md): the
  design, and how to add tokens, predicates, and profiles.
- [Errors](../reference/errors.md): the platform-side exception families a
  backend raises when validation reports problems.
- [API reference](../reference/api-qprogram.md#capability-protocol):
  auto-generated reference for the capability-protocol types.
