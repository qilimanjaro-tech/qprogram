# Fragments

A `Fragment` is a named, parameterized program template: an X gate, an echo
sequence, or a readout block defined once and instantiated wherever it is
needed. `program.call(...)` appends a `qp.operations.Call` node rather than
copying the body in, so the definition and every call site stay in the AST. A
composed program serializes as one `fragment` section per definition plus a
one-line statement per call site, and loads back the same way.
`program.expand()` produces the flat, fragment-free program that compilers and
validators consume.

## Defining a fragment

The `@fragment` decorator records a fragment from a function. The first
parameter receives the fragment builder, every parameter after it becomes a
`Parameter`, and the function's `__name__` becomes the fragment name:

```python
import qprogram as qp


@qp.fragment
def x_pulse(f, drive, amp):
    f.play(drive, qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))
```

The decorated name *is* the fragment: after decoration `x_pulse` is a
`Fragment` instance, not a function. The body runs once, at decoration time, to
record the AST, so a Python `if` or `for` inside it is evaluated at definition
and its outcome is baked into the recorded tree. Varying the body per call site
takes a parameter, not Python control flow.

The signature must be plain positional parameters with no defaults, because the
`.qp` grammar has no way to spell anything else. Defaults, `*args`, `**kwargs`,
keyword-only parameters, and a function with no builder parameter at all are
each rejected at decoration time:

```
ValidationError: @fragment 'bad': parameter 'amp' has a default value;
defaults are not supported

ValidationError: @fragment 'bad': parameter 'args' is variadic positional;
only plain positional parameters are supported
```

The explicit API records the same fragment and suits construction from data, a
fragment built per qubit for instance:

```python
xp = qp.Fragment("x_pulse")
drive = xp.parameter("drive")
amp = xp.parameter("amp")
xp.play(drive, qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))
```

The two fragments above compare equal, because `Fragment.__eq__` compares name,
parameter ids, local variables, and body, and nothing else. The name is used
verbatim as the `.qp` definition and call name, so it has to match
`[A-Za-z_][A-Za-z0-9_]*` and must not be in `qp.RESERVED_KEYWORDS`:

```
ValidationError: fragment name 'x pulse' is invalid: must match
[A-Za-z_][A-Za-z0-9_]* (no spaces or punctuation)

ValidationError: fragment name 'for' is a reserved keyword (see
qprogram.RESERVED_KEYWORDS)
```

`Fragment` also takes `label` and `description`, but a `.qp` fragment section
is headed by the name and parameter list alone, so neither survives
serialization. Use them for in-process tooling only.

A `Fragment` is a `QProgram` subclass, so a body is built exactly like a
program body: operations, `average`, `sweep`, `if_`, vendor namespaces
(`f.<vendor>.<operation>(...)`), local variables through `f.variable(...)`, and
`f.call(...)` to another fragment.

## Parameters

`parameter(id, *, label=None)` declares a placeholder and returns it.
`Parameter` subclasses `Variable`, so it takes part in expressions (`amp * 2`),
follows the same identifier rules, and serializes as a bare identifier.

Parameters are untyped. One `Parameter` may stand in value, bus, or waveform
position, and the binding at the call site is what decides which it was. That
is why kind errors surface at expansion rather than at definition, and why the
builder skips its usual bus and waveform checks on a position that holds a
parameter.

Parameters and fragment-local variables share one identifier namespace inside
the body, so a collision is rejected whichever order it happens in:

```
ValidationError: Parameter 'a' is already declared on fragment 'f'
ValidationError: Parameter 'a' collides with a local variable of fragment 'f'
ValidationError: Variable 'a' collides with a parameter of fragment 'f'
```

Sweep bounds are the one position a parameter cannot occupy. A sweep source
validates its numbers when it is constructed, which happens while the fragment
body is being recorded, long before any binding exists:

```
ValidationError: Range stop must be an int or float, got Parameter
```

A loop *variable* may be a parameter, which lets a fragment sweep a variable
the host owns. The binding then has to be a `Variable`, and anything else is
caught at expansion:

```
ValidationError: fragment 'scan': a loop variable must be bound to a variable, got int
```

