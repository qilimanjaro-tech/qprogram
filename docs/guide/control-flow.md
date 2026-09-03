# Control flow

Control flow is built from context managers. Each `with` block pushes a
container onto the program's block stack, and every operation appended while it
is open lands in that container. `program.body` is the root container, and
leaving a `with` block pops the stack back to the enclosing one.

Five constructs make up the control flow: `sweep`, which is the only loop;
`average`, which repeats a body and collapses the repetitions; the `if_` /
`elif_` / `else_` chain; `block`, a grouping with no semantics of its own; and
`Parallel`, which has no method of its own and is built with `|` on two or more
sweep contexts. None of them says where the code runs. Validation derives that
from the operations inside, which
[Real-time and host-side](#real-time-and-host-side) covers.

## sweep

A `Sweep` binds a `Variable` to each value a `SweepSource` produces. What
changes between a hardware ramp, an explicit table, a log-spaced set, and a
composed pattern is the source, not the block, so there is one loop type rather
than one per shape of values.

```python
import qprogram as qp

program = qp.QProgram()
freq = program.variable("freq", units="Hz")
with program.sweep(freq).from_range(4e9, 6e9, 1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

### Naming the source

`sweep(variable, source)` binds a source object directly. `sweep(variable)`,
with the source left out, returns a source builder whose `from_*` methods
construct one. Both produce the same `Sweep` node and the same `.qp` line, so
the choice is about the call site.

```python
with program.sweep(freq).from_range(4e9, 6e9, 1e6):
    ...
with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):
    ...
