# Getting started

This page goes from an empty environment to a program that runs and returns
labeled arrays. QProgram runs on Python 3.11 through 3.14, which is the range
the test matrix covers.

## Install

```bash
pip install qprogram
```

`numpy` (2.1 or newer) and `xarray` (2026.4.0 or newer) are the only runtime
dependencies. Two extras add optional pieces:

```bash
pip install "qprogram[viz]"   # matplotlib >= 3.10.9
pip install "qprogram[lsp]"   # pygls >= 2, < 3
```

The `viz` extra is what `Waveform.plot()` and `IQWaveform.plot()` need, and
`lsp` is what `python -m qprogram.lsp serve` needs. Both packages are imported
inside the call that uses them, so a missing extra raises
`ModuleNotFoundError` at that call rather than breaking `import qprogram`; the
language server catches that error and re-raises it naming the extra to
install, while `plot()` lets Python's own message through. The other two
language-server front-ends, `python -m qprogram.lsp check` and
`python -m qprogram.lsp explain`, need no extra at all: they run the parser
and validator the base install already carries, which is why an editor
integration can spawn them directly.

The base install covers the AST, expressions, sweep sources, waveforms, bus
schemas, serialization, validation, and the reference platform.
Vendor-specific operations come from separate packages that follow the protocol
described in [Building a vendor extension](developer/vendor-extensions.md).

### Working on QProgram itself

