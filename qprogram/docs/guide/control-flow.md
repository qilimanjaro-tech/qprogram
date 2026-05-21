# Control flow

Control flow in QProgram uses Python context managers. Each `with` block
pushes a new container onto the program's block stack; everything you append
inside lands in that container.

## `for_loop(variable, start, stop, step=1)`

A parametric sweep over a numeric range. The variable takes each value in
`range(start, stop, step)` during execution.

```python
freq = program.variable("freq", units="Hz")
with program.for_loop(freq, start=4e9, stop=6e9, step=1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

Whether the loop runs in hardware or software depends on the operations
inside it. The compiler decides.

## `loop(variable, values)`

Sweep over an arbitrary array. Use this when the values do not fit a
`range`:

```python
import numpy as np
amp = program.variable("amp")

with program.loop(amp, values=np.linspace(0.0, 1.0, 100)):
    program.set_gain("drive_q0", amp)
    program.play("drive_q0", "pi_pulse")
```

`values` is converted to a numpy array on construction; the AST stores it as
a single array, not a list of constants.

## `average(shots)`

Repeat the inner block `shots` times and average the result.

```python
with program.average(shots=1000):
    program.play("drive_q0", "pi_pulse")
    program.measure("readout_q0", "readout", "weights")
```

`average` is special-cased in the result-array shape: it does not produce a
dimension on the resulting `xarray.DataArray`. It collapses into a per-shot
average instead.

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

Branch on a measurement outcome. Build chains with sequential `with` blocks
— exactly mirroring Python's own `if`/`elif`/`else` shape.

```python
m = program.measure(q[0].readout, "readout", "weights", returns="iq,state")

with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
with program.elif_(m.state == 0):
    program.play(q[0].drive, "id_pulse")
with program.else_():
    program.sync()
```

Three rules to keep in mind:

- **The condition is a measurement-state predicate.** Today the
  accepted shapes are:

    - `handle.state == 0` / `handle.state != 1` — measurement vs `int` literal
    - `0 == handle.state` / `1 != handle.state` — reverse order (same node)
    - `m1.state == m2.state` / `m1.state != m2.state` — measurement vs measurement, useful for "did two qubits land in the same state?"
    - `qp.eq(handle.state, 0)` / `qp.ne(handle.state, 0)` — the helper form, equivalent to the operator form. Use this when you want to build a condition programmatically without relying on operator overloading.

  Wider shapes (bare `Variable` comparisons, logical combinations) are
  planned for a later release; the validator rejects them today with a
  clear error pointing at the rejected shape.

- **`elif_` and `else_` must follow immediately.** Each must come
  directly after the matching `if_` / `elif_` block at the same
  indentation level. Anything appended between arms closes the chain
  and a following `elif_` / `else_` raises `ValidationError`.
- **Every referenced measurement must request state classification.**
  Referencing `handle.state` requires the measurement's `returns` to
  include `"state"`. The validator emits a `missing-classification`
  diagnostic otherwise. Pass `returns="iq,state"` (or
  `returns="state"` for state-only) when calling `measure(...)`.
  In a `m1.state == m2.state` comparison, *both* measurements must
  request classification — the validator checks each `MeasurementRef`
  it finds in the condition.

### The shape of an `if` chain

The construct builds a `Conditional` block in the AST. Each arm is a
`(condition, body_block)` tuple; the terminal `else` lives in
`else_body`:

```python
cond = program.body.elements[-1]
isinstance(cond, qp.blocks.Conditional)        # True
len(cond.arms)                                  # 3 (if, elif, elif)
[(arm[0].op, arm[0].right.value) for arm in cond.arms]
# [('==', 1), ('==', 0), ('==', 2)]
cond.else_body                                  # Block | None
```

Iterate `arms` directly when writing analyzers or compilers; the
list preserves source order.

### Active reset, portably

The motivating use case is active reset — measure, then conditionally
apply a π-pulse if the qubit landed in `|1⟩`:

```python
m = program.measure(q[0].readout, "readout", "weights", returns="iq,state")
with program.if_(m.state == 1):
    program.play(q[0].drive, "pi_pulse")
```

The same intent used to require a vendor-specific
`program.<vendor>.active_reset(...)`. The conditional expresses it without
locking the program to one backend; platforms that have optimized
active-reset choreographies can still recognize the pattern at
compile time and lower it to their preferred form.

## Parallel loops with `|`

Combine multiple loops with the `|` operator to run them concurrently. All
loops in a parallel composition must have the same number of iterations.

```python
freq = program.variable("freq")
gain = program.variable("gain")

with program.for_loop(freq, 4e9, 6e9, 1e6) | program.for_loop(gain, 0.0, 1.0, 0.01):
    program.set_frequency("drive_q0", freq)
    program.set_gain("drive_q0", gain)
    program.play("drive_q0", "pi_pulse")
```

Different loop kinds mix freely:

```python
with program.for_loop(freq, 4e9, 6e9, 1e6) | program.loop(amp, custom_values):
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
    with program.for_loop(freq, 4e9, 6e9, 1e6):
        with program.for_loop(gain, 0.0, 1.0, 0.01):
            program.set_frequency("drive_q0", freq)
            program.set_gain("drive_q0", gain)
            program.play("drive_q0", "pi_pulse")
            program.sync()
            program.measure("readout_q0", "readout", "weights")
```

## Hardware vs software, in practice

QProgram does not have a "hardware loop" keyword. The same `for_loop` may run
on the sequencer for one program and in Python for another. The deciding
factor is what is inside it.

- **Likely hardware**: only pulse-level operations (`play`, `set_frequency`,
  `set_phase`, `set_gain`, `wait`, `sync`, `measure`).
- **Always software**: any of `set_parameter`, `get_parameter`,
  `set_crosstalk`. These talk to slow-control plumbing that the compiler
  cannot lift into a sequencer loop.

Programs often mix the two:

```python
with program.average(shots=1000):                          # hardware shot loop
    with program.for_loop(power_var, 0.0, 10.0, 0.5):       # software, sets a knob
        program.set_parameter("attenuator", "value", power_var)
        with program.for_loop(freq, 4e9, 6e9, 1e6):         # hardware frequency sweep
            program.set_frequency("drive_q0", freq)
            program.play("drive_q0", "pi_pulse")
            program.measure("readout_q0", "readout", "weights")
```

The platform decides; you do not have to think about it twice.

## Measurement names inside loops

Measurement counters are per-qubit and do not reset when you enter a loop.
A `measure` inside a `for_loop` still increments the same per-bus counter
across iterations. If you have two `measure` calls on the same bus inside
the same loop, you get distinct handles (`q0/readout/m0`,
`q0/readout/m1`), and the result array has both. See
[Measurements](measurements.md).

## A note on `Parallel`

`Parallel` is not exposed as a context manager directly; you build it with
`|`. If you need to construct one programmatically, the class lives at
`qprogram.blocks.Parallel`. Its `loops` attribute is the list of child loops.
