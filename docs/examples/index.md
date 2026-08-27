# Examples

Each page in this section is one program given in full: the Python that builds
it, the `.qp` text it serializes to, and the calls that run it and read the
results back. Nothing here needs an instrument, because `qp.simulate` runs on
the reference platform, the pure-Python interpreter that ships with the
package. The pages are ordered so that each one adds a few pieces to the ones
before it, and each names what those are in its opening paragraphs.

| Example | The features it exercises |
|---|---|
| [Rabi oscillation](rabi.md) | One sweep inside an averaging block, one pulse, one measurement, and a result with a single sweep dimension plus the trailing `IQ` axis. |
| [Qubit spectroscopy](qubit-spectroscopy.md) | A frequency window swept with `qp.Linspace` and written to the drive oscillator by `set_frequency`, a long saturation tone played as an alias, and an arithmetic expression over the swept variable. |
| [T1 and Ramsey](t1-and-ramsey.md) | Two programs sharing one skeleton: a swept `wait` as the axis, `reset_phase` and `set_phase` carrying an expression folded to a single constant, and `MeasurementField.STATE` read back as an excited-state population. |
| [CZ chevron](cz-chevron.md) | Two nested sweeps over a flux pulse's amplitude and duration, a single-channel bus, sweep variables as waveform parameters, and two measurements per shot on separate buses. |
| [Active reset](active-reset.md) | A measurement requesting state classification, an `if_` / `else_` chain conditioned on `handle.state`, two measurements on one bus in one shot, and the `qp.validate` diagnostic a missing `fields=` produces. |
| [Resonator spectroscopy](resonator-spectroscopy.md) | A sweep driven by `set_parameter`, the execution plan `qp.explain` renders for it, the `forced-host` warning that follows, and `qp.optimize` hoisting the sweep so the averaging runs in real time. |
| [CPMG on two qubits](cpmg-fragments.md) | A `@qp.fragment` whose Python loop unrolls at decoration time, two `program.call` sites on separate qubits, and `expand()` recovering the measurement handles a call site never returns. |
| [Multiplexed readout](multiplexed-readout.md) | Four readout buses measured in one shot from a Python loop, a `qp.WaveformLibrary` resolving one alias through its exact, family, and global tiers, and the `.wfl` text those tiers serialize to. |
| [Single-shot readout](single-shot-readout.md) | A program with no averaging block, a shot index as its own result dimension, `qp.Values` as a two-point preparation axis, and a hand-written `qp.MeasurementModel` that classifies each shot. |
| [Checking a program before it runs](checking-a-program.md) | A `qp.PlatformCapabilities` built by hand from a custom `qp.Profile` and predicate, three classes of `qp.validate` diagnostic from one program, and each diagnostic path resolved to a line of the `.qp` file. |

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
