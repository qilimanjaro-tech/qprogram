# Measurements and results

`measure` appends a `Measure` operation to the program and returns a
`MeasurementHandle`. The handle holds a name and nothing else, and that name is
what ties the stages of a measurement together: it goes into the `.qp` file, the
platform tags the record it produces with it, and `result.get(handle)` finds
that record again afterwards. Vendor measurement operations reached through a
`program.<vendor>.*` namespace return a handle the same way, because they derive
from the same `MeasurementOperation` base class.

```python
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q
readout = qp.waveforms.IQPair(
    qp.waveforms.Square(amplitude=1.0, duration=200),
    qp.waveforms.Square(amplitude=0.0, duration=200),
)

program = qp.QProgram(label="readout-demo", schema=schema)
m0 = program.measure(q[0].readout, readout, readout)

result = qp.simulate(program)
data = result.get(m0)  # xarray.DataArray with dims ("IQ",)
```

A handle has two public attributes and no more: `name`, the string, and `state`,
a proxy for referencing the measurement's classified outcome in a conditional.
It is `__slots__`-based, so nothing else can be attached to it, and
`qp.MeasurementHandle(name)` raises `ValidationError` unless `name` is a
non-empty string.

## The `measure` signature

<!-- check: skip -->

```python
program.measure(bus, waveform, weights, *, name=None, fields=(qp.MeasurementField.IQ,))
```

`bus` is the bus the readout runs on, either a `BusRef` taken from the program's
schema or a raw string. `waveform` is the readout pulse and `weights` the
integration weights; each is a concrete `IQWaveform` or a string alias that
`program.with_waveforms(library)` resolves later. `name` and `fields` are
keyword-only.

Every precondition `measure` can check, it checks at the call site rather than
during validation, so the traceback points at the line that built the
measurement:

```python
program.measure(q[0].drive, readout, readout)
# ValidationError: Bus 'q0/drive' does not support acquisition (acquires=False).
#                  measure() can only be called on buses with an ADC (e.g. readout buses).

program.measure(q[0].readout, qp.waveforms.Square(amplitude=1.0, duration=200), readout)
# ValidationError: Bus 'q0/readout' is an IQ channel but received a single-channel
#                  Waveform (Square). Use an IQWaveform (e.g. IQPair, IQDrag) instead.
```

A `BusRef` produced by a different `BusSchema` than the one attached to the
program is rejected too, with the message described in
[Buses and schemas](buses.md). Raw-string buses carry no metadata, so neither
the acquisition check nor the channel check applies to them: they are deferred
to the platform.

## The `fields` argument

`fields` names the data the platform should produce for this measurement. The
values come from `qp.MeasurementField`, whose declaration order is also the
canonical order fields are stored and serialized in.

| Member | Wire name | What comes back |
|---|---|---|
| `qp.MeasurementField.STATE` | `state` | The classified outcome, 0 or 1 per shot, averaged to the excited-state population inside an `average` block. Array dims `(*sweeps)`. Requesting it is what makes `handle.state` referenceable in a conditional. |
| `qp.MeasurementField.IQ` | `iq` | The demodulated, integrated I and Q pair. Array dims `(*sweeps, "IQ")`. The default, and the field `result.get` returns unless told otherwise. |
| `qp.MeasurementField.RAW` | `raw` | The raw ADC trace, one I/Q pair per time sample. Array dims `(*sweeps, "time", "IQ")`. |

`MeasurementField` is a `StrEnum`, so `qp.MeasurementField.IQ == "iq"` and the
members are strings wherever a field name travels: capability tokens, `.qp`
lines, and the keys of `MeasurementResult.fields`. The enum exists so an editor
can list the options and a type checker can catch a bad one; plain strings work
just as well.

```python
program.measure(q[0].readout, readout, readout)  # fields=(qp.MeasurementField.IQ,)
program.measure(
    q[0].readout,
    readout,
    readout,
    fields=(qp.MeasurementField.IQ, qp.MeasurementField.RAW),
)
program.measure(q[0].readout, readout, readout, fields=["iq", "raw"])  # the same
```

Whatever you pass goes through `normalize_fields`, which is strict about three
things. The value must be an iterable and not a bare string, because iterating a
string would yield its characters and there is no comma-separated spelling:

```python
program.measure(q[0].readout, readout, readout, fields="iq,raw")
# ValidationError: `fields` must be an iterable of MeasurementField values, not the
#                  bare string 'iq,raw' — iterating a string yields its characters, and
#                  the comma-separated form is not accepted. Pass a tuple:
#                  fields=("iq", "raw")
```

It must request at least one field; an empty iterable raises rather than
defaulting back to `iq`. And every name is checked against the registered
`measure.fields.*` capability tokens right there at the call:

