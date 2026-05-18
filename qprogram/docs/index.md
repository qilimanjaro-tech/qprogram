# QProgram

QProgram is a small Python DSL for describing pulse-level quantum experiments.
You write what you want the chip to do; the platform decides how to run it.

## What you get

- **One program, many backends.** The same `QProgram` runs on any platform
  that speaks the protocol. Vendor-specific extras (markers, active reset,
  triggers, slow-control parameters, ...) live in optional vendor packages
  and stay out of the core.
- **Real Python.** Use loops, variables, function arguments, list
  comprehensions. Pulses are objects you can pass around, inspect, and
  serialise.
- **A text file format.** `qp.save(program, "exp.qp")` produces a readable
  `.qp` file that diffs nicely, plays back identically, and survives version
  upgrades through explicit `require` lines.
- **Typed bus references.** A `BusSchema` gives you tab-completion and
  validation for drives, readouts, fluxes, and couplers, without locking you
  into any naming convention.

## A first program

```python
import qprogram as qp
from qprogram.buses import BusSchema
from qprogram.waveforms import IQDrag, IQPair, Square

schema = BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
gain = program.variable("gain", units="V")

with program.average(shots=1000):
    with program.for_loop(gain, 0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        m0 = program.measure(q[0].readout, "readout", "weights")

# Plug in calibrated waveforms at the very end.
resolved = program.with_waveforms({
    "pi_pulse": IQDrag(0.5, 40, 2.5, 0.1),
    "readout":  IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights":  IQPair(Square(1.0, 2000), Square(1.0, 2000)),
})

# Send it to whichever platform you have at hand.
result = platform.execute(resolved)
data = result.get(m0)        # xarray.DataArray with named dimensions
```

## Where to go next

| If you want to ...                              | Read                                                                |
|-------------------------------------------------|---------------------------------------------------------------------|
| install QProgram and run something              | [Getting started](getting-started.md)                                |
| understand the moving parts                     | [Core ideas](guide/concepts.md)                                      |
| sweep parameters with loops                     | [Control flow](guide/control-flow.md)                                |
| know which programs a platform will accept       | [Capabilities, diagnostics, and profiles](guide/capabilities.md)     |
| read the file format                            | [.qp file format](reference/qp-format.md)                            |
| build your own vendor package                   | [Building a vendor extension](developer/vendor-extensions.md)        |
| browse the full API                             | [API reference](reference/api-qprogram.md)                           |

## A package with no vendors

QProgram itself knows nothing about any specific instrument. It defines the
language, the AST, the file format, and the extension protocol. Vendors plug
in through three orthogonal hooks at import time: a runtime namespace, a
typed mixin for IDE autocomplete, and a serialization registry entry. The
[Architecture](developer/architecture.md) and
[Building a vendor extension](developer/vendor-extensions.md) pages explain
the pattern.

## Status

QProgram is still pre-1.0. The on-disk format carries a version number and
checks vendor compatibility at parse time, so files saved today should keep
parsing as long as the version contract holds. The Python API is allowed to
move during this phase. The two specifications in `.specs/` are the source of
truth when code and intent disagree.