## Calling a fragment

`call(fragment, *args, **kwargs)` appends the `Call` node. Arguments bind with
the Python calling convention: positionals in parameter declaration order, then
keywords by parameter id.

```python
@qp.fragment
def echo(f, drive, t):
    f.wait(drive, t)
    f.play(drive, "pi")
    f.wait(drive, t)


ro = qp.Fragment("readout")
bus = ro.parameter("bus")
n = ro.variable("n", label="settle", units="ns")
with ro.sweep(n, qp.Range(0, 20, 10)):
    ro.wait(bus, n)
handle = ro.measure(bus, "ro_wf", "weights", fields=("iq", "state"))
with ro.if_(handle.state == 1):
    ro.play(bus, "reset")

p = qp.QProgram()
g = p.variable("g")
with p.average(1000), p.sweep(g, qp.Range(0, 1, 0.5)):
    p.call(x_pulse, "drive_q0", g)
    p.call(echo, "drive_q0", 40)
    p.call(ro, bus="readout_q0")
    p.call(ro, bus="readout_q1")
```

A binding error is raised at the call site rather than deferred to expansion,
and it covers the four ways binding can go wrong:

```
ValidationError: fragment 'f' takes 1 argument(s) (a) but 2 positional
argument(s) were given
ValidationError: fragment 'f' has no parameter 'c'; parameters are (a)
ValidationError: fragment 'f' got multiple values for parameter 'a'
ValidationError: fragment 'f' missing argument(s) for parameter(s): b
```

The accepted argument kinds are numbers, expressions (which covers a
`Variable`, a `Parameter` of the calling fragment, and arithmetic over them),
buses as plain strings or `BusRef`s, and waveforms. Everything else is
rejected, `bool` included even though it is an `int` subclass:

```
ValidationError: fragment 'f' parameter 'a': unsupported argument type list;
expected a number, expression/variable, bus (string or BusRef), or waveform
```

A `BusRef` argument is validated against the calling program's schema the way a
bus argument to any operation is. A fragment built against a `BusSchema` lends
it to a program that has none; two different schema objects are an error,
because the `.qp` writer has a single `schema:` section to resolve bus paths
against:

```
ValidationError: fragment 'ro' was built against a different BusSchema than
this program's; a program and its fragments must share one schema
```

`call` also registers the fragment, and every fragment it calls, on
`program.fragments`, dependencies first, so iteration order is topological. The
registry is keyed by name, which makes two different fragments sharing a name a
build-time error rather than a silent overwrite. A fragment cannot call itself,
and a cycle is caught as soon as it is visible:

```
ValidationError: a different fragment named 'dup' is already used by this
program; fragment names must be unique within a program

ValidationError: fragment 'selfish' cannot call itself
ValidationError: fragment call cycle: a -> b -> a
```

Registration walks the callee's body each time, because a fragment can gain
nested calls after it was first registered. A cycle that closes only after
registration is caught by `expand()` instead, with the same message.
`program.fragments` returns a copy of the registry, so mutating what it returns
changes nothing.

## On the wire

Fragment definitions are top-level sections before `body:`, and a call site is
a bare `name(args)` statement. `qp.dumps(p)` on the program above gives:

```
#!QProgram 1.0

fragment x_pulse(drive, amp):
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)

fragment echo(drive, t):
  wait drive t
  play drive "pi"
  wait drive t

fragment readout(bus):
  var n label="settle" units="ns"

  for n in Range(start=0.0, stop=20.0, step=10.0):
    wait bus n
  measure bus "ro_wf" "weights" name="m0" fields=["state", "iq"]
  if m0.state == 1:
    play bus "reset"

body:
  var g

  average 1000:
    for g in Range(start=0.0, stop=1.0, step=0.5):
      x_pulse("drive_q0", g)
      echo("drive_q0", 40)
      readout("readout_q0")
      readout("readout_q1")
```

Arguments are emitted positionally in parameter order, so `p.call(ro,
bus="readout_q0")` is written `readout("readout_q0")`: the keyword spelling
used at build time is not part of the wire form. The parser still accepts
`key=value` at a call site, for hand-written files, and rejects a positional
argument that follows a keyword one.

