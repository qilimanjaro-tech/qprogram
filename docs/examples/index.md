# Examples

Each page in this section is one program given in full: the Python that builds
it, the `.qp` text it serializes to, and the calls that run it and read the
results back. Nothing here needs an instrument, because `qp.simulate` runs on
the reference platform, the pure-Python interpreter that ships with the
package. Rabi adds a plotting section; the chevron reads two result records
instead of one.

| Example | The features it exercises |
|---|---|
| [Rabi oscillation](rabi.md) | One sweep inside an averaging block, one pulse, one measurement, and a result with a single sweep dimension plus the trailing `IQ` axis. |
| [CZ chevron](cz-chevron.md) | Two nested sweeps over a flux pulse's amplitude and duration, a single-channel bus, sweep variables as waveform parameters, and two measurements per shot on separate buses. |

The alias split each program is built around is deliberate. The program is
written against string waveform aliases (`"pi_pulse"`, `"readout"` and
`"weights"` in the Rabi program) so that it describes the experiment rather
than one calibration of it. `program.with_waveforms(library)` then
returns a copy with those aliases replaced by concrete waveforms and leaves
the original alone, which is what lets one program text run against many
calibration sets. A platform consumes the resolved copy; the reference
executor accepts either, since it never renders an envelope.

The numbers a run produces come from a measurement model, not from physics.
`qp.simulate(program)` with no `model=` argument uses a
`qp.MockMeasurementModel` whose IQ response is `0j`, so every value comes back
as exactly `0.0`. The dimensions, coordinates, and shapes are the real
contract; the values are not, and both pages pass a response function where
the values are what the plot is about.

## Related pages

[Operations](../guide/operations.md) lists the operations these programs use,
[Control flow](../guide/control-flow.md) covers sweeps, averaging, and
parallel composition, and [Measurements and
results](../guide/measurements.md) covers handles, fields, and result shapes.
The generated [API reference](../reference/api-qprogram.md) carries the
signatures.
