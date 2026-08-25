# Errors

QProgram has a single-rooted exception hierarchy. Errors raised by the
library, and by a platform that adopts the contract, are subclasses of
`QProgramError`, with one documented gap: a malformed inline waveform
constructor in a `.qp` file escapes as a plain `TypeError`, described under
[Parse-time errors](#parse-time-errors). Catch the level of granularity you
need.

## Catching shorthand

| What you write                        | What you catch                                          |
|---------------------------------------|---------------------------------------------------------|
| `except QProgramError`                | Anything QProgram-related.                              |
| `except ValidationError`              | Construction-time validation only.                      |
| `except ParseError`                   | `.qp` parse failures only.                              |
| `except SerializationError`           | `.qp` write failures only.                              |
| `except VendorActivationError`        | A discovered vendor extension that fails to activate.   |
| `except WaveformResolutionError`      | A specific platform-side failure.                       |

Two of the construction-time exceptions also subclass `ValueError`, so a
generic `except ValueError` catches those two (see below).

## The hierarchy

```
QProgramError
  ValidationError
    InvalidVariableIdError       (also ValueError)
    UnassignedVariableError      (also ValueError)
  ParseError
  SerializationError
  VendorActivationError
  UnsupportedOperationError
  BusNotAvailableError
  WaveformResolutionError
  CompilationError
  HardwareError
```

`VendorActivationError` is raised by `qp.try_activate_vendor(...)` (and
surfaces, wrapped in a `ParseError`, during `loads()`) when a vendor's
`qprogram.vendors` entry point is found but its import fails or registers no
version: the extension is installed but broken. A vendor that is *not
installed* at all is a different case; discovery reports "no matching extension"
instead.

`ParseError` lives at `qprogram.ParseError`; the construction-time and
platform-side classes are exported from `qprogram` directly.

## Construction-time validation

### `ValidationError`

Raised by core QProgram whenever a program is being assembled and one of the
operations rejects its arguments. Examples:

- Playing an IQ waveform on a single-channel bus.
- Calling `measure(...)` on a bus whose `acquires` is `False`.
- Declaring two variables with the same `id` on one program.
- Holding onto a `BusRef` from a different schema and using it on a
  program built with another.
- A name collision on `MeasurementHandle` allocation.
- A `sweep` with a zero step, non-finite bounds, or a step pointing
  away from `stop`; an empty `Values([])` source; `average(shots=0)`.
- Parallel (`|`) loops with mismatched iteration counts.
- An `IQPair` whose I/Q channels have different concrete durations.
- `sync([])` (ambiguous: pass `None` to sync everything).

```python
try:
    program.play(q[0].drive, Square(0.5, 100))
except qp.ValidationError as e:
    print(e)  # Bus 'q0/drive' is an IQ channel but received a single-channel Waveform (Square). ...
```

The base `ValidationError` does not extend `ValueError`, so a generic
`except ValueError` does not catch QProgram's construction validation. Catch
`ValidationError` (or `QProgramError`).

### `SerializationError`

Raised by the `.qp` **writer** (`dumps`/`save`) when the program contains
something the format cannot represent faithfully: an unregistered
operation/block class, a vendor op whose extension never registered its
version, an attribute value with no `.qp` form, or a measurement name that
cannot survive the unquoted `name.field` wire form of a conditional
reference. Arrays are never truncated: `Arbitrary` samples and `Values`
sweeps are written in full.

A clean `dumps` is not by itself a promise that the text parses back. The
writer emits an expression wherever the AST holds one, including inside a
waveform or sweep-source constructor argument, and the parser does not accept
an expression in that position:

```python
import qprogram as qp
from qprogram.waveforms import Gaussian

program = qp.QProgram()
phi = program.variable("phi")
program.play("drive_q0", Gaussian(amplitude=qp.sin(phi), duration=40, sigma=8))

text = qp.dumps(program)
# play "drive_q0" Gaussian(amplitude=sin(phi), duration=40, sigma=8)

qp.loads(text)
# ParseError: Unknown waveform or sweep source type: sin
```

`Gaussian(amplitude=(phi * 2), ...)` fails the same way. Keep constructor
arguments to numbers, quoted strings, and bare variable references, the
shapes [the format documents](qp-format.md#inline-waveform-constructors), and
a file that writes without a `SerializationError` parses back into an
equal program. Nothing in the writer checks this for you.

### `InvalidVariableIdError`

A `Variable.id` fails the identifier rules. Two flavors share the class:

- **Pattern failure**: the id does not match `[A-Za-z_][A-Za-z0-9_]*`.
- **Reserved keyword**: the id matches the pattern but is one of
  [the reserved keywords](reserved.md).

`InvalidVariableIdError.reserved` distinguishes the two:

```python
try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    print((e.id, e.reserved))  # ('if', True)
```

The class also subclasses `ValueError`, so `except ValueError` catches an
invalid identifier as well.

### `UnassignedVariableError`

`Expression.evaluate_or_raise()` runs while at least one variable in the
expression is unbound. The error carries the expression and the set of free
variables for debugging:

```python
freq = program.variable("freq")
expr = freq * 2 + 100

try:
    expr.evaluate_or_raise()
except qp.UnassignedVariableError as e:
    e.expression  # the offending expression
    e.free_variables  # {freq}
```

Like `InvalidVariableIdError`, this class subclasses `ValueError` too.

## Parse-time errors

`ParseError` is what `qp.load` / `qp.loads` raise when a `.qp` file does not
follow the grammar or fails a compatibility check. Common cases:

- The header `#!QProgram <version>` is missing or has the wrong major.
- A `require` declaration cannot be satisfied (no matching extension; major
  mismatch; installed minor too old; malformed version).
- A `var` declaration duplicates an id already declared in the file. (An id
  that is reserved or malformed comes back as `InvalidVariableIdError`
  instead, because the `Variable` constructor rejects it before the parser
  gets a say.)
- A schema declaration has no elements or a malformed `info=` value.
- A bus path references an unknown element or kind.
- An operation, a sweep source, or a fragment call has the wrong number of
  arguments.

Inline waveform constructors are the gap in that last bullet. The parser
hands the arguments it read straight to the waveform class, so a missing or
misspelled constructor argument surfaces as the class's own `TypeError`
rather than a `ParseError`:

```python
qp.loads('#!QProgram 1.0\n\nbody:\n  play "b" Gaussian(amplitude=0.5)\n')
# TypeError: Gaussian.__init__() missing 2 required positional arguments:
# 'duration' and 'sigma'
```

Catch `(qp.ParseError, TypeError)` around `load` / `loads` if you are parsing
files you did not write.

Most messages name the 1-based line number and a short explanation, and carry
that number separately as `ParseError.line_num`. `line_num` is `0`, and the
message carries no `Line N:` prefix, when the failure happens in a helper that
has no view of the parser's cursor. Decoding a constructor name in an argument
position is the case you are most likely to hit; the sibling check on a `for`
header does carry a line, so the two read differently:

```python
qp.loads('#!QProgram 1.0\n\nbody:\n  play "b" Bogus(amplitude=0.5)\n')
# ParseError: Unknown waveform or sweep source type: Bogus
# ... with line_num == 0, even though the offending line is line 4

qp.loads("#!QProgram 1.0\n\nbody:\n  var x\n  for x in Bogus(start=1):\n    sync\n")
# ParseError: Line 5: unknown sweep source 'Bogus'; registered
# sources are ['Concat', 'File', ...]
# ... with line_num == 5
```

So treat `line_num == 0` as "no line attributed", not as "whole-file error".

## Platform-side errors

These five classes give platforms (vendor backends, ...) one hierarchy to
report failures through, so the catch surface is uniform across backends.
Four of them (`BusNotAvailableError`, `WaveformResolutionError`,
`CompilationError`, and `HardwareError`) are defined in `qprogram` and
raised only by platforms. `UnsupportedOperationError` is the exception: core
raises it too.

### `UnsupportedOperationError`

The platform cannot run an operation as written. Core raises it from
`ReferencePlatform.execute()`, the engine behind `qp.simulate`, on any
`severity="error"` diagnostic the validator reports, listing every one in the
message:

```python
qp.simulate(program)
# qprogram.errors.UnsupportedOperationError: program is not executable on the
# reference platform:
# [error] unknown-measurement: Conditional references measurement '...'
```

A hardware backend raises it for the same reason, and for anything it cannot
lower: a vendor operation it does not implement, a control-flow construct its
compiler does not support.

### `BusNotAvailableError`

The program references a bus name the backend does not expose. The program
is structurally well-formed; it just does not fit this particular platform.

### `WaveformResolutionError`

A string waveform alias is unresolved when execution starts. Typically a
missing entry in `with_waveforms` (or in whatever calibration system feeds
it).

### `CompilationError`

A backend-internal failure produces an invalid lowered representation:
timing constraints not satisfied, resource over-allocation, code-generation
bugs, anything that surfaces during compilation but does not fit the other
classes.

### `HardwareError`

Runtime failure at the instrument level: driver errors, SCPI failures, lost
trigger pulses. Anything raised *during* execution rather than at compile or
validate time.

## A quick guide

| Situation                                                  | Likely error                          |
|------------------------------------------------------------|---------------------------------------|
| Wrong waveform type on a schema-backed bus                  | `ValidationError`                     |
| Duplicate variable id                                       | `ValidationError`                     |
| `Variable("if")` or `Variable("1freq")`                     | `InvalidVariableIdError`              |
| `expr.evaluate_or_raise()` with unbound variable             | `UnassignedVariableError`             |
| Vendor operation whose extension is not installed           | `ParseError`                          |
| Bad argument list in an inline `.qp` waveform constructor    | `TypeError` (outside the hierarchy)   |
| An error diagnostic, on `qp.simulate` or a platform `execute` | `UnsupportedOperationError`         |
| `require myvendor 99.0` against an older install             | `ParseError`                          |
| Platform missing the bus you wrote                          | `BusNotAvailableError` (platform side)|
| `with_waveforms` never called and the platform runs         | `WaveformResolutionError` (platform side)|
| Anything else from a platform you do not recognize          | `QProgramError`                        |

## Why two parents on some classes

`InvalidVariableIdError` and `UnassignedVariableError` inherit from both
`ValidationError` and `ValueError`. Each reports a value that is wrong on its
own terms: an identifier that is not a legal identifier, an expression with
no number to compute. That is exactly what `ValueError` means in Python, so
both spellings catch them. Every other class in the hierarchy descends from
`QProgramError` alone.

## `Diagnostic` is not an exception

The validator surface lives next door, but it is **not** part of this
hierarchy. `qp.validate(program, caps)` returns a tuple
`(list[Diagnostic], ExecutionPlan)` rather than raising. The list comes
back, the caller decides what to do. A `Diagnostic` is a frozen dataclass
with `severity` (`"error"`, `"warning"`, or `"info"`), `code`, `message`,
`node`, `path`, `capability`, `limit`, and `domain` fields. Platforms
typically translate any `severity="error"` diagnostic into one of the
platform-side exceptions above (`UnsupportedOperationError` is the usual
choice) so end users see one consistent error class regardless of which axis
tripped. A `severity="warning"` diagnostic means the program runs but in a
degraded way. The `"forced-host"` notice on a block that lost real-time
dispatch is the one core qprogram emits, and it is surfaced without raising.
`severity="info"` diagnostics, such as the `"reorderable-averaging"` hint,
are passed through as advisory output.

See [Capabilities, diagnostics, and profiles](../guide/capabilities.md)
for the validator walkthrough.
