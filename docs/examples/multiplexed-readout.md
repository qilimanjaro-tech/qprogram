# Multiplexed readout

Readout resonators on a chip are spaced across one feedline so that a single
line can interrogate all of them at once. A program that reads four qubits per
shot is therefore not four experiments interleaved; it is one experiment with
four records, and the drive sweep in front of it calibrates four pi pulses in
the time one would take.

Two things follow from that, and they are what this page is about. Four
measurements need four handles, which a Python loop produces along with the
program itself. And four resonators are four different pulses under one alias,
which is what `qp.WaveformLibrary` exists for: a plain dict maps a name to one
waveform, while a library maps a name to a waveform per bus.
[Saving and loading](../guide/serialization.md) documents the library and its
`.wfl` format; here it decides what each qubit is actually sent.

## The program

```python
import numpy as np
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q
QUBITS = (0, 1, 2, 3)

program = qp.QProgram(
    label="multiplexed_rabi",
    description="One drive sweep, four qubits read out per shot",
    schema=schema,
)
amp = program.variable("amp", label="Drive amplitude", units="V")

handles = {}
with program.average(shots=1000):
    with program.sweep(amp, qp.Linspace(0.0, 1.0, num=21)):
        for i in QUBITS:
            program.set_gain(q[i].drive, amp)
            program.play(q[i].drive, "pi_pulse")
        program.sync()
        for i in QUBITS:
            handles[i] = program.measure(
                q[i].readout,
                "readout",
                "weights",
                fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
            )
```

### Why each piece is where it is

The Python loop runs while the program is built, so it writes four `set_gain`
and four `play` statements into the AST rather than a loop into the file. The
index is a Python value, and `q[i].drive` resolves it to a bus at that moment.

The drives all come before the single `sync`, which is what makes this one
shot rather than four. Each bus has its own timeline, so four pulses on four
drive buses are concurrent until something aligns them; the `sync` then holds
every readout until the last drive has finished.

`handles` is a dict rather than four names because the loop that builds it is
the loop that decides how many there are. Every one of the four is named
`m0`:

```python
{i: h.name for i, h in handles.items()}
# {0: "q0/readout/m0", 1: "q1/readout/m0", 2: "q2/readout/m0", 3: "q3/readout/m0"}
```

The counter is per bus, so the bus prefix is what distinguishes these, not the
number. Four measurements on four buses are all `m0`; three on one bus would be
`m0`, `m1`, `m2`.

## What it produces

```
#!QProgram 1.0

metadata:
  label: "multiplexed_rabi"
  description: "One drive sweep, four qubits read out per shot"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var amp label="Drive amplitude" units="V"

  average 1000:
    for amp in Linspace(start=0.0, stop=1.0, num=21):
      set_gain q[0].drive amp
      play q[0].drive "pi_pulse"
      set_gain q[1].drive amp
      play q[1].drive "pi_pulse"
      set_gain q[2].drive amp
      play q[2].drive "pi_pulse"
      set_gain q[3].drive amp
      play q[3].drive "pi_pulse"
      sync
      measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state", "iq"]
      measure q[1].readout "readout" "weights" name="q1/readout/m0" fields=["state", "iq"]
      measure q[2].readout "readout" "weights" name="q2/readout/m0" fields=["state", "iq"]
      measure q[3].readout "readout" "weights" name="q3/readout/m0" fields=["state", "iq"]
```

The schema block still declares one element with two bus kinds. It records what
kinds exist, not which qubits a program touched, so four qubits in the body add
nothing to it.

## One alias, four pulses

Every example before this one resolved its aliases with a plain dict, which is
one global tier: `"readout"` means the same waveform everywhere. Four
resonators at four frequencies need four readout pulses, and a
`qp.WaveformLibrary` keys an entry by a bus coordinate as well as a name:

```python
library = qp.WaveformLibrary()
library.set(
    "readout",
    qp.waveforms.IQPair(qp.waveforms.Square(0.9, 1000), qp.waveforms.Square(0.0, 1000)),
    element="q",
    idx=0,
    kind="readout",
)
library.set(
    "readout",
    qp.waveforms.IQPair(qp.waveforms.Square(0.7, 3000), qp.waveforms.Square(0.0, 3000)),
    element="q",
    idx=2,
    kind="readout",
)
library.set(
    "readout",
    qp.waveforms.IQPair(qp.waveforms.Square(0.5, 2000), qp.waveforms.Square(0.0, 2000)),
    element="q",
    kind="readout",
)
library.set(
    "weights",
    qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)),
)
library.set("pi_pulse", qp.waveforms.IQDrag(0.5, 40, 8, 0.1))
```

`element`, `idx`, and `kind` are keyword-only and only three combinations are
legal, one per tier: all three is an exact entry for one bus, `element` and
`kind` is a family entry for every index of that kind, and none of them is a
global entry. Any other combination raises `ValidationError`.

Lookup walks the three in order and takes the first hit, so the two qubits with
their own entry get it and the rest fall through to the family:

```python
for i in QUBITS:
    wf = library.get(q[i].readout, "readout")
    print(i, wf.I.amplitude, wf.get_duration())
# 0 0.9 1000     exact
# 1 0.5 2000     family
# 2 0.7 3000     exact
# 3 0.5 2000     family
```

The family tier is scoped by kind as well as element, so it covers
`q[*].readout` and not `q[*].drive`. A bus with no entry at any tier resolves
to `None` and the alias passes through the program as the string it was, which
is why a partial library is not an error:

```python
library.get(q[0].drive, "readout")  # None
library.get("legacy_readout", "readout")  # None
```