```

Reach for the builder when writing a sweep out by hand: it is the shorter
spelling and needs no source class in scope. Pass the object when the source is
computed rather than written, which covers holding it in a variable, building it
in a comprehension, reading it from a scan spec, and nesting combinators deeper
than the `rotate` and `repeat` shortcuts below reach.

An omitted source is detected with a sentinel rather than `None`, so
`sweep(freq, None)`, a source that failed to be computed, is rejected instead of
quietly returning a builder: `Sweep source must be a SweepSource or a 1-D
sequence of values, got None`. A bare 1-D sequence in the source position is
accepted as shorthand for `qp.Values`.

A builder is not a context manager, because it has no values yet. Entering one
raises `ValidationError` listing the `from_*` methods and the two-argument form,
rather than sweeping nothing. Reaching for `repeat` or `rotate` on a builder
raises `AttributeError` for the same reason: those shape values that have
already been picked.

Every registered source has a builder, matched on the class name with case and
underscores ignored, so `from_iq_table` finds a source class named `IQTable`.
The five scalar built-ins are also written out as real methods (`from_range`,
`from_linspace`, `from_logspace`, `from_values`, `from_file`) so that editors
complete and type-check them; everything else resolves against the live
registry, the combinators and vendor-registered sources included. Registering a
source is the whole of what its builder needs. A misspelling lists what exists:

```
AttributeError: no sweep source is registered for 'from_rang'. Did you mean from_range, from_rotate, from_repeat? Available: from_concat, from_file, from_linspace, from_logspace, from_range, from_repeat, from_rotate, from_values. Add one with qp.register_sweep_source(cls) and its builder appears here too.
```

### The built-in sources

| Source | Fluent builder | Points | Kind | Token |
|---|---|---|---|---|
| `qp.Range(start, stop, step=1)` | `.from_range(start, stop, step)` | `start + step * i` | linear | `sweep.range` |
| `qp.Linspace(start, stop, num)` | `.from_linspace(start, stop, num)` | `num` points over the closed interval | linear | `sweep.linspace` |
| `qp.Logspace(start, stop, num)` | `.from_logspace(start, stop, num)` | `numpy.geomspace(start, stop, num)` | arbitrary | `sweep.logspace` |
| `qp.Values(points)` | `.from_values(points)` | the array as given | arbitrary | `sweep.values` |
| `qp.File(path)` | `.from_file(path)` | the 1-D array a `.npy` file holds | arbitrary | `sweep.file` |
| `qp.Concat(sources)` | `.from_concat(sources)` | every part's points, in order | arbitrary | `sweep.concat` |
| `qp.Repeat(source, times)` | `.from_repeat(source, times)`, `.repeat(times)` | the inner points tiled `times` times | arbitrary | `sweep.repeat` |
| `qp.Rotate(source, by=1)` | `.from_rotate(source, by)`, `.rotate(by)` | the inner points shifted left by `by` | arbitrary | `sweep.rotate` |

`Range` takes `step=1` by default. The ramp always begins at `start` and holds
`round((stop - start) / step) + 1` points, so it lands on `stop` only when
`step` divides `stop - start` evenly. Otherwise the last point falls short or
overshoots: `qp.Range(0, 1, 0.3)` gives `0, 0.3, 0.6, 0.9`, `qp.Range(0, 1, 0.6)`
gives `0, 0.6, 1.2`, and `qp.Range(0, 0.4, 1)` is the single point `0.0`. The
rounding is deliberate, since it absorbs the floating-point division noise in a
range like `(0.0, 1.0, 0.01)`. Use `Linspace` when the last point has to land
exactly on `stop`. A zero step raises, and so does a step pointing away from
`stop`: `Range step -0.1 moves away from stop (0.0 -> 1.0); flip the step sign
or swap the bounds`.

`Linspace` includes both ends, and `num=1` yields `[start]`. It also exposes
`step()`, which returns `(stop - start) / (num - 1)` for a compiler that wants
the ramp in start-and-step form, and `0.0` for a single point, where the spacing
is undefined.

`Logspace` takes the actual first and last values, not the exponents
`numpy.logspace` takes, so a frequency sweep reads
`qp.Logspace(1e6, 1e9, num=50)` rather than `qp.Logspace(6, 9, num=50)`. Both
bounds must be strictly positive.

`Values` accepts anything `numpy.asarray` accepts and stores it as a 1-D `float`
array; a 2-D input raises, as does an empty one. Its parameter is named `points`
so that it does not collide with the `values()` method every source implements.
`values()` hands back the stored array itself rather than a copy, so treat it as
read-only: the source's equality and hash are derived from it.

`File` stores the path, so a `.qp` file records where the points came from
instead of inlining them. Nothing is cached, because a loaded array would enter
the source's structural equality and an already-loaded instance would stop
comparing equal to a fresh one. Both `length()` and `values()` therefore read
the file, which has to be readable wherever the program is validated or run: a
missing path raises `OSError`, and a file holding an empty or multi-dimensional
array raises `ValidationError`.

The three combinators wrap other sources. A bare sequence where a source is
expected is wrapped in `Values`, so `qp.Rotate([0.0, 1.57, 3.14], by=1)` needs
no inner constructor. `Concat` takes an iterable, so a generator expression
works directly, and a single source passed where the iterable belongs raises
with `Write Concat([a, b]) or Concat(gen_expr)`. `Repeat` tiles:
`qp.Repeat(qp.Values([0, 1]), times=3)` sweeps `0, 1, 0, 1, 0, 1`, and each
repetition is a sweep point of its own with its own result entry, which is what
separates it from `average`. `Rotate` shifts left by `by`, so
`qp.Rotate(qp.Values([0, 1, 2, 3]), by=1)` sweeps `1, 2, 3, 0`; `by` may be
negative, shifting right, or larger than the point count, wrapping as
`numpy.roll` does, and the point count is unchanged either way. The
phase-cycling pattern falls out of the two together:
`qp.Concat(qp.Rotate(base, by=i) for i in range(base.length()))`.

Nesting combinators more than two deep is a signal to write a named
`SweepSource` subclass instead. A registered subclass serializes from its own
public attributes, so the `.qp` file records one constructor call carrying the
parameters that describe the pattern rather than the stack of wrappers that
builds it.

A sweep with no points never executes its body, so a built-in rejects an empty
parameterization as soon as it can see one: at construction for the sources
that carry their values, and on first read for `File`, which learns the length
only when it loads the array. Wherever `length()` returns, it returns at least
one, and `qp.sweeps.validate_source` holds a subclass to the same rule.

### What a source has to answer

Three things, all without running the program: `length()`, `values()`, and the
class-level `KIND`. `Parallel` needs the length at construction to refuse loops
that cannot advance in lockstep, and the reference executor needs it to size
every result array before the first shot. A platform reads `KIND` through
`ValidationContext.sweep_kind_of` to choose between a loop register with an
increment, a value table, and a host-side step per point. The interpreter, the
result coordinates, and `qp.optimize` all need the concrete values.

That contract is why a source cannot wrap a callable: a deferred function
answers none of the three ahead of time. Passing one says so, and says what to
do instead.

```
ValidationError: Sweep source must be a SweepSource, not a callable. A source describes its values statically (length, kind, and a serializable parameterization); a function can answer none of those before the program runs. Materialize it — Values(f(...)) — or declare a SweepSource subclass with the parameters it needs.
```

### Linear or arbitrary

`KIND` is a claim about compilability, not a description of the numbers.
`sweep.linear` means the values are exactly `start + step * i`, which is what
lets a sequencer run the loop from one register plus an increment, and `Range`
and `Linspace` are the two sources that claim it. Everything else reports
`sweep.arbitrary`, meaning a value table or a host-side step per point.

Two sources can produce identical values and still differ here.
`qp.Values([0, 1, 2])` is arbitrary even though the numbers are evenly spaced,
because nothing about the source proves that regularity to a compiler.
Combinators degrade in the same conservative direction and always report
arbitrary, `Repeat` of a linear source included: a tiled ramp is re-runnable as
a nested loop, but it is not itself `start + step * i`. Under-claiming costs a
platform one optimization; over-claiming would have it emit a single ramp for a
sweep that is not one.

Capability tokens have the same two levels. A `Sweep` requires `block.sweep`
plus the source's own token and its `sweep.<kind>`, and a combinator unions the
tokens of what it wraps, so `qp.Rotate(qp.Logspace(1e6, 1e9, 50))` needs
`block.sweep`, `sweep.rotate`, `sweep.arbitrary`, and `sweep.logspace`. A
platform that cannot generate a `Logspace` therefore also refuses a rotation of
one, instead of silently materializing the points into a table.

### Shaping a bound source

`repeat` and `rotate` are also methods on the loop context, which covers the
everyday shaping without naming a combinator class. Each wraps whatever is bound
so far, so the outermost wrapper is the last call:

```python
base = [0.0, 1.57, 3.14]
with program.sweep(freq).from_values(base).rotate(by=1).repeat(3):
    ...  # qp.Repeat(qp.Rotate(qp.Values(base), by=1), times=3)
