# .qp file format

The `.qp` format is the text serialization of a QProgram. It maps 1:1 to the
Python API: anything you can build with `qp.QProgram(...).play(...)...` you
can also write by hand, and `qp.load` will rebuild it exactly. The format is
indentation-based, line-oriented, and has no external dependency.

## Top-level layout

A file has up to four optional declarations followed by a required body:

```
#!QProgram 1.0

require <vendor> <major.minor>     # zero or more
metadata:                          # optional
  ...
schema:                            # optional, at most one
  ...
fragment <name>(<params>):         # zero or more, before body
  ...
body:                              # required
  ...
```

The header is exactly `#!QProgram <major>.<minor>`. The version identifies
the format itself. Parsers reject unsupported versions.

## `require` declarations

If the body uses vendor operations, every vendor needs a `require` line:

```
require myvendor 0.1
require othervendor 1.2
```

The parser validates each line against the installed extension:

- The **major** version must match.
- The installed **minor** must be greater than or equal to the file's.
- The patch part is informational only; the writer truncates it.

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
    drive   info=IQ
    readout info=IQ+acquires
    flux    info=single
  element c:
    flux    info=single
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
play  q[0].drive    pulse              # schema-backed path
play  "drive_q0_raw" pulse              # plain string
```

The path form is `<element>[<index>].<kind>`. The index is an integer
(`q[0]`) or a comma-separated tuple (`c[0,1]`). No spaces inside brackets.

Plain string buses bypass schema validation. Mix the two freely.

## Variable declarations

Every variable used in the body must be declared:

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

One operation per line. Bus names and string references are quoted.
Variable references are unquoted. Numeric arguments are decimal.

```
play "drive_q0" pi_pulse
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
measure "readout_q0" "readout" "weights" name="q0/readout/m0"
measure "readout_q0" "readout" "weights" name="q0/readout/m1" returns="iq,raw"
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
set_parameter "cluster" "lo_frequency" 5e9
get_parameter "cluster" "lo_frequency" -> lo_freq
set_crosstalk matrix={"flux_q0": {"flux_q0": 1.0, "flux_q1": 0.03}} offsets={"flux_q0": 0.1}
```

Key rules:

- Aliases (resolved by `with_waveforms`) are quoted strings.
- Variable references are bare identifiers.
- Inline waveforms use constructor syntax.
- `get_parameter` uses `->` to assign the result to a variable.
- Sequence kwargs are bracket literals (`outputs=[1, 2]`), dict kwargs are brace literals
  (`matrix={"a": 1.0}`), and `null` is the literal for Python `None`.
- `set_crosstalk` carries its full matrix in `matrix=` / `offsets=` / `resistances=` sections,
  each omitted when empty (an empty matrix is the bare keyword).
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
same way. Sample arrays (`Arbitrary`, `Loop` values) are always written in
full — the format never truncates.

## Expressions

Arithmetic, comparison, and logical expressions appear inline in their canonical
**parenthesised** form (the only form the parser accepts — an unparenthesised `100 - t` is a
"too many arguments" error, never a silent drop); math functions and `where` use the
function-call form:

```
wait "drive_q0" (100 - t)
set_frequency "drive_q0" (5e9 + (freq * 1e6))
set_gain "drive_q0" where((amp > 0.5), amp, 0.0)
play "drive_q0" Gaussian(amplitude=sin(phi), duration=40, sigma=8)
```

Supported binary operators: `+`, `-`, `*`, `/`. Unary `-` and `+`.
Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`. Logical: `and`, `or`, `not`.
Math functions: `sin`, `cos`, `tan`, `exp`, `log`, `sqrt`, `abs`, `minimum`,
`maximum`. Tertiary: `where(cond, a, b)`.

## Control flow blocks

Indented children belong to the enclosing block.

### `for` loop with a range

```
for freq in range(4e9, 6e9, 1e6):
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
for amp in file("sweep_values.npy"):
  set_gain "drive_q0" amp
```

### Parallel loops with `|`

```
for freq in range(4e9, 6e9, 1e6) | for gain in range(0.0, 1.0, 0.01):
  set_frequency "drive_q0" freq
  set_gain "drive_q0" gain
  play "drive_q0" "pi_pulse"
```

All loops in a parallel composition must have the same iteration count.

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
  for freq in range(4e9, 6e9, 1e6):
    for gain in range(0.0, 1.0, 0.01):
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
  for g in range(0, 1, 0.1):
    x_pulse("drive_q0", g)
    x_pulse("drive_q1", amp=(g * 0.5))
