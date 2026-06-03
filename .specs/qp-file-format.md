# .qp File Format Specification (Draft)

> **Source / upstream:** https://www.notion.so/qilimanjaro/qp-File-Format-Specification-Draft-3307eec14c5381948e39e397b2062803
> **Reconciled:** 2026-06-02 with the reference implementation; pushed to the Notion page 2026-06-03 (incl. vendor auto-activation, fragments, and the §8 source-map note). This file and the Notion page are in sync.
> **Status:** Draft (specification — the implementation matches this revision)

---

# 1. Overview

The `.qp` format is the serialization format for QProgram. It is a **text-based**, **human-readable** language that maps 1:1 to the QProgram Python API. Any QProgram constructed in Python can be saved to `.qp` and loaded back without loss.

Design goals:

- **Human-readable** — looks like a clean, minimal programming language
- **Self-contained** — waveform data embedded inline or referenced by file path
- **Versionable** — format version declared in header
- **Extensible** — custom waveform types and operations can be registered
- **Round-trip safe** — `load(save(program)) == program`, with no truncation of any array or
  sample data and no silent drops: the parser rejects anything it doesn't recognise, and the
  writer raises `SerializationError` rather than emit output it cannot guarantee to reload

**Encoding:** `.qp` files are always UTF-8, independent of the platform locale.

---

# 2. File Structure

A `.qp` file has up to four kinds of optional declarations plus a required body, in order. `metadata` is optional; **at most one** `schema` declaration may appear (zero or one); `fragment` definitions are optional (any number, each with a unique name; Section 2.5); `body` is required.

```
#!QProgram 1.0

require <vendor> <major.minor>   # optional, one per vendor dependency

metadata:
  ...

schema:                          # optional, at most one (Section 3a)
  ...

fragment <name>(<params>):       # optional, any number (Section 2.5)
  ...

body:
  ...
```

Operations can use concrete inline waveforms directly (e.g. `Gaussian(amplitude=0.5, duration=40, sigma=8)`), or string aliases (e.g. `"pi_pulse"`) that are resolved externally via `with_waveforms()` at execution time.

## 2.1 Header

The first line must be the version header:

```
#!QProgram 1.0
```

This identifies the file as QProgram format and declares the specification version. Parsers must reject files with unsupported versions.

## 2.2 Require Declarations

If the program uses vendor-specific operations, the required vendors must be declared after the header, before any sections. Each declaration carries a **`<major>.<minor>`** version that the parser validates against the installed vendor extension:

```
#!QProgram 1.0

require qblox 0.1
require qdac 1.2
```

- Every `vendor.operation` in the body must have a corresponding `require` declaration; the version is **mandatory**.
- The version specifies the **vendor protocol version** (the version of the operation set the file expects), not the Python package version on PyPI. The vendor extension registers it on import via `register_vendor_version("qblox", "0.1.0")`.
- **Compatibility rules:**
  - same **major** as the installed extension is required;
  - the installed **minor** must be `≥` the file's minor.
  - Patch is informational only.
- The parser fails early with a clear error if any `require` cannot be satisfied, before parsing the body. Examples:
  - *file requires `qblox 1.0` (major 1); installed `qblox` is `0.1.0` (major 0) — major versions must match*
  - *file requires `qblox 0.5` or compatible; installed `qblox` is `0.1.0` — minor version too old*
  - *file requires vendor 'qblox' 0.1 but no matching extension is registered*
  - *`require` declaration must specify a version: `require <vendor> <major.minor>`*
- Programs using only core operations need no `require` declarations.
- The serializer always emits the version of whichever vendor extension is currently registered, so a saved-then-loaded file pins the version that produced it.

**Auto-activation (entry-point discovery).** A `require <vendor>` line whose extension is installed but not yet imported is **activated on demand**: the loader looks up the `qprogram.vendors` entry-point group, finds the entry point whose name is the vendor namespace, and imports its target module (which self-registers the namespace, version, operations, and profile). This makes a `.qp` file a self-contained contract — any environment with the extension *installed* can load it, whether or not the caller imported the package first. The compatibility check above then runs against the just-activated version. Notes:

- Discovery only triggers for an unregistered vendor; an already-imported extension is used as-is (no scan).
- If an entry point claims the vendor but its import fails (or imports without registering a version), parsing fails with a clear error naming the broken extension — distinct from "not installed".
- `loads(text, auto_activate=False)` / `load(path, auto_activate=False)` opt out: with auto-activation off, an unimported vendor is a hard error (no implicit imports), and the message tells the caller to `import qprogram_<vendor>` first.

