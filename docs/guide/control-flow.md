# Control flow

Control flow in QProgram uses Python context managers. Each `with` block
pushes a new container onto the program's block stack; everything you append
inside lands in that container.

## `sweep(variable)` / `sweep(variable, source)`

The only loop. It binds `variable` to each value a **sweep source** produces;
the source decides how those values come to be.

```python
freq = program.variable("freq", units="Hz")
with program.sweep(freq).from_range(4e9, 6e9, 1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

Whether the loop runs in real-time or host-side depends on the operations
inside it. The compiler decides.

### Two ways to say which source

Leave `source` out and you get a **source builder**: its `from_*` methods pick
the values. Pass a source object and it is bound directly. Both build the same
`Sweep` node and write the same `.qp` line, so pick by which reads better at the
call site:

```python
with program.sweep(freq).from_range(4e9, 6e9, 1e6):  # fluent, nothing to import
    ...
with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):  # explicit, the source is a value
    ...
```

The fluent form is the shorter one for a sweep you are writing by hand. Reach
for the explicit form when you are *computing* the source: holding it in a
variable, building it in a comprehension, reading it from a scan spec, or
nesting combinators deeper than the shortcuts below.

Every registered source has a builder, named after its class (`Range` →
`from_range`, `IQTable` → `from_iq_table`; case and underscores are ignored).
Vendor sources included: registering one is all it takes for its `from_*` to
appear. Misspell it and the `AttributeError` lists the ones that exist.

### Sources

A source is a small value object, the sweep analogue of a waveform. Pick the
one that describes your intent; the platform can then compile a ramp as a ramp
and a table as a table:

| Source | Fluent equivalent | Kind | Use it for |
|---|---|---|---|
| `qp.Range(start, stop, step=1)` | `.from_range(start, stop, step)` | linear | a ramp when you know the spacing |
| `qp.Linspace(start, stop, num)` | `.from_linspace(start, stop, num)` | linear | a ramp when you know the point count |
| `qp.Values(points)` | `.from_values(points)` | arbitrary | an explicit list: calibrated values, a measured table |
| `qp.Logspace(start, stop, num)` | `.from_logspace(start, stop, num)` | arbitrary | log spacing between two linear bounds |
| `qp.File(path)` | `.from_file(path)` | arbitrary | a 1-D array in a `.npy` file (the path is stored, not the values) |
| `qp.Repeat(source, times)` | `.repeat(times)` | arbitrary | the inner sweep, back to back |
| `qp.Rotate(source, by)` | `.rotate(by)` | arbitrary | the inner sweep, cyclically shifted |
| `qp.Concat(sources)` | `.from_concat(sources)` | arbitrary | several sweeps end to end |

```python
with program.sweep(amp).from_linspace(0.0, 1.0, num=101):
    ...
with program.sweep(det).from_logspace(1e6, 1e9, num=50):
    ...
with program.sweep(phi).from_values(calibrated_phases):
    ...

# Combinators compose, and accept a bare sequence in place of a source:
with program.sweep(phi, qp.Concat(qp.Rotate([0.0, 1.57, 3.14], by=i) for i in range(3))):
    ...
```

`Repeat` and `Rotate` are also chainable on the loop context, which covers the
everyday shaping without naming a source class. Each wraps what is bound so
far, in call order:

```python
with program.sweep(phi).from_values(base).rotate(by=1).repeat(3):
    ...  # same as sweep(phi, qp.Repeat(qp.Rotate(qp.Values(base), by=1), times=3))
```

Both are pure: they return a fresh context and leave the original alone, just
as `|` does. They shape one sweep, so calling them on a `|` composition raises.

`Range` and `Linspace` report kind **linear**, meaning exactly
`start + step * i`, which is what lets a sequencer run them from a loop
register. Everything else is
**arbitrary**: a value table, or a host-side step per point. Note that `Values`
is arbitrary even if the numbers you pass happen to be evenly spaced, because
nothing about the source proves that regularity to a compiler. Reach for `Range`
or `Linspace` when the sweep really is a ramp.

### Writing your own source

Subclass `qp.SweepSource` with declared parameters. This is the supported way to
express a computed sweep: a source describes its values, so it can never be a
plain function:

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

    def values(self):
        return np.linspace(self.center - self.span / 2, self.center + self.span / 2, self.num)
```

It now round-trips through `.qp` as `Chevron(center=..., span=..., num=...)`,
reports its token to the validator, and composes inside the combinators, all
without touching the core. For a one-off computation, materialize
it instead: `qp.Values(my_function(...))`.

## `average(shots)`

Repeat the inner block `shots` times and average the result.

```python
with program.average(shots=1000):
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

`average` is special-cased in the result-array shape: it does not produce a
dimension on the resulting `xarray.DataArray`. The shots collapse into an
average over them instead: `iq` and `raw` come back as means, and `state`
as the excited-state population.

## `block()`

A generic container with no semantics of its own. Use it to group operations
or scope a comment.

```python
with program.block():
    program.set_phase("drive_q0", 0.0)
    program.play("drive_q0", "pi_pulse")
    program.wait("drive_q0", 50)
```

## Conditional execution: `if_` / `elif_` / `else_`

Branch on a measurement outcome. Build chains with sequential `with` blocks,
mirroring Python's own `if`/`elif`/`else` shape.

```python
from qprogram import MeasurementField as MF

m = program.measure(q[0].readout, "readout", "weights", fields=(MF.IQ, MF.STATE))

with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
with program.elif_(m.state == 0):
    program.play(q[0].drive, "id_pulse")
with program.else_():
    program.sync()
