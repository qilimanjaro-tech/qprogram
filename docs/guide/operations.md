# Operations

Operations are the leaves of a QProgram's AST. Each `program.<verb>(...)` call
builds one typed `Operation` instance and appends it to whichever block is
currently active, so the order of the calls is the order of the program. Most
builders return `None`; `measure` returns a `MeasurementHandle` and
`get_parameter` returns a `Variable`.

Blocks are the containers, and are covered elsewhere: loops, averaging,
conditionals, and parallel composition are in
[Control flow](control-flow.md).

Every example below builds on this setup, and declares its own variables where
it needs them:

```python
import qprogram as qp

schema = qp.BusSchema.flux_tunable_transmon()
q = schema.q
program = qp.QProgram(schema=schema)
```

## Arguments every operation shares

A `bus` argument is either a plain string or a `BusRef` obtained from a
`BusSchema`. `BusRef` is a `str` subclass, so both spellings travel through
the AST as the same kind of value; what differs is how much the builder can
check and how the bus is written to `.qp`. A schema-backed ref carries
`element`, `idx`, `kind`, `channel`, `acquires`, and the producing schema, and
the builder uses them: it rejects a ref from a different `BusSchema` than the
program's, rejects `measure` on a bus with `acquires=False`, and rejects a
waveform whose channel count does not match the bus's. A plain string carries
none of that, so none of those checks run. On the wire, a schema-backed ref
serializes as a path (`q[0].drive`) and a plain string as a quoted name
(`"drive_q0"`).

A program may use exactly one schema. Passing a ref from a second one raises
`ValidationError` at the builder call:

```
BusRef 'q0/drive' (element='q', kind='drive') comes from a different BusSchema
than the one attached to this QProgram. A program may use only one schema; use
a plain string bus name if you need to reference a bus that lives outside the
schema.
```

A numeric argument accepts an `int` or `float`, a `Variable`, or any
`Expression` built from them. Nothing is evaluated at build time: the
expression tree is stored on the operation, and the tokens it needs are
reported alongside the operation's own (see
[Variables and expressions](variables.md)). Durations are nanoseconds,
frequencies hertz, phases radians; gain and offset are dimensionless.

A waveform argument accepts a concrete `Waveform` or `IQWaveform`, or a string
alias to be resolved later by `with_waveforms`. An alias defers the choice of
samples to the platform's calibration store; see
[Waveforms](waveforms.md).

## What a builder call appends

The appended object is an ordinary Python instance with the constructor
arguments as public attributes. Equality and hashing are structural, and each
class reports the capability tokens it needs:

```python
program.play("drive_q0", "pi_pulse")

op = program.body.elements[0]
type(op).__name__  # "Play"
op.bus  # "drive_q0"
op.waveform  # "pi_pulse"
op.required_capabilities()  # {'op.play', 'waveform.alias'}
op.buses()  # {'drive_q0'}
```

Every core operation but `call` carries an identity token spelled
`op.<verb>`, and adds refinement tokens for the state it holds: a waveform
contributes its channel kind and its per-class token, an `Expression`
contributes one token per node type it contains, and a measurement contributes
one per field it requests. Validation routes a bus-scoped operation to that
bus's slot, a slot being a `(bus, domain)` pair, and reports a missing token as
a `missing-capability` error naming the operation, the token, and the profile
that lacks it:

```
'Wait' requires capability 'op.wait' which is not supported by
'empty-profile' (rt) / 'empty-profile' (host)
```

Constructing operation classes directly is rarely needed, but
`qp.operations` exports every one (`qp.operations.Play`,
`qp.operations.Measure`, ...) for tests and program transformations.

## Every core operation

