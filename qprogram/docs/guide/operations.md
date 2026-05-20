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
program.play("drive_q0", "pi_pulse")
program.play(q[0].drive, IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1))
program.play(q[0].drive, Gaussian(amplitude=amp, duration=40 + t, num_sigmas=2.5))
```

The schema validator rejects mismatched channel types: IQ waveforms on
single-channel buses, or single-channel waveforms on IQ buses.

### `measure(bus, waveform, weights, *, name=None, returns=("iq",))`

Play a readout pulse and acquire the result. Returns a `MeasurementHandle`.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
m1 = program.measure(q[0].readout, "readout", "weights", name="custom_name")
m2 = program.measure(q[0].readout, "readout", "weights", returns="iq,raw")
```

`returns` is what the platform should produce for this measurement. The
default `("iq",)` requests in-phase / quadrature data. `"raw"` requests the
raw ADC trace alongside. You can pass a comma-separated string or any
iterable of strings; values are normalised to a canonical
`tuple[str, ...]`.

Measurement names follow a convention: schema-backed buses get
`{bus_string}/m<counter>` (`q0/readout/m0`, `c0_1/flux/m2`, ...) with
per-bus counters; raw-string buses fall back to a global `m<counter>`.
See [Measurements and results](measurements.md) for the full naming rules.

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

These talk to the platform's configuration layer. They are not hardware
real-time; the compiler may execute them in software.

### `set_parameter(alias, parameter, value, channel_id=None)`

Set a platform parameter. `parameter` is a string; each platform defines its
own vocabulary.

```python
program.set_parameter("cluster", "lo_frequency", 5e9)
program.set_parameter("attenuator", "value", 6, channel_id=3)
```

### `get_parameter(alias, parameter, channel_id=None) -> Variable`

Read a platform parameter into a new `Variable`. The variable is appended to
the program's variable list and the call returns it so you can use it in
later operations.

```python
lo_freq = program.get_parameter("cluster", "lo_frequency")
program.set_frequency("drive_q0", lo_freq + freq)
```

QProgram auto-generates a unique id for the resulting variable
(`cluster_lo_frequency`, ...). It keeps the original `alias.parameter` form
as the variable's label.

### `set_crosstalk(crosstalk)`

Apply a `CrosstalkMatrix` for flux crosstalk correction.

```python
xtalk = qp.CrosstalkMatrix()
xtalk["flux_q0"] = {"flux_q0": 1.0, "flux_q1": 0.03}
xtalk["flux_q1"] = {"flux_q0": 0.02, "flux_q1": 1.0}
program.set_crosstalk(xtalk)
```

`CrosstalkMatrix` also has `to_array`, `inverse`, `from_array`, and
`from_buses` for the common transforms.

## Vendor operations

Vendor-specific operations live behind a namespace. The shape is always

```python
program.<vendor>.<operation>(...)
```

For example, a vendor named `my_inst` that exposes a `beep` operation would
appear as `program.my_inst.beep(bus, duration)`. To use a vendor, install
its package and import it once at the top of your script; the import has
the side effect of registering the namespace.

[Building a vendor extension](../developer/vendor-extensions.md) walks
through what it takes to add a new vendor namespace of your own.

## What an operation looks like in the AST

Every operation is a typed class with explicit attributes:

```python
program.play("drive_q0", "pi_pulse")
op = program.body.elements[0]
op                                    # Play(bus="drive_q0", waveform="pi_pulse")
op.bus                                # "drive_q0"
op.waveform                           # "pi_pulse"
```

You will not usually need to construct operation classes directly, but if you
do (testing, transformations), `qprogram.operations.*` exposes every one.

## What you cannot do (yet)

A few things are intentionally out of scope today:

- **Conditional execution** based on measurement outcomes is on the roadmap
  but does not exist in the core. Vendor extensions can supply it (an
  `active_reset` op shipped by a vendor package is one example).
- **Mid-circuit classification** is similarly platform-specific.
- **`if`, `while`, `repeat`, ...** are reserved keywords for future syntax.
  See [Reserved keywords](../reference/reserved.md).
