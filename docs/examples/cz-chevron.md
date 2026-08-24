# CZ chevron

A CZ chevron pattern is a two-axis sweep used to calibrate a two-qubit gate.
You prepare a state, fire a flux pulse with varying amplitude and duration,
and read the qubits out. The signature pattern in the resulting heatmap
tells you where the gate lives in parameter space.

This example shows three useful features at once: a flux-tunable schema,
nested sweeps, and inline waveforms with variable parameters.

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
    with program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):
        with program.sweep(dur, qp.Range(10, 210, 2)):
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
- The two sweeps are nested, `amp` outside and `dur` inside, so the flux
  pulse visits every `(amp, dur)` combination — the square grid the chevron
  pattern lives on. Nesting order sets which axis moves fastest, and it is
  the order the result dimensions come back in.
- `FlatTop(amplitude=amp, duration=dur, smooth_duration=5)` is an inline
  waveform that carries the two sweep variables in its constructor. The
  platform decides how to materialize the sweep: update an amplitude
  register, regenerate the envelope, or whatever else its compiler supports.
- `m0` and `m1` are two separate `MeasurementHandle`s on the same shot. The
  result object carries a record for each, retrieved by handle.

## What it looks like on disk

```
#!QProgram 1.0

metadata:
  label: "cz_chevron"
  description: "Sweep flux amplitude and duration to map the CZ chevron"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires
    flux info=single

body:
  var amp label="Flux amplitude" units="V"
  var dur label="Flux duration" units="ns"

  average 1000:
    for amp in Range(start=0.0, stop=1.0, step=0.01):
      for dur in Range(start=10.0, stop=210.0, step=2.0):
        play q[1].drive "pi"
        sync
        play q[0].flux FlatTop(amplitude=amp, duration=dur, smooth_duration=5, buffer=0)
        sync
        measure q[0].readout "readout" "weights" name="q0/readout/m0"
        measure q[1].readout "readout" "weights" name="q1/readout/m0"
```

`FlatTop`'s `buffer` argument defaults to `0`; the writer spells out every
constructor argument so the file rebuilds the waveform exactly.

## Reading the results

The reference executor is a pure-Python interpreter that walks every shot of
every grid point. At the sizes above that is 101 amplitudes x 101 durations x
1000 shots x 2 measurements, which runs for a few minutes; an 11 x 11 grid at
the same shot count takes about 4.4 s. Coarsen the `Range` steps or lower
`average(shots=...)` while you are iterating — the dimensions and coordinates
below come back the same shape either way.

```python
from qprogram.waveforms import IQDrag, IQPair, Square

library = {
    "pi": IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
    "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
}
result = qp.simulate(program.with_waveforms(library))

data0 = result.get(m0)
data1 = result.get(m1)

# One dimension per enclosing sweep, outermost first.
data0.dims  # ("amp", "dur", "IQ")
data0.shape  # (101, 101, 2)
data0.coords["amp"]
data0.coords["dur"]
```

To plot the chevron, project to the IQ component you care about — the array
is already on the 2D grid:

```python
import matplotlib.pyplot as plt

I = data0.sel(IQ="I")
plt.pcolormesh(data0.coords["dur"], data0.coords["amp"], I)
plt.xlabel("Flux duration (ns)")
plt.ylabel("Flux amplitude (V)")
```

## Notes

- To move both parameters together — one diagonal cut through the grid
  instead of the whole grid — compose the loops in parallel:
  `with program.sweep(amp, ...) | program.sweep(dur, ...):`. Both loops must
  then have the same number of iterations, and the results come back on one
  `"amp|dur"` dimension carrying both coordinates. See
  [Control flow](../guide/control-flow.md).
- Swap `FlatTop` for `SuddenNetZero(amplitude=amp, duration=dur, b=0.5,
  t_phi=4)` for the SNZ flavor of CZ. The rest of the program stays the same.
- Add `fields=(qp.MeasurementField.IQ, qp.MeasurementField.RAW)` on the
  measurements if you also want the raw ADC trace. See
  [Measurements and results](../guide/measurements.md).