```python
program.measure(q[0].readout, readout, readout, fields=())
# ValidationError: `fields` must request at least one measurement field; valid values
#                  are ['state', 'iq', 'raw'] (plus any vendor-registered names)

program.measure(q[0].readout, readout, readout, fields=("stat",))
# ValidationError: unknown measurement field(s) ['stat']. Did you mean 'state'? Known
#                  fields: ['iq', 'raw', 'state']. A vendor extension adds its own by
#                  registering `measure.fields.<name>` via
#                  qprogram.protocol.register_capability_tokens.
```

Order and duplicates carry no meaning. The stored tuple is deduplicated and
sorted into canonical order, core fields first in declaration order and vendor
names alphabetically after them, so `fields=("iq", "state")` and
`fields=("state", "iq")` build the same AST node, hash the same, and write the
same `.qp` line, `fields=["state", "iq"]`.

The registry that catches the typo is also the extension point. A vendor
package that calls `qp.register_capability_tokens("measure.fields.counts")`
makes `fields=("counts",)` legal with no change to core qprogram. Whether a
given platform can deliver a field is a separate question: each requested field
contributes a `measure.fields.<name>` token to the operation's required
capabilities, and a platform that does not declare the token fails validation.
See [Capabilities](capabilities.md).

## Name allocation

When you pass `name=`, it must be a non-empty string that no other measurement
in the program already uses, and it is used verbatim. When you omit it, the name
is `prefix` plus the lowest integer from 0 upwards that is still free, where the
prefix depends on the bus: a `BusRef` gives `f"{bus}/m"` and a raw string gives
the bare `"m"`.

The `BusRef` prefix uses the bus's rendered string form, which is what the
schema's `BusNaming` pattern produces and what `MeasurementResult.bus` reports.
With the default pattern `{element}{index}/{kind}`, a measurement on
`q[0].readout` is named `q0/readout/m0`; a tuple index joins with an underscore,
so `c[0, 1]` yields names under `c0_1/<kind>/m`; and a schema built with
`qp.BusNaming("{kind}_{element}{index}_bus")` yields `readout_q0_bus/m0`.

Each prefix carries its own counter, so measuring `q[0].readout` and then
`q[1].readout` gives `q0/readout/m0` and `q1/readout/m0`. Every raw-string bus
in a program shares the single `m0`, `m1`, ... sequence, since there is no
metadata to scope it by.

```python
program = qp.QProgram(schema=schema)
program.measure(q[0].readout, readout, readout)  # q0/readout/m0
program.measure(q[1].readout, readout, readout)  # q1/readout/m0
program.measure(q[0].readout, readout, readout)  # q0/readout/m1
program.measure("readout_q9", readout, readout)  # m0
program.measure("readout_q9", readout, readout)  # m1
```

Because the counter is "lowest free integer" rather than "number of
measurements so far", an explicit name that looks like an auto-name is stepped
over instead of collided with. A program that starts with
`name="q0/readout/m1"` allocates `q0/readout/m0` next, then `q0/readout/m2`.

The used-name set is recomputed by walking the AST on every allocation. Nothing
about naming is stored on the program, which costs one walk per measurement and
buys freedom from hidden state: `copy.deepcopy`, `program.with_waveforms(...)`
and `qp.loads(qp.dumps(program))` all produce a program that carries on naming
exactly where the original left off. A derived program does carry its own
`BusSchema` instance, and a program accepts bus references only from the schema
attached to it, so reach for `derived.schema.q[0].readout` rather than a
reference built from the original schema, which raises `ValidationError`.

A name collision is reported when the measurement is built, not later:

```python
program.measure(q[0].readout, readout, readout, name="t1_ref")
program.measure(q[1].readout, readout, readout, name="t1_ref")
# ValidationError: measurement name 't1_ref' is already used by another measurement
#                  in this program
```

`program.rebind(...)` re-derives auto-allocated names from each operation's new
bus and leaves user-supplied names alone, which is why the handle records which
of the two it was. That flag is in-memory state and is not serialized, so a
handle reconstructed from a `.qp` file counts as user-supplied and keeps its name
through a rebind. Rebind before dumping, not after loading, if you want the
names to follow the new buses.

### How a name survives a `.qp` round-trip

The writer always emits the measurement name as a `name=` keyword, so nothing is
inferred on the way back in:

```
#!QProgram 1.0

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  measure q[0].readout "readout" "weights" name="q0/readout/m0"
  measure "readout_q9" "readout" "weights" name="m0" fields=["state", "iq"]
```

Note the asymmetry on the first line. A schema-backed bus is written as a bus
*path*, `q[0].readout`, because the path is what survives a change of naming
pattern, while the name holds the rendered string form the pattern produced at
construction time.

The parser resolves every `name=` through one table per parse, so a measurement
operation and every conditional that refers to the same name end up sharing a
single `MeasurementHandle` instance. A hand-written line that carries no `name=`
gets one allocated by the same rule the builder uses, computed against the part
of the program parsed so far.