with program.sweep(freq).from_values(base).repeat(3).rotate(by=1):
    ...  # qp.Rotate(qp.Repeat(qp.Values(base), times=3), by=1)
```

Both are pure, the way `|` is: they return a fresh context and leave the
original one usable. They shape one sweep, so calling either on a `|`
composition raises.

```
ValidationError: repeat() shapes one sweep's source, but this context already composes 2 sweeps with `|`. Call repeat() on each sweep before composing them.
```

### Writing your own source

Subclass `qp.SweepSource`, declare `KIND` and `TOKEN`, implement `length()` and
`values()`, and register the class. Its public attributes are its parameters, so
the `.qp` form is derived from the object rather than from a per-class callback.

```python
import numpy as np


@qp.register_sweep_source
class Chevron(qp.SweepSource):
    KIND = "arbitrary"
    TOKEN = "sweep.chevron"

    def __init__(self, center: float, span: float, num: int) -> None:
        self.center, self.span, self.num = center, span, num

    def length(self) -> int:
        return self.num

    def values(self) -> np.ndarray:
        return np.linspace(self.center - self.span / 2, self.center + self.span / 2, self.num)
```

`register_sweep_source` returns the class, so it works as a decorator. It keys
the registry by `__name__`, which is the constructor name on the wire, and adds
`TOKEN` to the capability registry so a profile can list it without a separate
call. Re-registering the same class is a no-op; registering a different class
under a taken name raises `ValueError`, since it would change how every existing
file parses that constructor.

The source now round-trips through `.qp` as
`Chevron(center=..., span=..., num=...)`, reports its token to the validator,
composes inside the combinators, and gets its own `from_chevron` builder, none
of which needs a change in the core. `qp.sweeps.validate_source(source)` checks
the `length()` and `values()` invariants; it materializes the values, so it
belongs in a test rather than on the hot path. For a one-off computation with no
parameters worth naming, skip the class and materialize the array:
`qp.Values(my_function(...))`.

## average

`average(shots)` repeats its body `shots` times and averages the measurement
results over the repetitions. `shots` has to be an integer of at least one, and
a `bool` is rejected even though it is an `int`: `Average shots must be an
integer >= 1, got True`.

Averaging is repetition, so the block occupies a repetition level on the
sequencer and counts toward the loop-nesting limit exactly as a sweep does.
Unlike a sweep it contributes no dimension to the results. The executor
accumulates a sum and a shot count per sweep point and divides, so `iq` and
`raw` come back as means and `state` as the excited-state population over the
shots. `qp.Repeat` is the opposite choice: it turns each repetition into a sweep
point with its own result entry. The block requires `block.average` and nothing
else.

```python
with program.average(shots=1000):
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

