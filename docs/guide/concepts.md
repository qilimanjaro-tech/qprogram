# Core ideas

QProgram is a fluent builder for a small AST. Everything else in the library
reads that one tree: `qp.dumps` writes it out, `qp.validate` classifies its
nodes against a platform, `qp.optimize` rewrites it, and a platform's `execute`
interprets it.

## The shape of a program

A `QProgram` owns a `label` and an optional `description`, a body, a list of
declared variables, and optionally a `BusSchema`.

The body is the root `Block`: every operation and sub-block you append lands in
it, in order. `variables` hands back a fresh list of the placeholders you
declared, in declaration order. `buses` is recomputed on every access by walking
the body.

```python
import qprogram as qp

program = qp.QProgram(label="rabi")
program.body  # Block, read-only
program.variables  # list[Variable], in declaration order
program.buses  # set[str], recomputed from the body on each access
```

`QProgram.buses` returns `body.buses()`, which unions each child's `buses()`,
and an operation's `buses()` reads the attributes its class lists in
`BUS_ATTRS`: `("bus",)` for most operations, `("targets",)` for `Sync`, and
empty for `Call`, which overrides `buses()` instead of reading the list. Two
things follow for a program that uses fragments. A bus named only inside a
fragment body stays invisible until `program.expand()` has substituted the call
arguments, and a `Call` reports every string-valued argument bound at its site,
because a `Parameter` is untyped and a string argument may be a bus, a waveform
alias, or neither. The set is therefore an over-approximation while calls are
unexpanded.

Calling something like `program.play(...)` does not run hardware. It appends a
typed `Play` node to the currently active block.

## Blocks and operations

Every node in the AST is one of two things. Operations are the leaves: `Play`,
`Measure`, `Wait`, `Sync`, `SetFrequency`, `SetGain`, `SetOffset`, `SetPhase`,
`ResetPhase`, `SetParameter`, `GetParameter`, and `Call`. Blocks are the
containers: `Block` (a plain grouping with no extra semantics), `Sweep`,
`Average`, `Parallel`, and `Conditional`.

Every block carries an `elements` list of children, operations or nested blocks.
The property returns the block's own list rather than a copy, and `append` is
the sanctioned way to extend it. Reading a program back is a matter of indexing
into that list, as here for a program that plays one pulse and then averages a
gain sweep:

```python
[type(el).__name__ for el in program.body.elements]
# ['Play', 'Average']
[type(el).__name__ for el in program.body.elements[1].elements]
# ['Sweep']
[type(el).__name__ for el in program.body.elements[1].elements[0].elements]
# ['SetGain', 'Play', 'Measure']
```

Two blocks keep children somewhere other than `elements`. A `Conditional` holds
`arms`, a list of `(condition, body)` pairs in source order, plus an optional
`else_body`; there is no shared body to append to, so `Conditional.append`
raises `ValidationError` and the arms are populated through the builder methods.
A `Parallel` keeps its composed loop headers on `loops` and only the shared body
in `elements`, which is why it occupies one repetition level rather than one per
composed loop.

Blocks that re-run their body set the class attribute `REPEATS` to `True`:
`Sweep`, `Average` (averaging is repetition), and `Parallel`. A plain `Block` and
a `Conditional` leave it `False`, since branching selects a body rather than
iterating over one. Validation reads `REPEATS` to compute loop nesting depth, so
a vendor block that repeats is counted against a platform's limit without any
change to the core.

## Walking the tree

`Block.walk()` yields the block itself first, then every descendant in
pre-order: depth first, children in declaration order. `Operation.walk()` yields
just that operation, so a caller can walk any node without first testing its
type.

The two blocks whose children live outside `elements` extend the walk. A
`Conditional` yields itself, then each arm body's nodes in source order, then
the `else` body's; the arm conditions are `Expression`s rather than AST nodes,
so they are not yielded and code that needs them reads `arms`. A `Parallel`
yields itself, then each composed `Sweep` header with its own descendants, then
the shared body, so a consumer meets the loops that bind the variables before
the operations that read them.

```python
program = qp.QProgram()
handle = program.measure("readout_q0", "readout", "weights")
with program.if_(handle.state == 1):
    program.play("drive_q0", "pi_pulse")
with program.else_():
    program.wait("drive_q0", 40)

[type(node).__name__ for node in program.body.walk()]
# ['Block', 'Measure', 'Conditional', 'Block', 'Play', 'Block', 'Wait']
```

The first `Block` in that list is the body itself; the other two are the arm
bodies of the conditional.

`Block.buses()`, `Block.waveforms()`, and `Block.variables()` aggregate the same
subtree into a set. Three blocks add what `elements` cannot reach:
`Sweep.variables()` adds the variable the loop binds, `Conditional.variables()`
adds the variables read by the arm conditions, and `Parallel.variables()` adds
the variables its loop headers bind.