## 2.3 Comments

Line comments start with `#` (except the header line). A `#` inside a quoted string — even
after an escaped quote — is string content, not a comment:

```
# This is a comment
play "drive_q0" pi_pulse  # inline comment
```

## 2.4 Indentation

Indentation is **2 spaces** per level. Tabs are not allowed. Indentation defines block structure (like Python).

## 2.5 Fragment Definitions

A fragment is a named, parameterized sub-program defined once and instantiated by bare call statements (Section 4.7). Definitions appear at the top level, **before `body:`**, after the (optional) `schema:` section when bus paths are used inside fragment bodies:

```
fragment x_pulse(drive, amp):
  play drive Gaussian(amplitude=amp, duration=40, sigma=8)

fragment rabi_point(drive, readout, amp):
  x_pulse(drive, amp)
  sync
  measure readout "ro_wf" "weights" name="m0"
```

- The header is `fragment <name>(<param>, ...):` — `<name>` and each `<param>` follow identifier rules (`[A-Za-z_][A-Za-z0-9_]*`, not reserved). Zero parameters is written `fragment reset():`.
- The body uses the **same statement grammar as `body:`** (operations, control flow blocks, `var` declarations, vendor dot-notation, calls to *previously defined* fragments), indented 2 spaces.
- Parameters are untyped placeholders referenced as bare identifiers; a parameter may stand in value, bus, or waveform position. The call site's argument determines the kind.
- `var` declarations inside a fragment are **fragment-local**: on expansion they are renamed onto the host program (`{fragment}_{id}`, numeric suffix on collision).
- Fragments may call previously-defined fragments — define-before-use is enforced, which makes the definition order topological by construction. Cycles are impossible to express in a file (and rejected everywhere else).
- The writer emits every fragment registered on the program, dependencies first; an unused definition in a hand-written file is preserved on round-trip.
- Errors (all `ParseError`): a definition after `body:`, duplicate fragment names, reserved/invalid names or parameters, duplicate parameters, malformed headers.

## 2.6 Parser strictness for fragments

A statement of the shape `name(args)` is **only** a fragment call. An unknown name in that shape is a hard error (with a dedicated hint when `name` is a registered waveform class — waveform constructors cannot stand alone as statements).

---

# 3. Metadata Section

```
metadata:
  label: "rabi"
  description: "Rabi oscillation experiment"
```

All fields are optional. Values are strings (quoted), numbers, or booleans (`true`/`false`).
`label` and `description` must be quoted strings; their values use the standard string escapes
(`\\`, `\"`, `\n`, `\r`, `\t`), applied symmetrically by the writer and parser. Unknown keys
are tolerated for forward compatibility; malformed lines (no `:`, unquoted label) are errors.

---

# 3a. Schema Declaration

Buses fall into two categories on the wire:

1. **Plain string** — `play "drive_q0_bus" pulse`. No schema section. The bus is just a string, no metadata.
2. **Schema-backed** — declared in a single inline `schema:` block at the top of the file. Operations then reference buses by path: `play q[0].drive pulse`.

The Python side offers preset constructors (`BusSchema.transmon()`, `.fluxonium()`, …) and a dynamic builder (`BusSchema()` + `add_element(...)`) as construction-time conveniences, but **both serialize to the same inline form** — the file format records the structural element/bus contents, not which constructor the user reached for. This keeps `.qp` files immune to silent drift if a preset class ever grows a new bus or renames a kind.

**One schema per program.** A `.qp` file declares at most one `schema:` block (written before `body:` and after `require` / `metadata:`). The single-schema model lets operation bus references be terse paths — `q[0].drive` rather than `transmon.q[0].drive` — because there's only one schema they could resolve against. Programs that need multiple chip layouts can still use plain-string buses for the second-citizen ones, or roll them into a single inline schema with disjoint element names.

## 3a.1 Inline schema form

```
schema:
  naming: "{element}{index}/{kind}"                # optional; default if omitted
  element q:
    drive   info=IQ
    readout info=IQ+acquires
    flux    info=single
  element c:
    flux    info=single
```

