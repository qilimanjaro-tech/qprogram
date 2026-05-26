# Capabilities, diagnostics, and profiles

A QProgram describes *what* you want to happen. A platform is what actually runs it. Different
platforms support different subsets of the language: one might cap loop nesting at four levels,
another might accept arbitrary numpy sweeps only at waveform parameters but not at
`Wait.duration`, another might not implement a particular vendor operation. And — crucially —
within a single platform, a logical bus may be wired to a different physical instrument than its
siblings (drive on a fast waveform generator, flux on a slow DAC), and an operation may run as a
real-time hardware loop on one path or as software-dispatched per-shot orchestration on another.

The **capability protocol** is how a platform declares which features it supports per bus and per
execution domain, and how QProgram catches programs that exceed the declaration *before* anything
reaches the sequencer. You can also call it yourself, at any time, to ask "will this run, and how?".

## The big idea

A platform's capability surface has two grains: **per-bus** and **platform-wide**. The per-bus
grain captures the fact that drive and readout buses may live on different hardware than flux
buses. The platform-wide grain captures features that don't belong to any single bus: control-flow
blocks, expression node kinds, and bus-less operations like `set_parameter`.

Both grains split into a **hw** and **sw** half. ``hw`` describes what runs in real-time on the
hardware sequencer; ``sw`` describes what runs as per-iteration software dispatch on the lab
server. The validator picks the domain for each AST node and yields an `ExecutionPlan` mapping
every block / operation to its allowed domain set.

Inside each (bus, domain) slot live the three orthogonal capability axes:

| Axis                | What it is                                                          |
|---------------------|---------------------------------------------------------------------|
| **Capabilities**    | Set of dotted string *tokens* like `op.play`, `vendor.<name>.acquire`. Flags. |
| **Limits**          | Dict of numeric thresholds like `max_loop_nesting`, `min_wait_duration_ns`. |
| **Predicates**      | Callables that walk the AST and emit `Diagnostic` (hard error) or `DomainConstraint` (soft domain restriction). |

A **profile** is a named bundle of those three axes. Profiles are domain-agnostic; a platform
decides which profile fills each (bus, domain) slot. Core qprogram ships `qprogram-base-v1` — a
platform-level base bundle of block / expression / bus-less-op tokens that vendors extend.

## A first validation

```python
import numpy as np
import qprogram as qp
import qprogram_myvendor   # registers myvendor-default-v1 and qprogram-base-v1 on import

caps = qp.PlatformCapabilities(
    bus={
        ("q", "drive"):   qp.BusCapabilities(
            hw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
            sw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        ),
        ("q", "readout"): qp.BusCapabilities(
            hw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
            sw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        ),
    },
    platform=qp.BusCapabilities(
        hw=qp.CompilerCapabilities.from_profile("qprogram-base-v1"),
        sw=qp.CompilerCapabilities.from_profile("qprogram-base-v1"),
    ),
    default_bus_profile=qp.BusCapabilities(
        hw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
        sw=qp.CompilerCapabilities.from_profile("myvendor-default-v1"),
    ),
)

program = qp.QProgram()
freq = program.variable("freq")
with program.average(1000), program.for_loop(freq, 5e9, 6e9, 1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")

diagnostics, plan = qp.validate(program, caps)
# diagnostics: [] when the program is supported with no fallback events
# plan: dict mapping each AST node to its frozenset of allowed domains
```

If you have a live platform handle, the same call lives on it:

```python
platform.validate(program)   # returns list[Diagnostic]
platform.plan(program)       # returns the ExecutionPlan
```

## Routing: which slot is checked?

The validator decides where each AST node's required tokens are checked based on what the node
touches:

| Node                                                       | Routes to                                                |
|------------------------------------------------------------|----------------------------------------------------------|
| Bus-touching op with a schema-bound `BusRef` (e.g. `schema.q[0].drive`) | `caps.bus[(bus.element, bus.kind)]`, or `caps.default_bus_profile` if no entry |
| Bus-touching op with a raw-string bus                       | `caps.default_bus_profile`                               |
| Bus-touching op with no bus (e.g. `Sync(targets=None)`)     | `caps.default_bus_profile`                               |
| Multi-bus op (e.g. `Sync(targets=[a, b])`)                 | Each bus's slot — intersected across them                |
| Bus-less op (`SetParameter`, `GetParameter`, `SetCrosstalk`) | `caps.platform`                                          |
| Any block (`for_loop`, `loop`, `average`, `parallel`, `conditional`, plain `block`) | `caps.platform`                                          |