## Structural equality

Operations, blocks, and waveforms all compare the same way. The two objects must
be of exactly the same class, since the check is `type(self) is type(other)` and
a subclass therefore never equals its base. Then every entry of `vars()` must
match, private attributes included: equality is over the whole instance
`__dict__`, not a curated subset, so a block compares its `_elements` list and a
`Sweep` also compares its `variable` and `source`. The per-value verdict comes
from `ast_eq`, which recurses through `list`, `dict`, and `numpy.ndarray` and
defers to the value's own `==` for everything else.

That delegation is what makes whole-tree comparison work. A `Variable` compares
by its `id` string, so a variable's currently assigned value never enters the
comparison and the body of a program loaded back from `.qp` compares equal to
the body that was written. A `Constant` compares by value and a `BusRef`
compares as the `str` it subclasses, so a schema-backed reference equals the raw
bus name it resolves to.
A `MeasurementHandle` compares by name, which means two auto-named measurements
on the same bus are not equal: their handles are `m0` and `m1`. An array only
ever compares equal to another array, so a list of samples and the equivalent
`ndarray` stay distinct.

Hashing walks the same attributes through `ast_hash` and combines the class name
with the sorted `(key, hash)` pairs, but it is not an exact mirror of equality.
An array hashes by `(shape, value.tobytes())`, which is dtype-sensitive where
`ast_eq` compares contents only, so two nodes that differ only in a sample
array's dtype compare equal yet land in different buckets of a `dict` or `set`.
Both `__eq__` and `__hash__` read live attributes, so a node
used as a dictionary key must not be mutated afterwards; `QProgram.rebind` and
`with_waveforms` rewrite a `deepcopy` for that reason.

`QProgram` itself defines no `__eq__`, so two programs compare by identity.
Compare `a.body == b.body` for the tree, and the label and description
separately.

```python
def build():
    program = qp.QProgram()
    t = program.variable("t")
    with program.sweep(t, qp.Range(0, 100, 10)):
        program.wait("drive_q0", t)
    return program


a, b = build(), build()
a.body == b.body  # True
a == b  # False: QProgram has no __eq__
a.variables[0].set_value(50)
a.body == b.body  # still True: a Variable compares by id, not by value
```

## Context managers push and pop blocks

Control flow lives inside `with` blocks. Each one pushes a new block onto a
stack, lets you append children to it, then pops it on exit. The active block is
the innermost one still open, and the program body is the outermost, so an
operation appended after a `with` exits lands back in the enclosing block.

```python
with program.average(shots=1000):
    with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):
        program.set_frequency("drive_q0", freq)
        program.play("drive_q0", "pi_pulse")
        program.measure("readout_q0", "readout", "weights")
```

The block context managers (`sweep`, `average`, `block`, and the
`if_` / `elif_` / `else_` chain) are described in
[Control flow](control-flow.md).

## Real-time and host-side execution

QProgram makes no syntactic distinction between a real-time sweep and a
host-side sweep. The same `sweep` over `play` may run on the sequencer while the
same `sweep` over `set_parameter` runs as a Python loop. What decides is not the
shape of the source but a computation `qp.validate` performs against the
platform's capability declaration.

Every node reports the capability tokens it needs, in isolation, from
`required_capabilities()`. The tokens are instance-aware, so what a node asks for
depends on the arguments it was built with:

```python
program = qp.QProgram()
t = program.variable("t")
with program.sweep(t, qp.Range(0, 100, 10)):
    program.play("drive_q0", qp.waveforms.IQDrag(0.5, 40, 8, 0.1))
    program.wait("drive_q0", t)

sweep = program.body.elements[0]
sorted(sweep.required_capabilities())
# ['block.sweep', 'sweep.linear', 'sweep.range']
sorted(sweep.elements[0].required_capabilities())
# ['op.play', 'waveform.iq', 'waveform.iq_drag']
sorted(sweep.elements[1].required_capabilities())
# ['expr.variable', 'op.wait']
```

A platform declares which of those tokens it carries per slot, a slot being a
`(bus, domain)` pair with the domains real-time (`rt`) and host-side (`host`).
Validation routes each node to a slot, blocks and bus-less operations to the
platform-wide slot and bus-touching operations to the slot of each bus they
name, then checks the node's tokens there. An operation's domains are the halves
of its slot that carry every token it asked for and whose predicates found
nothing wrong with it. A block's are its own allowance intersected with the
consensus of its immediate operation children, which is why a `sweep` holding
one host-side-only operation is host-side as a whole. When a block that could
have run in real time is pulled to the host that way, the operations inside it
still run in real time; what moves to the host is the block's iteration, one
real-time shot dispatched per point.

