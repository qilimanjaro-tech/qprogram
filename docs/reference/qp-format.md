# .qp file format

The `.qp` format is the text serialization of a QProgram. It tracks the Python
API closely: the operations, blocks, waveforms, and sweep sources
`program.play(...)`, `program.sweep(...)` and the rest of the builder produce
all have a written form, you can write that form by hand, and `qp.load`
rebuilds the program from it. The format is indentation-based, line-oriented,
and has no external dependency.

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

The header is exactly `#!QProgram <major>.<minor>`. The version identifies
the format itself. Parsers reject unsupported versions.

## `require` declarations

A file that uses vendor operations carries one `require` line per vendor:

```
require myvendor 0.1
require othervendor 1.2
```

The writer emits a line for every vendor the program touches — across the
body and every fragment definition, counting a vendor whose only contribution
is a block, and reaching vendor operations buried in conditional arms — so a
file `qp.dumps` produces is always complete. The parser does not check the
converse: a hand-written file that calls `myvendor.acquire` with no
`require myvendor` line loads without complaint, provided the extension is
already imported. Write the line anyway; it is what makes the file
self-contained.

What the parser does with the lines it finds is resolve each one against the
installed extension:

- The **major** version must match.
- The installed **minor** must be greater than or equal to the file's.
- The patch part is informational only; the writer truncates it.
- A vendor that is not imported yet is activated on the spot, through its
  `qprogram.vendors` entry point. This is the discovery hook, so a file
  missing the line never triggers it — the first dotted operation then fails
  as an unknown vendor operation instead.

If any check fails, parsing stops before the body is even read. The error
message names the file's version, the installed version, and what to do.

## Comments

Line comments start with `#`. They can occupy a whole line or trail an
operation:

```
# This is a comment
play "drive_q0" "pi_pulse"   # inline comment
```

The header line (`#!QProgram 1.0`) is the one place `#` is not a comment.

## Indentation

Two spaces per nesting level. Tabs are not allowed. Indentation defines
block structure, the way Python does.

## Metadata

```
metadata:
  label: "rabi"
  description: "Rabi oscillation"
```

Both fields are optional. Values are quoted strings.

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

The header is exactly `schema:` with no inline content. The optional
`naming:` line carries the pattern (placeholders `{element}`, `{index}`,
`{kind}`). One or more `element <name>:` blocks follow; each lists bus
kinds.

Each bus line is `<kind> info=<channel>[+acquires]`:

- `<channel>` is `single` or `IQ`.
- `acquires` is a flag indicating the bus has an ADC.

Programs without a schema simply omit the section. Bus references in the
body stay as quoted strings.

## Bus references in operations

Two forms appear in the body:

```
play q[0].drive "pulse"                # schema-backed path
play "drive_q0_raw" "pulse"            # plain string
```

The path form is `<element>[<index>].<kind>`. The index is an integer
(`q[0]`) or a comma-separated tuple (`c[0,1]`). No spaces inside brackets.

Plain string buses bypass schema validation. Mix the two freely.

## Variable declarations

To reference a variable, declare it. The `var` declaration is what turns a
bare identifier in an argument position into a `Variable`; an identifier with
no declaration decodes as a plain string instead, with no error. With no
`var pi_pulse` in the file, `play "b" pi_pulse` still parses; the waveform is
the string `"pi_pulse"`, and it writes back out as `play "b" "pi_pulse"`.

```
body:
  var freq                                            # bare id
  var amp  label="Drive amplitude"
  var dur  units="ns"
  var t    label="Idle time" units="ns" description="..."
```

Rules:

- `id` matches `[A-Za-z_][A-Za-z0-9_]*` (Python identifier rules).
- `id` is unique within the file.
- `id` is not one of the [reserved keywords](reserved.md).
- Optional attributes (`label`, `units`, `description`) are quoted
  `key="value"` pairs, in any order, on the same line. Each at most once.
- Backslashes and double quotes inside attribute values are escaped (`\\`,
  `\"`).

The parser rejects:

- Ids with spaces or punctuation (`var Wait Duration`).
- Ids starting with a digit (`var 1freq`).
- Ids matching a reserved keyword (`var if`).
- Duplicate ids.
- Unquoted attribute values (`label=foo`).
- Unknown or duplicate attributes.

## Operations