## block

A generic container with no semantics of its own, used to group operations or
scope a comment.

```python
with program.block():
    program.set_phase("drive_q0", 0.0)
    program.play("drive_q0", "pi_pulse")
    program.wait("drive_q0", 50)
```

It requires `block.block`, contributes no result dimension, and occupies no
repetition level. It is not inert to validation, though: the real-time and
host-side consensus is computed per block over that block's direct operation
children, so grouping operations changes which of them are compared with each
other.

## Conditionals

`if_`, `elif_`, and `else_` build a chain of arms from sequential `with` blocks,
mirroring the shape of Python's own `if` statement.

```python
schema = qp.BusSchema.transmon()
q = schema.q
program = qp.QProgram(schema=schema)

m = program.measure(
    q[0].readout,
    "readout",
    "weights",
    fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
)
with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
with program.elif_(m.state == 0):
    program.play(q[0].drive, "id_pulse")
with program.else_():
    program.sync()
```

### What a condition may reference

A condition is a single `Comparison` holding at least one measurement-state
reference and, apart from that, only `int` literals. Four spellings reach it:

- `m.state == 1` and `m.state != 0`, a measurement against an `int` literal
- `1 == m.state`, the reverse order, which builds the same node
- `m1.state == m2.state`, one measurement against another, for asking whether
  two qubits landed in the same state
- `qp.eq(m.state, 1)` and `qp.ne(m.state, 1)`, the helper forms, for building a
  condition without relying on operator overloading

`state` is the only field a condition may read. A classified scalar is the only
thing there is to branch on, so `iq` and `raw` are excluded by design rather
than by omission. Only `==` and `!=` exist, because those are the two operators
the proxy behind `handle.state` overloads: `m.state < 1` raises `TypeError` from
Python itself. Comparing against a `float` or a `bool` also raises `TypeError`,
the latter with `handle.state cannot be compared to a bool; use 0 or 1 to
compare against a classified state`.

Anything outside that shape is refused at the `if_()` or `elif_()` call, which
names what it got instead. A bare variable comparison and a logical combination
of two conditions both fail there:

```
ValidationError: if_() condition must reference at least one measurement-state ref (e.g. `handle.state`); got a comparison of Variable and Constant
ValidationError: if_() expects a Comparison condition such as `handle.state == 0` or `handle.state != 1`; got LogicalBinaryOp
```

There is no `and` or `or` of two conditions. Nest a second `if_` inside the arm
instead.

### Chain rules

`elif_` and `else_` find the open chain through pending-chain state that `if_`
records when it appends its `Conditional`, and that is cleared as soon as
anything else is appended at the same level. Each therefore has to follow the
matching `if_` or `elif_` immediately and at the same nesting level. An
operation or a `block()` in between closes the chain, and the following `elif_`
raises `elif_() must immediately follow an if_() / elif_() block at the same
nesting level; no open conditional chain`. Appends inside an arm body sit deeper
on the block stack and leave the chain open.

A chain takes at most one `else_`, which terminates it: leaving the `else_` body
clears the pending-chain state, so a following `elif_` or `else_` raises the
same no-open-chain error. Conditionals nest, and a chain written inside a loop
body is a chain at that level.

### Requesting state classification

