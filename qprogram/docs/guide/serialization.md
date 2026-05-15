# Saving and loading

QProgram ships its own text format. You save with `dumps` / `save` and load
with `loads` / `load`. No JSON, YAML, or pickle is involved; the parser and
writer are pure Python and have no external dependencies.

## The four functions

```python
import qprogram as qp

text     = qp.dumps(program)                  # program -> str
program  = qp.loads(text)                     # str -> program

qp.save(program, "experiment.qp")             # program -> file
program  = qp.load("experiment.qp")           # file -> program
```

`save` and `load` are thin wrappers over `dumps` / `loads`. Everything below
applies equally to both.

## What you get on disk

A trimmed-down Rabi experiment looks like this:

```
#!QProgram 1.0

metadata:
  label: "rabi"

body:
  var gain

  average 1000:
    for gain in range(0.0, 1.0, 0.01):
      set_gain "drive_q0" gain
      play "drive_q0" "pi_pulse"
      sync
      measure "readout_q0" "readout" "weights" name="m0"
```

The format is line-oriented, two-space indented, and human-friendly. See
[the .qp file format reference](../reference/qp-format.md) for the full
grammar.

## Round-trip guarantees

`dumps(loads(text)) == text` for any QProgram constructed via the public API.
`loads(dumps(program))` produces a program structurally equal to the
original:

```python
text1 = qp.dumps(program)
reloaded = qp.loads(text1)
text2 = qp.dumps(reloaded)
assert text1 == text2
assert reloaded.body == program.body
```

The structural equality covers the entire AST: blocks, operations,
expressions, waveforms, bus references, even the contents of `Loop.values`
(stored as numpy arrays).

## Version contract

Every file starts with a header:

```
#!QProgram 1.0
```

The parser rejects unsupported versions before doing anything else. New
minor versions add features in a backward-compatible way; new major versions
may break compatibility.

If a program uses vendor operations, every vendor declares itself once at
the top:

```
#!QProgram 1.0

require qblox 0.1
```

The parser checks:

- The same **major** version as the installed extension is required.
- The installed **minor** must be greater than or equal to the file's minor.

If the check fails or the vendor is not installed at all, the parser raises a
clear `ParseError` before touching the body. This makes a `.qp` file a
complete, executable contract: any environment that parses it without error
recognises every operation it references.

## Schemas serialise inline

If your program was constructed with a `BusSchema`, the schema lands at the
top of the file as a single inline block:

```
schema:
  element q:
    drive   info=IQ
    readout info=IQ+acquires
```

Bus paths in the body then reference the schema:

```
play q[0].drive pi_pulse
measure q[0].readout "readout" "weights" name="q0_m0"
```

The Python class identity does not survive: a program written from
`MyChipSchema()` loads back as a dynamic `BusSchema` with the same elements.
The structural data, the validation rules, and the bus paths in the body all
round-trip exactly.

## Variables in the file

Each `Variable` becomes one `var` declaration:

```
body:
  var freq label="Drive Frequency" units="Hz"
  var gain
  var t units="ns" description="Free-evolution time"
```

The `id` (the unquoted token after `var`) is the identifier used everywhere
else in the body. The other attributes are optional metadata.

## Inline waveforms vs aliases

A program may carry waveforms two ways:

```
play "drive_q0" "pi_pulse"                                  # string alias
play "drive_q0" Gaussian(amplitude=0.5, duration=40, num_sigmas=2.5)   # concrete
```

Both round-trip. Aliases stay as strings on reload, ready to be resolved
later by `with_waveforms`. Inline constructors come back as the same
`Gaussian` / `IQDrag` / `IQPair` instances they started as.

## Custom waveforms

If you registered a custom waveform with `@qp.register_waveform`, it
serialises through the same constructor syntax. The writer walks `vars(wf)`
to figure out which arguments to emit; the parser uses
`inspect.signature(cls.__init__)` to feed them back into the constructor.
See [Adding waveforms](../developer/adding-waveforms.md).

## Vendor operations

Vendor ops use dot notation:

```
require qblox 0.1

body:
  qblox.acquire "readout_q0" "weights" name="q0_m0"
  qblox.set_markers "drive_q0" "0001"
  qblox.active_reset "readout_q0" "readout" "weights" "drive_q0" "pi_pulse" trigger_address=1
```

Vendor operation serialisation is generic. Anything registered with
`register_vendor_operation` gets free round-tripping as long as its
constructor signature is introspectable. New vendor operations need no
parser or writer changes.

## What the writer cleans up

Two things are silently normalised on the way out:

1. **Default values.** Optional keyword arguments at their default value are
   not emitted. `measure(..., returns=("iq",))` saves as `measure ...` with
   no `returns="..."` suffix.
2. **Variable ids.** Within a single program, ids are already unique by
   construction. The writer never invents disambiguation suffixes.

## Where the parser stays strict

Some things will fail on load:

- A `var` line whose id matches a reserved keyword.
- A bus path against an unknown element or kind.
- A `schema:` block with no element declarations.
- A `require` declaration that does not match the installed extension.

Errors come back as `qprogram.ParseError` with a line/column pointer.

## Why a custom format?

The `.qp` format trades binary efficiency for readability and stability.
The decision is documented in the spec (`.specs/qp-file-format.md`); briefly,
the goals were:

- Human-readable, so a `.qp` file can sit in a git repo and review well.
- Self-contained, so a single file pins versions and is fully executable.
- Extensible, so vendor operations and custom waveforms slot in without
  re-shipping the format.
- Round-trip safe.

If you need a wire format optimised for size, write your own based on the
AST. The classes in `qprogram.operations`, `qprogram.blocks`,
`qprogram.waveforms`, and `qprogram.variable` are all stable and structural.
