# Active reset

A shot cannot start until the qubit is back in its ground state, and waiting
for it to get there on its own costs several relaxation times per shot. Active
reset measures the qubit instead and plays a pi pulse only if the measurement
says it is excited, which puts the repetition rate under the readout time
rather than under T1. The decision is made inside the shot, between two
operations, from a value that does not exist until the program is running.

That is the feature this page is about. Every program on the earlier pages is a
fixed sequence whose shape is known when the file is written; this one branches
on a measurement, so the `.qp` text describes two possible sequences and the
instrument picks one per shot. The reset is put in front of a small
[Rabi](rabi.md) sweep rather than left on its own, because a conditional is
something you add to an experiment rather than something you run.

## The program

```python
import numpy as np
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(
    label="active_reset",
    description="Conditional reset before a Rabi sweep on q0",
    schema=schema,
)
amp = program.variable("amp", label="Drive amplitude", units="V")

with program.average(shots=1000):
    with program.sweep(amp, qp.Linspace(0.0, 1.0, num=21)):
        check = program.measure(
            q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.STATE,),
        )
        with program.if_(check.state == 1):
            program.play(q[0].drive, "pi")
        with program.else_():
            program.wait(q[0].drive, 40)

        program.sync()
        program.set_gain(q[0].drive, amp)
        program.play(q[0].drive, "pi")
        program.sync()
        m0 = program.measure(
            q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )
```

### Why each piece is where it is

`check` is the herald. It asks only for `MeasurementField.STATE`, because the
program needs the classified outcome and nothing else from it, and requesting
`IQ` as well would record an array per sweep point that no one reads. The
handle it returns is what the conditional refers to.

`check.state` is a proxy whose `==` and `!=` operators build the comparison the
conditional needs. It is not a variable and it holds no value at build time;
it names a measurement and a field, and the instrument resolves it when the
shot reaches the branch. The comparison is against `1` because the classifier
reports an integer state, so the arm runs on the shots that came back excited.

Each arm is a separate `with` statement, and the chain is held open by
adjacency: `elif_()` and `else_()` must follow the arm they extend with nothing
appended in between. Appending any other operation between two arms closes the
chain, and the `elif_()` that follows then raises rather than silently starting
a new one.

The `else_` arm waits 40 ns, the length of the pi pulse, so both paths through
the branch take the same time. Nothing in the language requires that and the
reference executor does not measure it, but a branch whose arms have different
durations leaves the buses at different times on hardware, which the `sync()`
after the chain is what resolves.

The rest is the Rabi program: set the gain from the swept variable, play the
pulse, sync, and measure. That second measurement asks for both fields, since
the IQ point is the Rabi curve and the state is what a fidelity is computed
from.

## What it produces

```
#!QProgram 1.0

metadata:
  label: "active_reset"
  description: "Conditional reset before a Rabi sweep on q0"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var amp label="Drive amplitude" units="V"

  average 1000:
    for amp in Linspace(start=0.0, stop=1.0, num=21):
      measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state"]
      if q0/readout/m0.state == 1:
        play q[0].drive "pi"
      else:
        wait q[0].drive 40
      sync
      set_gain q[0].drive amp
      play q[0].drive "pi"
      sync
      measure q[0].readout "readout" "weights" name="q0/readout/m1" fields=["state", "iq"]
```

The condition is written as the measurement's name followed by the field, which
is why every measurement carries `name=` in the file even when the name was
allocated for it: the conditional is a reference by name, and a reload that
reallocated names would point the branch somewhere else. Both measurements are
on `q0/readout` and the per-bus counter distinguishes them, giving `m0` and
`m1`. A name you supply yourself does not consume a counter slot, so passing
`name="herald"` to the first one leaves the other two as `m0` and `m1`.

## What the condition can be

The accepted shape is one comparison between a measurement state and an integer
literal. That is narrower than the expression language everywhere else in a
program, and deliberately so: this is the one condition a sequencer has to
evaluate in real time, between two pulses, without a host round trip.

Everything else is rejected where it is written. Comparing against a
non-integer raises at the operator, before `if_` is reached:

```python
check.state == 1.0    # TypeError: handle.state can only be compared to int, ...
check.state == True   # TypeError: handle.state cannot be compared to a bool; use 0 or 1 ...
check.state > 0       # TypeError: '>' not supported between instances of ...
```

A condition of the wrong shape gets past the operator and is caught by `if_`
itself, as a `ValidationError`:

```python
program.if_(qp.and_(check.state == 1, other.state == 0))
# ValidationError: if_() expects a Comparison condition such as `handle.state == 0`
# or `handle.state != 1`; got LogicalBinaryOp

program.if_(qp.eq(amp, 1))
# ValidationError: if_() condition must reference at least one measurement-state ref
# (e.g. `handle.state`); got a comparison of Variable and Constant
```

The one failure that survives to validation is a herald that never asked to be
classified. Drop `fields=` from the first measurement and the program still
builds, because `measure` does not know a later conditional will refer to it:

```python
diagnostics, plan = qp.validate(program, qp.reference_capabilities())
# [error] missing-classification: Conditional references q0/readout/m0.state, but the
# measurement does not request state classification (add MeasurementField.STATE to
# fields=) (at body[0][0][1])
```

