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
  serialize.
- **A text file format.** `qp.save(program, "exp.qp")` produces a readable
  `.qp` file that diffs nicely, reloads to a structurally equal program, and
  names the vendor extensions it needs in explicit `require` lines that the
  parser checks against the installed packages on load.
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
    with program.sweep(gain).from_range(0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        m0 = program.measure(q[0].readout, "readout", "weights")

# Plug in calibrated waveforms at the very end.
resolved = program.with_waveforms(
    {
        "pi_pulse": IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
        "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
    }
)

# Run it. `qp.simulate` is the reference software executor that ships with the
# package; hardware platforms implement the same `PlatformProtocol` interface.
result = qp.simulate(resolved)
data = result.get(m0)  # xarray.DataArray with named dimensions
```

## Where to go next

| If you want to ...                              | Read                                                                |
|-------------------------------------------------|---------------------------------------------------------------------|
| install QProgram and run something              | [Getting started](getting-started.md)                                |
| understand the moving parts                     | [Core ideas](guide/concepts.md)                                      |
| sweep parameters with loops                     | [Control flow](guide/control-flow.md)                                |
| run a program without hardware                  | [Running programs](guide/execution.md)                               |
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

QProgram is pre-1.0, so the Python API is allowed to move. The on-disk
format is steadier: every `.qp` file carries a format version, and vendor
compatibility is checked at parse time. A saved file loads against any
installed extension that shares its major version and is no older in minor;
anything else is a `ParseError` rather than a silent partial load.

The reference for intended behavior is this documentation together with the
package itself. The [API reference](reference/api-qprogram.md) is generated
from the source, [.qp file format](reference/qp-format.md) describes the
on-disk grammar, and `src/qprogram/grammar/qp.lark` is the normative
machine-readable form of that grammar, kept in step with the production parser
by the test suite.