## Recovering handles after a round-trip

`measure` returns a handle, but the Python local that captured it is gone after
a `.qp` reload. `program.measurement_handles()` returns one handle per
measurement in declaration order:

```python
program = qp.load("rabi.qp")
handles = program.measurement_handles()
data = qp.simulate(program).get(handles[0])
```

These are the same Python instances the AST holds inside its measurement
operations and inside any `MeasurementRef` in a conditional, so a value written
onto one is visible everywhere the measurement is referenced. That is also true
in the other direction: the executor writes the classified state onto the
handle as each shot completes, which is what makes state feedback work without
any wiring between the conditional and the measurement.

Handles compare by name, so a freshly constructed one is a usable key even
without the original variable:

```python
qp.MeasurementHandle("q0/readout/m0") == program.measurement_handles()[0]  # True
```

Beyond a reload, the cases that call for `measurement_handles()` are test code
asserting against named measurements, and code that serializes a program, hands
it to another process, and has to match records back to operations there.
Everywhere else, hold on to what `measure` returned.

## Referencing a measurement in a conditional

`handle.state` is a proxy whose `==` and `!=` build a `Comparison` over the
measurement's classified outcome. The measurement has to have asked for the
classification:

```python
m = program.measure(q[0].readout, readout, readout, fields=(qp.MeasurementField.STATE,))
with program.if_(m.state == 1):
    program.play(q[0].drive, readout)
```

Without `STATE` in `fields`, validation reports an error rather than guessing:

```
[error] missing-classification: Conditional references q0/readout/m0.state, but the
measurement does not request state classification (add MeasurementField.STATE to
fields=) (at body[1])
```

On the wire the reference is the unquoted token `q0/readout/m0.state`, which
constrains the name: whitespace, a quote, `#`, a comma, a dot, a bracket, a
brace, or a parenthesis in the name has no unquoted spelling, and dumping such a
program raises `SerializationError`. Slashes are safe, so auto-names under the
default `BusNaming` pattern are too; a pattern that puts a dot or a bracket in
the bus name makes them unsafe. Pass a token-safe `name=` when you intend to
branch on a result. See
[Control flow](control-flow.md).

## The result object

`platform.execute(program)` and `qp.simulate(program)` return a
`QProgramResult`: a list of `MeasurementResult` records in construction order,
one per measurement operation in the AST. `len(result)` counts them,
`result.measurements` is the list itself, and `repr(result)` prints the count and
the names, which is usually enough to see why a lookup missed.

A `MeasurementResult` is a dataclass of four fields. `bus` is the bus the
measurement ran on, as a plain string. `name` is the handle's name. `fields` is
the mapping from field name to `xarray.DataArray`, one entry per requested
field. `data` is the primary array: the `iq` field when the measurement
requested it, otherwise the first requested field in canonical order.

`result.get` reads out of `fields`. Its full signature is

```python
result.get(measurement=0, bus=None, field=qp.MeasurementField.IQ)
```

and the first argument accepts three spellings:

```python
result.get(m0)  # by handle
result.get("q0/readout/m0")  # by name
result.get(0)  # by position in declaration order
result.get(0, bus="q0/readout")  # position within one bus
```

The handle and the name are the same lookup, since a handle is looked up by its
name. The integer form is positional sugar; a handle or a name says what it
means and survives a reordering of the program, so prefer either. `bus` filters
the candidate records before the lookup, which is what makes a position within
one bus meaningful.

A lookup that finds nothing raises rather than returning `None`. A handle or
name with no matching record raises `KeyError`. That is what a handle whose
program was never run gives you, and a handle from a different program too:

```python
result.get(qp.MeasurementHandle("q0/readout/m9"))
# KeyError: "No measurement named 'q0/readout/m9'"

result.get("q0/readout/m0", bus="q1/readout")
# KeyError: "No measurement named 'q0/readout/m0' on bus 'q1/readout'"

result.get(5, bus="q0/readout")
# IndexError: Measurement index 5 out of range for bus 'q0/readout' (1 measurements)
```

A record is only ever added by the platform, through
`result.append_measurement(bus, name, data, fields=None)`. Omitting `fields`
records `data` as the `iq` field, so a platform that produces one array per
measurement gets the common case right by default; a platform whose primary
array is something else has to pass the mapping, which keeps `get` from handing
back an array under the wrong field name.

### Picking a field

A measurement that requested several fields produces one array per field, and
they have different shapes. `field=` says which one you want, as a
`MeasurementField` member or a registered field name:

```python
m = program.measure(
    q[0].readout,
    readout,
    readout,
    fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE, qp.MeasurementField.RAW),
)
result = qp.simulate(program)

result.get(m)  # dims (*sweeps, "IQ")
result.get(m, field=qp.MeasurementField.STATE)  # dims (*sweeps)
result.get(m, field=qp.MeasurementField.RAW)  # dims (*sweeps, "time", "IQ")
result.get(m, field="state")  # identical to field=qp.MeasurementField.STATE
```

`field` defaults to `qp.MeasurementField.IQ`, matching the default of `measure`,
so a measurement you never passed `fields=` to reads back with a bare
`result.get(m)`. The default is a real field name and not "whatever this
measurement produced", so asking for a field the measurement did not request
raises, the default included:

```python
m = program.measure(q[0].readout, readout, readout, fields=(qp.MeasurementField.STATE,))
result = qp.simulate(program)

result.get(m)
# KeyError: "Measurement 'q0/readout/m0' has no field 'iq'; available: state"
result.get(m, field=qp.MeasurementField.STATE)  # correct
```

The alternative, returning the primary array whenever `iq` is missing, would
hand a state array to every reader downstream that expected IQ data. There is
no spelling of "give me the primary array" in `get` at all: `field=None` raises
`ValidationError` pointing at `MeasurementResult.data`, which is where the
primary array lives.

```python
result.measurements[0].data  # the "iq" field if requested, else the first in canonical order
```

## Result dimensions

The dimensions of every returned array are the enclosing `sweep` blocks,
outermost first, each named after its loop variable's id and carrying the swept
values as its coordinate. The trailing dimensions belong to the field: `"IQ"`
with coordinates `["I", "Q"]` for `iq`, `"time"` with an integer coordinate for
`raw`, and nothing for `state`. The full order is `(*sweeps, *field dims)`.

A sweep coordinate also carries whatever `label` and `units` its variable
declared, as the `long_name` and `units` attributes. The field dimensions carry
none: neither comes from a variable.

```python
program = qp.QProgram(label="spectroscopy", schema=schema)
freq = program.variable("freq", units="Hz")
gain = program.variable("gain")
with (
    program.average(100),
    program.sweep(freq, qp.Range(4e9, 4.2e9, 1e8)),
    program.sweep(gain, qp.Linspace(0.0, 1.0, 5)),
):
    program.set_frequency(q[0].readout, freq)
    program.measure(q[0].readout, readout, readout, fields=("iq", "raw"))

result = qp.simulate(program)
result.get(0).dims  # ("freq", "gain", "IQ"), shape (3, 5, 2)
result.get(0, field="raw").dims  # ("freq", "gain", "time", "IQ"), shape (3, 5, 16, 2)
result.get(0).coords["IQ"].values  # array(['I', 'Q'], dtype='<U1')
```

An `average(shots)` block contributes no dimension. It is accumulated away:
`iq` and `raw` come back as means over the shots, and `state` becomes the
excited-state population, a float in `[0, 1]` rather than the 0 or 1 of a single
shot. The `"time"` dimension of `raw` is as long as the measurement model's
`raw_samples`, 16 for the default `qp.MockMeasurementModel`, and its coordinate
is the sample index.

A parallel composition contributes one dimension shared by all of its loops,
named by joining the variable ids with `|`, and every composed variable becomes
a coordinate on that one dimension:

```python
program = qp.QProgram(schema=schema)
amp = program.variable("amp")
dur = program.variable("dur")
with program.sweep(amp, qp.Linspace(0.0, 1.0, 4)) | program.sweep(dur, qp.Linspace(10, 40, 4)):
    program.measure(q[0].readout, readout, readout)

data = qp.simulate(program).get(0)
data.dims  # ("amp|dur", "IQ")
list(data.coords)  # ['amp', 'dur', 'IQ']
```

A `Conditional` adds no dimension either; an arm only leaves some sweep points
unvisited. A measurement inside an arm holds NaN wherever the arm never ran,
which distinguishes "not measured" from the zero a plain sum would leave. Every
other `xarray` operation applies as usual, so `data.sel(IQ="I")` peels off the I
component and `data.sel(freq=5e9, method="nearest")` slices a named dimension.
[Running programs](execution.md) covers the reference executor that produces
these shapes.

## Multiple measurements

A program can hold any number of measurements, and their records appear in
declaration order. Combined with `bus=`, that gives a per-bus ordering too:

```python
program = qp.QProgram(schema=schema)
m0 = program.measure(q[0].readout, readout, readout)  # q0/readout/m0
m1 = program.measure(q[1].readout, readout, readout)  # q1/readout/m0
m2 = program.measure(q[0].readout, readout, readout)  # q0/readout/m1

result = qp.simulate(program)
result.get(m0)  # first record
result.get(m1)  # second record
result.get(m2)  # third record
result.get(0, bus="q0/readout")  # first measurement on q0/readout, same as m0
result.get(1, bus="q0/readout")  # third overall, second on q0/readout, same as m2
```