- The header is exactly `schema:` (no inline content on the same line).
- Optional `naming: "<pattern>"` line; the default is `"{element}{index}/{kind}"`. The pattern supports the placeholders `{element}`, `{index}`, `{kind}`.
- One or more `element <elem>:` blocks. Each lists bus kinds as `<kind> info=<channel>[+acquires]` lines.
- `info=` channel is required (`single` or `IQ`); the only currently defined flag is `acquires` (bus has an ADC). Examples: `info=single`, `info=IQ`, `info=IQ+acquires`.
- Indentation: 2 spaces for `naming:` / `element` headers, 4 spaces for bus-kind lines.

User-typed `BusSchema` subclasses (e.g. `MyChipSchema` with `@property` factories) serialize through the same form — the Python class identity doesn't survive round-trip, but the element/bus structure does, so the loaded program is structurally complete and validation post-load works against the rebuilt schema.

## 3a.2 Bus references in operations

Buses backed by the program's schema are written as paths in operation arguments — no schema name prefix, since there's only one schema:

```
play   q[0].drive    pulse
measure q[0].readout "r" "w"
set_offset c[0,1].flux 0.5
sync   q[0].readout  q[1].readout
```

- Form: `<element>[<index>].<kind>`.
- `<index>` is an integer (`q[0]`) or a comma-separated tuple of integers (`c[0,1]`). No spaces inside brackets.
- Plain string buses stay quoted (`"raw_bus"`), as in case 1.
- The parser resolves each path against `program.schema` (the one and only schema attached to the program) to produce a `BusRef` with full metadata (`kind`, `element`, `index`, `channel`, `acquires`). Validation (`_validate_waveform_channel`, `_validate_acquires`) works post-load on these refs.

## 3a.3 Round-trip notes

- Whether the writer is given a preset (`BusSchema.transmon()`) or a dynamic builder (`BusSchema().add_element(...)`), the same inline form is emitted.
- On load, the parser rebuilds a dynamic `BusSchema` via `add_element()` calls and attaches it as `program.schema`. The schema instance is dynamic, not the original Python preset/subclass.
- Plain string buses → no declaration; remain plain strings on reload.
- Mixed: a single program can mix schema-backed bus paths with plain-string buses freely.

The parser rejects:

- a second `schema:` declaration in the same file,
- a `schema:` line with trailing content (the inline form is the only form),
- inline schemas with no element declarations,
- malformed `info=` values (no channel, multiple channels, unknown token, duplicate flag),
- bus paths whose element/kind don't exist on the program's schema,
- bus paths with non-integer index components.

---

# 4. Body Section

The body contains variable declarations and the operation/control flow tree.

## 4.1 Variable Declarations

Each variable is declared with a mandatory **`id`** (which doubles as the identifier used everywhere else in the body) and up to three optional metadata attributes — `label`, `units`, `description`. The form is:

```
var <id> [label="..."] [units="..."] [description="..."]
```

- `<id>` must match `[A-Za-z_][A-Za-z0-9_]*` (Python identifier rules — letters, digits, underscores only; cannot start with a digit; no spaces or punctuation). Ids are used **verbatim** as the identifier in references such as `for <id> in range(...)`, so no transformation or quoting happens at write time.
- `<id>` must be unique among `var` declarations in a single file.
- `<id>` must not be one of the reserved keywords listed in the DSL spec's "Reserved keywords" section (`if`, `while`, `where`, `repeat`, `true`, `false`, …). The full set is exposed at runtime as `qprogram.RESERVED_KEYWORDS`; reservations apply to identifier-shaped names that future minor versions are likely to introduce as block keywords or literals.
- Optional attributes (`label`, `units`, `description`) carry human-readable metadata for plotting, results coordinates, and documentation. They appear as quoted `key="value"` pairs in any order, separated by whitespace, on the same line as the `var` keyword. Each attribute may appear at most once.
- Backslashes and double quotes inside attribute values are escaped (`\\`, `\"`).

**Examples**

```
body:
  var freq                                                          # bare id, no metadata
  var amp label="Amplitude"
  var dur units="ns"
  var t   label="Idle time" units="ns"
  var phi label="Phase offset" units="rad" description="NCO phase for echo arm"
```

The parser rejects:

- ids that contain spaces or non-identifier characters (`var Wait Duration (ns)`),
- ids that start with a digit (`var 1freq`),
- ids that match a reserved keyword (`var if`, `var while`, `var where`, …),
- duplicate ids in the same file,
- unquoted attribute values (`label=foo`),
- unknown attributes (`foo="bar"`),
- duplicate attributes on a single `var` line.

