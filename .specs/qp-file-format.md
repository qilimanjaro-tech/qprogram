# .qp File Format Specification (Draft)

> **Source:** https://www.notion.so/qilimanjaro/qp-File-Format-Specification-Draft-3307eec14c5381948e39e397b2062803
> **Fetched:** 2026-05-07
> **Status:** Draft (specification — code may not yet match)

---

# 1. Overview

The `.qp` format is the serialization format for QProgram. It is a **text-based**, **human-readable** language that maps 1:1 to the QProgram Python API. Any QProgram constructed in Python can be saved to `.qp` and loaded back without loss.

Design goals:

- **Human-readable** — looks like a clean, minimal programming language
- **Self-contained** — waveform data embedded inline or referenced by file path
- **Versionable** — format version declared in header
- **Extensible** — custom waveform types and operations can be registered
- **Round-trip safe** — `load(save(program)) == program`

---

# 2. File Structure

A `.qp` file has up to three kinds of optional declarations plus a required body, in order. `metadata` is optional; any number of `schema` declarations may appear (zero or more); `body` is required.

```
#!QProgram 1.0

require <vendor> <major.minor>   # optional, one per vendor dependency

metadata:
  ...

schema: transmon                 # zero or more schema declarations (Section 3a)
schema chip:
  element q:
    drive info=IQ
  ...

body:
  ...
```

Operations can use concrete inline waveforms directly (e.g. `Gaussian(amplitude=0.5, duration=40, num_sigmas=2.5)`), or string aliases (e.g. `"pi_pulse"`) that are resolved externally via `with_waveforms()` at execution time.

## 2.1 Header

The first line must be the version header:

```
#!QProgram 1.1
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

## 2.3 Comments

Line comments start with `#` (except the header line):

```
# This is a comment
play "drive_q0" pi_pulse  # inline comment
```

## 2.4 Indentation

Indentation is **2 spaces** per level. Tabs are not allowed. Indentation defines block structure (like Python).

---

# 3. Metadata Section

```
metadata:
  label: "rabi"
  description: "Rabi oscillation experiment"
```

All fields are optional. Values are strings (quoted), numbers, or booleans (`true`/`false`).

---

# 3a. Schema Declarations

Buses fall into three categories, each with its own serialization:

1. **Plain string** — `play "drive_q0_bus" pulse`. No schema section. The bus is just a string, no metadata.
2. **Preset schema** (one of the built-in `BusSchema` subclasses — `transmon`, `transmon_coupled`, `flux_tunable_transmon`, `flux_tunable_transmon_coupled`, `fluxonium`, `fluxonium_coupled`). One-liner declaration; the parser instantiates the preset class.
3. **Custom / dynamic schema** (`BusSchema(name=...)` populated via `add_element`, or a user subclass of `BusSchema`). Full inline declaration so the parser can rebuild the structure without the user's Python class.

Schema declarations are independent top-level items, written **before** `body:` and **after** any `require` and `metadata:` sections. A program may declare any number of schemas; each must have a unique name.

## 3a.1 Preset (one-liner) form

```
schema: transmon                                    # name = "transmon", default naming
schema: transmon("{kind}_{element}{index}_bus")     # default name, custom naming pattern
schema A: transmon                                  # aliased preset (useful with multiple schemas)
schema A: transmon("{kind}_{element}{index}_bus")   # aliased + custom naming
```

- `schema: <kind>` — bare form, equivalent to `schema <kind>: <kind>`.
- `schema <name>: <kind>` — gives the schema an explicit `<name>`. The body uses this name in bus paths.
- Optional `("<pattern>")` after the kind sets the `BusNaming` pattern. The pattern uses `{element}`, `{index}`, `{kind}` placeholders.
- `<kind>` must be a built-in preset (else the parser errors and points at the inline form).

## 3a.2 Custom / dynamic (inline) form

```
schema chip:
  naming: "{element}{index}/{kind}"                # optional; default if omitted
  element q:
    drive   info=IQ
    readout info=IQ+acquires
    flux    info=single
  element c:
    flux    info=single
```

- `schema <name>:` with no kind on the same line opens an inline body.
- `<name>` is mandatory and unique within the file.
- Optional `naming: "<pattern>"` line; the default is `"{element}{index}/{kind}"`.
- One or more `element <elem>:` blocks. Each lists bus kinds as `<kind> info=<channel>[+acquires]` lines.
- `info=` channel is required (`single` or `IQ`); the only currently defined flag is `acquires` (bus has an ADC). Examples: `info=single`, `info=IQ`, `info=IQ+acquires`.
- Indentation: 2 spaces for `naming:` / `element` headers, 4 spaces for bus-kind lines.

