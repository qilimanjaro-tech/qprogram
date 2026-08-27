# Resonator spectroscopy

Finding the readout resonator is the first measurement made on a new chip:
sweep the frequency of the tone sent down the readout line and watch where the
transmitted amplitude dips. Until that dip is located there is no readout, and
without readout none of the other pages have a measurement to make. The program
is the shortest one in this section, two operations inside two blocks.

What it is here to show is not its length. Every other example sweeps something
the sequencer owns, a gain register or an NCO word or a waveform parameter, and
the whole program runs in real time. A local oscillator is not that. It is a
synthesizer reprogrammed over a control interface, so retuning it is platform
configuration rather than an instruction in a pulse sequence, and a sweep that
retunes one per iteration cannot run on the sequencer at all. That fact
propagates outward through the program, and this page is about reading where it
went and rewriting the program so it costs less.

## The program

```python
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(
    label="resonator_spectroscopy",
    description="Sweep the readout LO across the resonator",
    schema=schema,
)
lo = program.variable("lo", label="Readout LO", units="Hz")

with program.average(shots=1000):
    with program.sweep(lo, qp.Linspace(7.0e9, 7.4e9, num=101)):
        program.set_parameter(q[0].readout, "lo_frequency", lo)
        m0 = program.measure(q[0].readout, "readout", "weights")
```

### Why each piece is where it is

`set_parameter(bus, parameter, value)` writes a bus-scoped parameter. It is the
operation for things a platform holds as configuration rather than as sequencer
state, and platforms expose it host-side only for that reason. The parameter
name is a free string that nothing validates at build time or at run time:
`"lo_frequency"` means whatever the platform decides it means, and a typo
becomes a parameter the platform has never heard of rather than an error.

There is no `play` in the loop. `measure` plays the readout waveform and
acquires against the weights itself, so a resonator sweep needs nothing else,
and no `sync` either since only one bus is involved. This is a one-tone
measurement, which is what separates it from the two-tone
[qubit spectroscopy](qubit-spectroscopy.md) scan: there the drive tone
interrogates the qubit and a separate readout reports on it, while here the
readout tone is both the probe and the measurement.

The window covers 400 MHz because a resonator's position is known only to
within the spread of the fabrication run, and 101 points put a sample every
4 MHz, comfortably finer than a linewidth of a few hundred kHz would need but
coarse enough to find the dip in one pass. The fine scan comes after.

## What the platform makes of it

```
#!QProgram 1.0

metadata:
  label: "resonator_spectroscopy"
  description: "Sweep the readout LO across the resonator"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var lo label="Readout LO" units="Hz"

  average 1000:
    for lo in Linspace(start=7000000000.0, stop=7400000000.0, num=101):
      set_parameter q[0].readout "lo_frequency" lo
      measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

Nothing in that file says anything about domains. Where each node can run is a
question about a platform, so it is answered by `qp.validate` and rendered by
`qp.explain` against a capability descriptor:

```python
caps = qp.reference_capabilities()
print(qp.explain(program, caps))
```

```
plan for 'resonator_spectroscopy' — errors: 0 · warnings: 1 · info: 1
body
└─ average 1000:                                                           [host]     ~ forced-host: contains host-side-only sub-block 'Sweep' (parameter 'lo_frequency' is swept via set_parameter (host-side dispatch per iteration))  i reorderable-averaging: Block 'Average' runs host-side only because it encloses a host-side sweep; its measurement sequence supports real-time hardware. Moving the sweep outside the average (hoisting the host-side-only setup with it) would let the averaging run in real-time hardware — see qprogram.optimize().
   └─ for lo in Linspace(start=7000000000.0, stop=7400000000.0, num=101):  [host]
      ├─ set_parameter q[0].readout "lo_frequency" lo                      [host]
      └─ measure q[0].readout "readout" "weights" name="q0/readout/m0"     [rt|host]
```

The measurement is `[rt|host]`, so the sequencer could run it. Everything above
it is `[host]`, and the reason is a chain: the `set_parameter` is host-side
only, which makes the sweep containing it host-side only, which makes the
average containing that host-side only. The `forced-host` warning is emitted
once, on the outermost block of that chain, because reporting it on all three
would say the same thing three times.

The cost is in the innermost block that got dragged. An averaging block that
runs host-side is a thousand separate acquisitions per sweep point, each with
its own round trip between the host and the instrument, where a real-time
average is one instruction the sequencer executes a thousand times. See
[Capabilities, diagnostics, and profiles](../guide/capabilities.md) for how the
domain of a block is decided and what the other diagnostic codes mean.

## Rewriting it with qp.optimize

The `reorderable-averaging` hint on the same row names the fix. The sweep has
to stay host-side, since the LO is what it is, but it does not have to be
*inside* the average. Turn the nesting inside out and the averaging is back on
the sequencer:

```python
optimized = qp.optimize(program, caps)
```

```
body:
  var lo label="Readout LO" units="Hz"

  for lo in Linspace(start=7000000000.0, stop=7400000000.0, num=101):
    set_parameter q[0].readout "lo_frequency" lo
    average 1000:
      measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