Each line ends after the last `key="value"` pair; tokens past those (without `=`) are reported as `unexpected token … in `var` declaration`.

**Why ids must be identifiers.** Ids are referenced unquoted inside loops, expressions, and `get_parameter -> <id>`. Restricting them to identifier syntax keeps the grammar regular and the parser simple. The optional `label` covers the cases that need a free-form string (axis names, plot titles, anything with spaces).

**Variables in the Python API.** Variables compare equal by id (structural). `program.variable(id, ...)` enforces id uniqueness within a single program, so a saved `.qp` file always has unique `<id>`s and no disambiguation suffixes are emitted. Two programs loaded independently from the same file produce structurally-equal ASTs even though their Python `Variable` instances are distinct objects.

## 4.2 Operations

One operation per line. Bus names and string references are quoted. Variable references are unquoted.

```
play "drive_q0" "pi_pulse"
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
measure "readout_q0" "readout" "default" name="m0"
measure "readout_q0" "readout" "default" name="m1" returns="iq,state"
wait "drive_q0" 100
wait "drive_q0" duration
sync
sync "drive_q0" "readout_q0"
set_frequency "drive_q0" 5e9
set_frequency "drive_q0" freq
set_phase "drive_q0" 1.5708
reset_phase "drive_q0"
set_gain "drive_q0" 0.5
set_gain "drive_q0" gain
set_offset "flux_q0" 0.1
set_offset "flux_q0" 0.1 offset_path1=0.2
set_parameter "cluster" "lo_frequency" 5e9
get_parameter "cluster" "lo_frequency" -> lo_freq
set_crosstalk matrix={"flux_q0": {"flux_q0": 1.0, "flux_q1": 0.03}} offsets={"flux_q0": 0.1}
qblox.set_trigger "drive_q0" 100 outputs=[1, 2] position="start"
qblox.wait_trigger "drive_q0" 1000 port=1
```

**Key syntax rules:**

- Waveform aliases (to be resolved externally via `with_waveforms()`) are in quotes: `"pi_pulse"`
- Variable references are unquoted: `freq`, `gain`
- Inline waveform constructors can appear directly: `Gaussian(amplitude=gain, duration=40, sigma=8)`
- `get_parameter` uses `->` to assign to a variable
- **Measurements carry their name as a `name="..."` kwarg.** The writer always emits it;
  hand-written files may omit it, in which case the parser auto-allocates (`m0`, `m1`, ...)
  with the same convention the Python builder uses. (Older files with the name as a bare 4th
  positional token still load.)
- **Sequence values** are bracket literals: `outputs=[1, 2]`. **Dict values** (string keys) are
  brace literals: `matrix={"a": {"b": 1.0}}`. `null` is the literal for Python `None`. The
  tokenizer treats `(...)`, `[...]`, and `{...}` as nesting, so spaces inside them are safe.
- `set_crosstalk` serialises its full matrix through three optional dict-literal sections —
  `matrix=`, `offsets=`, `resistances=` (each omitted when empty; an entirely empty matrix is
  the bare keyword).

**Strictness:** unknown operation keywords (core or vendor-dotted), unknown block keywords, and
unknown top-level sections are hard `ParseError`s — a file never loads with content silently
missing. Excess positional tokens on an operation line are also errors. On the write side, an
unregistered operation/block class or an attribute value the format cannot represent raises
`SerializationError` instead of emitting lossy output.

## 4.3 Inline Waveform Constructors

Concrete waveforms can appear directly in operations using constructor syntax:

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
play "drive_q0" IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
play "flux_q0" FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
measure "readout_q0" IQPair(Square(1.0, 2000), Square(0.0, 2000)) IQPair(Square(1.0, 2000), Square(1.0, 2000)) name="m0"
```

Parameters can be positional or named. Variable references are allowed as parameters (for sweeps).

## 4.4 Variable Expressions

Arithmetic expressions are **always parenthesised** — the canonical form the writer emits and
the only form the parser accepts. An unparenthesised `100 - t` would tokenize as three separate
arguments and is rejected with a "too many arguments … parenthesise it" error rather than
silently dropping the trailing tokens:

```
wait "drive_q0" (100 - t)
wait "drive_q0" (t + 200)
set_frequency "drive_q0" ((freq * 2) + 1000000.0)
```

Math functions and `where` use the function-call shape: `sin(x)`, `minimum(x, 0.5)`,
`where((x < 5), x, 0)`.

## 4.5 Control Flow Blocks

Blocks use a keyword followed by parameters, then a colon, with indented children.

**for_loop:**

```
for freq in range(4e9, 6e9, 1e6):
  set_frequency "drive_q0" freq
  play "drive_q0" pi_pulse
  measure "readout_q0" "readout" "default"