A raw-string bus reaches the global tier only, since a plain string carries no
element or kind to match on. That is the trap in mixing raw-string buses with a
tiered library: they silently get the global entry, or nothing.

## What the library looks like on disk

A library is its own file with its own format and version, separate from the
program:

```python
library.save("chip.wfl")
```

```
#!WaveformLibrary 1.0
"readout" q[0].readout = IQPair(I=Square(amplitude=0.9, duration=1000), Q=Square(amplitude=0.0, duration=1000))
"readout" q[2].readout = IQPair(I=Square(amplitude=0.7, duration=3000), Q=Square(amplitude=0.0, duration=3000))
"readout" q[*].readout = IQPair(I=Square(amplitude=0.5, duration=2000), Q=Square(amplitude=0.0, duration=2000))
"weights" = IQPair(I=Square(amplitude=1.0, duration=2000), Q=Square(amplitude=1.0, duration=2000))
"pi_pulse" = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
```

The coordinate before the `=` is the tier: a concrete index is an exact entry,
`[*]` is a family entry, and no coordinate at all is a global one. Entries are
written in insertion order, and `qp.WaveformLibrary.load("chip.wfl")` returns a
library that dumps byte-identically.

Two files rather than one is the point of the split. `chip.qp` says what the
experiment is and changes when the experiment changes; `chip.wfl` says what
this chip's pulses are today and changes after every calibration run.

## Reading four records

```python
resolved = program.with_waveforms(library)
result = qp.simulate(resolved, model=model)

result.get(handles[2], field=qp.MeasurementField.STATE).dims  # ("amp",)
```

`result.get` takes a handle, a name, or a position, and `bus=` narrows the
search before any of them. All of these select the same record:

```python
result.get(handles[2])  # the handle the builder returned
result.get("q2/readout/m0")  # its name
result.get(qp.MeasurementHandle("q2/readout/m0"))  # a handle rebuilt from the name
result.get(2)  # third measurement in the program
result.get(0, bus="q2/readout")  # first measurement on that bus
```

The position form is the one a loop wants, since `result.get(i, ...)` walks the
records in the order the program declared them. Note that `bus=` filters first,
so `get(0, bus="q2/readout")` is the first measurement on that bus rather than
the first overall. Asking for a record that is not there raises rather than
returning an empty array: `KeyError: "No measurement named 'q0/readout/m0' on
bus 'q1/readout'"`.

The four qubits only differ if the model makes them differ. `sample` receives
the bus string, so one model describes the whole chip:

```python
PERIOD = {"q0/readout": 1.0, "q1/readout": 2.0, "q2/readout": 3.0, "q3/readout": 4.0}


def rabi(bus, env):
    """Four qubits whose pi amplitudes differ by a factor of four across the chip."""
    return float(np.sin(np.pi * env["amp"] / PERIOD[bus]) ** 2)


model = qp.MockMeasurementModel(
    response=lambda bus, env: rabi(bus, env) + 0j,
    p_excited=rabi,
    noise=0.02,
    seed=0,
)

[float(result.get(i, field=qp.MeasurementField.STATE)[10]) for i in QUBITS]
# [1.0, 0.514, 0.255, 0.135]
```

Without the branch on `bus` all four records come back identical, which looks
like a working multiplexed readout and is not one.

Four records off one shot, each qubit reaching its pi amplitude somewhere
different, which is the calibration this measurement exists to produce:

![Excited-state population against drive amplitude for four qubits, each with a different Rabi period, labelled q0 through q3.](../assets/plots/multiplexed-readout-light.png#only-light)
![Excited-state population against drive amplitude for four qubits, each with a different Rabi period, labelled q0 through q3.](../assets/plots/multiplexed-readout-dark.png#only-dark)

## Adapting it

To move a program to different qubits, `rebind` maps element indices and
returns a copy:

```python
ported = program.rebind(elements={("q", 0): ("q", 5)})
```

Auto-allocated measurement names are re-derived from the new bus, so
`q0/readout/m0` becomes `q5/readout/m0` and the `.qp` line becomes
`measure q[5].readout ... name="q5/readout/m0"`. A name you supplied yourself
is left alone, since rebind can tell the two apart. Qubits not in the mapping
pass through untouched and the original program is unchanged.

Porting also moves a bus out from under its library entry, which is the thing
to watch:

```python
program.with_waveforms(library)  # q[0].readout gets the exact entry, amplitude 0.9
ported.with_waveforms(library)  # q[5].readout gets the family entry, amplitude 0.5
```

The library is keyed on the coordinate, not on the program, so a program that
was calibrated on q0 quietly plays the family pulse on q5. That is the right
behavior, since the whole point of the coordinate is that q5 is a different
resonator, but it does mean porting and calibrating are one step rather than
two.

Rebind before serializing, not after loading. Whether a name was
auto-allocated is in-memory state that `.qp` does not carry, so on a reloaded
program rebind treats every name as user-supplied and leaves it stale: the bus
becomes `q[5].readout` while the name stays `q0/readout/m0`.

A raw-string bus cannot be rebound at all, and rebind says so rather than
leaving it behind:

```
ValidationError: rebind left raw-string bus(es) unported: 'legacy_readout'.
Raw strings carry no schema metadata to re-resolve — map them via strings={...}
(map a name to itself to keep it), or pass allow_unported_strings=True to leave
them in place.
```

Mapping one with `strings={"legacy_readout": "new_readout"}` moves the bus, but
the value stays a plain string, so its measurement keeps the global `m0` prefix
and the name will not track the bus. A schema is what makes porting work.

To scale past four, `QUBITS` is the only line that changes. Eight qubits give
eight records read the same way, and the cost of the run is linear in the
number of buses.