User-typed `BusSchema` subclasses (e.g. `MyChipSchema` with `KIND = "my_chip"` and `@property` factories) serialize via the inline form — the Python class doesn't survive round-trip, but the element/bus structure does, so the loaded program is structurally complete.

## 3a.3 Bus references in operations

Buses backed by a schema reference are written as paths in operation arguments:

```
play   transmon.q[0].drive    pulse
measure transmon.q[0].readout "r" "w"
set_offset chip.c[0,1].flux   0.5
sync   transmon.q[0].readout  transmon.q[1].readout
```

- Form: `<schema_name>.<element>[<index>].<kind>`.
- `<index>` is an integer (`q[0]`) or a comma-separated tuple of integers (`c[0,1]`). No spaces inside brackets.
- Plain string buses stay quoted (`"raw_bus"`), as in case 1.
- The parser resolves each path against the parsed schemas to produce a `BusRef` with full metadata (`kind`, `element`, `index`, `channel`, `acquires`, `schema`). Validation (`_validate_waveform_channel`, `_validate_acquires`) works post-load on these refs.

## 3a.4 Round-trip notes

- Built-in preset → one-liner. Loader instantiates the same preset class.
- Custom/dynamic schema → inline. Loader rebuilds a `BusSchema` via `add_element()` calls. The schema instance is dynamic, not the original user subclass.
- Plain string buses → no declaration; remain plain strings on reload.
- Mixed: a single program can use any combination — multiple presets (each with a unique alias), plus a custom schema, plus plain strings.
- `BusRef` instances reconstructed by the loader carry the same `schema` link, so re-serialization is byte-stable.

The parser rejects:

- unknown preset `<kind>` with no inline body,
- inline schemas with no element declarations,
- duplicate schema names,
- malformed `info=` values (no channel, multiple channels, unknown token, duplicate flag),
- bus paths whose schema name is declared but whose element/kind don't exist on it,
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
- duplicate ids in the same file,
- unquoted attribute values (`label=foo`),
- unknown attributes (`foo="bar"`),
- duplicate attributes on a single `var` line.

Each line ends after the last `key="value"` pair; tokens past those (without `=`) are reported as `unexpected token … in `var` declaration`.

**Why ids must be identifiers.** Ids are referenced unquoted inside loops, expressions, and `get_parameter -> <id>`. Restricting them to identifier syntax keeps the grammar regular and the parser simple. The optional `label` covers the cases that need a free-form string (axis names, plot titles, anything with spaces).

**Identity-based variables in the Python API.** Variables in Python use identity-based equality, but `program.variable(id, ...)` enforces id uniqueness within a program, so a saved `.qp` file always has unique `<id>`s and no disambiguation suffixes are emitted.

## 4.2 Operations

One operation per line. Bus names and string references are quoted. Variable references are unquoted.

```
play "drive_q0" pi_pulse
play "drive_q0" Gaussian(amplitude=0.5, duration=40, num_sigmas=2.5)
measure "readout_q0" "readout" "default"
measure "readout_q0" readout_pulse readout_weights
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
set_offset "flux_q0" 0.1 0.2
set_parameter "cluster" "lo_frequency" 5e9
get_parameter "cluster" "lo_frequency" -> lo_freq
set_crosstalk crosstalk
set_trigger "drive_q0" 100 outputs=1 position="start"
wait_trigger "drive_q0" 1000 port=1
```

**Key syntax rules:**

- Waveform aliases (to be resolved externally via `with_waveforms()`) are in quotes: `"pi_pulse"`
- Variable references are unquoted: `freq`, `gain`
- Inline waveform constructors can appear directly: `Gaussian(amplitude=gain, duration=40, num_sigmas=2.5)`
- `get_parameter` uses `->` to assign to a variable

## 4.3 Inline Waveform Constructors

Concrete waveforms can appear directly in operations using constructor syntax:

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, num_sigmas=2.5)
play "drive_q0" IQDrag(amplitude=0.5, duration=40, num_sigmas=2.5, drag_coefficient=0.1)
play "flux_q0" FlatTop(amplitude=amp, duration=dur, smooth_duration=5)
measure "readout_q0" IQPair(Square(1.0, 2000), Square(0.0, 2000)) IQPair(Square(1.0, 2000), Square(1.0, 2000))
```

Parameters can be positional or named. Variable references are allowed as parameters (for sweeps).

## 4.4 Variable Expressions

Arithmetic expressions are written inline:

```
wait "drive_q0" 100 - t
wait "drive_q0" t + 200
```

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

**Parallel loops** (via `|`):

```
for freq in range(4e9, 6e9, 1e6) | for gain in range(0.0, 1.0, 0.01):
  set_frequency "drive_q0" freq
  set_gain "drive_q0" gain
  play "drive_q0" pi_pulse