Within the routed slot, the node's required tokens split: tokens in the `expr.*` namespace check
against `caps.platform` (they describe Python AST node kinds the compiler accepts, not anything
bus-specific), while every other token checks against the primary slot.

## Diagnostics

`Diagnostic` is a frozen dataclass that describes one issue. Its fields:

| Field         | Meaning                                                            |
|---------------|--------------------------------------------------------------------|
| `severity`    | `"error"` for hard failures; `"info"` for advisory events (the `forced-software` notice). |
| `code`        | Short machine-readable string (`missing-capability`, `limit-exceeded`, `empty-domain`, `forced-software`, vendor codes prefixed by the vendor name). |
| `message`     | Human-readable explanation.                                        |
| `node`        | The offending `Operation` / `Block`, when one is available.        |
| `capability`  | The missing token, when applicable.                                |
| `limit`       | A `(name, observed_value)` tuple, when a numeric limit fired.      |
| `domain`      | The domain the node ended up in, on `forced-software` info events. |

A diagnostic prints as `[error] missing-capability: …`:

```python
program = qp.QProgram()
program.play("drive_q0", "pi_pulse")

empty_slot = qp.BusCapabilities(
    hw=qp.CompilerCapabilities(profile="demo", version=(0, 0, 1),
                                capabilities=frozenset(), limits={}, predicates=(),
                                vendor_versions={}),
    sw=None,
)
empty_caps = qp.PlatformCapabilities(bus={}, platform=empty_slot, default_bus_profile=empty_slot)

diagnostics, _ = qp.validate(program, empty_caps)
for d in diagnostics:
    print(d)
# [error] missing-capability: 'Play' requires capability 'op.play' which is not supported by profile 'demo' in domain 'hw'
# [error] missing-capability: 'Play' requires capability 'waveform.alias' which is not supported by profile 'demo' in domain 'hw'
```

`validate` does **not** raise. It returns the diagnostic list and the execution plan so you can
decide how to react. A platform's `execute()` typically calls `validate` first and raises
`UnsupportedOperationError` if any `severity="error"` diagnostic is present;
`severity="info"` diagnostics are passed through as advisory output.

When at least one domain supports a node, the predicate diagnostics from the other domain are
*suppressed* — the fallback worked. The user only sees diagnostics when no domain can run a node.

## Capability tokens

Tokens are flat dotted strings. They group by prefix, and the prefix determines whether the token
lives on a bus profile, the platform profile, or (for `expr.*`) is checked against platform
regardless of routing:

| Prefix                 | Examples                                                            | Lives on        |
|------------------------|---------------------------------------------------------------------|-----------------|
| `op.<name>` (bus)      | `op.play`, `op.measure`, `op.set_frequency`, `op.wait`, `op.sync`   | bus             |
| `op.<name>` (bus-less) | `op.set_parameter`, `op.get_parameter`, `op.set_crosstalk`          | platform        |
| `block.<name>`         | `block.for_loop`, `block.loop`, `block.average`, `block.parallel`, `block.conditional` | platform |
| `sweep.<shape>`        | `sweep.linear` (from `for_loop`), `sweep.arbitrary` (from `loop`)   | platform        |
| `waveform.<kind>`      | `waveform.single`, `waveform.iq`, `waveform.alias`                  | bus             |
| `waveform.<class>`     | `waveform.square`, `waveform.iq_drag`, `waveform.iq_pair`, ...      | bus             |
| `expr.<kind>`          | `expr.constant`, `expr.variable`, `expr.binary_op`, `expr.math.sin` | always platform |
| `measure.returns.<t>`  | `measure.returns.iq`, `measure.returns.raw`, `measure.returns.state` | bus            |
| `vendor.<name>.<op>`   | `vendor.myvendor.acquire`, `vendor.myvendor.active_reset`, ...      | bus (or platform for bus-less vendor ops) |

Every `Operation` and `Block` declares the tokens *it* needs through `required_capabilities()`.
The set is **instance-aware** and **domain-agnostic**: it depends on the op's data, not just its
class, and is the same set whether the validator checks hw or sw.

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

The validator walks the AST and unions per-node sets. A token missing from every domain of the
routed slot produces one `missing-capability` diagnostic per missing token.

## Hardware vs software classification

The DSL makes no syntactic distinction between hardware-realtime and software-dispatched loops —
the same `for_loop` may run in either domain depending on what's inside. The validator's
classifier picks, and the answer is in the `ExecutionPlan` (a dict from each node to its allowed
domain set).

### `DomainConstraint`: predicates that narrow the domain instead of erroring

