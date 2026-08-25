# Core ideas

QProgram is a fluent builder for a small AST. Once you have the picture below
in your head, most of the rest follows.

## The shape of a program

A `QProgram` owns three things:

1. **Metadata**: a `label` and an optional `description`.
2. **A body**: a single root `Block` that contains every operation and
   sub-block, in order.
3. **A variable list**: the symbolic placeholders you declared, in the order
   you declared them.

```python
program = qp.QProgram(label="rabi")
program.body  # Block, read-only
program.variables  # list[Variable], read-only
program.buses  # set[str], computed from the body
```

Calling something like `program.play(...)` does not run hardware. It appends a
typed `Play` node to the currently active block.

## Blocks and operations

Every node in the AST is one of two things.

- **Operations** are the leaves. `Play`, `Measure`, `Wait`, `Sync`,
  `SetFrequency`, `SetParameter`, and so on.
- **Blocks** are the containers. `Block` (generic), `Sweep`, `Average`,
  `Parallel`, and `Conditional`.

The body of a program is always a `Block`, and every block carries an
`elements` list of children (operations or nested blocks).

```python
[type(el).__name__ for el in program.body.elements]
# ['Play', 'Average']
[type(el).__name__ for el in program.body.elements[1].elements]
# ['Sweep']
[type(el).__name__ for el in program.body.elements[1].elements[0].elements]
# ['SetGain', 'Play', 'Measure']
```

`Block.walk()` yields the block itself and then every descendant in source
order, and `Block.buses()` / `Block.waveforms()` / `Block.variables()`
aggregate references for the whole subtree. Use them when you need to inspect
or transform a program.

## Context managers push and pop blocks

Control flow lives inside `with` blocks. Each one pushes a new block onto a
stack, lets you append children to it, then pops it on exit.

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

## Real-time and host-side loops look identical

QProgram makes no syntactic distinction between a real-time sweep and a
host-side sweep. The same `sweep` over a `set_parameter` will run as a
host-side loop; the same `sweep` over a `play` may run on the sequencer.
The compiler in the platform decides which is which, based on the operations
inside the loop.

The rule of thumb:

| Inside the loop                                              | Likely execution    |
|--------------------------------------------------------------|---------------------|
| Only pulse-level operations (`play`, `set_frequency`, ...)   | Real-time           |
| Any of `set_parameter`, `get_parameter`                      | Host-side           |

The same `sweep` in the same `.qp` file can therefore run real-time on one
backend and host-side on another. You describe what; the platform decides how.

## Three kinds of "value"

The same operation often accepts a number, a `Variable`, or an
`Expression`:

```python
program.wait("drive_q0", 100)  # int
program.wait("drive_q0", t)  # Variable
program.wait("drive_q0", 100 + t * 2)  # Expression
program.set_frequency("drive_q0", 5e9 + freq * 1e6)  # arithmetic
```

This is how you sweep waveform parameters too:

```python
from qprogram.waveforms import Gaussian

amp = program.variable("amp")
with program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, sigma=8))
```

See [Variables and expressions](variables.md) for the full story.

## Buses are strings, with optional metadata

Every operation targets a bus by name. You can use a plain string
(`"drive_q0"`) or a `BusRef` that comes from a `BusSchema`. The AST stores
exactly the same thing in both cases (a `str`); the schema-backed form just
carries extra metadata so the program can validate channel types and
acquisition support before runtime.

```python
from qprogram.buses import BusSchema
from qprogram.waveforms import IQDrag, Square

schema = BusSchema.transmon()
q = schema.q

program.play(q[0].drive, IQDrag(0.5, 40, 8, 0.1))  # OK
program.play(q[0].drive, Square(0.5, 100))  # ValidationError
program.measure(q[0].drive, "readout", "weights")  # ValidationError
```

See [Buses and schemas](buses.md) for details.

## Waveforms are pure data

`Waveform` instances describe an envelope. They are equal by structure, they
can carry `Variable`s as parameters, and they only get evaluated to a sample
array when something asks for `.envelope()`.

```python
g = Gaussian(amplitude=amp, duration=40, sigma=8)
g.envelope()  # UnassignedVariableError before amp is bound
amp.set_value(0.7)
g.envelope()  # numpy array, peak ≈ 0.7
```

A program can carry waveforms inline or by string alias. Inline is concrete;
the alias gets resolved later via `with_waveforms`, usually from calibration
data the platform owns.

## Platforms declare what they support

QProgram describes *what* you want to happen. The platform decides *how* to
run it, and different platforms support different subsets of the language.
QProgram captures the difference through a **capability protocol**.

Each `Operation` and `Block` knows the *capability tokens* it needs
(instance-aware: a `Play(IQDrag(...))` needs different tokens than a
`Play(Square(...))`). A platform exposes a `PlatformCapabilities`
descriptor with per-bus profiles (each split into rt + host halves) and a
platform-wide slot for block and expression tokens. `qp.validate(program,
caps)` walks the AST and returns a tuple of `(diagnostics, plan)`. The
diagnostic list is empty when the program is supported with no
forced-host fallback events, and the plan maps each AST node to its
allowed execution domains.

```python
platform = qp.ReferencePlatform()  # or any other PlatformProtocol implementation
caps = platform.capabilities
diagnostics, plan = qp.validate(program, caps)
for d in diagnostics:
    print(d)
```

See [Capabilities, diagnostics, and profiles](capabilities.md) for the
full story.

## Measurements return handles

`program.measure(...)` returns a `MeasurementHandle`. The handle has a name
(`"q0/readout/m0"`, `"q0/readout/m1"`, ...) that survives `.qp` round-trips and
shows up in the result object after execution.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
m1 = program.measure(q[0].readout, "readout", "weights")

# After running ...
data0 = result.get(m0)
data1 = result.get(m1)
```

[Measurements and results](measurements.md) covers naming rules, the
`fields` argument, and access patterns.

## Four ways to read these docs

- **By task.** Each guide page solves a specific need.
- **By layer.** Start with this page, then [Operations](operations.md), then
  [Control flow](control-flow.md), then [Measurements](measurements.md).
- **By file format.** Read [.qp file format](../reference/qp-format.md) once
  to see how everything pins down on disk.
- **By API.** The [API reference](../reference/api-qprogram.md) is generated
  from docstrings and stays in sync with the code.