One operation per line. Quoting is the type distinction: a quoted token is a
plain string — a raw bus name, a waveform alias, a parameter name — and a bare
token is a variable reference or a schema-backed bus path. Numeric arguments
are decimal: integer, float, or scientific notation.

```
play "drive_q0" "pi_pulse"
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
measure "readout_q0" "readout" "weights" name="q0/readout/m0"
measure "readout_q0" "readout" "weights" name="q0/readout/m1" fields=["iq", "raw"]
wait "drive_q0" 100
wait "drive_q0" duration
sync
sync "drive_q0" "readout_q0"
set_frequency "drive_q0" 5e9
set_frequency "drive_q0" freq
set_phase "drive_q0" 1.5708
reset_phase "drive_q0"
set_gain "drive_q0" 0.5
set_offset "flux_q0" 0.1
set_offset "flux_q0" 0.1 0.2
set_parameter "drive_q0" "lo_frequency" 5e9
get_parameter "drive_q0" "lo_frequency" -> lo_freq
```

Key rules:

- Aliases (resolved by `with_waveforms`) are quoted strings.
- Variable references are bare identifiers.
- Inline waveforms use constructor syntax.
- `get_parameter` uses `->` to assign the result to a variable.
- Sequence values are bracket literals (`outputs=[1, 2]`) and string-keyed dict values are brace
  literals (`matrix={"a": 1.0}`); both are generic forms, available wherever an operation takes a
  list or a dict. `null` is the literal for Python `None`, `true`/`false` for the booleans.
- Unknown operations, unknown block keywords, and excess positional tokens are hard parse
  errors — a file never loads with content silently missing. Symmetrically, the writer raises
  `SerializationError` for anything it cannot represent faithfully (and never truncates arrays).

## Inline waveform constructors

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
play "drive_q0" IQDrag(0.5, 40, 8, 0.1)
play "flux_q0"  FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
measure "readout_q0" IQPair(Square(1.0, 2000), Square(0.0, 2000)) IQPair(Square(1.0, 2000), Square(1.0, 2000))
```

Arguments are positional or named. Variables work everywhere a number does.
Built-in waveform types: `Square`, `Gaussian`, `GaussianDragCorrection`,
`Ramp`, `FlatTop`, `SuddenNetZero`, `Sine`, `Cosine`, `Sech`, `Tukey`,
`Arbitrary`, `Chained`, `IQPair`, `IQDrag`, `Modulated`, `IQRotation`,
`IQZero`. Custom waveforms registered with `@qp.register_waveform` work the
same way. Sample arrays — `Arbitrary` samples, the values of a `Values`
sweep source — are always written in full; the format never truncates.

Constructor arguments are numbers, quoted strings, or bare variable
references. An expression or a math function in an argument position is not
part of the constructor syntax — `FlatTop(amplitude=(amp * 2), ...)` and
`Gaussian(amplitude=sin(phi), ...)` are both parse errors, and the `.qp`
language has no assignment statement to compute the value on a line of its
own. Fold the arithmetic into a Python number before serializing, or move it
into the sweep and pass the swept variable straight through. The writer does
emit these forms when the in-memory program holds them, and that is how a
`.qp` file that will not load again gets produced; see
[`SerializationError`](errors.md#serializationerror).

## Expressions

Arithmetic, comparison, and logical expressions appear inline in their canonical
**parenthesized** form (the only form the parser accepts — an unparenthesized `100 - t` is a
"too many arguments" error, never a silent drop); math functions and `where` use the
function-call form:

```
wait "drive_q0" (100 - t)
set_frequency "drive_q0" (5e9 + (freq * 1e6))
set_gain "drive_q0" where((amp > 0.5), amp, 0.0)
set_gain "drive_q0" sin(phi)
```

Expressions are argument values in their own right, not sub-expressions of a
constructor call: they appear where an operation takes a number, not inside a
waveform's or sweep source's argument list.

Supported binary operators: `+`, `-`, `*`, `/`. Unary `-` and `+`.
Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`. Logical: `and`, `or`, `not`.
Math functions: `sin`, `cos`, `tan`, `exp`, `log`, `sqrt`, `abs`, `minimum`,
`maximum`. Ternary: `where(cond, a, b)`.

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

Or external arrays loaded from disk:

```
for amp in File(path="sweep_values.npy"):
  set_gain "drive_q0" amp
```

