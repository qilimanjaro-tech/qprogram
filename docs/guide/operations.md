# Operations

Operations are the verbs of QProgram. Every `program.<verb>(...)` call appends
one AST node to the current block. This page lists them all.

The conventions:

- `bus` accepts a plain string or a `BusRef`. Schema-backed BusRefs are
  validated; plain strings are not.
- Numeric parameters accept `int` / `float`, `Variable`, or any `Expression`.
- Waveform parameters accept a concrete `Waveform` / `IQWaveform`, or a string
  alias to be resolved later via `with_waveforms`.

## Pulse operations

These produce real-time activity on a specific bus.

### `play(bus, waveform)`

Output a waveform.

```python
from qprogram.waveforms import Gaussian, IQDrag

program.play("drive_q0", "pi_pulse")
program.play(q[0].drive, IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1))
program.play("flux_q0", Gaussian(amplitude=amp, duration=40 + t, sigma=8))
```

The schema validator rejects mismatched channel types: IQ waveforms on
single-channel buses, or single-channel waveforms on IQ buses.

### `measure(bus, waveform, weights, *, name=None, fields=(MeasurementField.IQ,))`

Play a readout pulse and acquire the result. Returns a `MeasurementHandle`.

```python
from qprogram import MeasurementField as MF

m0 = program.measure(q[0].readout, "readout", "weights")
m1 = program.measure(q[0].readout, "readout", "weights", name="custom_name")
m2 = program.measure(q[0].readout, "readout", "weights", fields=(MF.IQ, MF.RAW))
```

`fields` names which data the platform should produce — an iterable of
`MeasurementField` members (`STATE`, `IQ`, `RAW`), defaulting to `(IQ,)`.
Order and duplicates don't matter (the stored tuple is canonical), a bare
string is rejected, and an unknown field name raises `ValidationError` right
here at the call site. See
[Measurements and results](measurements.md#the-fields-argument) for the full
rules.

Measurement names follow a convention: schema-backed buses get
`{bus_string}/m<counter>` (`q0/readout/m0`, `q0/readout/m1`, `q1/readout/m0`,
...) with per-bus counters; raw-string buses fall back to a global
`m<counter>`. See [Measurements and results](measurements.md) for the full
naming rules.

### `wait(bus, duration)`

Idle a bus for the given duration in nanoseconds.

```python
program.wait("drive_q0", 100)
program.wait("drive_q0", 100 + t)
```

### `sync(buses=None)`

Wait for the listed buses to reach the same point in time. Passing `None`
syncs every bus in scope.

```python
program.sync()
program.sync([q[0].readout, q[1].readout])
```

## Parameter control

These touch real-time hardware registers.

### `set_frequency(bus, frequency)`

Set the NCO frequency in Hz.

```python
program.set_frequency("drive_q0", 5e9)
program.set_frequency("drive_q0", freq + 1e6)
```

### `set_phase(bus, phase)`

Set the NCO phase in radians.

```python
program.set_phase("drive_q0", 1.5708)
program.set_phase("drive_q0", phi)
```

### `reset_phase(bus)`

Zero the NCO phase on the bus.

```python
program.reset_phase("drive_q0")
```

### `set_gain(bus, gain)`

Set the output gain (dimensionless).

```python
program.set_gain("drive_q0", 0.5)
program.set_gain("drive_q0", amp)
```

### `set_offset(bus, offset_path0, offset_path1=None)`

Set the DC offset. The second argument applies on IQ buses where each path
needs its own offset; pass `None` (the default) for single-channel buses.

```python
program.set_offset("flux_q0", 0.1)
program.set_offset(q[0].drive, 0.1, 0.0)
```

## Platform parameters

These talk to the platform's configuration layer rather than the real-time
sequencer, so they run host-side.

### `set_parameter(bus, parameter, value)`

Set a bus-level platform parameter. `parameter` is a string; each platform
defines its own vocabulary. This is a **host-side** operation — it talks to
the platform's configuration layer for that bus, not the real-time sequencer.

```python
program.set_parameter("drive_q0", "lo_frequency", 5e9)
program.set_parameter(q[0].drive, "lo_frequency", 5e9)
```

### `get_parameter(bus, parameter) -> Variable`

Read a bus-level platform parameter into a new `Variable`. The variable is
appended to the program's variable list and the call returns it so you can use
it in later operations.

```python
lo_freq = program.get_parameter("drive_q0", "lo_frequency")
program.set_frequency("drive_q0", lo_freq + freq)
```

QProgram auto-generates a unique id for the resulting variable, derived from
the bus and parameter (bus `q[0].drive`, parameter `lo_frequency` →
`q0_drive_lo_frequency`). It keeps the `bus.parameter` form
(`q0/drive.lo_frequency`) as the variable's label.

> Addressing an instrument by **alias** (with an optional channel selector)
> rather than by bus is hardware-stack specific, so it belongs in a vendor
> extension rather than the core. A vendor named `acme` would expose it as
> `program.acme.set_parameter("cluster", "lo_frequency", 5e9, channel_id=3)`
> and `program.acme.get_parameter(...)` under its own namespace; importing the
> extension registers the namespace, and the `.qp` file then carries a
> `require acme` line.

> Crosstalk correction is **not** a core operation. It is hardware-stack
> specific, so a vendor extension that needs it ships its own correction type
> and operation under its namespace (`program.<vendor>.set_crosstalk(...)`).

## Vendor operations

Vendor-specific operations live behind a namespace. The shape is always

```python
program.<vendor>.<operation>(...)
```

For example, a vendor named `acme` that exposes a `beep` operation would
appear as `program.acme.beep(bus, duration)`. To use a vendor, install its
package and import it once at the top of your script; the import has the side
effect of registering the namespace.

[Building a vendor extension](../developer/vendor-extensions.md) walks
through what it takes to add a new vendor namespace of your own.

## What an operation looks like in the AST

Every operation is a typed class with explicit attributes:

```python
program.play("drive_q0", "pi_pulse")
op = program.body.elements[0]
type(op).__name__  # "Play"
op.bus  # "drive_q0"
op.waveform  # "pi_pulse"
op.required_capabilities()  # {'op.play', 'waveform.alias'}
```

You will not usually need to construct operation classes directly, but if you
do (testing, transformations), `qprogram.operations.*` exposes every one.

## Control flow is not an operation

Loops, averaging, conditionals, and grouping are **blocks**, not operations:
they are context managers that contain other nodes rather than builder calls
that append a leaf. `sweep`, `average`, `block`, the `if_` / `elif_` /
`else_` chain, and parallel composition with `|` all live in
[Control flow](control-flow.md).

## What is out of scope

- **Mid-circuit classification** beyond the `state` measurement field is
  platform-specific, so it belongs in a vendor extension.
- **Timing and scheduling primitives** past `wait` and `sync`: QProgram
  records intent, and the platform's compiler owns the schedule.
- A handful of identifiers — `while`, `repeat`, `match`, `gate`, ... — are
  held back for future syntax and rejected as variable ids. See
  [Reserved keywords](../reference/reserved.md).