Reading `handle.state` requires that the producing measurement asked for
classification. `measure(...)` defaults to `fields=(MeasurementField.IQ,)`, so
pass `fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE)`, or
`fields=(qp.MeasurementField.STATE,)` for state alone. Without it `qp.validate`
emits a `missing-classification` error: `Conditional references
q0/readout/m0.state, but the measurement does not request state classification
(add MeasurementField.STATE to fields=)`. In an `m1.state == m2.state`
comparison both measurements are checked. A condition naming a handle that no
measurement in the program produces gives `unknown-measurement` instead. Both
checks are profile-independent, so they run whatever the platform declares.

### The Conditional node

`Conditional` does not use the inherited `elements` list, because each arm
carries its own body and there is no shared body to put there. `arms` holds
`(condition, body)` pairs in source order, and the terminal `else` body lives on
`else_body`, which is `None` when the chain has none. `append` raises rather
than landing a node somewhere with no meaning.

```python
cond = program.body.elements[-1]
isinstance(cond, qp.blocks.Conditional)  # True
len(cond.arms)  # 2: the if_ and the elif_
[(arm[0].op, arm[0].right.value) for arm in cond.arms]  # [('==', 1), ('==', 0)]
cond.else_body  # the else_ body, or None
```

`walk()` yields the conditional, then each arm body in source order, then the
`else` body. Arm conditions are expressions rather than AST nodes, so they are
not yielded; read `arms` for those. `variables()` does include the conditions'
variables, so a branch taken on a swept threshold counts that variable as part
of the conditional even when no operation inside reads it. Branching selects a
body rather than iterating, so a conditional occupies no repetition level.

The block requires `block.conditional` plus the `expr.*` tokens of every arm
condition, which for `m.state == 1` are `expr.comparison`,
`expr.measurement_ref`, and `expr.constant`. The `else` arm has no condition and
contributes none.

### Active reset

Reset by measurement is what the construct exists for: measure the qubit, then
apply a pi-pulse only if it landed in `|1⟩`.

```python
m = program.measure(
    q[0].readout,
    "readout",
    "weights",
    fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
)
with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
```

Written this way the program names no vendor operation, so it runs anywhere
`block.conditional` and `measure.fields.state` are declared. Compared with
calling a vendor's packaged `active_reset`, the trade-off is that a platform
with a tuned reset choreography receives the general pattern rather than a
request for its own.

## Parallel loops with `|`

`|` on two or more sweep contexts composes them into a `Parallel` block that
advances the loops in lockstep over one shared body. That is how a program
sweeps coupled parameters along a single axis instead of over their cross
product.

```python
gain = program.variable("gain")

with program.sweep(freq, qp.Linspace(4e9, 6e9, 101)) | program.sweep(gain, qp.Linspace(0.0, 1.0, 101)):
    program.set_frequency("drive_q0", freq)
    program.set_gain("drive_q0", gain)
    program.play("drive_q0", "pi_pulse")
```

Every composed loop must report the same number of iterations. Sources answer
that statically, so the check happens when the `with` block opens rather than at
run time, and the error names the counts it found. Composing
`qp.Range(4e9, 6e9, 1e6)` with `qp.Range(0.0, 1.0, 0.01)` is the easy mistake:
the two read as a matched pair but hold 2001 and 101 points.

```
ValidationError: parallel loops must have the same number of iterations to advance in lockstep; got Sweep('freq'): 2001, Sweep('gain'): 101
```

Kinds may differ. A linear ramp composes with an explicit table, as long as the
table holds the same number of points:

```python
with program.sweep(freq).from_linspace(4e9, 6e9, 41) | program.sweep(gain).from_values(measured_gains):
    program.play("drive_q0", qp.waveforms.Gaussian(amplitude=gain, duration=40, sigma=8))
```

Extra pipes chain more than two. `__or__` is pure: it returns a fresh context
carrying the concatenated list and touches the program only on entry, so a list
of sweeps can be folded programmatically.

```python
import functools
import operator

specs = [(freq, qp.Linspace(4e9, 6e9, 41)), (gain, qp.Linspace(0.0, 1.0, 41))]
composed = functools.reduce(operator.or_, [program.sweep(v, s) for v, s in specs])
with composed:
    program.play("drive_q0", "pi_pulse")
```

