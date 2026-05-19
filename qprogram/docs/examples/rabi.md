# Rabi oscillation

A Rabi experiment sweeps the drive amplitude on a qubit and reads it out at
each amplitude. This is the smallest QProgram that does something interesting.

## The program

```python
import qprogram as qp
from qprogram.buses import BusSchema

schema = BusSchema.transmon()
q = schema.q

program = qp.QProgram(
    label="rabi",
    description="Rabi oscillation on q0",
    schema=schema,
)
gain = program.variable("gain", label="Drive amplitude", units="V")

with program.average(shots=1000):
    with program.for_loop(gain, start=0.0, stop=1.0, step=0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        m0 = program.measure(q[0].readout, "readout", "weights")

print(qp.dumps(program))
```

## What it produces

`qp.dumps(program)` returns this:

```
#!QProgram 1.0

metadata:
  label: "rabi"
  description: "Rabi oscillation on q0"

schema:
  element q:
    drive   info=IQ
    readout info=IQ+acquires

body:
  var gain label="Drive amplitude" units="V"

  average 1000:
    for gain in range(0.0, 1.0, 0.01):
      set_gain q[0].drive gain
      play q[0].drive "pi_pulse"
      sync
      measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

The schema declaration captures the chip's structure. Bus references use the
compact `q[0].drive` path. The measurement name `q0/readout/m0` was auto-allocated
and the `name="q0/readout/m0"` keyword makes it stable across round-trips.

## Plugging in calibration data

The program uses string aliases for `pi_pulse`, `readout`, and `weights`. To
actually run on hardware, supply concrete waveforms:

```python
from qprogram.waveforms import IQDrag, IQPair, Square

resolved = program.with_waveforms({
    "pi_pulse": IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1),
    "readout":  IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights":  IQPair(Square(1.0, 2000), Square(1.0, 2000)),
})
```

The original `program` is unchanged. `resolved` has every alias replaced by
the corresponding waveform object.

## Executing and reading results

```python
result = platform.execute(resolved)

data = result.get(m0)             # xarray.DataArray
data.dims                         # ("gain", "IQ")
data.shape                        # (100, 2)
data.coords["gain"]                # 0.00, 0.01, ..., 0.99
data.coords["IQ"]                 # ["I", "Q"]

I = data.sel(IQ="I")
Q = data.sel(IQ="Q")

import matplotlib.pyplot as plt
plt.plot(data.coords["gain"], I, label="I")
plt.plot(data.coords["gain"], Q, label="Q")
plt.xlabel("Drive amplitude (V)")
plt.legend()
```

## Adapt this to your chip

- Change the schema if you are not on a fixed-frequency transmon. See
  [Buses and schemas](../guide/buses.md).
- Sweep frequency instead of gain by adding a second `for_loop` (or a
  parallel `|` composition). See [Control flow](../guide/control-flow.md).
- Use `returns="iq,raw"` if you also want the raw ADC trace. See
  [Measurements and results](../guide/measurements.md).