The repository uses [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/qilimanjaro-tech/qprogram
cd qprogram
uv sync --all-extras
uv run pytest
```

Previewing the documentation needs the `docs` group as well. The
`--all-extras` still matters there, because mkdocstrings imports the package to
render the API reference:

```bash
uv sync --all-extras --group docs
uv run zensical serve
```

## A first program

Save this as `rabi.py` and run it with `python rabi.py`.

```python
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
gain = program.variable("gain", units="V")

with program.average(shots=1000):
    with program.sweep(gain).from_range(0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        handle = program.measure(q[0].readout, "readout", "weights")

print(qp.dumps(program))
```

`BusSchema.transmon()` declares one element kind, `q`, with an IQ `drive` bus
and an IQ `readout` bus that acquires. `q[0].drive` is a `BusRef`, a `str`
subclass whose value is the resolved bus name (`"q0/drive"` under the default
`{element}{index}/{kind}` naming) and which also carries the element, index,
and kind it was resolved from, so a later `rebind` can re-resolve it against
another schema. `program.measure` returns a `MeasurementHandle`; keep it, since
that is how you address the measurement's data after a run.

No platform is involved yet. The output is the program in `.qp` form:

```
#!QProgram 1.0

metadata:
  label: "rabi"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var gain units="V"

  average 1000:
    for gain in Range(start=0.0, stop=1.0, step=0.01):
      set_gain q[0].drive gain
      play q[0].drive "pi_pulse"
      sync
      measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

A `Range` holds `round((stop - start) / step) + 1` points and lands on `stop`
only when `step` divides `stop - start` evenly, as it does here: 101 points
from `0.0` to `1.0`. Reach for `qp.Linspace` when the count matters more than
the spacing. The `average 1000` block re-runs its body 1000 times and
contributes no result dimension of its own. The measurement name was allocated
from the bus path because the call passed no `name=`, and it is written into the
file, so `q0/readout/m0` still addresses the same measurement after a reload.
[The .qp file format](reference/qp-format.md) has the grammar.

## Save and reload

```python
qp.save(program, "rabi.qp")
reloaded = qp.load("rabi.qp")

assert reloaded.body == program.body
assert qp.dumps(reloaded) == qp.dumps(program)
```

`dumps` and `loads` take and return a string; `save` and `load` take a path and
go through the same writer and parser, always in UTF-8 regardless of locale.
Blocks and operations compare structurally, so the two bodies are equal even
though every node in `reloaded` is a new object. The writer is deterministic
too, so two dumps of the same program are byte-identical and a diff between two
files shows only what changed.

One thing does change across the round trip. `program.schema` was the
`TransmonSchema` that `BusSchema.transmon()` returned; the parser rebuilds a
plain `BusSchema` from the `schema:` section, because the file records the
elements and their buses rather than which constructor produced them.
`reloaded.schema.q[0].drive` still resolves to `"q0/drive"` at runtime, through
`BusSchema.__getattr__`, but a type checker no longer knows that `q` exists.
Measurement handles keep their names, and `reloaded.measurement_handles()`
returns them in declaration order.

## Resolving waveform names

The program above names its waveforms (`"pi_pulse"`, `"readout"`, `"weights"`)
instead of spelling them out. The concrete pulses are calibration data, which
changes far more often than the experiment does, so they live outside the
program and are attached before execution:

```python
resolved = program.with_waveforms(
    {
        "pi_pulse": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
        "weights": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)),
    }
)
```

`with_waveforms` deep-copies the program and resolves the names in the copy, so
`program` keeps its aliases and every node, variable, and handle in `resolved`
is a distinct object. Structural equality bridges the gap: a
`MeasurementHandle` compares equal by name, so the `handle` you kept from
building `program` still addresses the right record in a result produced from
`resolved`. A name with no entry in the mapping stays a string, with no error,
and an already-concrete waveform passes through untouched. Each replacement
re-runs the channel-type check, so an IQ pulse aimed at a single-channel bus
raises `ValidationError` here rather than in a vendor compiler later.

A plain mapping resolves on every bus. Pass a `qp.WaveformLibrary` when one
name has to mean different pulses on different buses: it keys entries at three
tiers, `q[0].drive` exactly, the `q[*].drive` family, or globally, and takes
the most specific match for the bus being resolved. The library is not part of
a `.qp` file, and the aliases do not survive resolution: `qp.dumps(resolved)`
writes the pulses inline as `IQDrag(...)` and `IQPair(...)` constructor calls.
The alias form is therefore the one to keep under version control, with the
library saved separately as `.wfl` through `WaveformLibrary.save`.

## Run it

QProgram never talks to instruments. A platform does, through
[`PlatformProtocol`](reference/api-qprogram.md#qprogram.PlatformProtocol). The
package ships one, `ReferencePlatform`, which is a pure-Python interpreter, and
`qp.simulate` wraps it for the one-off case:

```python
result = qp.simulate(resolved)

data = result.get(handle)
print(data.dims, data.shape)  # ('gain', 'IQ') (101, 2)
print(data.coords["gain"].values[:3])  # [0.   0.01 0.02]
```

`simulate` builds a throwaway `ReferencePlatform`, expands any fragment calls,
validates the program against that platform's capabilities, and interprets it.
An error-severity `Diagnostic` raises `UnsupportedOperationError` listing every
error; a warning is re-emitted through `warnings.warn` as `ExecutionWarning`
and does not stop the run; an info-severity one is dropped.

What the interpreter models is control flow and bookkeeping. A sweep binds its
variable once per iteration and gives every measurement inside it one result
dimension, named after the variable's id and carrying the sweep values as
coordinates; `average` re-runs its body and divides the accumulated sums by the
per-point shot count; a conditional evaluates its condition against the state
already written onto a measurement handle; `set_parameter` and `get_parameter`
read and write a flat store keyed `"bus.parameter"`. What it does not model is
physics or timing: `play`, `wait`, `sync`, and the `set_*` operations evaluate
their expressions and then do nothing, so pulse shape, duration, and ordering
never reach the numbers. The shape of the result is real; the values come from
a measurement model.

That model is consulted once per measurement per shot. The default,
`qp.MockMeasurementModel()`, responds `0j` with no noise and keeps every shot
in the ground state, so a first run returns zeros of the right shape. Give it a
response function to get a curve:

```python
import numpy as np
import qprogram as qp

model = qp.MockMeasurementModel(
    response=lambda bus, env: np.sin(np.pi * env["gain"]) ** 2 + 0j,
    noise=0.02,
    seed=7,
)
result = qp.simulate(resolved, model=model)
```

`env` holds the bound loop variables by id plus the parameter store keyed
`"bus.parameter"`, so a model can respond to whatever the program set. All
randomness comes from one generator seeded by `seed`, so the same program and
seed give the same numbers.

A hardware platform is a drop-in for the same call:
`platform.execute(resolved)` returns a `QProgramResult` of the same shape.
[Running programs](guide/execution.md) covers platforms and models in full, and
[Measurements and results](guide/measurements.md) the result contract.

## Reading a result

`QProgramResult.get` returns one `xarray.DataArray`, and takes the measurement
three ways:

```python
data = result.get(handle)  # by handle
same = result.get("q0/readout/m0")  # by name
also = result.get(0)  # by position in declaration order
```

A handle is the spelling to prefer, because it says what it means and survives
reordering. A name is what you have after a `.qp` round trip, where
`reloaded.measurement_handles()` hands back handles that compare equal to the
originals. A position is sugar; passing `bus=` narrows the candidates before
the handle, name, or position is matched.

The `field=` argument picks which measurement field to return and defaults to
`qp.MeasurementField.IQ`, matching the default of `measure(..., fields=)`. A
field the measurement never requested raises rather than substituting another
one, so `result.get(handle, field=qp.MeasurementField.STATE)` on this program
reports:

```
KeyError: "Measurement 'q0/readout/m0' has no field 'state'; available: iq"
```

The `iq` array carries a trailing `"IQ"` dimension with coordinates `["I",
"Q"]`, so `data.sel(IQ="I")` is the in-phase component and `data.values` is the
underlying `numpy` array.

To inspect a program without running it, `qp.validate(program, caps)` returns
the diagnostics and the `ExecutionPlan`, and `qp.explain(program, caps)`
renders that plan as a tree with a domain column per node;
`qp.reference_capabilities()` is the capability descriptor to pass for the
reference platform. Two smaller tools need no program at all:
`Expression.evaluate_or_raise()` reduces an expression to a number in pure
Python once its variables have values, and `Waveform.envelope()` renders a
shape to samples.

## Related pages

- [Core ideas](guide/concepts.md) covers the AST, blocks, operations, and the
  real-time versus host-side boundary.
- [Buses and schemas](guide/buses.md) explains typed bus references and what
  the schema catches that a raw string does not.