The composed headers live on `loops`, not among `elements`, which holds the
shared body. `walk()` yields the block, then each header with its descendants,
then the body, so a consumer meets the loops that bind the variables before the
operations that read them, and `variables()` unions the headers' variables back
in, since the inherited walk over the body alone would miss them.

In the result `DataArray` a parallel composition is one dimension, named by
joining the variable ids with `|` (`"freq|gain"`), and each variable contributes
its own coordinate array on that shared dimension. `plot` reads the first two of
them on an axis and a twin axis opposite it, in the order the name gives — see
[Plotting results](plotting.md#two-variables-on-one-axis).

`Parallel` has no context-manager method of its own. Constructing one directly,
as an analyzer or a code generator might, is `qp.blocks.Parallel(loops=[...])`
with at least two `qp.blocks.Sweep` instances: fewer raises `Parallel requires
at least two loops, got 1`.

## Nesting

Blocks nest to any depth, and the innermost open one receives whatever is
appended next.

```python
delay = program.variable("delay")
freqs = qp.Linspace(4e9, 6e9, 41)
gains = qp.Linspace(0.0, 1.0, 41)

with program.average(shots=1000):  # level 1
    with program.sweep(freq, freqs) | program.sweep(gain, gains):  # level 2
        with program.block():  # no level
            with program.sweep(delay, qp.Range(0, 200, 4)):  # level 3
                program.set_frequency("drive_q0", freq)
                program.set_gain("drive_q0", gain)
                program.wait("drive_q0", delay)
                program.play("drive_q0", "pi_pulse")
                program.measure("readout_q0", "readout", "weights")
```

What a platform limit counts is repetition levels, not blocks. `Sweep`,
`Parallel`, and `Average` each declare `REPEATS = True` and contribute one
level, a `Parallel` one in total rather than one per composed loop, because its
headers advance together instead of nesting. A `Conditional` and a plain `block`
contribute none. Validation reads that flag rather than testing concrete
classes, so a vendor block that repeats its body is counted correctly by
declaring the flag too. The deepest count wrapping any leaf is
`max_loop_nesting`, which is 3 in the program above: the average, the parallel
composition, and the inner sweep.

When the platform declares a `max_loop_nesting` limit and the program exceeds
it, validation returns a `limit-exceeded` error, `Program nests loops 4 deep;
limit max_loop_nesting=3`. `max_parallel_loops` is checked the same way against
the widest `Parallel` in the program.

## Wire form

A sweep is a `for` header: `for <var> in <Source>(param=value, ...):`, with
every source parameter written as a keyword so that positional ordering cannot
drift. `Values` is the one special case, rendered as the bracket literal
`[0.1, 0.2, 0.3]` and never truncated, since the literal has to reload to
exactly the same sweep. A `Parallel` joins its headers with a pipe, repeating the
`for` keyword for each. `average <shots>:` and `block:` are keyword-led like any
registered block, and a `Conditional` writes one header per arm, `if <cond>:`,
`elif <cond>:`, `else:`, with the condition's outer parentheses dropped.

The program below uses every construct on this page.

```python
schema = qp.BusSchema.transmon()
q = schema.q
program = qp.QProgram(label="control-flow-forms", schema=schema)
freq = program.variable("freq", units="Hz")
gain = program.variable("gain")
phase = program.variable("phase")

with program.average(shots=1000):
    with program.sweep(freq).from_linspace(4e9, 6e9, 101) | program.sweep(gain).from_linspace(0.0, 1.0, 101):
        program.set_frequency(q[0].drive, freq)
        program.set_gain(q[0].drive, gain)
        with program.block():
            program.play(q[0].drive, "pi_pulse")
            program.sync()
        m = program.measure(
            q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )
        with program.if_(m.state == 1):
            program.play(q[0].drive, "pi_pulse")
        with program.else_():
            program.wait(q[0].drive, 40)
with program.sweep(phase, qp.Concat(qp.Rotate([0.0, 1.57, 3.14], by=i) for i in range(3))):
    program.set_phase(q[0].drive, phase)

print(qp.dumps(program))
```

```
#!QProgram 1.0

metadata:
  label: "control-flow-forms"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var freq units="Hz"
  var gain
  var phase

  average 1000:
    for freq in Linspace(start=4000000000.0, stop=6000000000.0, num=101) | for gain in Linspace(start=0.0, stop=1.0, num=101):
      set_frequency q[0].drive freq
      set_gain q[0].drive gain
      block:
        play q[0].drive "pi_pulse"
        sync
      measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state", "iq"]
      if q0/readout/m0.state == 1:
        play q[0].drive "pi_pulse"
      else:
        wait q[0].drive 40
  for phase in Concat(sources=[Rotate(source=[0.0, 1.57, 3.14], by=0), Rotate(source=[0.0, 1.57, 3.14], by=1), Rotate(source=[0.0, 1.57, 3.14], by=2)]):
    set_phase q[0].drive phase
```

## Real-time and host-side

QProgram has no real-time loop keyword. The same `sweep` may compile to the
sequencer in one program and be stepped from Python in another, and validation
decides per node. An operation's domain is the set of slots, `rt` and `host`, of
its routed bus that declare the operation's tokens. A block takes the consensus
of its own operation children, and operation children in different domains at
one level are a `mixed-domain` error. Domain constraints contributed by platform
predicates subtract from the block they target, a real-time-only block may not
contain a host-side block child (`host-in-rt`) while the reverse is always
allowed, and a block that could have run real-time but ends up host-side carries
one `forced-host` warning naming the reason.
[Capabilities, diagnostics, and profiles](capabilities.md) has the routing rules
in full.

The reference platform declares the bus-scoped parameter operations,
`set_parameter` and `get_parameter`, in the `host` half of each bus slot only,
and carries a predicate that excludes `rt` from the loop binding a variable fed
to `set_parameter`. Real platforms are wired the same way, which is what makes
plans and `forced-host` warnings against the reference platform mean something.
A program that sweeps a slow-control knob outside a pulse loop classifies like
this:

```python
platform = qp.ReferencePlatform(schema=schema)
program = qp.QProgram(schema=schema)
freq = program.variable("freq")
power = program.variable("power")

with program.average(shots=1000):
    with program.sweep(power).from_range(0.0, 10.0, 5.0):
        program.set_parameter(q[0].drive, "attenuation", power)
        with program.sweep(freq).from_linspace(4e9, 6e9, 3):
            program.set_frequency(q[0].drive, freq)
            program.play(q[0].drive, "pi_pulse")
            program.measure(q[0].readout, "readout", "weights")

print(qp.explain(program, platform.capabilities))
```

```
plan — errors: 0 · warnings: 1 · info: 0
body
└─ average 1000:                                                              [host]     ~ forced-host: contains host-side-only sub-block 'Sweep' (parameter 'attenuation' is swept via set_parameter (host-side dispatch per iteration))
   └─ for power in Range(start=0.0, stop=10.0, step=5.0):                     [host]
      ├─ set_parameter q[0].drive "attenuation" power                         [host]
      └─ for freq in Linspace(start=4000000000.0, stop=6000000000.0, num=3):  [rt|host]
         ├─ set_frequency q[0].drive freq                                     [rt|host]
         ├─ play q[0].drive "pi_pulse"                                        [rt|host]
         └─ measure q[0].readout "readout" "weights" name="q0/readout/m0"     [rt|host]
```

The shot loop is host-side here as well, and not because of anything inside it:
a host-side-only sub-block forces its parent, which is what the warning reports.
The inner frequency sweep keeps both domains, so a compiler is free to run it on
the sequencer.

## Measurement names inside loops

Measurement names are allocated when `measure` is called, not per iteration, and
the counter is derived from the AST on each call rather than stored on the
program. A `measure` inside a `sweep` therefore has exactly one name however
many times the loop runs. Two `measure` calls on the same bus in one loop body
get distinct names, `q0/readout/m0` and `q0/readout/m1`, and the result array
carries both. See [Measurements and results](measurements.md).

## Related pages

- [Operations](operations.md) for the operations that go inside these blocks
- [Variables and expressions](variables.md) for what a swept variable can feed
- [The `.qp` format](../reference/qp-format.md) for the grammar behind the wire
  forms above