A fragment's own `var` declarations and auto-allocated measurement names are
scoped to its section. The measurement above is `m0` inside `fragment readout`
regardless of how many measurements the host body has, because auto-naming
counts within the fragment; the uniquifying happens at expansion instead.

The writer computes definition order itself, depth-first over the nested `Call`
nodes, rather than trusting registration order, so a file always defines a
fragment before any fragment that calls it. Define-before-use is what the
parser enforces, which makes the file order topological by construction. A
forward reference, a name defined twice, and a definition that comes after
`body:` are each a `ParseError`:

```
ParseError: Line 4: unknown fragment 'inner'; fragments must be defined in a
`fragment inner(...):` section before use

ParseError: Line 6: duplicate fragment definition 'f'

ParseError: Line 6: fragment definitions must appear before the `body:` section
```

A vendor operation reachable only through a call still gets its `require` line:
the writer scans fragment bodies alongside the program body when it collects
vendor requirements. A definition nothing calls also round-trips, since
`loads()` registers every `fragment` section it reads and the writer emits
every registered fragment.

A fragment cannot be serialized on its own, because the definition is a section
of a host program and carries no header, schema, or metadata of its own:

```
SerializationError: cannot serialize Fragment 'f' directly; fragments are
emitted as `fragment ...:` sections of the host QProgram that calls them —
serialize that program
```

