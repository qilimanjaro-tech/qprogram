# Capabilities, diagnostics, and profiles

A QProgram describes *what* you want to happen. A platform is what actually
runs it. Different platforms support different subsets of the language: one
might cap loop nesting at four levels, another might accept arbitrary numpy
sweeps only at waveform parameters but not at `Wait.duration`, another might
not implement a particular vendor operation.

The **capability protocol** is how a platform declares which features it
supports, and how QProgram catches programs that exceed the declaration
*before* anything reaches the sequencer. You can also call it yourself, at
any time, to ask "will this run?".

## The big idea

Three orthogonal axes describe what a platform supports:

| Axis                | What it is                                                          |
|---------------------|---------------------------------------------------------------------|
| **Capabilities**    | Set of dotted string *tokens* like `op.play`, `vendor.qblox.acquire`. Flags. |
| **Limits**          | Dict of numeric thresholds like `max_loop_nesting`, `min_wait_duration_ns`. |
| **Predicates**      | Callables that walk the AST and emit diagnostics for context-sensitive checks. |

A **profile** is a named bundle of those three. Vendors ship one or more
profiles; platforms expose them as `platform.capabilities`. You validate a
program against a profile and get back a list of `Diagnostic` objects
(empty means the program is compatible).

## A first validation

```python
import numpy as np
import qprogram as qp
import qprogram_qblox      # registers the qblox-default-v1 profile on import

caps = qp.CompilerCapabilities.from_profile("qblox-default-v1")

program = qp.QProgram()
freq = program.variable("freq")
with program.average(1000), program.for_loop(freq, 5e9, 6e9, 1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")

qp.validate(program, caps)
# []   — empty list means the program is supported
```

If you have a live platform handle, the same call lives on it:

```python
platform.validate(program)        # uses platform.capabilities
```

## Diagnostics

`Diagnostic` is a frozen dataclass that describes one issue. Its fields:

| Field         | Meaning                                                            |
|---------------|--------------------------------------------------------------------|
| `severity`    | Always `"error"` today. `"warning"` is reserved for the future.    |
| `code`        | Short machine-readable string (`missing-capability`, `limit-exceeded`, vendor codes prefixed by the vendor name). |
| `message`     | Human-readable explanation.                                        |
| `node`        | The offending `Operation` / `Block`, when one is available.        |
| `capability`  | The missing token, when applicable.                                |
| `limit`       | A `(name, observed_value)` tuple, when a numeric limit fired.      |

A diagnostic prints as `[error] missing-capability: …`:

```python
program = qp.QProgram()
program.play("drive_q0", "pi_pulse")

empty_caps = qp.CompilerCapabilities(
    profile="demo",
    version=(0, 0, 1),
    capabilities=frozenset(),   # nothing supported
    limits={},
    predicates=(),
    vendor_versions={},
)

for d in qp.validate(program, empty_caps):
    print(d)
# [error] missing-capability: 'Play' requires capability 'op.play' which is not supported by profile 'demo'
# [error] missing-capability: 'Play' requires capability 'waveform.alias' which is not supported by profile 'demo'
```

`validate` does **not** raise. It returns the whole list so you can decide
how to react. A platform's `execute()` typically calls `validate` first and
raises `UnsupportedOperationError` if the list is non-empty.

## Capability tokens

Tokens are flat dotted strings. They group by prefix:

| Prefix                 | Examples                                                            |
|------------------------|---------------------------------------------------------------------|
| `op.<name>`            | `op.play`, `op.measure`, `op.set_frequency`, `op.wait`, `op.sync`   |
| `block.<name>`         | `block.for_loop`, `block.loop`, `block.average`, `block.parallel`   |
| `waveform.<kind>`      | `waveform.single`, `waveform.iq`, `waveform.alias`                  |
| `waveform.<class>`     | `waveform.square`, `waveform.iq_drag`, `waveform.iq_pair`, ...      |
| `sweep.<shape>`        | `sweep.linear` (from `for_loop`), `sweep.arbitrary` (from `loop`)   |
| `expr.<kind>`          | `expr.constant`, `expr.variable`, `expr.binary_op`, `expr.math.sin` |
| `measure.returns.<t>`  | `measure.returns.iq`, `measure.returns.raw`, `measure.returns.state` |
| `vendor.<name>.<op>`   | `vendor.qblox.acquire`, `vendor.qblox.active_reset`, ...            |

