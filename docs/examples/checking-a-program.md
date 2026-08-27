# Checking a program before it runs

Every other page here ends by running something. This one does not run
anything, because the question it answers comes earlier: given a program and a
particular instrument, what will that instrument refuse, and where in the file
is the offending line? Answering it costs a fraction of a millisecond and no
hardware, which is the point of asking before a fridge is booked.

The program is the Ramsey sequence from [T1 and Ramsey](t1-and-ramsey.md),
unchanged. What changes is the platform it is pointed at: an instrument that
can play, wait, sync and measure, but cannot set an oscillator phase, cannot
classify a state, and can only step a loop register by a constant. Three
different things are wrong with the program on that box, and a fourth is wrong
with the program as a whole. All four come back from one call.

The pieces used here are covered individually in
[Capabilities, diagnostics, and profiles](../guide/capabilities.md), which is
the reference for what each field means. This page puts them on one program.

## Describing the instrument

A `qp.PlatformCapabilities` is a lookup from slot to what that slot can do,
where a slot is a bus and a domain. Each entry is a `qp.CompilerCapabilities`:
a set of capability tokens, some numeric limits, and any predicates that
inspect nodes the tokens alone cannot judge.

```python
import numpy as np
import qprogram as qp
from qprogram.operations import Wait


def no_arbitrary_wait(node, ctx):
    """Reject a wait whose duration is bound by an arbitrary-valued sweep."""
    if not isinstance(node, Wait) or not isinstance(node.duration, qp.Variable):
        return
    if ctx.sweep_kind_of(node.duration) == "arbitrary":
        yield qp.Diagnostic(
            severity="error",
            code="oneloop.fixed-step-register-only",
            message=(
                "wait duration is bound by an arbitrary-valued sweep; "
                "this instrument's loop register only steps by a constant"
            ),
            node=node,
        )


drive = qp.CompilerCapabilities(
    profile="oneloop-drive",
    version=(1, 0, 0),
    capabilities=frozenset(
        {
            "op.play",
            "op.wait",
            "op.sync",
            "op.measure",
            "op.reset_phase",
            "waveform.iq",
            "waveform.alias",
            "measure.fields.iq",
        }
    ),
    limits={},
    predicates=(no_arbitrary_wait,),
    vendor_versions={},
)
```

The absences are the interesting part. `op.set_phase` is not in the set, and
neither is `measure.fields.state`, so the two things this instrument cannot do
are expressed by not saying it can. A capability token has to be registered
before it can be named; an unregistered one raises `ValueError: ... Register
via qprogram.protocol.register_capability_tokens before use.` rather than being
treated as a capability nobody has.

A predicate is a plain generator taking the node and a `ValidationContext`, and
yielding a `qp.Diagnostic` for each thing it objects to. Yielding nothing means
it has no objection. It exists because "can this instrument step a wait" is not
a property of the `Wait` node alone: it depends on the sweep that binds the
duration, which is what `ctx.sweep_kind_of` reaches. Codes from a predicate are
conventionally prefixed with the vendor's name so they cannot collide with the
validator's own.

Whole-program limits live on the platform slot rather than on a bus, so they go
in a separate descriptor. A `qp.Profile` is the reusable bundle, and
`from_profile` turns a registered one into capabilities:

```python
qp.register_profile(
    qp.Profile(
        name="oneloop-v1",
        version=(1, 0, 0),
        extends=None,
        capabilities=frozenset(
            {
                "block.block",
                "block.average",
                "block.sweep",
                "sweep.linear",
                "sweep.arbitrary",
                "sweep.range",
                "sweep.linspace",
                "sweep.values",
                "expr.constant",
                "expr.variable",
                "expr.binary_op",
            }
        ),
        limits={"max_loop_nesting": 1},
    )
)
platform = qp.CompilerCapabilities.from_profile("oneloop-v1")

caps = qp.PlatformCapabilities(
    bus={("q", "drive"): qp.BusCapabilities(rt=drive, host=drive)},
    platform=qp.BusCapabilities(rt=platform, host=platform),
    default_bus_profile=qp.BusCapabilities(rt=drive, host=drive),
)
```

`register_profile` is global and keyed by name, so run it once at module scope.
The registry compares by object identity rather than by value, so re-running the
same cell in a notebook builds a second `Profile` with identical fields and
raises `ValueError: Profile 'oneloop-v1' is already registered with different
content`. Passing the very same object twice is accepted.

`max_loop_nesting=1` says the sequencer has one loop register. The Ramsey
program has an `average` and a `sweep`, which is two.

## The program, unchanged

```python
schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(
    label="ramsey_on_oneloop",
    description="Ramsey on a restricted instrument",
    schema=schema,
)
delay = program.variable("delay", label="Free evolution", units="ns")

with program.average(shots=1000):
    with program.sweep(delay, qp.Values([0.0, 20.0, 60.0, 140.0, 300.0])):
        program.reset_phase(q[0].drive)
        program.play(q[0].drive, "pi_half")
        program.wait(q[0].drive, delay)
        program.set_phase(q[0].drive, 2 * np.pi * 2e-3 * delay)
        program.play(q[0].drive, "pi_half")
        program.sync()
        program.measure(
            q[0].readout,
            "readout",
            "weights",
            fields=(qp.MeasurementField.IQ, qp.MeasurementField.STATE),
        )
```

The delays are given as `qp.Values` rather than the `qp.Linspace` the Ramsey
page uses, because a hand-picked list is `KIND` `"arbitrary"` and the predicate
above only objects to that kind. On a `Linspace` the same program loses one of
its four problems.

## What comes back

```python
diagnostics, plan = qp.validate(program, caps)
for d in diagnostics:
    print(d)
```