| Builder call | `.qp` statement | Capability tokens |
|---|---|---|
| `play(bus, waveform)` | `play <bus> <waveform>` | `op.play` plus waveform tokens |
| `measure(bus, waveform, weights, *, name=None, fields=(MeasurementField.IQ,))` | `measure <bus> <waveform> <weights> name="..."` (`fields=[...]` when not the default) | `op.measure`, `waveform.iq`, waveform tokens, one `measure.fields.<name>` per field |
| `wait(bus, duration)` | `wait <bus> <duration>` | `op.wait` plus expression tokens |
| `sync(buses=None)` | `sync` or `sync <bus> [<bus> ...]` | `op.sync` |
| `set_frequency(bus, frequency)` | `set_frequency <bus> <frequency>` | `op.set_frequency` plus expression tokens |
| `set_phase(bus, phase)` | `set_phase <bus> <phase>` | `op.set_phase` plus expression tokens |
| `reset_phase(bus)` | `reset_phase <bus>` | `op.reset_phase` |
| `set_gain(bus, gain)` | `set_gain <bus> <gain>` | `op.set_gain` plus expression tokens |
| `set_offset(bus, offset_path0, offset_path1=None)` | `set_offset <bus> <offset_path0>` (`offset_path1=<v>` when given) | `op.set_offset` plus expression tokens |
| `set_parameter(bus, parameter, value)` | `set_parameter <bus> "<parameter>" <value>` | `op.set_parameter` plus expression tokens |
| `get_parameter(bus, parameter)` | `get_parameter <bus> "<parameter>" -> <ident>` | `op.get_parameter` |
| `call(fragment, *args, **kwargs)` | `<fragment_name>(<args>)` | none; validation expands calls first |

The `.qp` statement of an operation with no custom serializer is derived from
its constructor signature: the keyword, then the required parameters
positionally in declaration order, then any optional parameter whose value
differs from its default, as `name=value`. That is why `set_offset` writes its
second path as a keyword and `measure` writes `fields=[...]` only when the
program asked for something other than `("iq",)`. `sync`, `measure`, and
`get_parameter` have hand-written serializers, for the variadic bus list, the
`name=` kwarg, and the `->` arrow respectively.

## Pulse operations

These four put something on a bus's timeline: a waveform, an acquisition, an
idle, or an alignment with other buses.

### `play(bus, waveform)`

Output a waveform on a bus.

```python
amp = program.variable("amp")

program.play("drive_q0", "pi_pulse")
program.play(
    q[0].drive,
    qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
)
program.play(q[0].flux, qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))
```

The tokens depend on what the waveform argument is. A string alias adds
`waveform.alias` and nothing else, since the samples are not known yet. A
concrete waveform adds `waveform.iq` or `waveform.single` for its channel
kind, plus the per-class token registered for its type
(`waveform.gaussian`, `waveform.iq_drag`, and so on) when there is one; an
unregistered vendor class contributes no per-class token, and the validator
skips that refinement for it.

Channel mismatch is caught at the call site whenever the bus is schema-backed
and the waveform is concrete, in both directions:

```
Bus 'q0/flux' is a single channel but received an IQWaveform (IQPair).
Use a single-channel Waveform (e.g. Square, FlatTop) instead.
```

```
Bus 'q0/drive' is an IQ channel but received a single-channel Waveform
(Square). Use an IQWaveform (e.g. IQPair, IQDrag) instead.
```

A raw-string bus or a string alias skips the check, because neither side has
the metadata to compare.

### `measure(bus, waveform, weights, *, name=None, fields=(MeasurementField.IQ,))`

