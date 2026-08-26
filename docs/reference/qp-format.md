# .qp file format

The `.qp` format is the text serialization of a QProgram. It tracks the Python
API closely: the operations, blocks, waveforms, and sweep sources
`program.play(...)`, `program.sweep(...)` and the rest of the builder produce
all have a written form, you can write that form by hand, and `qp.load`
rebuilds the program from it. The format is indentation-based, line-oriented,
and parses with no external dependency.

The correspondence has one hole, in the builder's favor. The builder accepts
an expression anywhere a number goes, including inside a waveform or
sweep-source constructor argument; the writer emits it; the parser rejects it
there (see [Inline waveform constructors](#inline-waveform-constructors)).
Keep constructor arguments to numbers, strings, and bare variable references
and the round trip is exact.

## Top-level layout

A file opens with the header, then carries up to four kinds of declaration
ahead of the body:

```
#!QProgram 1.0

require <vendor> <major.minor>     # zero or more
metadata:                          # optional
  ...
schema:                            # optional, at most one
  ...
fragment <name>(<params>):         # zero or more, before body
  ...
body:                              # the program itself
  ...
```

That is the order the writer emits. The parser enforces three of the ordering
rules: `require` lines sit directly after the header, a `fragment` section
precedes `body:`, and there is at most one `schema:`. Breaking one of the three
stops the parse, each on the offending line:

```
`require` declarations must appear directly after the header, before any
section
fragment definitions must appear before the `body:` section
duplicate schema declaration — a program may have at most one schema
```

Everything else is tolerated. `metadata:` and `schema:` may follow `body:`, a
second `body:` section appends its statements to the same body rather than
starting a new one, and a file whose body is empty, or which has no `body:`
section at all, parses to an empty program.

A top-level line that is none of these is a hard error rather than a skipped
line, so a typo such as `bodyy:` cannot load as an empty-but-valid program:

```
unexpected top-level line 'bodyy:'; expected `metadata:`, `schema:`,
`fragment ...:`, or `body:`
```

## Header

The header is exactly `#!QProgram <major>.<minor>`, matched by the terminal
`/#!QProgram[ \t]+[0-9]+\.[0-9]+/`. Blank lines before it are skipped.

Only the major component is binding. The running format version is
`qprogram.serialization._format.FORMAT_VERSION`, currently `"1.0"`, and a file
loads when its major matches, whatever its minor: `#!QProgram 1.7` parses under
this release. A different major, or a header with no version at all, stops the
parse on line 1:

```
Line 1: Unsupported format version 2.0
Line 1: Unsupported format version unknown
```

A file with no header at all reports `Missing #!QProgram header`.

## `require` declarations

A file that uses vendor operations carries one `require` line per vendor:

```
require myvendor 0.1
require othervendor 1.2
```

The writer emits a line for every vendor the program touches: across the body
and every fragment definition, counting a vendor whose only contribution is a
block, and reaching vendor operations buried in conditional arms. A file
`qp.dumps` produces is always complete. The parser does not check the
converse: a hand-written file that calls `myvendor.acquire` with no
`require myvendor` line loads without complaint, provided the extension is
already imported. Write the line anyway; it is what makes the file
self-contained.

What the parser does with the lines it finds is resolve each one against the
installed extension. The major version must match exactly, the installed minor
must be greater than or equal to the file's, and a patch component is accepted
and ignored, since compatibility is decided at major.minor. The writer
truncates the version it emits to `major.minor` for the same reason. A vendor
that is not imported yet is activated on the spot through its
`qprogram.vendors` entry point. That discovery is the reason to write the line:
a file missing it never triggers the import, and the first dotted operation
then fails as an unknown vendor operation instead.

Each failure stops the parse before the body is read, and names both versions.
Against an installed `myvendor 0.1.3`, the three shapes are:

```
Line 3: file requires myvendor 1.0 (major 1); installed myvendor is 0.1.3
(major 0) — major versions must match

Line 3: file requires myvendor 0.2 or compatible; installed myvendor is 0.1.3
— minor version too old

Line 3: file requires vendor 'othervendor' 0.1 but no matching extension is
registered in this environment — install the package that declares the
'qprogram.vendors' entry point for 'othervendor', or import the extension
before loading
```

`require myvendor 0.1.9` against that same installation loads, since the
file's patch component is ignored.

`qp.loads(text, auto_activate=False)` turns entry-point discovery off, and the
third message then ends differently:

```
Line 3: file requires vendor 'othervendor' 0.1 but no matching extension is
registered in this environment — auto-activation is disabled; import the
extension before loading (e.g. `import qprogram_othervendor`)
```

An extension that is installed but fails to import, or that registers no
version, surfaces as a `ParseError` wrapping a `qp.VendorActivationError`.

## Comments

Line comments start with `#`. They can occupy a whole line or trail an
operation:

```
# This is a comment
play "drive_q0" "pi_pulse"   # inline comment
```

The scan for the comment marker is quote-aware, and honors `\"` inside a
string, so `play "drive#0" "pi"` keeps its bus name and
`measure "ro" "w" "wt" name="a\"#b"` keeps the measurement name `a"#b`. The
header line is the one place `#` never starts a comment: it is taken whole, so
`#!QProgram 1.0 # note` fails with `Line 1: Unsupported format version note`
rather than parsing as a header with a trailing comment.

## Indentation

The writer emits two spaces per nesting level and never a tab. The parser is
looser: a block's body is every following line indented at least two columns
past the header, and the first line indented less ends the block. Over-indent
and the file still loads, normalizing to two spaces when it is written back
out.

Under-indent and it also loads, but it means something else. A body indented
less than two columns past its header binds to the enclosing block instead, and
nothing warns:

```
#!QProgram 1.0

body:
  average 10:
   sync
```

reloads and rewrites as

```
#!QProgram 1.0

body:
  average 10:
  sync
```

with an empty `average` block and the `sync` outside it. Indentation is the
only thing carrying block membership, so it is the one part of a hand-written
file worth checking with `python -m qprogram.lsp explain`.

Tabs are counted as one column each by the production parser, which means a
tab-indented statement reads as a dedent rather than as an indent. At the top
level that surfaces as `unexpected top-level line 'play "b" "p"'`, which does
not mention indentation at all. The reference Lark parser is configured with
`tab_len = 8` and accepts tabs, so this is one dimension in which the two
parsers are not interchangeable. Use spaces.

## Metadata

```
metadata:
  label: "rabi"
  description: "Rabi oscillation"
```

Both fields are optional and both values are quoted strings. Backslash, double
quote, newline, carriage return, and tab are escaped as `\\`, `\"`, `\n`, `\r`,
and `\t`, and unescaped on the way back in, so a label holding a quote or a
newline survives the round trip.

The two defaults differ, which is why the writer's output differs. `label`
defaults to `""` and is omitted when empty; `description` defaults to `None`,
so an explicit empty description is a distinct value and is emitted as
`description: ""`.

A line with no colon, and an unquoted `label` or `description`, each report
their own failure:

```
Line 4: invalid metadata line 'label rabi'; expected `key: value`
Line 4: metadata 'label' must be a quoted string, got: 'rabi'
```

Any other key is ignored for forward compatibility, and its value is not
checked at all, so `author: someone here` loads and is dropped.

## Schema declaration

A program with a `BusSchema` emits one inline schema block:

```
schema:
  naming: "{element}{index}/{kind}"        # optional, default shown
  element q:
    drive info=IQ
    readout info=IQ+acquires
    flux info=single
  element c:
    flux info=single
```

The header is exactly `schema:` with no inline content, and anything else
reports what it expected instead. The optional `naming:` line carries the
pattern, whose placeholders are `{element}`, `{index}`, and `{kind}`; the
writer emits it only when it differs from `BusNaming.DEFAULT_PATTERN`, which is
`"{element}{index}/{kind}"`. One or more `element <name>:` blocks follow, in
declaration order, each listing bus kinds.

Each bus line is `<kind> info=<channel>[+acquires]`, where `<channel>` is
`single` or `IQ` and `acquires` flags a bus with an ADC. The `info` value is a
`+`-joined token list carrying exactly one channel and at most one flag, and
each way of getting the section wrong has its own message:

```
Line 3: invalid schema declaration: expected `schema:` followed by indented
`element <name>:` / bus declarations; got 'schema: transmon'
Line 5: unexpected line in schema body: 'elemental q:'
Line 6: invalid bus declaration in schema body: 'drive IQ'; expected
`<kind> info=<channel>[+acquires]`
Line 6: bus `info` has multiple channel tokens (single|IQ): 'IQ+single'
Line 6: bus `info` has duplicate flag 'acquires': 'IQ+acquires+acquires'
Line 6: bus `info` has unknown token 'digital'; allowed: IQ, acquires, single
Line 6: bus `info` must specify a channel (single|IQ): 'acquires'
Line 7: duplicate bus 'drive' in element
Line 8: schema `naming` must be a quoted string: 'naming: {element}'
Line 8: inline schema has no element declarations
```

The writer always emits this expanded form, including for the typed factories
such as `qp.BusSchema.transmon()`. Those are construction-time conveniences on
the Python side; recording the structural contents instead means that adding a
bus to a preset, or spelling one of its kinds differently, cannot change the
meaning of a `.qp` file that already exists.

Programs without a schema omit the section, and bus references in the body stay
as quoted strings.

## Bus references in operations

Two forms appear in the body:

```
play q[0].drive "pulse"                # schema-backed path
play "drive_q0_raw" "pulse"            # plain string
```

The path form is `<element>[<index>].<kind>`, tokenized whole. The index is an
integer (`q[0]`) or a comma-separated tuple (`c[0,1]`), with no spaces inside
the brackets.

Promotion from the written token back to a typed `qp.BusRef` runs only on the
attributes an operation declares in its `BUS_ATTRS`, which is `("bus",)` for
every core operation except `sync`, whose list lives under `("targets",)`, and
`Call`, which declares none. Restricting it that way is what keeps a quoted
string that merely looks like a path, such as a vendor parameter alias
`"cluster[0].module"`, from turning into a bus reference on reload. Quoting
carries the same distinction for buses: a quoted `"q[0].drive"` stays the
string it was written as, and only the bare form is promoted.

Promotion also requires the program to declare a schema. Without one, every
bus stays exactly as written. With one, the element and the bus kind must both
be declared, and the failure names what is available instead:

```
Line 8: bus path 'r[0].drive' does not resolve against the program schema: No
element 'r' in schema. Available: q
Line 8: bus path 'q[0].flux' does not resolve against the program schema: 'q'
has no bus 'flux'. Available: drive
```

The index is not checked, because a schema declares element kinds rather than
a count: `q[9].drive` resolves against a schema that declares one `q` element,
and a tuple index joins with an underscore in the resolved name, so
`c[0,1].flux` under the default naming pattern is the bus `c0_1/flux`.

Plain string buses bypass schema validation entirely. Mix the two freely.

## Variable declarations

To reference a variable, declare it. The `var` declaration is what turns a
bare identifier in an argument position into a `qp.Variable`; an identifier
with no declaration decodes as a plain string instead, with no error. With no
`var pi_pulse` in the file, `play "b" pi_pulse` still parses; the waveform is
the string `"pi_pulse"`, and it writes back out as `play "b" "pi_pulse"`.

```
body:
  var freq                                            # bare id
  var amp  label="Drive amplitude"
  var dur  units="ns"
  var t    label="Idle time" units="ns" description="..."
```

The rules on the identifier and its attributes:

- `id` matches `[A-Za-z_][A-Za-z0-9_]*` (Python identifier rules).
- `id` is unique within the file, and within a fragment for a fragment-local
  declaration.
- `id` is not one of the [reserved keywords](reserved.md).
- The optional attributes are exactly `label`, `units`, and `description`,
  written as quoted `key="value"` pairs in any order on the same line, each at
  most once.
- Attribute values use the same escapes as metadata values.

A loop header may name a variable that no `var` line declares. The parser
declares it on demand, so the loop works and `qp.dumps` writes the missing
declaration out.

The failures divide by who raises them. A malformed declaration is a
`ParseError` carrying the line number:

```
Line 4: `var` declaration must have the form `var <id> [label="..."]
[units="..."] [description="..."]`. Got: 'var'
Line 4: variable id '1freq' is invalid: must match [A-Za-z_][A-Za-z0-9_]*
(no spaces or special characters)
Line 4: unexpected token 'ns' in `var` declaration; expected key="value"
Line 4: unknown variable attribute 'unit'; allowed: description, label, units
Line 4: variable attribute 'label' must be a quoted string, got: 'foo'
Line 4: duplicate variable attribute 'label'
Line 5: duplicate variable id 'amp'
```

A reserved id is different. The declaration is well formed, and it is the
program under construction that rejects it, so `var if` raises
`qp.InvalidVariableIdError` with no line number at all:

```
Variable id 'if' is reserved for future QProgram syntax (see
qprogram.RESERVED_KEYWORDS). Pick a non-reserved id such as 'if_var', or carry
the original name in the optional `label` argument.
```

Both `qp.loads` and `qp.load` document that escape from `ParseError`.

## Operations

One operation per line. Quoting is the type distinction: a quoted token is a
plain string (a raw bus name, a waveform alias that `with_waveforms` resolves
later from a [waveform library](#the-wfl-format), a parameter name) and a bare
token is a variable reference, a schema-backed bus path, or a measurement field
reference. Numeric arguments are
decimal integers, floats, or scientific notation, and the parser preserves the
distinction between `40` and `40.0` so a rewrite does not silently promote an
integer to a float.

There are eleven core operation keywords. The syntax of most follows its
constructor signature, so the table is the signature written on the wire:
parameters with no default appear positionally in declaration order, and
parameters with a default appear as `key=value`, and only when the value
differs from that default. Three are special-cased instead: `measure` writes
its handle as `name=`, `sync` writes a variadic bus list, and `get_parameter`
writes its target variable after the `->`.

| Keyword | Wire syntax | Notes |
|---|---|---|
| `play` | `play <bus> <waveform>` | Waveform is an alias string or an inline constructor |
| `measure` | `measure <bus> <waveform> <weights> name="<handle>" [fields=[...]]` | See [Measurements](#measurements) |
| `wait` | `wait <bus> <duration>` | Duration is an integer or an expression |
| `sync` | `sync` or `sync <bus> [<bus> ...]` | The bare keyword means every bus in the program |
| `set_frequency` | `set_frequency <bus> <frequency>` | |
| `set_phase` | `set_phase <bus> <phase>` | |
| `reset_phase` | `reset_phase <bus>` | |
| `set_gain` | `set_gain <bus> <gain>` | |
| `set_offset` | `set_offset <bus> <offset_path0> [offset_path1=<value>]` | The second path defaults to `None` |
| `set_parameter` | `set_parameter <bus> "<parameter>" <value>` | |
| `get_parameter` | `get_parameter <bus> "<parameter>" -> <var>` | The `->` target is the variable the read populates |

One line of each, as the writer emits them:

```
#!QProgram 1.0

body:
  var d_lo label="d.lo"

  measure "ro" "wf" "w" name="m0"
  measure "ro" "wf" "w" name="m1" fields=["state"]
  play "d" "pi"
  wait "d" 100
  set_offset "f" 0.1
  set_offset "f" 0.1 offset_path1=0.2
  sync
  sync "a" "b"
  set_parameter "d" "lo" 5000000000.0
  get_parameter "d" "lo" -> d_lo
  reset_phase "d"
  set_phase "d" 1.5
  set_frequency "d" 5000000000.0
  set_gain "d" 0.5
```

`5e9` is written `5000000000.0` because the writer renders a Python float with
`str`. Both spellings parse to the same value, so a hand-written `5e9` is
correct and comes back in decimal form.

The keyword form of an optional argument is what the writer emits, not the
only form the parser takes: `set_offset "f" 0.1 0.2` binds the second value
positionally and is rewritten as `set_offset "f" 0.1 offset_path1=0.2`.

Sequence values are bracket literals (`fields=["state", "iq"]`,
`outputs=[1, 2]`) and string-keyed dict values are brace literals
(`matrix={"a": 1.0}`). Both are generic forms, available wherever an operation
takes a list or a dict, and both are tokenized whole, so the spaces after the
commas are safe. `null` is the literal for Python `None`, and `true` / `false`
for the booleans. A dict entry with an unquoted key reports `dict keys must be
quoted strings, got 'a'`.

A positional argument that the constructor has no parameter for is an error
rather than a dropped token, since dropping it would load a different program:

```
Line 5: too many arguments for 'Wait': 4 positional tokens but the operation
takes at most 2; unexpected: ['-', 't']. If you meant an arithmetic
expression, parenthesize it: `(100 - t)`.
```

Unknown operations and unknown block keywords are hard errors for the same
reason. A keyword that names a registered block but carries no trailing colon
gets its own message, since the fix is one character:

```
Line 4: unknown operation 'fly': no core operation is registered under that
name
Line 4: 'average' is a block keyword — block headers need a trailing colon:
`average 10:`
```

Symmetrically, the writer raises `SerializationError` for anything it cannot
represent faithfully, and never truncates an array or emits a placeholder for
a node it does not recognize.

### Measurements

A measurement's handle is written as a `name=` keyword rather than as a
positional string, so the line reads as intent:
`measure q[0].readout "readout" "weights" name="q0/readout/m0"`. Every
measurement operation and every conditional referring to the same name resolve
to one `qp.MeasurementHandle` instance after a load.

Three spellings are accepted. The canonical `name="..."` keyword is what the
writer emits. A quoted string in the `handle` positional slot names the handle
too. With neither, the parser allocates one from the global `m0`, `m1`, ...
counter, even on a `q[0].readout` line: the bus is still the written token at
that point rather than a `qp.BusRef`, so the per-bus `q0/readout/m0` prefix the
builder uses for a schema-backed bus is never reached. Files the writer
produced are unaffected, since it always emits an explicit `name=`. A `name=`
that is not a quoted string reports `measurement name= must be a quoted
string, got 42`.

`fields=` selects what the measurement produces. The values are the
`qp.MeasurementField` members, `"state"`, `"iq"`, and `"raw"`, and the default
is `["iq"]`, which the writer therefore omits. An unknown name is rejected at
the line:

```
Line 4: unknown measurement field(s) ['bogus']. Known fields: ['iq', 'raw',
'state']. A vendor extension adds its own by registering
`measure.fields.<name>` via qprogram.protocol.register_capability_tokens.
```

The older `returns="state,iq"` spelling has its own diagnostic, because
neither the keyword nor the value shape is guessable from a generic unexpected
keyword error:

```
Line 4: `returns=` was replaced by `fields=`, and the value is now a bracket
list of field names rather than a comma-joined string: write
`fields=["state", "iq"]` instead of `returns="state,iq"`.
```

A vendor measurement operation registers with the same two callbacks core
`measure` uses, so `myvendor.acquire` takes `name=` and `fields=` in exactly
this form.

## Inline waveform constructors

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
play "drive_q0" IQDrag(0.5, 40, 8, 0.1)
play "flux_q0"  FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
measure "readout_q0" IQPair(Square(1.0, 2000), Square(0.0, 2000)) IQPair(Square(1.0, 2000), Square(1.0, 2000))
```

The name resolves through the waveform registry and the arguments bind to the
class's `__init__`, so the table of built-ins is the table of signatures.
Custom waveforms registered with `qp.register_waveform` are spelled the same
way and need no format change.

| Constructor | Parameters |
|---|---|
| `Square` | `amplitude`, `duration` |
| `Gaussian` | `amplitude`, `duration`, `sigma` |
| `GaussianDragCorrection` | `amplitude`, `duration`, `sigma`, `beta` |
| `Ramp` | `from_amplitude`, `to_amplitude`, `duration` |
| `FlatTop` | `amplitude`, `duration`, `smooth_duration`, `buffer=0` |
| `SuddenNetZero` | `amplitude`, `duration`, `b`, `t_phi` |
| `Sine` | `amplitude`, `duration`, `frequency`, `phase=0.0` |
| `Cosine` | `amplitude`, `duration`, `frequency`, `phase=0.0` |
| `Sech` | `amplitude`, `duration`, `tau` |
| `Tukey` | `amplitude`, `duration`, `alpha=0.5` |
| `Arbitrary` | `samples` (a bracket literal) |
| `Chained` | `waveforms` (a bracket literal of constructors) |
| `IQPair` | `I`, `Q` |
| `IQDrag` | `amplitude`, `duration`, `sigma`, `beta` |
| `Modulated` | `envelope`, `frequency`, `phase=0.0` |
| `IQRotation` | `base`, `phase` |
| `IQZero` | `envelope` |

Arguments are either all positional or all named; the two are not combined. A
call carrying any `key=value` argument is constructed from its keyword
arguments alone and its positional ones are dropped, so
`Gaussian(0.5, duration=40, sigma=8)` fails with
`TypeError: Gaussian.__init__() missing 1 required positional argument:
'amplitude'` rather than filling `amplitude` in. The writer spells every
constructor argument as a keyword, so a written file never hits this; a
hand-written mix does. Sample arrays, meaning `Arbitrary` samples and the
values of a `Values` sweep source, are always written in full; the format never
truncates.

Constructor arguments are numbers, quoted strings, bare variable references,
and nested constructors. An expression or a math function in an argument
position is not part of the constructor syntax, and the `.qp` language has no
assignment statement to compute the value on a line of its own. Both forms
report the same class of error, from the constructor-name lookup that never
finds a name:

```
play "flux_q0" FlatTop(amplitude=(amp * 2), duration=40, smooth_duration=5)
# ParseError: Unknown waveform or sweep source type:

play "drive_q0" Gaussian(amplitude=sin(phi), duration=40, sigma=8)
# ParseError: Unknown waveform or sweep source type: sin
```

Neither error carries a line number, because the lookup runs below the
parser's cursor. Fold the arithmetic into a Python number before serializing,
or move it into the sweep and pass the swept variable straight through. The
writer does emit these forms when the in-memory program holds them, and that
is how a `.qp` file that will not load again gets produced; see
[`SerializationError`](errors.md#serializationerror).

## Expressions

Arithmetic, comparison, and logical expressions appear inline in their
canonical **parenthesized** form. That is the only form the parser accepts: an
unparenthesized `100 - t` is a "too many arguments" error, never a silent drop.
Math functions and `where` use the function-call form:

```
wait "drive_q0" (100 - t)
set_frequency "drive_q0" (5e9 + (freq * 1e6))
set_gain "drive_q0" where((amp > 0.5), amp, 0.0)
set_gain "drive_q0" sin(phi)
```

Expressions are argument values in their own right, not sub-expressions of a
constructor call: they appear where an operation takes a number, not inside a
waveform's or sweep source's argument list.

The binary arithmetic operators are `+`, `-`, `*`, `/`; unary `-` and `+` are
written with no space between the sign and the operand, as `(-g)`. The
comparisons are `==`, `!=`, `<`, `<=`, `>`, `>=`, and the logical operators are
`and`, `or`, and `not`. The math functions are `sin`, `cos`, `tan`, `exp`,
`log`, `sqrt`, `abs`, `minimum`, and `maximum`, and the ternary is
`where(cond, a, b)`, which reports `where(...) requires 3 arguments
(condition, then, else); got 2` on any other arity.

Inside the parentheses the parser accepts exactly three shapes: one token
opening with a sign, two tokens starting with `not`, or three tokens whose
middle one is an operator. Anything else reports `could not parse expression:
'(a b c d)'` or `unknown operator '%' in expression '(a % b)'`.

A measurement field reference is written unquoted as `<name>.<field>`, and
`state` is the only field a condition may branch on: the enum lists what a
measurement may produce, while a condition needs a classified scalar, so
`m0.iq` raises a plain `ValueError`, not a `ParseError`:
`MeasurementRef field must be one of ['state'], got 'iq'`. The name has to be
a single clean token for the unquoted form to survive, which is
why the writer refuses to emit a `MeasurementRef` whose handle name carries
whitespace, a quote, `#`, a comma, a dot, or a bracket. A field reference to a
name no measurement in the file declared decodes as a plain string and then
fails as an operand: `cannot use 'm0.state' (str) as an expression operand`.

## Control flow blocks

Indented children belong to the enclosing block.

### `for` loop with a range

```
for freq in Range(start=4e9, stop=6e9, step=1e6):
  set_frequency "drive_q0" freq
  play "drive_q0" "pi_pulse"
```

### `for` loop over arbitrary values

```
for amp in [0.0, 0.1, 0.3, 0.5, 0.7, 1.0]:
  set_gain "drive_q0" amp
  play "drive_q0" "pi_pulse"
```

The bracket literal is sugar for a `Values` source, and it is the form the
writer emits for one, since a source whose only parameter is the sweep itself
reads better as the sweep.

Every other sweep source takes the constructor shape, resolved through the
sweep-source registry by class name, exactly as a waveform is:

| Source | Parameters |
|---|---|
| `Range` | `start`, `stop`, `step=1` |
| `Linspace` | `start`, `stop`, `num` |
| `Logspace` | `start`, `stop`, `num` |
| `Values` | `points` (or the bracket literal) |
| `File` | `path` |
| `Repeat` | `source`, `times` |
| `Rotate` | `source`, `by=1` |
| `Concat` | `sources` (a bracket literal) |

The last three are combinators, and nest at any depth, because a nested
`Name(args)` argument resolves through the same registry lookup:

```
for amp in Concat(sources=[Range(start=0.0, stop=1.0, step=0.5), Rotate(source=[1, 2, 3], by=1)]):
  set_gain "drive_q0" amp
```

`File` reads an external array from disk, which keeps a long sweep out of the
file:

```
for amp in File(path="sweep_values.npy"):
  set_gain "drive_q0" amp
```

A vendor source registered with `qp.register_sweep_source` is spelled the same
way and needs no format change. An unregistered name reports the whole
registry, so the spelling of the one you wanted is in the message:

```
Line 4: unknown sweep source 'Bogus'; registered sources are ['Concat',
'File', 'Linspace', 'Logspace', 'Range', 'Repeat', 'Rotate', 'Values']
```

A malformed header, and a source that is neither a bracket literal nor a call,
report separately:

```
Line 4: invalid for loop header: 'for g range(0, 1, 0.1)'
Line 4: unknown sweep source 'Range'; expected `Name(args)` or `[values]`
```

### Parallel loops with `|`

```
for freq in Range(start=4e9, stop=6e9, step=2e7) | for gain in Range(start=0.0, stop=1.0, step=0.01):
  set_frequency "drive_q0" freq
  set_gain "drive_q0" gain
  play "drive_q0" "pi_pulse"
```

All loops in a parallel composition must have the same iteration count, 101
apiece above, because they advance in lockstep. A mismatch is a `ParseError`
reported on the header line, and it names the counts:

```
Line 4: parallel loops must have the same number of iterations to advance in
lockstep; got Sweep('a'): 11, Sweep('b'): 3
```

### `if` / `elif` / `else`

```
measure "readout_q0" "readout" "weights" name="m0" fields=["state"]
if m0.state == 0:
  play "drive_q0" "pi_pulse"
elif m0.state == 1:
  play "drive_q0" "id"
else:
  sync
```

The condition on an `if` or `elif` header is written without the outer
parentheses a nested comparison would carry, which is why the writer strips
them there. A fully parenthesized expression is accepted too, so
`if (not flag):` works when `flag` is a declared variable. A condition whose
operand is neither a declared variable, a number, nor a measurement field
reference reports `cannot use 'flag' (str) as an expression operand`.

The chain ends at the first line at the header's indent that is not `elif` or
`else`. Every way of getting the chain wrong is reported:

```
Line 4: `if` requires a condition: `if <expr>:`
Line 9: `elif` cannot follow `else` in the same chain
Line 9: multiple `else` arms in the same conditional chain
Line 7: 'elif' without a preceding `if:` at the same indent level
```

The last one covers a bare `elif:` as well as a genuinely orphaned arm: a
condition-less `elif` never reads as part of the chain, so it is reported as
one that has no `if` above it.

### `average`

```
average 1000:
  play "drive_q0" "pi_pulse"
  measure "readout_q0" "readout" "weights" name="m0"
```

The shot count is a single positional integer, at least 1. Omitting it reports
`average requires a shot count`, a non-integer reports `average: invalid shots
count '10.5'`, and zero or a negative count reaches the block's own validation:
`Average shots must be an integer >= 1, got 0`.

### Generic `block`

```
block:
  play "drive_q0" "pi_pulse"
  wait "drive_q0" 100
```

### Nesting

Blocks compose arbitrarily:

```
average 1000:
  for freq in Range(start=4e9, stop=6e9, step=1e6):
    for gain in Range(start=0.0, stop=1.0, step=0.01):
      set_frequency "drive_q0" freq
      set_gain "drive_q0" gain
      play "drive_q0" "pi_pulse"
      sync
      measure "readout_q0" "readout" "weights" name="m0"
```

## Fragments

Reusable sub-programs are declared in top-level `fragment` sections (before
`body:`) and instantiated by bare `name(args)` call statements:

```
fragment x_pulse(drive, amp):
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)

body:
  var g
  for g in Range(start=0, stop=1, step=0.1):
    x_pulse("drive_q0", g)
    x_pulse("drive_q1", amp=(g * 0.5))
```

Fragment bodies use the same statement grammar as `body:`, including `var`
declarations (fragment-local), control flow, vendor operations, and calls to
*already defined* fragments. Define-before-use is enforced by the definition
table itself, which is what makes a written file list fragments in dependency
order; the writer computes that order depth-first over the call graph rather
than trusting registration order, and refuses to write a call cycle.

Call arguments follow the Python calling convention: positionals in parameter
order, then `key=value` keywords. Argument tokens take the same shapes as
operation arguments, meaning numbers, quoted strings, bus paths, identifiers,
parenthesized expressions, and inline waveform constructors, and a bare
path-shaped token promotes to a `qp.BusRef` here too, so a fragment can take a
bus as a parameter. The writer emits every argument positionally, so the
keyword spelling a caller used at build time is not part of the wire form.

Every failure here is a hard `ParseError`:

```
Line 3: invalid fragment header 'fragment f1:'; expected
`fragment <name>(<param>, ...):`
Line 3: duplicate fragment definition 'x_pulse'
Line 3: invalid fragment parameter '1amp': must match [A-Za-z_][A-Za-z0-9_]*
Line 7: unknown fragment 'x_pulse'; fragments must be defined in a
`fragment x_pulse(...):` section before use
Line 7: fragment call 'x_pulse': duplicate keyword argument 'amp'
Line 7: fragment call 'x_pulse': positional argument after keyword argument
```

A whole-line call whose name is a registered waveform gets a pointed message
instead, since the mistake is a misplaced constructor:

```
Line 5: waveform constructor 'Gaussian' cannot stand alone as a statement;
waveforms appear as operation arguments (e.g. `play "bus" Gaussian(...)`)
```

A fragment name that is a [reserved keyword](reserved.md) raises
`qp.ValidationError`, not a `ParseError`. See the
[fragments guide](../guide/fragments.md) for the Python API and expansion
semantics.

## Vendor operations

Vendor operations use dot notation, and the vendor is named in a `require`
line above the body:

```
require myvendor 0.1

body:
  myvendor.acquire "readout_q0" "weights" name="q0/readout/m0"
  myvendor.set_markers "drive_q0" "0001"
  myvendor.active_reset "readout_q0" "readout" "weights" "drive_q0" "pi_pulse" trigger_address=1
```

The same parsing rules apply: positional arguments first, optional keyword
arguments as `key=value`, bound to `inspect.signature(cls.__init__)`. Vendor
*blocks* work the same way and take a trailing colon, as
`myvendor.infinite_loop:`, and need no grammar change, because an operation and
a keyword-led block share one shape and it is the trailing colon that tells
them apart.

Resolution goes through the vendor's registered operations, so an extension
that never got imported produces:

```
Line 5: unknown vendor operation myvendor.'acquire': no operation is
registered under that name. Import the 'myvendor' extension package before
loading, and check the file's `require myvendor <x.y>` declaration.
```

That is the failure the `require` line exists to prevent; see
[`require` declarations](#require-declarations).

## A file exercising every section

The writer's output for a program with metadata, a schema, a fragment, an
averaged sweep, a conditional, and a two-index bus path:

```
#!QProgram 1.0

metadata:
  label: "rabi"
  description: "Rabi oscillation"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires
  element c:
    flux info=single

fragment x_pulse(drive, amp):
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)

body:
  var gain label="Drive amplitude" units="a.u."

  average 1000:
    for gain in Range(start=0.0, stop=1.0, step=0.01):
      x_pulse(q[0].drive, gain)
      sync
      measure q[0].readout "readout" "weights" name="m0" fields=["state"]
      if m0.state == 1:
        set_offset c[0,1].flux 0.1
      else:
        wait q[0].drive 100
```

Reading that back and writing it again reproduces it byte for byte, which is
the property `tests/test_round_trip.py` and the hypothesis strategies in
`tests/test_round_trip_property.py` check across generated programs.

## Two-qubit CZ chevron

```
#!QProgram 1.0

metadata:
  label: "cz_chevron"

body:
  var amp
  var dur

  average 1000:
    for amp in Range(start=0.0, stop=1.0, step=0.01):
      for dur in Range(start=10.0, stop=210.0, step=2.0):
        play "drive_q1" "pi"
        sync
        play "flux_q0" FlatTop(amplitude=amp, duration=dur, smooth_duration=5, buffer=0)
        sync
        measure "readout_q0" "readout" "default" name="m0"
        measure "readout_q1" "readout" "default" name="m1"
```

## Grammar summary

```
file           := header require* section*
header         := "#!QProgram" VERSION
require        := "require" IDENT VERSION
section        := metadata_sec | schema_sec | fragment_sec | body_sec

fragment_sec   := "fragment" IDENT "(" (IDENT ("," IDENT)*)? ")" ":" NEWLINE INDENT statement+
                                                    # before body_sec; statement+ as in body

metadata_sec   := "metadata:" NEWLINE INDENT kv_pair+
kv_pair        := IDENT ":" STRING     # `label` / `description`; other keys are ignored.
                                       # qp.lark's `meta_value` also admits a number or
                                       # true/false, which this parser rejects for these keys.

schema_sec     := "schema:" NEWLINE INDENT
                    ("naming:" STRING NEWLINE)?
                    element_block+
element_block  := "element" IDENT ":" NEWLINE INDENT bus_line+
bus_line       := IDENT "info=" INFO NEWLINE
INFO           := CHANNEL ("+" INFO_FLAG)*
CHANNEL        := "single" | "IQ"
INFO_FLAG      := "acquires"

bus_ref        := STRING | bus_path
bus_path       := ELEMENT "[" INDEX "]" "." KIND_NAME
ELEMENT        := IDENT
KIND_NAME      := IDENT
INDEX          := NUMBER | NUMBER ("," NUMBER)+

body_sec       := "body:" NEWLINE [INDENT statement+]   # an empty body is legal
statement      := var_stmt | for_stmt | if_stmt | call_stmt | line_stmt
call_stmt      := FRAGMENT_NAME "(" (call_arg ("," call_arg)*)? ")"   # whole statement
call_arg       := (IDENT "=")? (value | bus_path | expression)
var_stmt       := "var" ID var_attr*
var_attr       := ("label" | "units" | "description") "=" STRING
ID             := [A-Za-z_][A-Za-z0-9_]*

# Operations and keyword blocks share one shape: the registries decide which
# keywords are legal, and the tail decides which of the two a statement is.
# That is why a vendor block needs no grammar change.
line_stmt      := op_name op_arg* line_tail
op_name        := IDENT | VENDOR "." IDENT
op_arg         := value | kwarg
kwarg          := IDENT "=" value
line_tail      := ":" NEWLINE INDENT statement+   # block: `average 1000:`, `block:`, `<vendor>.<name>:`
               | ("->" IDENT)? NEWLINE            # operation; `->` target is get_parameter's
expression     := "(" value (BIN_OP | CMP_OP | "and" | "or") value ")"
               | "(" ("-" | "+") value ")" | "(" "not" value ")"
BIN_OP         := "+" | "-" | "*" | "/"
CMP_OP         := "==" | "!=" | "<" | "<=" | ">" | ">="

for_stmt       := for_header ("|" for_header)* ":" NEWLINE INDENT statement+
for_header     := "for" IDENT "in" sweep_source
sweep_source   := source_call | list_literal      # `Range(start=…)`; `[...]` is sugar for Values
                                                  # built-ins: Range, Linspace, Logspace, Values,
                                                  # File, Repeat, Rotate, Concat

if_stmt        := "if" condition ":" NEWLINE INDENT statement+
                    ("elif" condition ":" NEWLINE INDENT statement+)*
                    ("else" ":" NEWLINE INDENT statement+)?
condition      := value CMP_OP value | expression  # e.g. `if m0.state == 0:`

# Every constructor call has the same shape; the name says which registry
# resolves it (waveforms, sweep sources, math functions / `where`).
source_call    := SOURCE_TYPE "(" (arg_list)? ")"
waveform_expr  := WAVEFORM_TYPE "(" (arg_list)? ")"
arg_list       := (IDENT "=")? value ("," (IDENT "=")? value)*

value          := STRING | NUMBER | BOOL | "null" | IDENT | bus_path | waveform_expr
               | source_call | expression | list_literal | dict_literal | measurement_ref
list_literal   := "[" (value ("," value)*)? "]"
dict_literal   := "{" (STRING ":" value ("," STRING ":" value)*)? "}"
measurement_ref:= HANDLE_NAME "." FIELD          # FIELD is `state`
```

## Canonical grammar and editor tooling

The machine-readable grammar ships with the package as
`src/qprogram/grammar/qp.lark`, in the Lark dialect, parsed LALR with a
two-space `Indenter`. Read it at runtime with
`qprogram.grammar.grammar_text()`, build the reference parser with
`qprogram.grammar.parser()`, or parse one document with
`qprogram.grammar.parse_text(text)`, which appends the trailing newline the
grammar expects and returns the Lark tree. `lark` is a development dependency,
so the last two raise `ModuleNotFoundError` unless it is installed;
`grammar_text()` reads the shipped file and needs nothing.

The grammar is normative but over-approximates everything
semantic: any identifier is a valid operation or block keyword, section order
is free, and duplicate declarations and bus-path resolution are post-parse
checks the production parser performs and the grammar does not. It is exact
about token shapes, meaning quoting, parenthesized expressions, call adjacency
(`name(` with no intervening space), and the literal forms.

CI cross-checks the two in `tests/test_grammar.py`: a corpus of writer output
and hypothesis-generated programs must parse under the grammar, and a corpus
of syntactic malformations must be rejected by both. That keeps them in step
over everything the corpus reaches, which is a narrower promise than "they
cannot drift".

Two cases the corpus does not reach are worth knowing. An auto-allocated
measurement name embeds the bus path, so a conditional on a schema-backed
measurement is written `if q0/readout/m0.state == 1:`. The production parser
reads that back exactly; the Lark grammar rejects it, because its name
terminals exclude `/`. Pass an explicit `name=` to any measurement you intend
to branch on, since `if m0.state == 1:` satisfies both. And the two disagree on
tabs, as described under [Indentation](#indentation).

Editor support builds on the real toolchain rather than on the grammar:

- `python -m qprogram.lsp check <file|->` writes a JSON array of diagnostics to
  stdout, one object per diagnostic with `line`, `end_line`, `severity`,
  `code`, and `message`. Lines are 0-based, for the LSP's benefit. The exit
  status is 1 when anything of error severity was found. A parse failure
  reports one `parse-error` diagnostic; otherwise reference-platform validation
  runs and its `Diagnostic`s are mapped onto lines through the program's source
  map. `--no-validate` reports syntax only.
- `python -m qprogram.lsp explain <file|->` writes the execution-plan tree.
- `python -m qprogram.lsp serve` runs an LSP server over stdio for any editor
  that speaks the protocol, and needs the `qprogram[lsp]` extra.

## Parser and writer API

```python
import qprogram as qp

qp.save(program, "experiment.qp")
program = qp.load("experiment.qp")

text = qp.dumps(program)
program = qp.loads(text)
```

Files are read and written as UTF-8 regardless of the platform's locale.
`qp.loads` and `qp.load` both take `auto_activate`, which defaults to `True`
and controls whether a `require` line may import an extension through its
entry point. Both raise `qp.ParseError` for malformed input,
`qp.ValidationError` for a declaration the grammar accepts and the program
rejects (a reserved variable id), and a plain `TypeError` for a constructor
call that does not fit its class's signature. `qp.dumps` and `qp.save` raise
`qp.SerializationError`, including for a `qp.Fragment` passed directly, which
has no file form of its own: a fragment is emitted as a section of the host
program that calls it.

The parser is recursive-descent, in pure Python, with no external dependencies
(no `pyyaml`, no `lark`). The writer walks the AST directly and emits text on
the fly. Both live under `qprogram.serialization`, whose `loads`, `load`, and
`ParseError` are resolved through a module `__getattr__` so that importing the
package does not close the parser-to-program import cycle.

## Versioning

The header version (`#!QProgram 1.0`) is the format version. New minor
versions add operations, waveforms, control-flow constructs, or sections in
backward-compatible ways, and a parser accepts any minor within its own major.
Major version bumps are reserved for breaking changes, and an older parser
refuses to read a higher major version.

Vendor protocol versions (`require myvendor 0.1`) are independent: they
describe the vendor's operation set, not the file format. The vendor extension
registers its own version on import, through `qp.register_vendor_version`.

## The `.wfl` format

`.wfl` is the other text format the package reads and writes. It holds the
concrete pulses that the quoted waveform aliases in a `.qp` body resolve to,
and it is a separate file because the two change on different schedules: the
program is edited when the experiment changes, the library when the qubits are
recalibrated. Nothing in a `.qp` file references a `.wfl` file, and the `.qp`
grammar has no section that could carry one. A
[`WaveformLibrary`](api-qprogram.md#qprogram.WaveformLibrary) writes and reads
it through its own `dumps`, `loads`, `save`, and `load`, described under
[waveform libraries and the `.wfl` file](../guide/serialization.md#waveform-libraries-and-the-wfl-file).

A document is a header line and one entry per line:

```
#!WaveformLibrary 1.0
"pi_pulse" q[0].drive = IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
"pi_pulse" q[1].drive = IQDrag(amplitude=0.9, duration=40, sigma=8, beta=0.1)
"cz" c[0,1].flux = Square(amplitude=0.3, duration=200)
"readout" q[*].readout = IQPair(I=Square(amplitude=1.0, duration=2000), Q=Square(amplitude=0.0, duration=2000))
"weights" = IQPair(I=Square(amplitude=1.0, duration=2000), Q=Square(amplitude=1.0, duration=2000))
```

```
file      := BLANK* header entry_line*
header    := "#!WaveformLibrary" VERSION
entry_line:= NAME coord? "=" waveform_expr NEWLINE
NAME      := STRING                     # the alias played in the program; non-empty
coord     := ELEMENT "[" (INDEX | "*") "]" "." KIND_NAME
INDEX     := DIGITS ("," DIGITS)*       # a tuple for a multi-index element
```

The coordinate between the name and the `=` is the entry's tier: a coordinate
with an index binds the name to that one bus, `[*]` binds it to every index of
that element and bus kind, and no coordinate at all binds it to every bus.
Resolution takes the most specific match for the bus being resolved. Indices
are non-negative decimal digits, optionally comma-separated, so `q[-1].drive`
is not a coordinate. The element and kind names are not checked against any
schema, since a library is a standalone artifact and knows nothing about the
program it will be applied to.

The waveform after the `=` is exactly the constructor syntax
[inline waveform constructors](#inline-waveform-constructors) describes, looked
up in the same registry, so every built-in and every class registered with
`qp.register_waveform` is spelled the same way in both formats and a vendor
waveform needs its package imported before the load. Exactly one constructor
call is allowed after the `=`. The writer names every argument, while a
hand-written positional call such as `Square(0.1, 40)` is accepted and comes
back named. There are no variables in a library, so a bare token in an argument
position is read as a string rather than as a reference: a hand-written
`Gaussian(amp, 40, 8)` loads with `amplitude` set to the string `"amp"`. In the
other direction, a library holding a waveform built from a `Variable` cannot be
written at all, and `dumps` raises `qp.SerializationError`.

The header must be the first non-blank line, and a comment ahead of it is an
error rather than a comment. After the header, blank lines and lines whose
first non-space character is `#` are skipped, and every other line must be an
entry. Leading whitespace on an entry line is ignored, since the format has no
nesting. A `#` further along an entry line is not a comment and fails as an
extra token. Entries are written in insertion order and read in file order,
and a name repeated at the same coordinate keeps the last value, so
`loads(dumps(library))` reproduces the library exactly. An empty library is the
header line by itself.

Parse failures raise `qp.ParseError` carrying the 1-based line number:

| Malformed input | Message |
|---|---|
| `"x" = Gaussian(...)` with no header, or a comment before it | `Line 1: Missing #!WaveformLibrary header` |
| `#!WaveformLibrary 9.0` | `Line 1: Unsupported WaveformLibrary format version 9.0` |
| `x = Gaussian(amplitude=0.5, duration=40, sigma=8)` | `Line 2: entry must start with a quoted waveform name` |
| `"x" Gaussian(amplitude=0.5, duration=40, sigma=8)` | `Line 2: WaveformLibrary entry must contain '=': '"x" Gaussian(amplitude=0.5, duration=40, sigma=8)'` |
| `"x" q0.drive = Square(amplitude=0.1, duration=40)` | `Line 2: invalid entry coordinate 'q0.drive'; expected element[idx].kind or element[*].kind` |
| `"x" q[0].drive extra = Square(amplitude=0.1, duration=40)` | `Line 2: unexpected tokens before '=': ['q[0].drive']` |
| `"x" = Square(amplitude=0.1, duration=40)  # pi/2` | `Line 2: expected exactly one waveform after '='` |
| `"x" = Bogus(1, 2)` | `Line 2: invalid waveform: Unknown waveform or sweep source type: Bogus` |

The last of those differs from `.qp`, where an unknown constructor name comes
back with no line number at all: the library parser wraps that lookup failure
in a `ParseError` that carries one.

The header version comes from `WAVEFORM_LIBRARY_FORMAT_VERSION` in
`qprogram/waveform_library.py` and is independent of the `.qp`
`FORMAT_VERSION`; the two formats version separately, and a `.wfl` version says
nothing about which `.qp` version it accompanies. Only the major component is
compared, so `#!WaveformLibrary 1`, `1.0.3`, and `1.7` all load on today's
reader while a different major is refused outright, and the compatibility
contract is the same as `.qp`'s: a minor version may add entry forms and
waveform vocabulary, a major bump is reserved for a change an older reader
cannot handle. The version token is read as the last whitespace-separated token
on the header line, so a header with anything after the version reports that
trailing token as an unsupported version. The writer always emits the current
version, which means rewriting a `1.7` file on a `1.0` reader writes `1.0` and
drops the claim to have come from a newer minor.
