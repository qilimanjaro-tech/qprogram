# Measurements and results

Every `measure(...)` call (and every vendor measurement op exposed under a
`program.<vendor>.*` namespace) returns a `MeasurementHandle`. The handle is
just a stable name. It is the contract between the program description and
the result you get back after running.

## The lifecycle of a measurement

1. **Construction.** You call `measure(...)`. QProgram appends a `Measure`
   node and returns a `MeasurementHandle` holding the chosen name.
2. **Serialization.** The name lands in the `.qp` file as part of the
   `measure` line. Round-tripping through `loads`/`dumps` preserves it.
3. **Execution.** The platform tags every result it produces with the same
   name.
4. **Retrieval.** `result.get(handle)` looks the result up by name.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
# ... later ...
data = result.get(m0)  # xarray.DataArray
```

## Naming rules

If you do not pass `name=`, QProgram picks the name for you.

| Bus kind                        | Auto-name format                                              |
|---------------------------------|---------------------------------------------------------------|
| Schema-backed `q[0].readout`    | `q0/readout/m<counter>` (`q0/readout/m0`, `q0/readout/m1`, …) |
| Schema-backed tuple index       | the index flattens with `_`, so `c[0, 1]` gives `c0_1/<kind>/m<counter>` |
| Raw string `"readout_q0"`       | `m<counter>`, global across raw-string buses                  |

The prefix for schema-backed buses is the bus's full string form: what the
`BusSchema.naming` pattern renders, and what the result store reports as
`MeasurementResult.bus`. The `.qp` line is the exception: there a
schema-backed bus is written as a bus *path*, so the measurement above
serializes as `measure q[0].readout "readout" "weights" name="q0/readout/m0"`,
with the path on the left and the string form inside the name.

Each unique bus carries its own counter that always starts at 0, so a
program that measures `q[0].readout` and then `q[1].readout` produces
`q0/readout/m0` and `q1/readout/m0`, with different counters because they live
on different buses.

The counter is recomputed by walking the AST. There is no hidden counter
state on the program, so building further on a derived program
(`copy.deepcopy`, `with_waveforms`, `qp.loads(qp.dumps(...))`) picks the next
name up exactly where the original left off. A derived program does carry its
own `BusSchema` instance, and a program accepts bus references only from the
schema attached to it: reach for `derived.schema.q[0].readout` rather than a
reference built from the original schema, which raises `ValidationError`.

### Explicit names

You can pass `name=` to force a specific value:

```python
m_custom = program.measure(q[0].readout, "readout", "weights", name="t1_ref")
```

Duplicate names raise `ValidationError`.

## Recovering handles after a round-trip

`measure()` returns a handle, but the Python local that captured it is gone
after a `.qp` reload. `measurement_handles()` returns the program's
canonical handles in declaration order:

```python
program = qp.load("rabi.qp")
handles = program.measurement_handles()  # canonical instances
data = result.get(handles[0])
```

The handles returned are the **same Python instances** the AST holds
inside its measurement ops and any `MeasurementRef`s. Writing to one
via `handle._set_value(field, value)` is immediately visible everywhere
the measurement is referenced.

Handles also use structural equality, so a freshly-constructed
`MeasurementHandle("q0/readout/m0")` compares equal to the canonical one,
which is handy for result lookups by name without holding the original
variable:

```python
from qprogram import MeasurementHandle

MeasurementHandle("q0/readout/m0") == handles[0]  # True
```

## The `fields` argument

`measure` and vendor measurement ops accept a `fields` argument naming which
data the platform should produce. The values come from `qp.MeasurementField`:

| Member                   | Wire name | Meaning                                              |
|--------------------------|-----------|------------------------------------------------------|
| `MeasurementField.STATE` | `state`   | Classified outcome. Required for `handle.state`.     |
| `MeasurementField.IQ`    | `iq`      | Integrated I and Q values. The default.              |
| `MeasurementField.RAW`   | `raw`     | Raw ADC trace.                                       |

```python
from qprogram import MeasurementField as MF