Play a readout pulse, acquire the response, and return a `MeasurementHandle`.
`waveform` is the readout pulse and `weights` the integration weights; both
take a concrete `IQWaveform` or a string alias.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
m1 = program.measure(q[0].readout, "readout", "weights", name="custom_name")
m2 = program.measure(
    q[0].readout,
    "readout",
    "weights",
    fields=(qp.MeasurementField.IQ, qp.MeasurementField.RAW),
)
```

`fields` names which data the platform should produce: an iterable of
`MeasurementField` members (`STATE`, `IQ`, `RAW`) or of registered field-name
strings, defaulting to `(MeasurementField.IQ,)`. The tuple is stored
deduplicated and sorted into enum declaration order, so `(IQ, STATE)` and
`(STATE, IQ)` build equal, equally-hashing, identically-serializing programs.
A bare string is rejected, and so is an unknown name, both at the call site
rather than later:

```
`fields` must be an iterable of MeasurementField values, not the bare string
'iq' — iterating a string yields its characters, and the comma-separated form
is not accepted. Pass a tuple: fields=("iq",)
```

```
unknown measurement field(s) ['iqq']. Did you mean 'iq'? Known fields:
['iq', 'raw', 'state']. A vendor extension adds its own by registering
`measure.fields.<name>` via qprogram.protocol.register_capability_tokens.
```

An empty `fields` is refused too, since a measurement that produces nothing is
almost certainly a mistake. See
[Measurements and results](measurements.md#the-fields-argument) for the
complete rules.

Two further checks run at the call site. A schema-backed bus with
`acquires=False` is refused (`measure() can only be called on buses with an
ADC (e.g. readout buses).`), and an explicit `name` that another measurement
in the program already uses is refused as well. Omit `name` and one is
allocated: schema-backed buses get `{bus}/m<counter>` with a per-bus counter
(`q0/readout/m0`, `q0/readout/m1`, `q1/readout/m0`), raw-string buses share a
global `m<counter>`. The counters are recomputed from the AST on each call
rather than stored on the program, which is what keeps `deepcopy`,
`with_waveforms`, and `dumps`/`loads` round-trips free of hidden state.

What an `average` block accumulates is measurement results, so only
measurements decide whether the averaging itself can run as a real-time
feature. `MeasurementOperation` sets `AFFECTS_AVERAGING = True` and the other
core operations leave it `False`, which is why an `average` block's execution
domain is fixed by the measurements in its body and not by the pulses around
them.

### `wait(bus, duration)`

Idle a bus for `duration` nanoseconds.

```python
t = program.variable("t", units="ns")

program.wait("drive_q0", 100)
program.wait("drive_q0", 100 + t)
```

A platform that declares a `min_wait_duration_ns` limit on the bus slot has it
checked against the stored duration, producing a `limit-exceeded` error:

```
Wait duration 2 ns is shorter than min_wait_duration_ns=4
```

The check only applies when `duration` is a plain `int`. An `Expression` has
no static value to compare, so it is left to the platform.

### `sync(buses=None)`

Bring the listed buses to a common point in time. `None`, which is the
default, means every bus active in the program: the operation declares
`BROADCASTS_WHEN_NO_BUS = True`, so validation intersects the capabilities of
every bus the program touches instead of consulting one slot.

```python
program.sync()
program.sync([q[0].readout, q[1].readout])
```

An empty list raises rather than being read as either extreme:

```
sync([]) is ambiguous; pass None (or no argument) to sync all buses
```

On the AST node the attribute is `targets`, not `buses`, so that a list
attribute does not shadow the `Operation.buses()` introspection method. The
builder keeps the readable `buses=` keyword.

## Real-time parameter control

These five write the per-bus registers of the signal chain. Whether they run
in the real-time sequencer or host-side is the platform's call, declared per
slot; nothing about the operations themselves forces one domain.

### `set_frequency(bus, frequency)`

Retune the bus oscillator. `frequency` is in hertz.

```python
freq = program.variable("freq", units="Hz")

program.set_frequency("drive_q0", 5e9)
program.set_frequency("drive_q0", freq + 1e6)
```

### `set_phase(bus, phase)`

Set the oscillator phase to `phase` radians.

```python
phi = program.variable("phi")

