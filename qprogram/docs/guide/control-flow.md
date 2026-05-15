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
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, num_sigmas=2.5))
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
A `measure` inside a `for_loop` still increments the same `q0_m<N>` counter
across iterations. If you have two `measure` calls on the same qubit inside
the same loop, you get distinct handles (`q0_m0`, `q0_m1`), and the result
array has both. See [Measurements](measurements.md).

## A note on `Parallel`

`Parallel` is not exposed as a context manager directly; you build it with
`|`. If you need to construct one programmatically, the class lives at
`qprogram.blocks.Parallel`. Its `loops` attribute is the list of child loops.