```
[error] oneloop.fixed-step-register-only: wait duration is bound by an arbitrary-valued sweep; this instrument's loop register only steps by a constant (at body[0][0][2])
[error] missing-capability: 'SetPhase' requires capability 'op.set_phase' which is not supported by 'oneloop-drive' (rt) / 'oneloop-drive' (host) (at body[0][0][3])
[error] missing-capability: 'Measure' requires capability 'measure.fields.state' which is not supported by 'oneloop-drive' (rt) / 'oneloop-drive' (host) (at body[0][0][6])
[error] limit-exceeded: Program nests loops 2 deep; limit max_loop_nesting=1
```

Three codes from three different mechanisms. The first came from the predicate,
which had to look past the node at the sweep binding it. The next two came from
token lookup, and each names the token it wanted and both slots it looked in.
The last came from a whole-program limit check, which is why it has no path: it
is a statement about the program's shape rather than about any one node.

Nothing here is a warning. Every one of these stops execution, and a platform's
`execute` raises `UnsupportedOperationError` rather than running a program that
would produce the wrong data. The `forced-host` case on the
[resonator spectroscopy](resonator-spectroscopy.md) page is the other kind: the
program runs, differently from how it was written.

## From a diagnostic to a line of the file

`Diagnostic.path` is a structural address into the program body, and
`qp.format_path` renders it the way the messages above print it. Turning one
into a line number needs the file, which means the program has to have come
from one:

```python
text = qp.dumps(program)
reloaded = qp.loads(text)

for d in diagnostics:
    if d.path is None:
        continue
    line = reloaded.source_map[d.path]
    print(f"{qp.format_path(d.path)} -> line {line}: {text.splitlines()[line - 1].strip()}")
```

```
body[0][0][2] -> line 19: wait q[0].drive delay
body[0][0][3] -> line 20: set_phase q[0].drive (0.012566370614359173 * delay)
body[0][0][6] -> line 23: measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state", "iq"]
```

The reload is not incidental. `program.source_map` on the program built above
is `{}`, because a program assembled in Python has no source to map to; the
parser is what records which line each node came from, so the map is populated
only on a program that came through `qp.loads`. `expand()` returns a copy with
an empty map for the same reason, since inlining a fragment call produces nodes
no line of the file ever held.

The path itself resolves in both directions without a file. `qp.resolve_path`
takes a path to its node, and `qp.node_path` takes a node back to its path.

## Reading the plan

`qp.explain` renders the same information as a tree, with what each node's
domain came out as in the right-hand column:

```
plan for 'ramsey_on_oneloop' — errors: 4 · warnings: 0 · info: 0
body
└─ average 1000:                                                                               [rt|host]
   └─ for delay in [0.0, 20.0, 60.0, 140.0, 300.0]:                                            [--]
      ├─ reset_phase q[0].drive                                                                [rt|host]
      ├─ play q[0].drive "pi_half"                                                             [rt|host]
      ├─ wait q[0].drive delay                                                                 [--]       !! oneloop.fixed-step-register-only: wait duration is bound by an arbitrary-valued sweep; this instrument's loop register only steps by a constant
      ├─ set_phase q[0].drive (0.012566370614359173 * delay)                                   [--]       !! missing-capability: 'SetPhase' requires capability 'op.set_phase' which is not supported by 'oneloop-drive' (rt) / 'oneloop-drive' (host)
      ├─ play q[0].drive "pi_half"                                                             [rt|host]
      ├─ sync                                                                                  [rt|host]
      └─ measure q[0].readout "readout" "weights" name="q0/readout/m0" fields=["state", "iq"]  [--]       !! missing-capability: 'Measure' requires capability 'measure.fields.state' which is not supported by 'oneloop-drive' (rt) / 'oneloop-drive' (host)

!! limit-exceeded: Program nests loops 2 deep; limit max_loop_nesting=1

```

`[--]` is a node with no domain left: not real-time, not host-side, nowhere.
Three operations are marked that way, each with its own diagnostic, and the
whole-program error is printed under the tree because it belongs to no row.

Two things about that tree are worth reading carefully. The sweep is `[--]` and
carries no annotation of its own, because its emptiness is a consequence rather
than a finding: an operation that can run nowhere empties its parent's domain
too, and the child's diagnostic already says why. Scanning for a reason on the
loop's own line will not find one.

The `average` above it, meanwhile, still reads `[rt|host]` even though its only
child can run nowhere. A block's children are treated as units and do not
constrain their parent's domain, so the average is reporting what it could do
rather than what this body lets it do. It is not a contradiction, but it does
mean the tree is read from the leaves up.

## Adapting it

Making the program run on this instrument is four edits, one per diagnostic:
give the delays as a `qp.Linspace` so the sweep is `"linear"`, drop
`MeasurementField.STATE` and read the fringe as an IQ trajectory, drop the
`set_phase` and accept a Ramsey at the real detuning rather than an artificial
one, and lift the sweep out of the `average` so only one loop is nested. Each
of those is a real experimental compromise, which is the useful thing about
seeing them together: the diagnostics are a list of what the instrument costs
you.

To describe a bus kind that differs from the rest, add an entry to the `bus`
mapping keyed by the `(element, kind)` pair. Anything with no entry falls back
to `default_bus_profile`, which is why the readout bus above is checked against
the same descriptor as the drive.

To check a program that is already on disk without building it in Python,
`qp.loads` it and validate that. It arrives with its `source_map` populated, so
every diagnostic can be reported against a line without the round trip this
page had to do.

`qprogram.lsp` runs this same machinery over `.qp` text and reports the
findings as editor diagnostics, which is the same check moved earlier still.