program.set_phase("drive_q0", 1.5708)
program.set_phase("drive_q0", phi)
```

### `reset_phase(bus)`

Zero the oscillator phase on the bus. It takes no value, so `op.reset_phase`
is the only token it needs.

```python
program.reset_phase("drive_q0")
```

### `set_gain(bus, gain)`

Set the output gain, dimensionless.

```python
gain = program.variable("gain")

program.set_gain("drive_q0", 0.5)
program.set_gain("drive_q0", gain)
```

### `set_offset(bus, offset_path0, offset_path1=None)`

Set the DC offset on one or both signal paths. `offset_path0` is the only path
on a single-channel bus and I on an IQ bus; `offset_path1` is Q, and `None`
leaves that path's offset alone rather than zeroing it. An unset
`offset_path1` contributes no expression tokens.

```python
program.set_offset(q[0].flux, 0.1)
program.set_offset(q[0].drive, 0.1, 0.0)
```

## Host-side platform parameters

`set_parameter` and `get_parameter` are the two core operations that are
host-side only. They name a parameter by string and target a bus, so they
route to that bus's slot like the real-time `set_*` operations do, but writing
or reading a named parameter is an action against the platform's configuration
layer rather than an instruction the sequencer can hold. Platforms therefore
list `op.set_parameter` and `op.get_parameter` in a bus slot's `host` half and
omit them from its `rt` half. `QPROGRAM_BASE_V1`, the platform-level base
profile, does not carry them at all: they are bus-scoped, and it covers only
the non-bus capabilities.

The parameter vocabulary is the platform's, not QProgram's. Nothing in the
core validates a parameter name.

### `set_parameter(bus, parameter, value)`

Write a bus-scoped platform parameter.

```python
program.set_parameter("drive_q0", "lo_frequency", 5e9)
program.set_parameter(q[0].drive, "lo_frequency", 5e9)
```

Sweeping a parameter is allowed, and it constrains the loop rather than the
operation. When `value` is a swept `Variable`, a validation predicate excludes
`rt` from the loop that binds it, with the reason `parameter '<name>' is swept
via set_parameter (host-side dispatch per iteration)`:

```python
sweep_demo = qp.QProgram(schema=schema)
lo = sweep_demo.variable("lo", units="Hz")
with sweep_demo.sweep(lo, qp.Range(5e9, 6e9, 1e8)):
    sweep_demo.set_parameter(q[0].drive, "lo_frequency", lo)
    sweep_demo.play(q[0].drive, "pi")
    sweep_demo.measure(q[0].readout, "ro", "w")

print(qp.explain(sweep_demo, qp.reference_capabilities()))
```

```
plan — errors: 0 · warnings: 0 · info: 0
body
└─ for lo in Range(start=5000000000.0, stop=6000000000.0, step=100000000.0):  [host]
   ├─ set_parameter q[0].drive "lo_frequency" lo                              [host]
   ├─ play q[0].drive "pi"                                                    [rt|host]
   └─ measure q[0].readout "ro" "w" name="q0/readout/m0"                      [rt|host]
```

Targeting the loop and not the operation is what lets the operations inside
stay real-time capable while the iteration is dispatched from the host.

### `get_parameter(bus, parameter) -> Variable`

Read a bus-scoped platform parameter into a fresh `Variable`. The variable is
declared on the program and returned, so later operations can use it:

```python
detuning = program.variable("detuning", units="Hz")

