# Getting started

This page walks you from an empty environment to a running QProgram. It
assumes Python 3.11 or newer.

## Install

```bash
pip install qprogram
```

That pulls in `numpy` and `xarray`, the only runtime dependencies. Two extras
are available:

```bash
pip install "qprogram[viz]"   # matplotlib, for Waveform.plot()
pip install "qprogram[lsp]"   # pygls, for the .qp language server
```

The `qprogram` import alone gives you the AST, expressions, serialization, the
bus schema, and the reference software executor. Vendor-specific operations
come from separate packages that follow the protocol described in
[Building a vendor extension](developer/vendor-extensions.md).

### Working on QProgram itself

The repository uses [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/qilimanjaro-tech/qprogram
cd qprogram
uv sync --all-extras
uv run pytest
```

To preview the documentation:

```bash
uv run --group docs zensical serve
```

## A first program

Save this as `rabi.py` and run it with `python rabi.py`.

```python
import qprogram as qp
from qprogram.buses import BusSchema

schema = BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
gain = program.variable("gain", units="V")

with program.average(shots=1000):
    with program.sweep(gain).from_range(0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        program.measure(q[0].readout, "readout", "weights")

print(qp.dumps(program))
```

Running this prints the program in `.qp` form. No platform is needed yet; you
are just building the AST and serializing it.

## Save and reload

`qp.save` and `qp.load` go through the same code path as `dumps` / `loads`.
They take a file path; `dumps` / `loads` take and return a string.

```python
qp.save(program, "rabi.qp")
reloaded = qp.load("rabi.qp")

assert reloaded.body == program.body  # structural equality
assert qp.dumps(reloaded) == qp.dumps(program)
```

The on-disk text is human-readable and stable. See
[.qp file format](reference/qp-format.md) for the full grammar.

## Plug calibration data in

A QProgram normally references waveforms by alias (`"pi_pulse"`,
`"readout"`, ...). The actual pulses are calibration data that the platform
supplies at execution time. You can do the same yourself:

```python
from qprogram.waveforms import IQDrag, IQPair, Square

resolved = program.with_waveforms(
    {
        "pi_pulse": IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
        "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
    }
)
```

`with_waveforms` returns a new program; the original keeps its string
aliases. This separation matters: the same program description can run
against many calibration sets without rewriting.

## Run it

QProgram itself never talks to instruments. A platform implementation does
that, via the
[`PlatformProtocol`](reference/api-qprogram.md#qprogram.PlatformProtocol). The
package ships one such platform: `ReferencePlatform`, a pure-Python
interpreter. A program is therefore runnable before any hardware is involved:

```python
result = qp.simulate(resolved)

# Results are xarray.DataArray instances with dimensions named after the loops.
data = result.get(measurement=0)
print(data.dims, data.shape)  # ('gain', 'IQ') (101, 2)
```

A hardware platform is a drop-in for the same call:
`platform.execute(resolved)` returns the same `QProgramResult` shape. See
[Running programs](guide/execution.md) for the executor and
[Measurements and results](guide/measurements.md) for the result contract.

If you only want to inspect a program without running it,
`Expression.evaluate_or_raise()` binds variables and pulls out numbers in pure
Python, and `Waveform.envelope()` renders a shape to samples.

## Where to next?

- [Core ideas](guide/concepts.md) covers the AST, blocks, operations, and the
  real-time-vs-host-side boundary.
- [Buses and schemas](guide/buses.md) explains typed bus references and why
  you almost always want them.
- [Building a vendor extension](developer/vendor-extensions.md) shows how to
  add a new namespace with custom operations.