program.measure(q[0].readout, "readout", "weights")  # default: (MF.IQ,)
program.measure(q[0].readout, "readout", "weights", fields=(MF.IQ, MF.RAW))
program.measure(q[0].readout, "readout", "weights", fields=["iq", "raw"])  # equivalent
```

`MeasurementField` is a `StrEnum`, so `MeasurementField.IQ == "iq"`: the
members *are* strings. The enum is there so your editor can complete the
options and a type checker can catch a bad one; plain strings work too.

Three rules worth knowing:

**It must be an iterable, never a bare string.** `fields="iq"` would iterate
to `("i", "q")`, so it raises `ValidationError`, and there is no
comma-separated spelling:

```python
program.measure(q[0].readout, "readout", "weights", fields="iq,raw")
# ValidationError: `fields` must be an iterable of MeasurementField values, not
#                  the bare string 'iq,raw' — ... Pass a tuple: fields=("iq", "raw")
```

**Order and duplicates don't matter.** The stored value is deduplicated and
sorted into canonical order (`state`, `iq`, `raw`), so two measurements asking
for the same data are the same AST node and save to the same `.qp` line:

```python
a = program.measure(q[0].readout, "readout", "weights", fields=("iq", "state"))
# stored as ("state", "iq") — as is fields=("state", "iq")
```

**A typo fails immediately.** Field names are checked against the registered
`measure.fields.*` capability tokens at the `measure(...)` call, not later
during validation:

```python
program.measure(q[0].readout, "readout", "weights", fields=("stat",))
# ValidationError: unknown measurement field(s) ['stat']. Did you mean 'state'?
#                  Known fields: ['iq', 'raw', 'state']. ...
```

That registry is also the extension point. A vendor package that calls
`register_capability_tokens("measure.fields.counts")` makes
`fields=("counts",)` legal with no change to core qprogram; vendor field names
sort after the core members. Platforms still declare which fields they
*support* through the same tokens in their bus profiles. See
[Capabilities](capabilities.md).

## The result object

`platform.execute(program)` returns a `QProgramResult`. Each measurement
inside is an `xarray.DataArray`:

```python
result = platform.execute(program)
data = result.get(m0)

data.dims  # ("freq", "gain", "IQ") for a 2D sweep
data.shape  # (2001, 101, 2)
data.coords["freq"]  # xarray coordinate, values are np.array([4e9, ...])
data.coords["IQ"]  # ["I", "Q"]
```

Three lookup forms work:

```python
result.get(m0)  # by handle (preferred)
result.get("q0/readout/m0")  # by name string
result.get(0)  # by integer position
result.get(0, bus="q0/readout")  # the same, filtered to one bus
```

`data.sel(IQ="I")` peels off the I trace; `data.sel(freq=5e9, method="nearest")`
slices into the named dimensions. Anything `xarray` does on `DataArray`s
works here.

### Picking a field

A measurement that requested several fields produces one array per field, and
they have different shapes. `field=` says which one you want: a
`MeasurementField` member or a registered field name, since the members *are*
strings:

```python
m = program.measure(q[0].readout, "readout", "weights", fields=(MF.IQ, MF.STATE, MF.RAW))
result = platform.execute(program)

result.get(m)  # dims (*sweeps, "IQ")           — iq, the default
result.get(m, field=MF.STATE)  # dims (*sweeps)                 — population under average
result.get(m, field=MF.RAW)  # dims (*sweeps, "time", "IQ")
result.get(m, field="state")  # identical to field=MF.STATE
```

`field` defaults to `MeasurementField.IQ`, the same default `measure` uses, so
a measurement you never passed `fields=` to reads back with a bare
`result.get(m)`. The default is a real field name rather than "whatever this
measurement produced", so asking for a field the measurement didn't request
raises, the default included:

```python
m = program.measure(q[0].readout, "readout", "weights", fields=(MF.STATE,))
result.get(m)  # KeyError: has no field 'iq'; available: state
result.get(m, field=MF.STATE)  # correct
```

That is deliberate: a state array handed back from `result.get(m)` would look
like IQ data to every reader downstream. When you genuinely want the record's
primary array whatever it happens to be, read it off the record:

```python
result.measurements[0].data  # the "iq" field if requested, else the first in canonical order
```

## Parallel loops produce composite dimensions

When a measurement sits inside a parallel composition, the loops share a
single dimension named after every variable:

```python
with program.sweep(freq, qp.Range(4e9, 6e9, 1e6)) | program.sweep(amp, qp.Values(custom)):
    program.measure(q[0].readout, "readout", "weights")

# data.dims == ("freq|amp", "IQ")
# data.coords["freq"].values  -> numpy array
# data.coords["amp"].values   -> numpy array
```

## Multiple measurements

A program can have any number of `measure` calls. They appear in `result`
in declaration order:

```python
m0 = program.measure(q[0].readout, "r", "w")
m1 = program.measure(q[1].readout, "r", "w")
m2 = program.measure(q[0].readout, "r", "w")  # q0/readout/m1

result.get(m0)  # first
result.get(m1)  # second
result.get(m2)  # third
result.get(measurement=0, bus="q0/readout")  # first measurement on q0
result.get(measurement=1, bus="q0/readout")  # third overall, second on q0
```

## When you might call `measurement_handles()` yourself

Three cases:

1. After `qp.load(...)` to get handles back without re-running the program.
2. In test code that wants to assert against named measurements.
3. When you serialize a program, hand it to another process, and need to
   match results to operations later, or to inject classified state
   values into the AST so any `Conditional` referencing them evaluates
   correctly in Python.

In every other case, hold on to the handle returned by `measure`.