The canonical example: qblox can hold a real-time loop sweeping `IQDrag.amplitude` (a register
write per iteration) but cannot recompute `IQDrag.sigma` between iterations (the gaussian
envelope is precomputed at upload). Sweeping `sigma` in a hardware loop must fall back to
per-iteration software dispatch — but per-iteration dispatch *does* work.

Profiles express this with a `DomainConstraint`:

```python
from qprogram.operations.play import Play
from qprogram.waveforms import IQDrag
from qprogram.variable import Variable

def drag_sigma_is_software_only(node, ctx):
    if not isinstance(node, Play) or not isinstance(node.waveform, IQDrag):
        return
    if isinstance(node.waveform.sigma, Variable) and ctx.sweep_kind_of(node.waveform.sigma) is not None:
        yield qp.DomainConstraint(
            node=node,
            exclude=frozenset({"hw"}),
            reason="IQDrag.sigma sweep is not real-time",
        )
```

`DomainConstraint` is a soft outcome: the node *would* be supported, except in the listed
domains. The classifier subtracts the exclusion from the node's support set, and any block
containing the node inherits the constraint via intersection.

`Diagnostic` is reserved for hard outcomes — cases the compiler genuinely cannot run anywhere.
Predicate authors choose:

- *Hard error?* Yield a `Diagnostic`. The validator surfaces it when no domain can run the node.
- *Just a domain restriction?* Yield a `DomainConstraint`. Silent if some domain fallback works.

### `"forced-software"` info events

When a block's support is reduced from `{hw, sw}` to `{sw}` (because of a DomainConstraint
somewhere in its subtree), the validator emits one `severity="info"` `"forced-software"`
diagnostic — on the *highest* block in that forced chain. Ancestors that are software-only purely
because of this child are not separately reported; the highest-block rule keeps the diagnostic
output skimmable, and the reason text walks down to the originating constraint.

```python
program = qp.QProgram()
sigma = program.variable("sigma")
with program.average(100), program.for_loop(sigma, 1.0, 10.0, 1.0):
    program.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))

diagnostics, plan = qp.validate(program, caps)
for d in diagnostics:
    print(d)
# [info] forced-software: Block 'Average' falls back to software execution;
#        a descendant excludes hardware-realtime.
```

The plan shows the same conclusion structurally:

```python
for node, domains in plan.items():
    print(type(node).__name__, "→", set(domains))
# Average → {'sw'}
# ForLoop → {'sw'}
# Play    → {'sw'}
```

## Numeric limits

A profile's `limits` dict carries numeric thresholds. The validator measures the program (max
loop nesting, total measurements, etc.) and emits a `limit-exceeded` diagnostic when one fires.