Every sweep source takes the same constructor shape, so the rest read the
same way: `Linspace(start=…, stop=…, num=…)`, `Logspace(start=…, stop=…,
num=…)`, and the combinators `Repeat(source=…, times=…)`,
`Rotate(source=…, by=…)`, `Concat(sources=[…])`. A vendor source registered
with `register_sweep_source` is spelled the same and needs no format change.

### Parallel loops with `|`

```
for freq in Range(start=4e9, stop=6e9, step=2e7) | for gain in Range(start=0.0, stop=1.0, step=0.01):
  set_frequency "drive_q0" freq
  set_gain "drive_q0" gain
  play "drive_q0" "pi_pulse"
```

All loops in a parallel composition must have the same iteration count — 101
apiece above. A mismatch is a `ParseError` reported on the header line.

### `average`

```
average 1000:
  play "drive_q0" "pi_pulse"
  measure "readout_q0" "readout" "weights" name="m0"
```

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

Fragment bodies use the same statement grammar as `body:` — including `var`
declarations (fragment-local), control flow, vendor operations, and calls to
*already defined* fragments (define-before-use is enforced). Call
arguments follow the Python calling convention: positionals in parameter
order, then `key=value` keywords; argument tokens take the same shapes as
operation arguments (numbers, quoted strings, bus paths, identifiers,
parenthesized expressions, inline waveform constructors). Unknown call
names, duplicate definitions, reserved names, and arity mismatches are hard
`ParseError`s. See the [fragments guide](../guide/fragments.md) for the
Python API and expansion semantics.

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

The same parsing rules apply: positional args first, optional kwargs as
`key=value`. The parser uses `inspect.signature(cls.__init__)` to fill in
the right attributes. Resolution goes through the vendor's registered
operations, so the `require` line is what gets the extension imported when it
is not loaded already; see [`require` declarations](#require-declarations).

## Complete example

```
#!QProgram 1.0

metadata:
  label: "rabi"
  description: "Rabi oscillation experiment"

body:
  var gain

  average 1000:
    for gain in Range(start=0.0, stop=1.0, step=0.01):
      set_gain "drive_q0" gain
      play "drive_q0" "pi_pulse"
      sync
      measure "readout_q0" "readout" "weights" name="m0"
```

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
measurement_ref:= HANDLE_NAME "." FIELD
```

## Canonical grammar and editor tooling

The machine-readable grammar ships with the package as
`src/qprogram/grammar/qp.lark` (Lark dialect; read it at runtime with
`qprogram.grammar.grammar_text()`, or build the reference parser with
`qprogram.grammar.parser()`). CI cross-checks it against the production parser
in `tests/test_grammar.py`: a corpus of writer output and generated programs
must parse under it, and a set of syntactic malformations must be rejected by
both. That keeps the two in step over everything the corpus reaches, which is
a narrower promise than "they cannot drift".

One case the corpus does not reach: an auto-allocated measurement name embeds
the bus path, so a conditional on a schema-backed measurement is written
`if q0/readout/m0.state == 1:`. The production parser reads that back exactly;
the Lark grammar rejects it, because its name terminals exclude `/`. Pass an
explicit `name=` to any measurement you intend to branch on — `if m0.state ==
1:` satisfies both — if you also run your files through the Lark grammar.

Editor support builds on the real toolchain rather than the grammar:

- `python -m qprogram.lsp check <file|->` — one-shot JSON diagnostics
  (line-tagged parse errors + reference-platform validation via source maps).
- `python -m qprogram.lsp explain <file|->` — the execution-plan tree.
- `python -m qprogram.lsp serve` — an LSP server over stdio (`qprogram[lsp]`
  extra), for any editor that speaks LSP.

## Parser and writer API

```python
import qprogram as qp

qp.save(program, "experiment.qp")
program = qp.load("experiment.qp")

text = qp.dumps(program)
program = qp.loads(text)
```

The parser is recursive-descent, in pure Python, with no external
dependencies (no `pyyaml`, no `lark`). The writer walks the AST directly and
emits text on the fly. Both live under `qprogram.serialization`.

## Versioning

The header version (`#!QProgram 1.0`) is the format version. New minor
versions add operations, waveforms, control-flow constructs, or sections in
backward-compatible ways. Major version bumps are reserved for breaking
changes. An older parser refuses to read a higher major version.

Vendor protocol versions (`require myvendor 0.1`) are independent: they
describe the vendor's operation set, not the file format. The vendor
extension registers its own version on import.