The validator never raises. `qp.validate` returns the `Diagnostic`s together
with an `ExecutionPlan` mapping each visited node to the domains it can run in,
and leaves the reaction to the caller: `ReferencePlatform.execute` raises
`UnsupportedOperationError` on any diagnostic of severity `"error"`, re-emits
warnings through `warnings.warn` as `ExecutionWarning`, and drops info-level
ones.

Because the domains come from the platform's declaration and not from the source
text, the same `sweep` in the same `.qp` file can run real-time on one backend
and host-side on another.
[Capabilities, diagnostics, and profiles](capabilities.md) has the routing
table, the classification rules in full, the ten diagnostic codes, the numeric
limits, and the predicate protocol.

## Numbers, variables, and expressions

The same operation often accepts a number, a `Variable`, or an `Expression`:

```python
t = program.variable("t")
freq = program.variable("freq")

program.wait("drive_q0", 100)  # int
program.wait("drive_q0", t)  # Variable
program.wait("drive_q0", 100 + t * 2)  # Expression
program.set_frequency("drive_q0", 5e9 + freq * 1e6)  # arithmetic
```

This is how you sweep waveform parameters too. The waveform holds the variable,
and the sweep that binds it decides the values:

```python
amp = program.variable("amp")
with program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):
    program.play("drive_q0", qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))
```

An expression built this way is a tree of `Expression` nodes, and its shape is
what produces the `expr.*` tokens the validator checks against `caps.platform`.
See [Variables and expressions](variables.md) for the operators, the math
functions, and how binding works at run time.

## Buses are strings, with optional metadata

Every operation targets a bus by name. You can use a plain string
(`"drive_q0"`) or a `BusRef` that comes from a `BusSchema`. The AST stores
exactly the same thing in both cases, because `BusRef` subclasses `str`. The
schema-backed form additionally carries `element`, `idx`, `kind`, `channel`,
`acquires`, and its producing `schema` as attributes, which is what lets the
builder reject a wrong target at the call site rather than at run time.

```python
schema = qp.BusSchema.transmon()
q = schema.q

program.play(q[0].drive, qp.waveforms.IQDrag(0.5, 40, 8, 0.1))  # OK
program.play(q[0].drive, qp.waveforms.Square(0.5, 100))  # ValidationError
program.measure(q[0].drive, "readout", "weights")  # ValidationError
```

The second call fails because `q[0].drive` has `channel="IQ"` and `Square` is a
single-channel `Waveform`:

```
Bus 'q0/drive' is an IQ channel but received a single-channel Waveform (Square).
Use an IQWaveform (e.g. IQPair, IQDrag) instead.
```

The third fails because `q[0].drive` has `acquires=False`, so it has no ADC to
measure with. Routing uses the same metadata: `caps.for_bus(q[0].drive)` looks
up `caps.bus[("q", "drive")]` and falls back to `caps.default_bus_profile` when
there is no entry, while a plain string always takes the default profile. See
[Buses and schemas](buses.md) for the built-in schemas and the naming
conventions.

## Waveforms are pure data

`Waveform` instances describe an envelope. They compare and hash structurally,
they can carry `Variable`s as parameters, and they only get evaluated to a
sample array when something asks for `.envelope()`.

```python
g = qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8)
g.envelope()  # UnassignedVariableError before amp is bound
amp.set_value(0.7)
g.envelope()  # numpy array of 40 float64 samples, peak 0.6986
```

The peak sample falls short of the requested amplitude because the Gaussian is
centered in the sample window and an even sample count straddles the center
rather than landing on it.

A program can carry waveforms inline or by string alias. Inline is concrete; the
alias contributes a `waveform.alias` token instead of a per-class one and gets
resolved later via `with_waveforms`, usually from calibration data the platform
owns.

## Measurements return handles

`program.measure(...)` returns a `MeasurementHandle`. Its name survives `.qp`
round-trips and identifies the record in the result object after execution. When
you do not pass a name, one is allocated: a `BusRef` gives the bus path followed
by `/m` and a per-bus counter (`q0/readout/m0`, `q0/readout/m1`, ...), while
raw-string buses share one global `m0`, `m1`, ... counter. The counters are
derived from the AST on each call rather than stored on the program, which keeps
`deepcopy`, `with_waveforms`, and `.qp` round-trips free of hidden state at the
cost of one walk per measurement.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
m1 = program.measure(q[0].readout, "readout", "weights")

# After running ...
data0 = result.get(m0)
data1 = result.get(m1)
```

`program.measurement_handles()` returns the same handle instances the AST holds,
in declaration order, which is how a conditional reading `m0.state` sees the
value the runtime wrote. [Measurements and results](measurements.md) covers
naming rules, the `fields` argument, and access patterns.

## Related pages

- [Operations](operations.md) for the signature and semantics of each leaf.
- [.qp file format](../reference/qp-format.md) for how the tree is written to
  disk.
- [API reference](../reference/api-qprogram.md), generated from the docstrings.