lo_freq = program.get_parameter(q[0].drive, "lo_frequency")
program.set_frequency(q[0].drive, lo_freq + detuning)
```

The variable's id is derived from `f"{bus}_{parameter}"` with every non-word
character replaced by an underscore, and a `_2`, `_3`, ... suffix appended on
collision, so reading `lo_frequency` on `q[0].drive` twice gives
`q0_drive_lo_frequency` and `q0_drive_lo_frequency_2`. The `bus.parameter`
form is kept as the variable's label (`q0/drive.lo_frequency`) so a result
axis can be traced back to what produced it. A bus or parameter name with
letters or digits outside ASCII sanitizes to something `Variable` refuses, and
the resulting `ValidationError` surfaces here.

### What a vendor extension handles instead

Addressing an instrument by alias, with an optional channel selector, instead
of by bus is specific to a hardware stack, so it belongs in a vendor
extension. A vendor named `fake_inst` would expose it as
`program.fake_inst.set_parameter("cluster", "lo_frequency", 5e9, channel_id=3)`
and `program.fake_inst.get_parameter(...)` under its own namespace, and the
`.qp` file would then carry a `require fake_inst <major>.<minor>` line.

Crosstalk correction is likewise hardware-stack specific. A vendor extension
that needs it ships its own correction type and its own operation under its
namespace, as `program.<vendor>.set_crosstalk(...)`.

## Fragment calls

A fragment is a reusable, parameterized piece of program body. Calling one
appends a single leaf naming the fragment and the arguments bound at that site,
which is what lets a `.qp` file carry the definition once and the call sites
separately.

### `call(fragment, *args, **kwargs)`

Instantiate a `Fragment` at the current position. Arguments bind to the
fragment's parameters with the Python calling convention, positional in
declaration order and then by keyword, and may be numbers, variables or
expressions, buses, or waveforms.

```python
@qp.fragment
def x_pulse(f, drive, amp):
    f.play(drive, qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))


a = program.variable("a")
with program.sweep(a, qp.Range(0, 1, 0.1)):
    program.call(x_pulse, "drive_q0", a)
```

`Call` is an `Operation` subclass, and departs from the other leaves in three
ways. It reports no capability tokens, because `validate` expands every call
before it checks anything. Its `buses()` over-approximates: a `Parameter` is
untyped, so every string-valued argument is reported as a possible bus, and the
buses named inside the fragment body only become visible after `expand`. And
its `.qp` form is the one that is not keyword-led, written as
`x_pulse("drive_q0", a)` against a `fragment x_pulse(drive, amp):` section
emitted earlier in the file.

Binding errors raise at the call site, naming the fragment and what went
wrong:

```
fragment 'x_pulse' missing argument(s) for parameter(s): amp
```

```
fragment 'x_pulse' takes 2 argument(s) (drive, amp) but 3 positional argument(s) were given
```

Passing something that is not a `Fragment` raises
`call() expects a Fragment, got str`. A fragment cannot call itself, a call
cycle is refused, and a fragment whose schema differs from the program's is
refused on the same grounds a mismatched `BusRef` is. See
[Fragments](fragments.md).

## Vendor operations

Vendor-specific operations live behind a namespace, always spelled

<!-- check: skip -->

```python
program.<vendor>.<operation>(...)
```

A vendor named `fake_inst` exposing a `beep` operation appears as
`program.fake_inst.beep(bus, duration)`, needs the capability token
`vendor.fake_inst.beep`, and writes to `.qp` as
`fake_inst.beep <bus> <duration>` below a `require fake_inst <major>.<minor>`
line, which the writer emits directly after the header. To use a vendor,
install its package and import it once at the top of the script; the import
registers the namespace.

[Building a vendor extension](../developer/vendor-extensions.md) covers adding
a namespace of your own.

## Control flow is not an operation

Loops, averaging, conditionals, and grouping are blocks: context managers that
contain other nodes, rather than builder calls that append a leaf. `sweep`,
`average`, `block`, the `if_` / `elif_` / `else_` chain, and parallel
composition with `|` are all in [Control flow](control-flow.md).

## What is out of scope

Mid-circuit classification beyond the `state` measurement field is
platform-specific and belongs in a vendor extension. So do timing and
scheduling primitives past `wait` and `sync`: a QProgram records intent, and
the platform's compiler owns the schedule. A handful of identifiers (`while`,
`repeat`, `match`, `gate`, ...) are held back for future syntax and rejected
as variable ids, listed in [Reserved keywords](../reference/reserved.md).
