# QProgram

[![Tests](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/tests.yml/badge.svg)](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/tests.yml)
[![Code Quality](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/code_quality.yml/badge.svg)](https://github.com/qilimanjaro-tech/qprogram/actions/workflows/code_quality.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

QProgram is a Python DSL for describing pulse-level quantum experiments. You
write what you want the chip to do; the platform decides how to run it.

The core package knows nothing about any particular instrument. It defines the
language, the AST, a text file format, a capability protocol platforms validate
programs against, and the extension hooks vendor packages plug into. Its only
runtime dependencies are `numpy` and `xarray`.

## Installation

```bash
pip install qprogram
```

Optional extras: `qprogram[viz]` adds `Waveform.plot()`, and `qprogram[lsp]`
adds the language server used by editor integrations.

## A first program

```python
import qprogram as qp

schema = qp.BusSchema.transmon()
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
        "pi_pulse": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
        "weights": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)),
    }
)

# Run it. `qp.simulate` is the reference software executor that ships with the
# package; hardware platforms implement the same `PlatformProtocol` interface.
result = qp.simulate(resolved)
data = result.get(m0)  # xarray.DataArray with named dimensions
```

`data` comes back with dimensions `("gain", "IQ")` and shape `(101, 2)`:
dimensions are named after the enclosing loops, outermost first, and the 1000
shots of the `average` block are reduced rather than kept. The reference
executor is the executable definition of the language's semantics.

## What the package does

Nothing in the package reaches an instrument. A program is a description: it is
built, checked, and handed over. Turning it into instrument code, placing it on
a timeline, and calibrating its pulses all happen behind `qp.PlatformProtocol`,
which is why the same program runs wherever that protocol is implemented. The
core stays small for that reason: it holds only what any platform could be
asked to do. Instrument-specific work (markers, active reset, triggers,
slow-control parameters) comes from optional vendor packages that register
themselves on import, and a `.qp` file that uses one records the dependency as
a `require` line and refuses to load without it.

## Documentation

Full documentation, including the user guide, the `.qp` format reference, and
the generated API reference, lives at
<https://qilimanjaro-tech.github.io/qprogram/>.

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

The design is described in *"QProgram: A Hardware-Agnostic DSL for Portable
Pulse-Level Quantum Programming"* by Vyron Vasileiadis, Flavie Le Bars, and
David Arcos (Qilimanjaro Quantum Tech, Barcelona, Spain).

## License

Apache License 2.0. See [LICENSE](LICENSE).