```

Three rules to keep in mind:

- **The condition is a measurement-state predicate.** The accepted
  shapes are:

    - `handle.state == 0` / `handle.state != 1`: measurement against an `int`
      literal
    - `0 == handle.state` / `1 != handle.state`: reverse order, same node
    - `m1.state == m2.state` / `m1.state != m2.state`: measurement against
      measurement, useful for "did two qubits land in the same state?"
    - `qp.eq(handle.state, 0)` / `qp.ne(handle.state, 0)`: the helper form,
      equivalent to the operator form. Use this when you want to build a
      condition programmatically without relying on operator overloading.

  Anything else, such as a bare `Variable` comparison or a logical combination
  of two conditions, raises `ValidationError` at the `if_()` call, naming the
  node kind it got instead.

- **`elif_` and `else_` must follow immediately.** Each must come
  directly after the matching `if_` / `elif_` block at the same
  indentation level. Anything appended between arms closes the chain
  and a following `elif_` / `else_` raises `ValidationError`.
- **Every referenced measurement must request state classification.**
  Referencing `handle.state` requires the measurement's `fields` to
  include `MeasurementField.STATE`. The validator emits a
  `missing-classification` diagnostic otherwise. Pass
  `fields=(MeasurementField.IQ, MeasurementField.STATE)` (or
  `fields=(MeasurementField.STATE,)` for state-only)
  when calling `measure(...)`.
  In a `m1.state == m2.state` comparison, *both* measurements must
  request classification; the validator checks each `MeasurementRef`
  it finds in the condition.

### The shape of an `if` chain

The construct builds a `Conditional` block in the AST. Each arm is a
`(condition, body_block)` tuple; the terminal `else` lives in
`else_body`:

```python
from qprogram.blocks import Conditional

cond = program.body.elements[-1]
isinstance(cond, Conditional)  # True
len(cond.arms)  # 2: the `if_` and the `elif_`
[(arm[0].op, arm[0].right.value) for arm in cond.arms]
# [('==', 1), ('==', 0)]
cond.else_body  # the `else_` body, or None when the chain has no else
```

Iterate `arms` directly when writing analyzers or compilers; the
list preserves source order.

### Active reset, portably

The motivating use case is active reset: measure, then conditionally
apply a π-pulse if the qubit landed in `|1⟩`:

```python
m = program.measure(q[0].readout, "readout", "weights", fields=(MF.IQ, MF.STATE))  # MF = MeasurementField
with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
```

Expressing this in the core language keeps the program portable: it does not
name a vendor's packaged `active_reset` operation, so it runs anywhere the
`block.conditional` and `measure.fields.state` capabilities are supported.
Platforms with an optimized active-reset choreography can still recognize the
pattern at compile time and lower it to their preferred form.

## Parallel loops with `|`

Combine multiple loops with the `|` operator to advance them in lockstep. All
loops in a parallel composition must have the same number of iterations;
mismatched lengths raise `ValidationError` when the `with` block opens.

```python
freq = program.variable("freq")
gain = program.variable("gain")

with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)) | program.sweep(gain, qp.Range(0.0, 1.0, 0.01)):
    program.set_frequency("drive_q0", freq)
    program.set_gain("drive_q0", gain)
    program.play("drive_q0", "pi_pulse")
```

Different loop kinds mix freely:

```python
from qprogram.waveforms import Gaussian

with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)) | program.sweep(amp, qp.Values(custom_values)):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, sigma=8))
```

Chain more than two with extra pipes:

```python
with a_loop | b_loop | c_loop:
    ...
```

In the result `DataArray`, parallel loops share a single dimension named
after both variables (for example `"freq|gain"`). Each variable contributes
its own coordinate array.

## Nesting

Blocks nest arbitrarily. The program's `body` is the root; everything else
is a descendant.

```python
with program.average(shots=1000):
    with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):
        with program.sweep(gain, qp.Range(0.0, 1.0, 0.01)):
            program.set_frequency("drive_q0", freq)
            program.set_gain("drive_q0", gain)
            program.play("drive_q0", "pi_pulse")
            program.sync()
            program.measure("readout_q0", "readout", "weights")
```

## Real-time vs host-side, in practice

QProgram does not have a "real-time loop" keyword. The same `sweep` may run
on the sequencer for one program and in Python for another. The deciding
factor is what is inside it.

- **Likely real-time**: only pulse-level operations (`play`, `set_frequency`,
  `set_phase`, `set_gain`, `wait`, `sync`, `measure`).
- **Always host-side**: any of `set_parameter`, `get_parameter`. These talk to
  slow-control plumbing that the compiler cannot lift into a sequencer loop.

Programs often mix the two:

```python
with program.average(shots=1000):  # real-time shot loop
    with program.sweep(power_var, qp.Range(0.0, 10.0, 0.5)):  # host-side, sets a knob
        program.set_parameter("drive_q0", "attenuation", power_var)
        with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)):  # real-time frequency sweep
            program.set_frequency("drive_q0", freq)
            program.play("drive_q0", "pi_pulse")
            program.measure("readout_q0", "readout", "weights")
```

The platform decides; you do not have to think about it twice.

## Measurement names inside loops

Measurement counters are per-bus and do not reset when you enter a loop.
A `measure` inside a `sweep` still increments the same per-bus counter
across iterations. If you have two `measure` calls on the same bus inside
the same loop, you get distinct handles (`q0/readout/m0`,
`q0/readout/m1`), and the result array has both. See
[Measurements](measurements.md).

## A note on `Parallel`

`Parallel` is not exposed as a context manager directly; you build it with
`|`. If you need to construct one programmatically, the class lives at
`qprogram.blocks.Parallel`. Its `loops` attribute holds the composed `Sweep`
headers. They live there rather than in `elements`, which holds the
shared body.