The grammar for both forms, with the argument token shapes a call statement
accepts, is in [the `.qp` format reference](../reference/qp-format.md#fragments).

## Expansion

`expand()` returns a new program with every call inlined; the original is
untouched. On the program above:

```
#!QProgram 1.0

body:
  var g
  var readout_n label="settle" units="ns"
  var readout_n_2 label="settle" units="ns"

  average 1000:
    for g in Range(start=0.0, stop=1.0, step=0.5):
      block:
        play "drive_q0" Gaussian(amplitude=g, duration=40, sigma=8)
      block:
        wait "drive_q0" 40
        play "drive_q0" "pi"
        wait "drive_q0" 40
      block:
        for readout_n in Range(start=0.0, stop=20.0, step=10.0):
          wait "readout_q0" readout_n
        measure "readout_q0" "ro_wf" "weights" name="m0" fields=["state", "iq"]
        if m0.state == 1:
          play "readout_q0" "reset"
      block:
        for readout_n_2 in Range(start=0.0, stop=20.0, step=10.0):
          wait "readout_q1" readout_n_2
        measure "readout_q1" "ro_wf" "weights" name="m0_2" fields=["state", "iq"]
        if m0_2.state == 1:
          play "readout_q1" "reset"
```

Each call site becomes a plain `block:` holding a copy of the fragment body.
The block is what keeps a multi-operation fragment from being spliced into its
surroundings, and it is an ordinary `qp.blocks.Block`, so nothing downstream
needs to know a fragment was ever involved.

Three substitutions happen inside that copy. Parameters are replaced by their
bound arguments. Fragment-local variables are hoisted onto the host program
under `{fragment}_{id}`, taking the lowest free numeric suffix when the name is
taken, whether by a repeated call (`readout_n`, `readout_n_2`) or by a host
variable that already holds it. Measurement names already in use gain the same
kind of suffix (`m0`, `m0_2`), and the rename lands on the shared
`MeasurementHandle`, so the `handle.state` conditional inside the fragment body
keeps pointing at its own measurement rather than at the first call's.

Value substitution preserves the shape the builder would have produced. A bare
parameter in value position is replaced by the raw binding, so `f.wait(drive,
t)` bound to `40` becomes `wait "drive_q0" 40` and not a wrapped constant,
while a parameter inside an expression is wrapped: `t * 2` bound to `50`
becomes `Constant(50) * Constant(2)`. A bound host `Variable` is substituted by
identity, so a sweep over it drives the operation inside the expanded body at
run time.

Kinds are checked as the substitution lands, and each error names the fragment
and what it found:

```
ValidationError: fragment 'f': parameter 'x' is used in an expression but
bound to a Gaussian; bind a number, variable, or expression

ValidationError: fragment 'f': Wait.bus must be a bus (string or BusRef)
after expansion, got Square

ValidationError: fragment 'f': Play.waveform must be a waveform or alias
after expansion, got float
```

A bound `BusRef` also re-runs the two checks the builder had to skip while the
position held a parameter, so a fragment written for a single-channel bus and
called with an IQ bus fails here rather than in a vendor compiler:

```
ValidationError: Bus 'q0/drive' is an IQ channel but received a single-channel
Waveform (Square). Use an IQWaveform (e.g. IQPair, IQDrag) instead.

ValidationError: Bus 'q0/drive' does not support acquisition (acquires=False).
measure() can only be called on buses with an ADC (e.g. readout buses).
```

Nested calls expand recursively, including calls inside conditional arms, and
expansion runs in document order, so expanding twice yields structurally equal
programs. A program with no calls at all comes back as a deep copy with the
same structure. Either way the returned program's `fragments` registry is
empty, and so is its `source_map`: expansion restructures the tree that the
recorded paths address, which would leave the map pointing at the wrong nodes.

```python
flat = p.expand()
assert not flat.fragments
```

One transform does not follow calls on its own. `with_waveforms` walks the
program body, so a string waveform alias inside a fragment body is left
unresolved while the call is still a call. Expand first when resolving against
a library:

```python
flat = p.expand().with_waveforms({"pi": qp.waveforms.Square(0.5, 40)})
```

`rebind` expands first whenever `program.fragments` is non-empty, so its
result always carries an empty registry. `optimize` expands only when the
program's own body holds an `average`, which is what forces the classifying
validation; a program whose only `average` sits in a fragment body comes back
as a deep copy with its calls intact.

## Keeping calls or expanding

Keep the calls while a program is being written, stored, or read. The composed
`.qp` text above carries one `readout` definition where the expanded text
carries two copies of it, and an edit to the definition reaches every call site
at once. Structural equality and the round trip both work on the composed form,
so it is the form to compare and to archive.

Expand when something needs a flat tree, or when you need to hold the nodes
that will actually execute. The identity-keyed `ExecutionPlan` is the usual
reason: it is keyed on the node instances of whichever program was validated,
and validating a composed program validates a private expansion, so a `Call`
node you hold is not in the plan. Expand yourself and validate the result if
you want to look nodes up. Expansion is also the way to hand a program to code
that predates fragments, since a `Call` is an `Operation` subclass a compiler
will not recognize.

## Validation and execution

`qp.validate` expands the program when it finds a `Call` anywhere in the body,
then checks capabilities against the substituted bodies. No platform needs a
"call" capability token, and `Call.required_capabilities()` returns the empty
set to match. The diagnostics and the plan describe nodes of that internal
expansion:

```python
platform = qp.ReferencePlatform()
diagnostics, plan = qp.validate(p, platform.capabilities)
```

Looking up a node you hold in that plan raises `KeyError` on the node's repr,
`Call(x_pulse(drive='drive_q0', amp=Variable('g')))` for instance. Expand
explicitly and validate the expanded program when you need those lookups.

`qp.explain` expands on the same condition and says so in its header, which
reads `plan (fragments expanded)` followed by the severity counts.
`ReferencePlatform.execute` expands whenever `program.fragments` is non-empty,
on a copy, before validating and interpreting, and the platform convention asks
a vendor back end to do the same. A `Call` node is therefore not something a
compiler has to know about: every entry point removes it first.

One place the composed form is visible to a caller is `program.buses`. A `Call`
reports every string-valued argument as a bus, because a `Parameter` is untyped
and only the position it lands in inside the body decides whether the string
was a bus or a waveform alias. The set therefore over-approximates for a
composed program, and is exact once expanded.

## Related pages

- [Running programs](execution.md): what `execute` does with a program, and the
  diagnostics it raises.
- [Capabilities and validation](capabilities.md): `Diagnostic`s, domains, and
  the `ExecutionPlan`.
- [Measurements and results](measurements.md): handles, names, and
  `QProgramResult`.
