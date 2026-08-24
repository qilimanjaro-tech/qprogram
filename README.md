# QProgram

[![Tests](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/tests.yml/badge.svg)](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/tests.yml)
[![Code Quality](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/code_quality.yml/badge.svg)](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/code_quality.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

QProgram is a Python DSL for describing pulse-level quantum experiments. You write what you
want the chip to do; the platform decides how to run it.

The core package knows nothing about any particular instrument. It defines the language, the
AST, a text file format, a capability protocol platforms validate programs against, and the
extension hooks vendor packages plug into.

## Installation

```bash
pip install qprogram
```

Optional extras: `qprogram[viz]` adds `Waveform.plot()`, and `qprogram[lsp]` adds the language
server used by editor integrations.

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

# Bind calibrated waveforms at the very end; the program itself only names them.
resolved = program.with_waveforms(
    {
        "pi_pulse": IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
        "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
    }
)

result = qp.simulate(resolved)
data = result.get(m0)  # xarray.DataArray with named dimensions
```

`qp.simulate` runs the reference software executor that ships with the package, which is also
the executable definition of the language's semantics. Hardware platforms implement the same
`PlatformProtocol` interface.

## What you get

- **One program, many backends.** The same `QProgram` runs on any platform that speaks the
  protocol. Vendor-specific extras — markers, active reset, triggers, slow-control parameters —
  live in optional vendor packages and stay out of the core.
- **Real Python.** Loops, variables, function arguments, and comprehensions all work. Pulses are
  objects you can pass around, inspect, and serialize.
- **A text file format.** `qp.save(program, "exp.qp")` writes a readable `.qp` file that diffs
  cleanly, plays back identically, and survives version upgrades through explicit `require`
  lines.
- **Typed bus references.** A `BusSchema` gives tab-completion and validation for drives,
  readouts, fluxes, and couplers without locking you into a naming convention.
- **Capability-checked programs.** Platforms declare what they support per bus and domain, and
  `qp.explain(program, capabilities)` shows exactly which part of a program a backend cannot run
  and why.

## How a program travels

A pulse experiment passes through six steps — **Define → Serialize → Explain → Optimize →
Execute → Results**. QProgram owns all of them but Execute, which the platform you supply
carries out. Results come back shaped by the program that produced them: labelled `xarray`
arrays whose axes are named by the sweeps that generated them, rather than unlabelled buffers.

## Documentation

Full documentation, including the user guide, the `.qp` format reference, and the generated API
reference, lives at <https://qilimanjaro-tech.github.io/qprogram/>.

## Development

The project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras          # create .venv and install everything
uv run pytest                 # run the test suite
uv run ruff check .           # lint
uv run ruff format .          # format
uv run ty check               # type-check
uv run --group docs zensical serve   # preview the documentation
```

## Reference

The design is described in *"QProgram: A Hardware-Agnostic DSL for Portable Pulse-Level Quantum
Programming"* by Vyron Vasileiadis, Flavie Le Bars, and David Arcos (Qilimanjaro Quantum Tech,
Barcelona, Spain).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