```

**loop** (arbitrary values):

```
for amp in [0.0, 0.1, 0.3, 0.5, 0.7, 1.0]:
  set_gain "drive_q0" amp
  play "drive_q0" pi_pulse
```

Or with external array reference:

```
for amp in file("sweep_values.npy"):
  set_gain "drive_q0" amp
```

**Parallel loops** (via `|`) — all composed loops must have the same number of iterations
(mismatches are rejected at parse time):

```
for freq in range(4e9, 6e9, 1e6) | for gain in range(0.0, 2.0, 0.001):
  set_frequency "drive_q0" freq
  set_gain "drive_q0" gain
  play "drive_q0" pi_pulse
```

Mixing loop types:

```
for freq in range(5e9, 5.4e9, 1e8) | for amp in [0.1, 0.3, 0.5, 0.7, 0.9]:
  set_frequency "drive_q0" freq
  play "drive_q0" Gaussian(amplitude=amp, duration=40, sigma=8)
```

**average:**

```
average 1000:
  play "drive_q0" pi_pulse
  measure "readout_q0" "readout" "default"
```

**block** (generic scope):

```
block:
  play "drive_q0" pi_pulse
  wait "drive_q0" 100
```

## 4.6 Nesting

```
average 1000:
  for freq in range(4e9, 6e9, 1e6):
    for gain in range(0.0, 1.0, 0.01):
      set_frequency "drive_q0" freq
      set_gain "drive_q0" gain
      play "drive_q0" pi_pulse
      sync
      measure "readout_q0" "readout" "default"
```

## 4.7 Fragment Calls

A fragment defined in a `fragment` section (Section 2.5) is instantiated by a bare call statement — the name followed immediately by a parenthesised argument list:

```
body:
  var g
  average 1000:
    for g in range(0, 1, 0.01):
      rabi_point(q[0].drive, q[0].readout, g)
      rabi_point("drive_q1", "readout_q1", amp=(g * 0.5))
```

- Arguments follow the **Python calling convention**: positional in parameter order, then `key=value` keywords. Duplicate bindings, unknown keywords, missing or excess arguments, and positionals after a keyword are all errors.
- Argument tokens use the same shapes as operation arguments: numbers, quoted strings, bare bus paths (promoted to `BusRef`s against the schema; quoted path-lookalikes stay strings), identifiers (variables — or enclosing-fragment parameters when the call appears inside another fragment), parenthesised expressions, and inline waveform constructors.
- The statement shape `name(args)` is unambiguous: operations separate their arguments with spaces, and block headers end with `:`.
- On the Python side a call appears as a first-class `Call` node; `program.expand()` replaces it with the substituted fragment body (DSL spec §6.4).

---

# 5. Complete Example

A program using string aliases (waveform values provided externally at execution time):

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

---

# 6. Another Example: Two-Qubit CZ Chevron

Uses inline waveforms with variable parameters and string aliases for calibration-dependent pulses:

```
#!QProgram 1.0

metadata:
  label: "cz_chevron"
  description: "CZ chevron pattern - sweep flux amplitude and duration"

body:
  var amp
  var dur

  average 1000:
    for amp in range(0.0, 1.0, 0.01) | for dur in range(10, 200, 2):
      # Prepare q1 in |1>
      play "drive_q1" "pi"
      sync

      # Apply flux pulse (inline waveform with variable parameters)
      play "flux_q0" FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
      sync

      # Readout both qubits
      measure "readout_q0" "readout" "default" name="m0"
      measure "readout_q1" "readout" "default" name="m1"
```

---

# 7. Grammar Summary

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
expression     := "(" expr_body ")"
expr_body      := value (BIN_OP value)
               | ("-" | "+") value                  # unary, no space
               | "not" value
               | value CMP_OP value
               | value ("and" | "or") value
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

value          := STRING | NUMBER | BOOL | "null" | IDENT | waveform_expr | expression
               | list_literal | dict_literal | measurement_ref
list_literal   := "[" (value ("," value)*)? "]"
dict_literal   := "{" (STRING ":" value ("," STRING ":" value)*)? "}"
measurement_ref:= HANDLE_NAME "." FIELD            # e.g. m0.state (unquoted, token-safe name)
```