This is enforced rather than advisory. `qp.simulate` validates before it
executes, so the same program raises
`UnsupportedOperationError: program is not executable on the reference
platform` with that diagnostic attached, which is what a hardware platform
does with it too. The path `body[0][0][1]` locates the conditional as the
second child of the sweep, itself the first child of the average block; see
[Capabilities, diagnostics, and profiles](../guide/capabilities.md) for
resolving a path back to a line of the file.

`qp.explain` renders the chain as a node of its own with the arms beneath it:

```
plan for 'active_reset' — errors: 0 · warnings: 0 · info: 0
body
└─ average 1000:                                                                               [rt|host]
   └─ for amp in Linspace(start=0.0, stop=1.0, num=21):                                        [rt|host]
      ├─ measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state"]        [rt|host]
      ├─ if/elif/else chain                                                                    [rt|host]
      │  ├─ if q0/readout/m0.state == 1:                                                       [rt|host]
      │  │  └─ play q[0].drive "pi"                                                            [rt|host]
      │  └─ else:                                                                              [rt|host]
      │     └─ wait q[0].drive 40                                                              [rt|host]
      ├─ sync                                                                                  [rt|host]
      ├─ set_gain q[0].drive amp                                                               [rt|host]
      ├─ play q[0].drive "pi"                                                                  [rt|host]
      ├─ sync                                                                                  [rt|host]
      └─ measure q[0].readout "readout" "weights" name="q0/readout/m1" fields=["state", "iq"]  [rt|host]
```

Every row here is `[rt|host]`, meaning the reference platform can run it in
either domain. A platform whose readout chain cannot classify a state inside a
sequence is the one that reports otherwise, and the chain is where it says so.

## Running it

The herald needs a population to classify, which is what `p_excited` supplies.
A tenth of the shots arriving excited is a reasonable stand-in for a qubit that
has not fully relaxed since the previous shot:

```python
library = {
    "pi": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
    "readout": qp.waveforms.IQPair(
        qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)
    ),
    "weights": qp.waveforms.IQPair(
        qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)
    ),
}

result = qp.simulate(
    program.with_waveforms(library),
    model=qp.MockMeasurementModel(
        response=lambda bus, env: np.sin(np.pi * env["amp"] / 2) ** 2 + 0j,
        p_excited=lambda bus, env: 0.1,
    ),
)

result.get(m0).dims  # ("amp", "IQ")
result.get(m0).shape  # (21, 2)

heralds = result.get(check, field=qp.MeasurementField.STATE)
heralds.dims  # ("amp",)
heralds.mean()  # about 0.1, the fraction of shots that needed the reset pulse
```

The herald is a result record like any other, so how often the reset fired is
data you already have rather than something to instrument for. Reading it is
worth doing: a herald rate that climbs over a run is the readout heating the
qubit or the previous shot's pulse leaking, and neither shows up in the Rabi
curve until it has already distorted it.

Asking `check` for a field it never requested raises rather than substituting
one:

```python
result.get(check)
# KeyError: "Measurement 'q0/readout/m0' has no field 'iq'; available: state"
```

The branch is real in the reference executor even though the pulses are not.
The interpreter classifies each herald shot from `p_excited`, evaluates the
comparison, and walks one arm, so a measurement placed inside an arm is
recorded only on the shots that took it. A grid point where an arm was never
selected holds `NaN`, since the executor divides by a shot count of zero there:

```python
with program.if_(check.state == 1):
    program.play(q[0].drive, "pi")
    reset_check = program.measure(q[0].readout, "readout", "weights")
# result.get(reset_check) is all NaN when p_excited is 0.0 everywhere
```

What the executor does not do is time anything. The pi pulse in the taken arm
and the `wait` in the other are both no-ops, so the run says nothing about
whether the reset would fit inside a real repetition period. See
[Running programs](../guide/execution.md).

## Adapting it

To verify the reset rather than assume it, measure again inside the arm that
fired and read that handle back. The fraction of second heralds still reporting
1 is the reset infidelity, and it is the number that decides how many rounds
are needed.

To make it more than one round, repeat the measure-and-branch pair. A Python
`for` loop around it writes the rounds into the program at build time, each
with its own handle, and the counter names them `m0` through `m3`:

```python
for _ in range(4):
    round_check = program.measure(
        q[0].readout, "readout", "weights", fields=(qp.MeasurementField.STATE,)
    )
    with program.if_(round_check.state == 1):
        program.play(q[0].drive, "pi")
    with program.else_():
        program.wait(q[0].drive, 40)
```

Naming the repeated pair once with a [fragment](../guide/fragments.md) is the
alternative, and it keeps the four rounds from being four copies in the file.

To branch on a three-level classifier, extend the chain with `elif_`:

```python
with program.if_(check.state == 1):
    program.play(q[0].drive, "pi")
with program.elif_(check.state == 2):
    program.play(q[0].drive, "pi_ef")
    program.play(q[0].drive, "pi")
with program.else_():
    program.wait(q[0].drive, 40)
```

Only the first matching arm runs, and an `else_` is optional; without one, a
shot matching no arm does nothing. At most one `else_` per chain, and it has to
be last.

To condition on a measurement of a different qubit, pass that qubit's handle.
The comparison names a measurement, not a bus, so nothing requires the branch
to act on the qubit that was measured, which is what a parity check or a
teleported correction needs. Whether a platform can route a classification from
one readout chain to another sequencer in time is a capability question, and
[Capabilities, diagnostics, and profiles](../guide/capabilities.md) covers how
it is declared.