Every `Operation` and `Block` declares the tokens *it* needs through
`required_capabilities()`. The set is **instance-aware**: it depends on the
op's data, not just its class.

```python
from qprogram.operations.play import Play
from qprogram.waveforms import IQDrag, Square

Play("drive_q0", Square(0.5, 100)).required_capabilities()
# {'op.play', 'waveform.single', 'waveform.square'}

Play("drive_q0", IQDrag(0.5, 40, 2.5, 0.1)).required_capabilities()
# {'op.play', 'waveform.iq', 'waveform.iq_drag'}

Play("drive_q0", "pi_pulse").required_capabilities()
# {'op.play', 'waveform.alias'}
```

The validator walks the AST once and unions per-node sets. A token missing
from the platform's capability set produces one `missing-capability`
diagnostic per missing token.

## Numeric limits

A profile's `limits` dict carries numeric thresholds. The validator
measures the program (max loop nesting, total measurements, etc.) and emits
a `limit-exceeded` diagnostic when one fires.

The built-in limit keys the validator understands:

| Limit                    | Compared against                                        |
|--------------------------|---------------------------------------------------------|
| `max_loop_nesting`       | Deepest nested-loop depth in the AST.                    |
| `max_parallel_loops`     | Largest number of loops composed by any `Parallel`.      |
| `max_measurements`       | Total `MeasurementOperation` count.                      |
| `min_wait_duration_ns`   | Each constant-valued `Wait.duration`.                    |

Profiles may declare additional limit keys; the validator silently ignores
any key it does not recognise. This lets future versions of a vendor
profile carry forward-looking limits without breaking older validator
versions.

```python
caps = qp.CompilerCapabilities.from_profile("qblox-default-v1")

p = qp.QProgram()
p.wait("drive_q0", 2)         # profile sets min_wait_duration_ns=4

for d in qp.validate(p, caps):
    print(d)
# [error] limit-exceeded: Wait duration 2 ns is shorter than min_wait_duration_ns=4
```

### A live device can tighten limits

The profile's limits are *defaults*. A concrete device may know its
specific hardware is tighter and pass `limit_overrides` when constructing
the descriptor:

```python
tight = qp.CompilerCapabilities.from_profile(
    "qblox-default-v1",
    limit_overrides={"max_loop_nesting": 1},
)
```

Inside a platform implementation, this is how device-specific limits flow
through without re-publishing the vendor profile.

## Predicates: the data-flow escape hatch

Some constraints depend on how *several* AST nodes interact, not on any one
node in isolation. The canonical example: `Wait.duration` accepts a
`Variable`, but on qblox the wait instruction takes a fixed-step register,
so a variable bound by an arbitrary-array `loop` is illegal — while the
same variable bound by a `for_loop` is fine. A flat token can't express
this; the answer depends on the binding loop, which is a different node.

Profiles can carry predicates for these cases:

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

The predicate gets the AST node plus a `ValidationContext` that exposes
cross-op data-flow facts:

| Query                          | Returns                                                                |
|--------------------------------|------------------------------------------------------------------------|
| `ctx.sweep_kind_of(var)`       | `"linear"`, `"arbitrary"`, `"averaged"`, or `None`                      |
| `ctx.binding_loop_of(var)`     | The `Block` that binds `var`, or `None`                                 |
| `ctx.max_loop_nesting`         | Deepest nesting observed in the program                                 |
| `ctx.max_parallel_arity`       | Largest `len(parallel.loops)`                                            |
| `ctx.measurement_count`        | Total number of measurement operations                                  |

`qblox-default-v1` ships exactly the predicate above, so the motivating
case is already covered when you import `qprogram_qblox`:

```python
program = qp.QProgram()
d = program.variable("d")
with program.loop(d, np.array([100, 200, 400])):     # arbitrary sweep
    program.wait("drive_q0", d)

for diag in qp.validate(program, caps):
    print(diag)
# [error] qblox.arbitrary-wait-sweep: Variable 'd' is swept with arbitrary
# values and used at Wait.duration, which qblox does not support …
```