`qp.optimize(program, caps)` returns a new program and never touches the one it
was given. The rewrite lifts the sweep to the outside, hoists the leading run
of host-side-only operations along with it, and leaves the rest inside a fresh
average. Re-explaining the result reports `errors: 0 · warnings: 0 · info: 0`,
with the average now `[rt|host]` and only the sweep and the parameter write
still `[host]`.

It is opt-in rather than automatic because it is not unconditionally
equivalent. It groups all thousand shots of one frequency together instead of
interleaving passes over the whole window, which averages out drift differently
and is identical only for a system that is not drifting. It also moves each
hoisted operation from once per shot to once per sweep point, which is
harmless for an idempotent parameter write and is not harmless in general. The
rewrite protects itself by only ever hoisting a leading contiguous run, never
an operation that sits after one it is keeping, since that would reorder the
two.

The pattern it matches is narrow, and it is worth seeing it decline. Nest a
second sweep inside the first and the average is still forced host-side, but
the hint is gone and `qp.optimize` returns the program unchanged:

```python
with program.average(shots=1000):
    with program.sweep(lo, qp.Linspace(7.0e9, 7.4e9, num=101)):
        program.set_parameter(q[0].readout, "lo_frequency", lo)
        with program.sweep(power, qp.Linspace(0.1, 1.0, num=10)):
            program.set_gain(q[0].readout, power)
            program.measure(q[0].readout, "readout", "weights")
# [warning] forced-host: ... and no reorderable-averaging hint
```

The average's sole child must be one flat sweep whose body holds no nested
block. The hint and the rewrite share the same predicate, so the two can never
disagree: an absent hint means an absent rewrite, and reading the diagnostics
is how you find out that calling `optimize` did nothing.

## Running it

The parameter store lives on the platform rather than in the program, so this
is the one example that builds a `qp.ReferencePlatform` directly instead of
going through `qp.simulate`. Keeping the platform is what lets the writes be
read back afterwards:

```python
import warnings

library = {
    "readout": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
    "weights": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)),
}


def notch(bus, env):
    """A resonator dip: unit transmission away from 7.2 GHz, zero at it."""
    f = env["q0/readout.lo_frequency"]
    return 1.0 - 1.0 / (1.0 + ((f - 7.2e9) / 4e6) ** 2) + 0j


platform = qp.ReferencePlatform(
    schema=schema,
    model=qp.MockMeasurementModel(response=notch, noise=0.005),
)

with warnings.catch_warnings():
    warnings.simplefilter("ignore", qp.ExecutionWarning)
    result = platform.execute(program.with_waveforms(library))

result.get(m0).dims  # ("lo", "IQ")
result.get(m0).shape  # (101, 2)
platform.parameters  # {"q0/readout.lo_frequency": 7400000000.0}
```

A swept parameter reaches the model differently from a swept variable. The
earlier pages read `env["freq"]` or `env["delay"]`, the id of the loop
variable; a parameter write puts its value in the same `env` under
`"bus.parameter"`, so the model reads `env["q0/readout.lo_frequency"]`. Both
are available at once, and here the loop variable `env["lo"]` holds the same
number, but the parameter key is what a model should use, because it reports
what the instrument was actually configured to rather than what a loop
happened to be carrying.

`platform.parameters` survives the run and holds the last value written, which
is `7.4e9`, the end of the sweep. The store is copied at construction and then
read by `get_parameter`, written by `set_parameter`, and passed to the model,
so writes accumulate across successive `execute` calls on the same platform.
`qp.simulate` builds a throwaway platform and discards it, which is why it is
the wrong entry point when the parameter store is part of what you are looking
at.

The `forced-host` warning is re-emitted at execution as a `qp.ExecutionWarning`
rather than being raised, since the program does run, just not the way it was
written. Filtering it, as above, is reasonable once you have read it; leaving
it unfiltered is better while the program is still changing. Errors are not
handled this way: an error diagnostic raises `UnsupportedOperationError` and
nothing executes.

## Adapting it

To read a parameter rather than write one, `get_parameter` returns a freshly
declared variable the runtime fills in:

```python
current = program.get_parameter(q[0].readout, "lo_frequency")
# var q0_readout_lo_frequency label="q0/readout.lo_frequency"
# get_parameter q[0].readout "lo_frequency" -> q0_readout_lo_frequency
```

The variable id is derived from the bus and parameter with the non-word
characters replaced, and the original dotted form is kept as the label. From
there it is an ordinary variable and can be used in any expression.

A punchout measurement adds a power axis outside the frequency one and watches
the dip move as the resonator is driven past its critical photon number. That
is the nested shape `qp.optimize` declines, which is the honest trade: the
second axis costs the real-time averaging back.

The same experiment is not host-side on every instrument. A platform whose
readout chain has its own NCO can sweep the tone with `set_frequency` instead
of retuning an LO, and then nothing here happens at all: the sweep is
`[rt|host]`, the average never falls back, and there is no rewrite to apply.
Which of the two a program should be written against is a capability question
you can ask before running anything, with
[`qp.explain`](../guide/capabilities.md) against that platform's descriptor.