---

# 8. Parser / Serializer API

The QProgram library provides built-in parser and serializer:

```python
import qprogram as qp

# Save QProgram to .qp file
qp.save(program, "experiment.qp")

# Load .qp file into QProgram
program = qp.load("experiment.qp")

# Serialize to string (for embedding, transmission, etc.)
text = qp.dumps(program)

# Parse from string
program = qp.loads(text)
```

The parser is implemented in pure Python with no external dependencies (no `ruamel.yaml`, no `pyyaml`, no `lark`). It is a simple recursive-descent parser following the grammar in Section 7.

While parsing the `body:` section, the loader records a **source map** on the returned program — `program.source_map` maps each node's structural path (DSL spec §9.8) to the 1-based line that produced it, so a `Diagnostic.path` computed against a structurally-equal program locates the offending `.qp` line. Fragment-internal statements are not mapped.

---

# 9. Extensibility

## 9.1 Custom Waveform Types

User-defined waveform types can be registered with the parser:

```python
@qp.register_waveform
class MyCustomPulse(Waveform):
    def __init__(self, amplitude: float, duration: int, custom_param: float): ...
    def envelope(self, resolution=1) -> np.ndarray: ...
    def get_duration(self) -> int: ...
```

Once registered, it can appear inline in `.qp` operations:

```
play "drive_q0" MyCustomPulse(amplitude=0.5, duration=100, custom_param=3.14)
```

## 9.2 Custom Operations and Vendor Extensions

Vendor-specific operations are defined as `Operation` subclasses in the vendor library (e.g. QiliLab) and registered with a `VendorNamespace`. Each registered Operation class maps to a `(vendor, operation_name)` pair used for serialization. See Section 5.4 of the DSL Specification for the full Python internals.

In `.qp` files, vendor operations use **dot notation**:

```
#!QProgram 1.0

require qblox 0.1
require quantum_machines 0.1
require qdac 0.1

body:
  var freq

  # Core operations
  play "drive_q0" pi_pulse
  measure "readout_q0" "readout" "default"

  # Vendor-specific operations
  qblox.acquire "readout_q0" "default"
  qblox.set_markers "drive_q0" "0001"
  qblox.active_reset "readout_q0" "readout" "default" "drive_q0" "reset" trigger_address=1
  quantum_machines.measure "readout_q0" "readout" "default" rotation=0.5 demodulation=true
  qdac.play "flux_q0" flux_pulse dwell=100 repetitions=5
```

**Serialization:** The `.qp` serializer looks up each Operation instance in the registry to find its `(vendor, name)` pair. `Acquire` registered under `("qblox", "acquire")` serializes to `qblox.acquire ...`.

**Deserialization:** The parser looks up `("qblox", "acquire")` in the registry to find the `Acquire` class and reconstruct it with the correct typed attributes.

**Versioned `require`:** Every `require` declaration carries a `<major>.<minor>` version (see Section 2.2). The parser validates compatibility against the installed extension before parsing the body, so unrecognised or incompatible vendor operations are caught upfront with actionable error messages — a `.qp` file is a complete, executable contract.

**Discovery:** A vendor package declares a `qprogram.vendors` entry point so the loader can import it on demand when a `require` line names it (see Section 2.2, *Auto-activation*):

```toml
# in the vendor package's pyproject.toml
[project.entry-points."qprogram.vendors"]
qblox = "qprogram_qblox"   # entry-point name = vendor namespace; value = self-registering module
```

## 9.3 Versioning

The header version (`#!QProgram 1.0`) determines the parser behavior. New versions may add:

- New operations
- New waveform types
- New control flow constructs
- New sections

Older parsers must reject files with a higher major version. Minor version increments are backward-compatible.

---

# 10. Open Questions

- [x] **Encoding**: **Resolved** — UTF-8 only; `save()`/`load()` pin the encoding explicitly.
- [ ] **Max line length**: Should there be a recommended max line length for readability?
- [ ] **Multi-line constructors**: Should waveform constructors with many parameters support line continuation?
- [ ] **Import mechanism**: Should `.qp` files be able to import waveform definitions from other `.qp` files?
