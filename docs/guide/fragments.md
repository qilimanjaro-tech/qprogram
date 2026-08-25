# Fragments

A `Fragment` is a named, parameterized program template: define an X gate, an
echo sequence, or a readout block once, instantiate it anywhere. Fragments are
first-class: definitions and call sites live in the AST and round-trip
through `.qp`. `program.expand()` lowers everything to a plain, fragment-free
program when a compiler needs one.

## Defining a fragment

Two equivalent styles. The **decorator** reads like the `.qp` syntax, where the
function signature *is* the parameter list (first argument is the builder,
the rest become parameters, the name comes from the function):

```python
import qprogram as qp
from qprogram import fragment
from qprogram.waveforms import Gaussian


@fragment
def x_pulse(f, drive, amp):
    f.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))
```

The body runs **once**, at decoration time, to record the AST, so Python `if`s
inside it are evaluated at definition, not per call. `*args` / `**kwargs` /
defaults / keyword-only parameters are rejected.

The **explicit API** mirrors `program.variable()` and suits programmatic
construction (e.g. a fragment generated per qubit):

```python
from qprogram import Fragment

xp = Fragment("x_pulse")
drive = xp.parameter("drive")
amp = xp.parameter("amp")
xp.play(drive, Gaussian(amplitude=amp, duration=40, sigma=8))
```

A `Fragment` *is* a `QProgram`: the whole builder surface works inside a
body, including control flow, vendor namespaces (`f.<vendor>.<operation>(...)`), local
variables (`f.variable("n")`), and calls to other fragments (`f.call(...)`,
cycles are rejected).

## Parameters are untyped placeholders

A `Parameter` subclasses `Variable`, so it participates in expressions
(`amp * 2`) and may stand in **value**, **bus**, or **waveform** position.
The binding at the call site determines the kind; a mismatch (say, a waveform
bound to a parameter used in arithmetic) raises `ValidationError` at
expansion, naming the fragment and parameter. One restriction: loop *bounds*
can't be parameterized (`sweep` validates its numbers eagerly); loop
bodies and operation/waveform arguments can.

## Calling

```python
p = qp.QProgram()
g = p.variable("g")
with p.average(1000), p.sweep(g, qp.Range(0, 1, 0.01)):
    p.call(x_pulse, "drive_q0", g)  # positional…
    p.call(x_pulse, "drive_q1", amp=(g * 0.5))  # …and/or keyword
```

`call()` binds with the Python calling convention (missing / extra /
duplicate arguments are errors), appends a first-class `Call` node, and
registers the fragment on `program.fragments`, together with any fragments
*it* calls, dependencies first. Accepted argument kinds: numbers,
expressions/variables, buses (strings or `BusRef`s), waveforms. A fragment
built against a `BusSchema` shares it with the host program; two *different*
schemas are an error.

## On the wire

Fragments serialize as top-level sections before `body:`; calls are bare
`name(args)` statements:

```
#!QProgram 1.0

fragment x_pulse(drive, amp):
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)

body:
  var g

  average 1000:
    for g in Range(start=0.0, stop=1.0, step=0.01):
      x_pulse("drive_q0", g)
      x_pulse("drive_q1", (g * 0.5))
```

Keyword arguments at the call site are resolved during binding, so both calls
above are written positionally on the wire.

Definitions must precede `body:` and each other (define-before-use), so the
file order is always topological. The parser is strict: unknown call names,
duplicate definitions, and arity mismatches are hard `ParseError`s.

## Expansion

`expand()` returns a **new** fragment-free program (the original is
untouched). Each call site becomes a plain `block:` holding the fragment body
with parameters substituted; fragment-local variables are renamed onto the
host, prefixed with the fragment name (`x_pulse_n`, `x_pulse_n_2`, …);
colliding measurement names gain a suffix (`m0`, `m0_2`, …) by renaming the
shared handle, so `handle.state` conditionals inside the fragment stay
consistent. Nested calls expand
recursively, and expansion is deterministic: expanding twice yields
structurally equal programs.

```python
flat = p.expand()
assert not flat.fragments
```

## Validation and execution

`qp.validate()` auto-expands programs containing calls, so capabilities are
checked against the substituted bodies and no platform needs a "call"
capability token. If you need the identity-keyed `ExecutionPlan` for nodes
you hold, expand explicitly and validate the expanded program. Platforms
follow the same convention: `execute()` lowers via `expand()` before
compiling.