```

Fragment bodies use the same statement grammar as `body:` — including `var`
declarations (fragment-local), control flow, vendor operations, and calls to
*previously defined* fragments (define-before-use is enforced). Call
arguments follow the Python calling convention: positionals in parameter
order, then `key=value` keywords; argument tokens take the same shapes as
operation arguments (numbers, quoted strings, bus paths, identifiers,
parenthesised expressions, inline waveform constructors). Unknown call
names, duplicate definitions, reserved names, and arity mismatches are hard
`ParseError`s. See the [fragments guide](../guide/fragments.md) for the
Python API and expansion semantics.

## Vendor operations

Vendor operations use dot notation. The vendor must appear in a `require`
line:

```
require myvendor 0.1

body:
  myvendor.acquire "readout_q0" "weights" name="q0/readout/m0"
  myvendor.set_markers "drive_q0" "0001"
  myvendor.active_reset "readout_q0" "readout" "weights" "drive_q0" "pi_pulse" trigger_address=1
```

The same parsing rules apply: positional args first, optional kwargs as
`key=value`. The parser uses `inspect.signature(cls.__init__)` to fill in
the right attributes.

## Complete example

```
#!QProgram 1.0

metadata:
  label: "rabi"
  description: "Rabi oscillation experiment"

body:
  var gain

  average 1000:
    for gain in range(0.0, 1.0, 0.01):
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
    for amp in range(0.0, 1.0, 0.01) | for dur in range(10, 200, 2):
      play "drive_q1" "pi"
      sync
      play "flux_q0" FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
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
kv_pair        := IDENT ":" value

schema_decl    := "schema:" NEWLINE INDENT
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

body_sec       := "body:" NEWLINE INDENT statement+
statement      := var_decl | operation | control_block | call_stmt
call_stmt      := FRAGMENT_NAME "(" (call_arg ("," call_arg)*)? ")"   # whole statement
call_arg       := (IDENT "=")? (value | bus_path | expression)
var_decl       := "var" ID var_attr*
var_attr       := ("label" | "units" | "description") "=" STRING
ID             := [A-Za-z_][A-Za-z0-9_]*
operation      := (VENDOR ".")? OP_NAME arg* kwarg*
arg            := value | expression
kwarg          := IDENT "=" (value | expression)
expression     := "(" value (BIN_OP | CMP_OP | "and" | "or") value ")"
               | "(" ("-" | "+") value ")" | "(" "not" value ")"
BIN_OP         := "+" | "-" | "*" | "/"
CMP_OP         := "==" | "!=" | "<" | "<=" | ">" | ">="

control_block  := for_block | average_block | block_block | parallel_block
for_block      := "for" IDENT "in" (range_expr | array_expr) ":" NEWLINE INDENT statement+
parallel_block := for_block ("|" for_block)+ ":" NEWLINE INDENT statement+
average_block  := "average" NUMBER ":" NEWLINE INDENT statement+
block_block    := "block:" NEWLINE INDENT statement+

range_expr     := "range(" NUMBER "," NUMBER "," NUMBER ")"
array_expr     := "[" NUMBER ("," NUMBER)* "]" | "file(" STRING ")"
waveform_expr  := WAVEFORM_TYPE "(" (arg_list)? ")"
arg_list       := (IDENT "=")? value ("," (IDENT "=")? value)*

value          := STRING | NUMBER | BOOL | "null" | IDENT | waveform_expr
               | list_literal | dict_literal | measurement_ref
list_literal   := "[" (value ("," value)*)? "]"
dict_literal   := "{" (STRING ":" value ("," STRING ":" value)*)? "}"
measurement_ref:= HANDLE_NAME "." FIELD
```

## Canonical grammar and editor tooling

The machine-readable grammar ships with the package: `qprogram/grammar/qp.lark`
(Lark dialect; `qprogram.grammar.grammar_text()` / `parser()`). CI cross-checks
it against the production parser — everything the writer emits parses under it,
and syntactic malformations are rejected by both — so the grammar file and this
document cannot silently drift from the implementation.

Editor support builds on the real toolchain rather than the grammar:

- `python -m qprogram.lsp check <file|->` — one-shot JSON diagnostics
  (line-tagged parse errors + reference-platform validation via source maps).
- `python -m qprogram.lsp explain <file|->` — the execution-plan tree.
- `python -m qprogram.lsp serve` — an LSP server over stdio (`qprogram[lsp]`
  extra) for Neovim/Helix/Emacs.
- `editors/vscode-qp/` — a no-build VS Code extension: TextMate syntax
  highlighting, live diagnostics, snippets, and a `qp: Explain execution plan`
  command.

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
