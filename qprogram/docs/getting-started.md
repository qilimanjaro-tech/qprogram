# Getting started

This page walks you from a clean checkout to a running QProgram. It assumes
Python 3.11+ and [uv](https://docs.astral.sh/uv/) for environment management.
The package also installs fine with plain `pip`.

## Install

```bash
cd qprogram
uv sync
uv run python -c "import qprogram; print(qprogram.__doc__)"
```

This pulls in `numpy` and `xarray` (the only runtime dependencies) plus the
dev tooling. The `qprogram` import alone gives you the AST, expressions,
serialization, and bus schema. Vendor-specific operations come from separate
packages that follow the protocol described in
[Building a vendor extension](developer/vendor-extensions.md).

### Building the docs

```bash
cd qprogram
uv sync --group docs
uv run mkdocs serve
```

## A first program

Save this as `rabi.py` inside the `qprogram` directory and run it with
`uv run python rabi.py`.

```python
import qprogram as qp
from qprogram.buses import BusSchema

schema = BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
gain = program.variable("gain", units="V")

with program.average(shots=1000):
    with program.for_loop(gain, 0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        program.measure(q[0].readout, "readout", "weights")

print(qp.dumps(program))
```

Running this prints the program in `.qp` form. No platform is needed yet; you
are just building the AST and serialising it.

## Save and reload

`qp.save` and `qp.load` go through the same code path as `dumps` / `loads`.
You can pass either a file path or a string.

```python
qp.save(program, "rabi.qp")
reloaded = qp.load("rabi.qp")

assert reloaded.body == program.body     # structural equality
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

resolved = program.with_waveforms({
    "pi_pulse": IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1),
    "readout":  IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights":  IQPair(Square(1.0, 2000), Square(1.0, 2000)),
})
```

`with_waveforms` returns a new program; the original keeps its string
aliases. This separation matters: the same program description can run
against many calibration sets without rewriting.

## Send it to hardware

QProgram itself never talks to instruments. A platform implementation does
that, via the [`PlatformProtocol`](reference/api-qprogram.md#qprogram.PlatformProtocol):

```python
from qililab import build_platform        # any platform that implements the protocol

platform = build_platform("my_chip.yaml")
result = platform.execute(resolved)

# Results are xarray.DataArray instances with dimensions named after the loops.
data = result.get(measurement=0)
print(data.dims, data.shape)
```

If you only want to inspect a program without running it, the
`evaluate_or_raise` helpers on every `Expression` and `Waveform` let you bind
variables and pull out numbers in pure Python.

## Where to next?

- [Core ideas](guide/concepts.md) covers the AST, blocks, operations, and the
  hardware-vs-software boundary.
- [Buses and schemas](guide/buses.md) explains typed bus references and why
  you almost always want them.
- [Building a vendor extension](developer/vendor-extensions.md) shows how to
  add a new namespace with custom operations.