```

Mixing loop types:

```
for freq in range(4e9, 6e9, 1e6) | for amp in [0.1, 0.3, 0.5, 0.7, 0.9]:
  set_frequency "drive_q0" freq
  play "drive_q0" Gaussian(amplitude=amp, duration=40, num_sigmas=2.5)
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
      measure "readout_q0" "readout" "weights"
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
      measure "readout_q0" "readout" "default"
      measure "readout_q1" "readout" "default"
```

---

# 7. Grammar Summary

```
file           := header require* section*
header         := "#!QProgram" VERSION
require        := "require" IDENT VERSION
section        := metadata_sec | schema_sec | body_sec

metadata_sec   := "metadata:" NEWLINE INDENT kv_pair+
kv_pair        := IDENT ":" value

schema_decl    := preset_schema | inline_schema
preset_schema  := "schema" (IDENT)? ":" KIND ("(" STRING ")")? NEWLINE
inline_schema  := "schema" IDENT ":" NEWLINE INDENT
                    ("naming:" STRING NEWLINE)?
                    element_block+
element_block  := "element" IDENT ":" NEWLINE INDENT bus_line+
bus_line       := IDENT "info=" INFO NEWLINE
INFO           := CHANNEL ("+" INFO_FLAG)*
CHANNEL        := "single" | "IQ"
INFO_FLAG      := "acquires"
KIND           := "transmon" | "transmon_coupled"
                | "flux_tunable_transmon" | "flux_tunable_transmon_coupled"
                | "fluxonium" | "fluxonium_coupled"
bus_ref        := STRING | bus_path
bus_path       := SCHEMA_NAME "." ELEMENT "[" INDEX "]" "." KIND_NAME
SCHEMA_NAME    := IDENT
ELEMENT        := IDENT
KIND_NAME      := IDENT
INDEX          := NUMBER | NUMBER ("," NUMBER)+

body_sec       := "body:" NEWLINE INDENT statement+
statement      := var_decl | operation | control_block
var_decl       := "var" ID var_attr*
var_attr       := ("label" | "units" | "description") "=" STRING
ID             := [A-Za-z_][A-Za-z0-9_]*
operation      := (VENDOR ".")? OP_NAME arg*
arg            := STRING | NUMBER | IDENT | waveform_expr | expression
expression     := (IDENT | NUMBER) ("+" | "-") (IDENT | NUMBER)

control_block  := for_block | average_block | block_block | parallel_block
for_block      := "for" IDENT "in" (range_expr | array_expr) ":" NEWLINE INDENT statement+
parallel_block := for_block ("|" for_block)+ ":" NEWLINE INDENT statement+
average_block  := "average" NUMBER ":" NEWLINE INDENT statement+
block_block    := "block:" NEWLINE INDENT statement+

range_expr     := "range(" NUMBER "," NUMBER "," NUMBER ")"
array_expr     := "[" NUMBER ("," NUMBER)* "]" | "file(" STRING ")"
waveform_expr  := WAVEFORM_TYPE "(" (arg_list)? ")"
arg_list       := (IDENT "=")? value ("," (IDENT "=")? value)*

value          := STRING | NUMBER | BOOL | IDENT | waveform_expr | array_literal
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
  qblox.measure_reset "readout_q0" "readout" "default" "drive_q0" "reset" trigger_address=1
  quantum_machines.measure "readout_q0" "readout" "default" rotation=0.5 demodulation=true
  qdac.play "flux_q0" flux_pulse dwell=100 repetitions=5
```

**Serialization:** The `.qp` serializer looks up each Operation instance in the registry to find its `(vendor, name)` pair. `Acquire` registered under `("qblox", "acquire")` serializes to `qblox.acquire ...`.

**Deserialization:** The parser looks up `("qblox", "acquire")` in the registry to find the `Acquire` class and reconstruct it with the correct typed attributes.

**Versioned `require`:** Every `require` declaration carries a `<major>.<minor>` version (see Section 2.2). The parser validates compatibility against the installed extension before parsing the body, so unrecognised or incompatible vendor operations are caught upfront with actionable error messages — a `.qp` file is a complete, executable contract.

## 9.3 Versioning

The header version (`#!QProgram 1.0`) determines the parser behavior. New versions may add:

- New operations
- New waveform types
- New control flow constructs
- New sections

Older parsers must reject files with a higher major version. Minor version increments are backward-compatible.

---

# 10. Open Questions

- [ ] **Encoding**: UTF-8 only? Or support other encodings?
- [ ] **Max line length**: Should there be a recommended max line length for readability?
- [ ] **Multi-line constructors**: Should waveform constructors with many parameters support line continuation?
- [ ] **Import mechanism**: Should `.qp` files be able to import waveform definitions from other `.qp` files?