| Limit                    | Lives on    | Compared against                                        |
|--------------------------|-------------|---------------------------------------------------------|
| `max_loop_nesting`       | platform    | Deepest nested-loop depth in the AST.                    |
| `max_parallel_loops`     | platform    | Largest number of loops composed by any `Parallel`.      |
| `max_measurements`       | platform    | Total `MeasurementOperation` count.                      |
| `min_wait_duration_ns`   | bus         | Each constant-valued `Wait.duration` (against the touched bus's limits). |

Profiles may declare additional limit keys; the validator silently ignores any key it does not
recognise. This lets future versions of a vendor profile carry forward-looking limits without
breaking older validator versions.

### A live device can tighten limits

The profile's limits are *defaults*. A concrete device may know its specific hardware is tighter
and pass `limit_overrides` when constructing the descriptor:

```python
tight = qp.CompilerCapabilities.from_profile(
    "myvendor-default-v1",
    limit_overrides={"min_wait_duration_ns": 8},
)
```

Inside a platform implementation, this is how device-specific limits flow through without
re-publishing the vendor profile.

## Predicates: the data-flow escape hatch

Some constraints depend on how *several* AST nodes interact, not on any one node in isolation.
The canonical hard-error example: `Wait.duration` accepts a `Variable`, but on some backends the
wait instruction takes a fixed-step register, so a variable bound by an arbitrary-array `loop` is
illegal — while the same variable bound by a `for_loop` is fine. A flat token can't express this;
the answer depends on the binding loop, which is a different node.

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

The predicate gets the AST node plus a `ValidationContext` that exposes cross-op data-flow facts:

| Query                          | Returns                                                                |
|--------------------------------|------------------------------------------------------------------------|
| `ctx.sweep_kind_of(var)`       | `"linear"`, `"arbitrary"`, `"averaged"`, or `None`                      |
| `ctx.binding_loop_of(var)`     | The `Block` that binds `var`, or `None`                                 |
| `ctx.max_loop_nesting`         | Deepest nesting observed in the program                                 |
| `ctx.max_parallel_arity`       | Largest `len(parallel.loops)`                                            |
| `ctx.measurement_count`        | Total number of measurement operations                                  |
| `ctx.measurement_returns(name)` | The `returns` tuple of the named measurement                            |

## Profile bundles

A `Profile` is a named, versioned bundle of capabilities, limits, predicates, and vendor
versions. Importing a vendor package registers its profile as a side effect — the same activation
pattern the serializer uses.

```python
import qprogram_myvendor              # registers "myvendor-default-v1"
from qprogram_myvendor.profiles import MYVENDOR_DEFAULT_V1

MYVENDOR_DEFAULT_V1.name              # "myvendor-default-v1"
MYVENDOR_DEFAULT_V1.version           # (0, 1, 0)
len(MYVENDOR_DEFAULT_V1.capabilities) # 30 or so
MYVENDOR_DEFAULT_V1.limits
# {'min_wait_duration_ns': 4}
```

Core qprogram ships `qprogram-base-v1` containing every non-bus capability the DSL declares:
block-structure tokens, expression tokens, sweep-shape tokens, and bus-less ops
(`set_parameter` / `get_parameter` / `set_crosstalk`). Vendor platforms typically set their
platform-level slot via `CompilerCapabilities.from_profile("qprogram-base-v1", limit_overrides=...)`
and only declare what's different.

### Profiles can extend other profiles

A profile can inherit from another by name. Capabilities and predicates *accumulate* (parent →
child union); limits *inherit and may be overridden* (child wins). This mirrors QIR's profile
design and is the only composition mode the protocol supports — arbitrary intersection of
unrelated profiles is intentionally out of scope.

```python
strict = qp.Profile(
    name="myplat-strict-v1",
    version=(0, 1, 0),
    extends="myvendor-default-v1",   # inherits everything
    capabilities=frozenset(),         # add nothing new
    limits={"min_wait_duration_ns": 8},  # tighten one limit
    predicates=(my_extra_predicate,),
)
qp.register_profile(strict)
```

Cycles in the `extends` chain are detected at resolution time and raise `ValueError`. There is no
`removes=` field today; a vendor with exotic constraints builds a profile from scratch instead of
subtracting from a parent.

## Putting it together

```python
import numpy as np
import qprogram as qp
import qprogram_myvendor

caps = platform.capabilities   # built by the platform from its registered profiles

# A program that hits both an operation issue and a domain constraint
p = qp.QProgram()
d = p.variable("d")
sigma = p.variable("sigma")
with p.loop(d, np.array([100, 200, 400])):                      # arbitrary sweep
    p.wait("drive_q0", d)                                       # hard predicate rejects
with p.for_loop(sigma, 1.0, 10.0, 1.0):                         # linear sweep
    p.play("drive_q0", qp.waveforms.IQDrag(0.5, 40, sigma, 0.1))  # DomainConstraint hw→sw

diagnostics, plan = qp.validate(p, caps)
for diag in diagnostics:
    print(diag)
# [error] myvendor.arbitrary-wait-sweep: Variable 'd' is swept with arbitrary
# values and used at Wait.duration ...
# [info] forced-software: Block 'ForLoop' falls back to software execution;
# a descendant excludes hardware-realtime.
```

## Quick reference

| You want to ...                                              | Use                                                       |
|--------------------------------------------------------------|-----------------------------------------------------------|
| Validate a program against a platform                         | `qp.validate(program, platform.capabilities)`             |
| Get the execution plan                                        | `platform.plan(program)`                                  |
| Ask "is this token supported in this slot?"                   | `caps.platform.hw.supports(token)` (or `.bus[selector]`)   |
| Materialise capabilities from a registered profile             | `qp.CompilerCapabilities.from_profile(name)`              |
| Tighten one limit for a specific device                       | `from_profile(name, limit_overrides={...})`               |
| Add a one-off predicate without editing the profile           | `from_profile(name, extra_predicates=(my_pred,))`         |
| Inspect what an op needs                                       | `op.required_capabilities()`                              |
| Look up the canonical token for a waveform class               | `qp.protocol.waveform_token(wf)`                          |

## See also

- [Building a vendor extension](../developer/vendor-extensions.md) — how vendors ship their own profile.
- [Capability protocol internals](../developer/capability-protocol.md) — the design and how to add tokens, predicates, and profiles.
- [Errors](../reference/errors.md) — the platform-side exception families a backend raises when validation reports problems.
- [API reference](../reference/api-qprogram.md#capability-protocol) — auto-generated reference for the capability-protocol types.
