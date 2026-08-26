# Saving and loading

A program serializes to `.qp`, a line-oriented text format whose parser and
writer live in `qprogram.serialization`. Both are hand-written Python: the
parser imports nothing outside the standard library, and the writer adds numpy
for array values. There is no JSON, YAML, or pickle layer underneath, so the
text in the file is the whole representation.

## The four functions

```python
import qprogram as qp

text = qp.dumps(program)
program = qp.loads(text)

qp.save(program, "experiment.qp")
program = qp.load("experiment.qp")
```

`dumps` and `loads` hold the implementation. `save` is `dumps` followed by a
`write_text(..., encoding="utf-8")`, and `load` is the matching read, so the
encoding is fixed rather than taken from the platform locale and a file
written on one machine parses identically on another. `save` writes one file
and does nothing else; it will not create a missing parent directory.

`loads` and `load` each take one keyword argument, `auto_activate`, which
defaults to `True` and is described under
[vendor activation at parse time](#vendor-activation-at-parse-time).

On the way out, `dumps` raises `qp.SerializationError` rather than emitting
output it cannot read back: an operation or block class that is not registered,
a vendor with no registered version, a value type the format has no
representation for, an array of rank other than one, a dict with non-string
keys, a fragment call cycle, or two different fragments under one name. A
`Fragment` passed directly is refused for the same reason, since a fragment is
emitted as a section of the program that calls it:

```
SerializationError: cannot serialize Fragment 'pi_pulse' directly; fragments are emitted as `fragment ...:` sections of the host QProgram that calls them — serialize that program
```

On the way in, malformed input raises `qp.ParseError`, whose message carries
the 1-based line number of the offending line and whose `line_num` attribute
holds the same number. Two other errors travel through unwrapped: a
`ValidationError` when a declaration the grammar accepts is rejected by the
program being built, and a `TypeError` when a constructor call in the file
does not fit its class's signature.

## A file end to end

This is `qp.dumps` output for a T1 experiment built on
`qp.BusSchema.transmon()`, with an averaging block around a delay sweep:

```
#!QProgram 1.0

metadata:
  label: "t1"
  description: "Energy relaxation"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

body:
  var t units="ns" description="Free-evolution time"

  average 1000:
    for t in Range(start=0.0, stop=200.0, step=4.0):
      play q[0].drive IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.2)
      wait q[0].drive t
      sync
      measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

The writer always emits the sections in this order: the `#!QProgram` header,
one `require` line per vendor, `metadata:`, `schema:`, any fragment
definitions, then `body:`. Nesting is two-space indentation, one statement per
line, and a `#` starts a comment that runs to the end of the line. The full
grammar is in [the `.qp` file format reference](../reference/qp-format.md).

## What the round trip preserves

For a program whose waveform and sweep-source arguments are numbers, quoted
strings, or bare variable references, loading the text back produces a
structurally equal program, and serializing that copy reproduces the same
bytes:

```python
text = qp.dumps(program)
reloaded = qp.loads(text)

assert qp.dumps(reloaded) == text
assert reloaded.body == program.body
assert reloaded.variables == program.variables
assert reloaded.label == program.label
assert reloaded.description == program.description
```

The structural equality covers the whole AST: blocks, operations, expressions,
waveforms, bus references, measurement handles (which compare by name), and
the point arrays inside sweep sources such as `Values`, which are written out
in full because a truncated array could not be reconstructed.
`tests/test_round_trip.py` asserts this one feature surface at a time, and
`tests/test_round_trip_property.py` asserts both halves over programs
generated with hypothesis.

The one shape that writes but does not read back is an expression or a math
function inside a constructor argument, such as
`Gaussian(amplitude=qp.sin(phi), ...)`. The writer emits it as it stands, the
grammar has no production for it, and the load fails with `ParseError: Unknown
waveform or sweep source type: sin`. Nothing in `dumps` checks for it, so a
clean write is not by itself a promise that the text parses. The argument
forms the parser does accept are in
[inline waveform constructors](../reference/qp-format.md#inline-waveform-constructors).

## What the round trip does not preserve

The Python class of a schema does not survive. `qp.BusSchema.transmon()`
returns a `TransmonSchema`; after a round trip `program.schema` is a plain
`BusSchema` carrying the same elements, bus kinds, and naming pattern. Bus
paths in the body are unaffected, because a `BusRef` is a `str` subclass and
the reloaded dynamic schema resolves `schema.q[0].drive` to the same
`q0/drive`. What is gone is the static type, and with it editor completion and
type checking on the accessors. `BusSchema` has no structural equality either,
so the original and the reloaded schema never compare equal.

Whether a measurement name was auto-allocated or user-supplied is in-memory
state, not part of the file. `QProgram.measure` records it so that `rebind`
knows which names to re-derive when a bus changes, and every handle
reconstructed from `.qp` looks user-supplied:

```python
program.measure(schema.q[0].readout, "readout", "weights")
moved = {("q", 0): ("q", 1)}

qp.dumps(program.rebind(elements=moved))
# ... measure q[1].readout "readout" "weights" name="q1/readout/m0"

qp.dumps(qp.loads(qp.dumps(program)).rebind(elements=moved))
# ... measure q[1].readout "readout" "weights" name="q0/readout/m0"
```

Rebind before saving when the names matter. Runtime values written onto a
`MeasurementHandle` by an execution are in-memory state for the same reason:
the file records the name, never the result.

`QProgram.source_map` runs the other way. It is empty for a program built in
Python and filled by `loads` and `load` with the 1-based line of every node in
the `body:` section, which is what lets a diagnostic point at a line of the
file it came from. `expand` clears it, since the expansion restructures the
tree the paths were computed against.

Going the other way, from text to program to text, is a normalization rather
than an identity. Comments and blank lines are the author's and are not
recorded in the AST, so they do not come back. Metadata keys the parser does
not know are accepted for forward compatibility and dropped on the next write,
so an `author: "..."` line loads without error but is not written back.
Fragment call arguments are re-emitted positionally in parameter order whatever
spelling the caller used, and a hand-written `measure` line with no `name=`
comes back as `m0`, `m1`, and so on even when its bus is a schema path, because
the handle is allocated before the path is promoted to a `BusRef`.

## Format version and the require line

Every file opens with the format version, which the writer takes from
`FORMAT_VERSION` in `qprogram/serialization/_format.py`, the single constant
both sides read:

```
#!QProgram 1.0
```

Only the major component is binding. The parser checks the header before
anything else and rejects a different major, so `#!QProgram 2.0` fails with
`Line 1: Unsupported format version 2.0` while `#!QProgram 1.7` loads on
today's parser, which reads it with the features it knows. That is the
compatibility contract: minor versions add sections, operations, and
constructs without breaking older readers, and a major bump is reserved for a
change that does.

A program that uses vendor operations or vendor blocks carries one `require`
line per vendor, directly after the header:

```
#!QProgram 1.0

require myvendor 0.1

body:
  myvendor.acquire "readout_q0" "weights" name="q0/readout/m0"
  myvendor.set_markers "drive_q0" "0001"
```

The writer emits a line for every vendor it finds anywhere in the program,
including inside fragment definitions and conditional arms, and including a
vendor whose only contribution is a block. The version comes from whatever the
installed extension registered, truncated to `major.minor`, because that is
the granularity the compatibility check works at.

The parser resolves each line against the extension registered in this
environment, and does it before reading the body. The majors must be equal and
the installed minor must be at least the file's, which gives two failures with
distinct messages:

```
Line 3: file requires myvendor 2.0 (major 2); installed myvendor is 0.1.0 (major 0) — major versions must match
Line 3: file requires myvendor 0.7 or compatible; installed myvendor is 0.1.0 — minor version too old
```

A patch component is informational and is ignored by the comparison. A
`require` line that appears after the first section is rejected outright, so
the dependency list is always readable off the top of the file:

```
Line 6: `require` declarations must appear directly after the header, before any section
```

## Vendor activation at parse time

When a `require` line names a vendor that is not registered yet, the parser
tries to make it registered instead of failing. A vendor package declares an
entry point whose name is the vendor namespace and whose value is a module
that self-registers on import:

```toml
[project.entry-points."qprogram.vendors"]
myvendor = "qprogram_myvendor"
```

The parser looks the vendor up in the `qprogram.vendors` group, imports the
module behind it, and checks that a protocol version is now registered. The
discovery scan is memoized for the life of the process, and if two installed
distributions claim the same namespace the first one found wins, which is a
packaging mistake rather than a supported arrangement. Because the import runs
the extension's registration side effects, an environment where every required
extension is installed and declares that entry point can load the file, with
nothing imported by hand first. An extension installed without the entry point
still has to be imported before the load.

Two failures are worth telling apart. If no installed package claims the
vendor, the file cannot be loaded here at all:

```
Line 3: file requires vendor 'myvendor' 0.1 but no matching extension is registered in this environment — install the package that declares the 'qprogram.vendors' entry point for 'myvendor', or import the extension before loading
```

If a package does claim it but the import raises, or imports without calling
`register_vendor_version`, that is a defect in the extension, and the
`VendorActivationError` describing it is wrapped in the `ParseError` so the
message names the entry point that failed.

Activation is driven by the `require` line and nothing else. A hand-written
file that calls `myvendor.acquire` with no `require myvendor` line loads fine
if the extension is already imported, and otherwise fails later, at the first
dotted operation, as an unknown vendor operation. Write the line.

Pass `auto_activate=False` to `qp.loads` or `qp.load` to turn the on-demand
import off. An unregistered vendor is then a hard error whatever is installed,
and the message says so:

```
Line 3: file requires vendor 'myvendor' 0.1 but no matching extension is registered in this environment — auto-activation is disabled; import the extension before loading (e.g. `import qprogram_myvendor`)
```

## What a vendor package registers

The grammar has no built-in keyword lists. Both sides dispatch through
registries, so a vendor adds vocabulary by registering it at import time and
needs no parser or writer change. These are the calls it makes, all reachable
from the top-level package:

| Call | What it adds |
|---|---|
| `qp.register_vendor_version(vendor, version)` | The protocol version, once per package. Requires at least integer `major.minor`; being registered is also what marks the vendor as active. |
| `qp.register_vendor_operation(vendor, name, cls)` | An operation written as `<vendor>.<name>`. |
| `qp.register_vendor_block(vendor, name, cls)` | A control-flow block written as `<vendor>.<name>:` with an indented suite. |
| `qp.register_waveform(cls)` and `qp.register_sweep_source(cls)` | A constructor name for a waveform or a sweep source. `register_sweep_source` also registers the class's `TOKEN` with the capability registry. |

Operations accept optional `serialize` and `parse` callbacks for a wire form
that signature-driven serialization cannot produce, and blocks accept
`serialize_header` and `parse_header` for the same reason; without them, the
operation is written from its `__init__` signature with required parameters
positional in declaration order and optional ones as `key=value` only where
the stored value differs from the default. `register_operation` and
`register_block` underneath take a `vendor=None` argument and register core
vocabulary; they are reachable as `qp.serialization.register_operation` and
`qp.serialization.register_block`.

Re-registering the same class under the same name is allowed, since an
import-time side-effect module can run more than once. Claiming a name that
another class already holds raises `ValueError` naming the incumbent, because
replacing it would change how every existing file with that keyword parses.
Vendor names are checked against the reserved set (`qp.RESERVED_KEYWORDS` plus
the `core` sentinel) and rejected if they collide.

[Building a vendor extension](../developer/vendor-extensions.md) walks a
package through all of this end to end.

## Schemas serialize inline

If your program was constructed with a `BusSchema`, the schema lands at the
top of the file as a single inline block:

```
schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires
```

Bus paths in the body then reference the schema:

```
play q[0].drive "pi_pulse"
measure q[0].readout "readout" "weights" name="q0/readout/m0"
```

The writer emits this expanded form even for the presets, so
`BusSchema.transmon()` writes the same block a hand-built schema of the same
shape would. The presets are construction-time conveniences on the Python
side; recording their contents rather than their names means adding a bus to a
preset cannot change what an existing file says. A non-default naming pattern
is emitted as a `naming:` line inside the block.

An unquoted path and a quoted string mean different things. An unquoted
`q[0].drive` in a program that declares a schema resolves against it, and a
path naming an element or a bus kind the schema does not have is a parse
error. A quoted `"q[0].drive"` stays the string it looks like, which is what
keeps a raw-string bus that happens to be path-shaped from being promoted on
reload. In a program with no `schema:` block, an unquoted path-shaped token
has nothing to resolve against and stays a plain string.

## Variables in the file

Each `Variable` becomes one `var` declaration:

```
body:
  var freq label="Drive Frequency" units="Hz"
  var gain
  var t units="ns" description="Free-evolution time"
```

The unquoted token after `var` is the identifier used everywhere else in the
body; the rest is optional metadata, and only the annotations the variable
actually carries are emitted, so a bare variable writes as `var gain` and
reloads identically. A fragment's parameters and locals form their own scope
and are declared inside the `fragment` section rather than here.

## Waveform aliases and inline constructors

A program may carry waveforms two ways, and both round-trip:

```
play "drive_q0" "pi_pulse"
play "drive_q0" IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.2)
```

An alias stays a string on reload, ready to be resolved later by
`with_waveforms` against a
[waveform library](#waveform-libraries-and-the-wfl-file). An inline
constructor comes back as the same class it started as, with the same argument
values.

A waveform registered with `qp.register_waveform` uses that same constructor
syntax with no further work, but the class has to satisfy one contract: the
writer emits every public attribute the instance holds, taken from `vars(wf)`
in assignment order, and the parser passes those keywords straight to the
constructor. Attribute names and parameter names therefore have to agree, and
a public attribute that is not a constructor parameter breaks the round trip.
A class that caches a derived array as `self.samples_cache` writes that array
into the file and then fails on reload with
`TypeError: Blip.__init__() got an unexpected keyword argument 'samples_cache'`.
Give the cache a leading underscore and the writer skips it.
[Adding waveforms](../developer/adding-waveforms.md) has the rest of the class
contract.

Sweep sources work the same way and go through the same code path, which is
what lets one nest inside a combinator's argument list. `Values` is the
exception: it writes as the bracket literal `[0.1, 0.2, 0.3]` rather than a
constructor call, and the literal holds every point.

## Waveform libraries and the `.wfl` file

An alias has to be resolved before a program can run, and `qp.WaveformLibrary`
is what resolves it. The split is there so that a program can say which pulse
to play without saying what that pulse currently is, which is what lets it
survive a recalibration unchanged; the concrete pulses live in a library
instead, an object with no platform attached that is replaced after every
tune-up.

```python
import qprogram as qp

library = qp.WaveformLibrary()
library.set("pi_pulse", qp.waveforms.IQDrag(0.5, 40, 8, 0.1), element="q", idx=0, kind="drive")
library.set("pi_pulse", qp.waveforms.IQDrag(0.9, 40, 8, 0.1), element="q", idx=1, kind="drive")
library.set("cz", qp.waveforms.Square(0.3, 200), element="c", idx=(0, 1), kind="flux")
library.set(
    "readout",
    qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
    element="q",
    kind="readout",
)
library.set("weights", qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)))

resolved = program.with_waveforms(library)
```

Which keyword arguments `set` receives decides the tier the entry is stored at,
and only three combinations are accepted. `element`, `idx`, and `kind` together
register an exact entry, reachable from one bus only. `element` and `kind`
register a family entry, reachable from that bus kind at any index. None of the
three registers a global entry, reachable from every bus. `idx` takes a tuple
for a multi-index element, which is how the coupler entry above covers
`c[0,1].flux`. Any other combination raises `ValidationError` rather than
guessing at the tier:

```
WaveformLibrary.set: specify (element, idx, kind) for an exact entry, (element, kind) for a family default, or none of them for a global entry; got element='q', idx=0, kind=None
```

`library.get(bus, name)` tries the three tiers most specific first and returns
the first entry that matches, or `None` when none does, so a more specific
entry shadows a less specific one for the buses it covers and the global tier
acts as the default. Reaching the exact and family tiers needs the
`(element, idx, kind)` metadata a `BusRef` carries: a raw-string bus has none
of it, so `library.get("q0/drive", "pi_pulse")` is `None` even with the exact
`q[0].drive` entry registered. Asked for the same name on the schema path,
the library returns the `IQDrag` with amplitude 0.5 on `q[0].drive` and the one
with amplitude 0.9 on `q[1].drive`. Setting the same name at the
same tier twice overwrites, so the last entry registered wins.

`with_waveforms` deep-copies the program and rewrites every string waveform
attribute its operations declare, which covers a `measure`'s weights as well as
a `play`'s waveform. A name with no entry stays a string and an already
concrete waveform passes through, both without error, and each replacement
re-runs the channel check, so an IQ pulse resolved onto a single-channel bus
raises `ValidationError` here rather than in a vendor compiler. `apply` is the
same operation spelled from the library's side, for tooling and tests that
resolve without going through a platform: `library.apply(program)` calls
`program.with_waveforms(library)`. A plain `{name: waveform}` mapping is
accepted where a library is, and goes through
`qp.WaveformLibrary.from_mapping`, which puts every key at the global tier, so
a mapping resolves on every bus and cannot express a per-bus difference.

The library carries its own four functions, matching the program's in name and
in behavior, with `save` and `load` fixed to UTF-8 the same way. `loads` and
`load` are class methods and return a new library:

```python
library.save("cal.wfl")
library = qp.WaveformLibrary.load("cal.wfl")

text = library.dumps()
library = qp.WaveformLibrary.loads(text)
```

`dumps` writes the header line and one line per entry, in insertion order, so
`qp.WaveformLibrary.loads(library.dumps()).dumps()` reproduces the text
exactly. This is the library built above:

```
#!WaveformLibrary 1.0
"pi_pulse" q[0].drive = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
"pi_pulse" q[1].drive = IQDrag(amplitude=0.9, duration=40, sigma=8, beta=0.1)
"cz" c[0,1].flux = Square(amplitude=0.3, duration=200)
"readout" q[*].readout = IQPair(I=Square(amplitude=1.0, duration=2000), Q=Square(amplitude=0.0, duration=2000))
"weights" = IQPair(I=Square(amplitude=1.0, duration=2000), Q=Square(amplitude=1.0, duration=2000))
```

The coordinate between the name and the `=` is the tier: `element[idx].kind`
for an exact entry, `element[*].kind` for a family entry, and nothing at all
for a global one. Waveforms use the same constructor syntax as `.qp` and are
looked up in the same registry, so a class registered with
`qp.register_waveform` needs no further work to appear in a `.wfl` file, and a
vendor waveform needs its package imported before the file loads. An empty
library writes as the header alone and loads back empty.

What `dumps` refuses to write is a waveform that is not concrete, since a
calibration set that carries a `Variable` is not something an instrument can be
handed:

```
SerializationError: cannot serialize the waveform stored under name 'x': a WaveformLibrary must hold concrete waveforms (no Variables / symbolic parameters). Underlying error: 'amp'
```

A library is never part of a `.qp` file, and resolution is one-way: the aliases
are gone from `resolved`, and `qp.dumps(resolved)` writes the pulses inline as
`IQDrag(...)` and `IQPair(...)` calls. The pair worth keeping is therefore the
alias-bearing program, which changes when the experiment changes, and the
`.wfl` next to it, which changes when the calibration does.
[The `.wfl` format](../reference/qp-format.md#the-wfl-format) gives the
grammar, the header, and the version rules.

## What the writer normalizes

Optional keyword arguments sitting at their default are not emitted at all.
`measure(..., fields=(qp.MeasurementField.IQ,))` writes as a bare `measure`
line with no `fields=` suffix, which also means adding a new optional
parameter with a default does not change what existing programs write.
Requested fields are canonically ordered on the way out, by
`MeasurementField` declaration order and then vendor names alphabetically, so
`fields=("iq", "state")` and `fields=("state", "iq")` both write as
`fields=["state", "iq"]` and both compare equal in memory.

Variable identifiers are used verbatim. Ids are validated as Python-style
identifiers and `QProgram.variable` rejects duplicates, so the writer never
has to invent a disambiguation suffix.

Nothing in the output depends on the state of the process that produced it.
There are no timestamps, no object addresses, and no set iteration left
unordered: `require` lines are sorted by vendor name, fragments are emitted in
dependency order computed at write time rather than in registration order,
variables and schema elements follow declaration order, and measurement fields
follow their canonical order. Floats are written with `repr`, which is the
shortest text that reads back as the same double. Two runs of the same program
therefore produce byte-identical files, and a regenerated `.qp` file in a git
repository shows a diff only where the program actually changed.

## Where the parser stays strict

Anything the parser cannot map onto a registered class fails on load rather
than loading as something else. That covers an unknown operation name, core or
dotted-vendor; an unknown block keyword; an unknown top-level section; an
unknown waveform or sweep-source constructor; a bus path that does not resolve
against the program's schema; a `schema:` block with no element declarations; a
`label` or `description` whose value is not a quoted string; and a `require`
line that does not match the installed extension.

The messages name the offending token and, where the answer is a small closed
set, list the alternatives:

```
Line 4: unknown operation 'playy': no core operation is registered under that name
Line 6: unknown sweep source 'Rango'; registered sources are ['Concat', 'File', 'Linspace', 'Logspace', 'Range', 'Repeat', 'Rotate', 'Values']
Line 3: unexpected top-level line 'bodyy:'; expected `metadata:`, `schema:`, `fragment ...:`, or `body:`
```

Excess positional tokens on an operation line are an error too, since dropping
them would load a different program without saying so:

```
Line 4: too many arguments for 'Play': 4 positional tokens but the operation takes at most 2; unexpected: ['"extra"', '"more"']. If you meant an arithmetic expression, parenthesize it: `(100 - t)`.
```

Two error shapes come from outside the line-tracking path and so carry no
line number: an unknown constructor name, reported as
`Unknown waveform or sweep source type: Gaussion`, and a constructor call
whose arguments do not fit its signature, where the class's own `TypeError`
travels out unwrapped. A `var` line whose id is a reserved keyword is a third
exception, and surfaces the same `InvalidVariableIdError` the builder raises at
the `variable()` call.

## Why the format is text

`.qp` trades binary compactness for two properties that matter more for the
files people keep. It is readable, so a program reviews as a diff in a git
repository and can be edited by hand or generated by a script that does not
link against QProgram. It is self-contained, so a file pins the format version
and the protocol version of every extension it depends on, and an environment
that satisfies those can load and run it without further context.

The cost is size. A sweep over ten thousand explicit points is ten thousand
numbers of text, since the writer refuses to truncate an array it could not
reconstruct. If you need a wire format optimized for size, build it on the AST
directly rather than post-processing the text: the classes in
`qp.operations`, `qp.blocks`, and `qp.waveforms`, and the expression types
`qp.Variable` and `qp.Expression`, are structural and stable.

## Related pages

[Serialization internals](../developer/serialization-internals.md) covers the
registries, the writer's dispatch, and the parser's structure.