Switch to a `for_loop` and the same program validates clean:

```python
program = qp.QProgram()
d = program.variable("d")
with program.for_loop(d, 100, 500, 100):              # linear sweep
    program.wait("drive_q0", d)

qp.validate(program, caps)
# []
```

## Profile bundles

A `Profile` is a named, versioned bundle of capabilities, limits,
predicates, and vendor versions. Importing a vendor package registers its
profile as a side effect — exactly the same activation pattern the
serializer uses.

```python
import qprogram_qblox            # registers "qblox-default-v1"
from qprogram_qblox.profiles import QBLOX_DEFAULT_V1

QBLOX_DEFAULT_V1.name             # "qblox-default-v1"
QBLOX_DEFAULT_V1.version          # (0, 1, 0)
len(QBLOX_DEFAULT_V1.capabilities)  # 44
QBLOX_DEFAULT_V1.limits
# {'max_loop_nesting': 8, 'max_parallel_loops': 4,
#  'min_wait_duration_ns': 4, 'max_measurements': 1024}
```

`CompilerCapabilities.from_profile(name)` materialises a descriptor from a
registered profile, walking the `extends` chain (if any) and merging
parent capabilities, limits, and predicates.

### Profiles can extend other profiles

A profile can inherit from another by name. Capabilities and predicates
*accumulate* (parent → child union); limits *inherit and may be overridden*
(child wins). This mirrors QIR's profile design and is the only
composition mode the protocol supports — arbitrary intersection of
unrelated profiles is intentionally out of scope.

```python
strict = qp.Profile(
    name="myplat-strict-v1",
    version=(0, 1, 0),
    extends="qblox-default-v1",     # inherits everything
    capabilities=frozenset(),        # add nothing new
    limits={"max_measurements": 16}, # tighten one limit
    predicates=(my_extra_predicate,),
)
qp.register_profile(strict)
```

Cycles in the `extends` chain are detected at resolution time and raise
`ValueError`.

## Putting it together

```python
import numpy as np
import qprogram as qp
import qprogram_qblox

caps = qp.CompilerCapabilities.from_profile("qblox-default-v1")

# A program that hits both an operation issue and the data-flow predicate
p = qp.QProgram()
d = p.variable("d")
with p.loop(d, np.array([100, 200, 400])):
    p.wait("drive_q0", d)          # the predicate will reject this
    p.wait("drive_q0", 2)          # limit-exceeded: shorter than min wait

for diag in qp.validate(p, caps):
    print(diag)
# [error] qblox.arbitrary-wait-sweep: Variable 'd' is swept with arbitrary
# values and used at Wait.duration ...
# [error] limit-exceeded: Wait duration 2 ns is shorter than min_wait_duration_ns=4
```

## Quick reference

| You want to ...                                              | Use                                                       |
|--------------------------------------------------------------|-----------------------------------------------------------|
| Validate a program against a platform                         | `qp.validate(program, platform.capabilities)`             |
| Ask "is this token supported?"                                | `caps.supports(token)`                                    |
| Materialise capabilities from a registered profile             | `qp.CompilerCapabilities.from_profile(name)`              |
| Tighten one limit for a specific device                       | `from_profile(name, limit_overrides={...})`               |
| Add a one-off predicate without editing the profile           | `from_profile(name, extra_predicates=(my_pred,))`         |
| Inspect what an op needs                                       | `op.required_capabilities()`                              |
| Look up the canonical token for a waveform class               | `qp.protocol.waveform_token(wf)`                          |

## See also

- [Building a vendor extension](../developer/vendor-extensions.md) — how vendors ship their own profile.
- [Capability protocol internals](../developer/capability-protocol.md) — the design and how to add tokens, predicates, and profiles.
- [Errors](../reference/errors.md) — the platform-side exception families a backend raises when validation reports problems.
- [API reference](../reference/api-qprogram.md#capability-protocol) — auto-generated reference for `CompilerCapabilities`, `Diagnostic`, `Profile`, `ValidationContext`.
