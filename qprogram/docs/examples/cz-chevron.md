# CZ chevron

A CZ chevron pattern is a two-axis sweep used to calibrate a two-qubit gate.
You prepare a state, fire a flux pulse with varying amplitude and duration,
and read the qubits out. The signature pattern in the resulting heatmap
tells you where the gate lives in parameter space.

This example shows three useful features at once: a flux-tunable schema,
parallel loops, and inline waveforms with variable parameters.

## The program

```python
import qprogram as qp
from qprogram.buses import BusSchema
from qprogram.waveforms import FlatTop

schema = BusSchema.flux_tunable_transmon()
q = schema.q

program = qp.QProgram(
    label="cz_chevron",
    description="Sweep flux amplitude and duration to map the CZ chevron",
    schema=schema,
)
amp = program.variable("amp", label="Flux amplitude", units="V")
dur = program.variable("dur", label="Flux duration", units="ns")

with program.average(shots=1000):
    with program.for_loop(amp, 0.0, 1.0, 0.01) | program.for_loop(dur, 10, 200, 2):
        # Prepare q1 in |1>.
        program.play(q[1].drive, "pi")
        program.sync()

        # Apply the flux pulse with sweeping amplitude and duration.
        program.play(q[0].flux, FlatTop(amplitude=amp, duration=dur, smooth_duration=5))
        program.sync()

        # Read out both qubits.
        m0 = program.measure(q[0].readout, "readout", "weights")
        m1 = program.measure(q[1].readout, "readout", "weights")
```

## What is going on

- The schema is `flux_tunable_transmon`, so each qubit has `drive`,
  `readout`, and a `flux` line.
- `program.for_loop(amp, ...) | program.for_loop(dur, ...)` runs the two
  sweeps in parallel. The flux pulse therefore sees `(amp, dur)` pairs in
  lockstep, which is what makes a chevron rather than a square grid.
- `FlatTop(amplitude=amp, duration=dur, smooth_duration=5)` is an inline
  waveform that carries the two sweep variables in its constructor. The
  platform decides how to materialise the sweep: update an amplitude
  register, regenerate the envelope, or whatever else its compiler supports.
- `m0` and `m1` are two separate `MeasurementHandle`s on the same shot. The
  result object will have both, indexed by qubit.

## What it looks like on disk

```
#!QProgram 1.0

metadata:
  label: "cz_chevron"
  description: "Sweep flux amplitude and duration to map the CZ chevron"

schema:
  element q:
    drive   info=IQ
    readout info=IQ+acquires
    flux    info=single

body:
  var amp label="Flux amplitude" units="V"
  var dur label="Flux duration" units="ns"

  average 1000:
    for amp in range(0.0, 1.0, 0.01) | for dur in range(10, 200, 2):
      play q[1].drive "pi"
      sync
      play q[0].flux FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
      sync
      measure q[0].readout "readout" "weights" name="q0_m0"
      measure q[1].readout "readout" "weights" name="q1_m0"
```

## Reading the results

```python
result = platform.execute(program.with_waveforms({...}))

data0 = result.get(m0)
data1 = result.get(m1)

# Parallel loops share a dimension named after both variables.
data0.dims              # ("amp|dur", "IQ")
data0.coords["amp"]
data0.coords["dur"]
```

To plot the chevron, project to the IQ component you care about and reshape
back to the original 2D grid:

```python
import numpy as np
import matplotlib.pyplot as plt

I = data0.sel(IQ="I").values
amp_vals = data0.coords["amp"].values
dur_vals = data0.coords["dur"].values
plt.pcolormesh(dur_vals, amp_vals, I.reshape(len(amp_vals), len(dur_vals)))
```

## Notes

- The two loops must have the same number of iterations for the `|`
  composition to work. The platform raises a clear error if they do not.
- Replace `FlatTop` with `SuddenNetZero` for the SNZ flavour of CZ. The rest
  of the program stays the same.
- Add `returns="iq,raw"` on the measurements if you also want the raw ADC
  trace.
