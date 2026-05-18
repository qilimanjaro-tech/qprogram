# Errors

QProgram has a single-rooted exception hierarchy. Every error raised by the
library (or by a platform that adopts the contract) is a subclass of
`QProgramError`. Catch the level of granularity you need.

## Catching shorthand

| What you write                        | What you catch                                          |
|---------------------------------------|---------------------------------------------------------|
| `except QProgramError`                | Anything QProgram-related.                              |
| `except ValidationError`              | Construction-time validation only.                      |
| `except ParseError`                   | `.qp` parse failures only.                              |
| `except WaveformResolutionError`      | A specific platform-side failure.                       |

For two construction-time exceptions, `except ValueError` keeps working too
(see below).

## The hierarchy

```
QProgramError
  ValidationError
    InvalidVariableIdError       (also ValueError)
    UnassignedVariableError      (also ValueError)
  ParseError
  UnsupportedOperationError
  BusNotAvailableError
  WaveformResolutionError
  CompilationError
  HardwareError
```

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

```python
try:
    program.play(q[0].drive, Square(0.5, 100))
except qp.ValidationError as e:
    print(e)
```

The base `ValidationError` does not extend `ValueError`. Generic
`except ValueError` is no longer a catch-all for QProgram's construction
validation; use `ValidationError` (or `QProgramError`).

### `InvalidVariableIdError`

A `Variable.id` failed the identifier rules. Two flavours share the class:

- **Pattern failure**: the id does not match `[A-Za-z_][A-Za-z0-9_]*`.
- **Reserved keyword**: the id matches the pattern but is one of
  [the reserved keywords](reserved.md).

`InvalidVariableIdError.reserved` distinguishes the two:

```python
try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    print(e.id, e.reserved)        # "if", True
```

The class also subclasses `ValueError` for back-compat with code that
predates the QProgram hierarchy.

### `UnassignedVariableError`

`Expression.evaluate_or_raise()` was called while at least one variable in
the expression was unbound. The error carries the expression and the set of
free variables for debugging:

```python
expr = freq * 2 + 100

try:
    expr.evaluate_or_raise()
except qp.UnassignedVariableError as e:
    e.expression          # the offending expression
    e.free_variables      # {freq}
```

Like `InvalidVariableIdError`, this class subclasses `ValueError` too.

## Parse-time errors

`ParseError` is what `qp.load` / `qp.loads` raise when a `.qp` file does not
follow the grammar or fails a compatibility check. Common cases:

- The header `#!QProgram <version>` is missing or has the wrong major.
- A `require` declaration cannot be satisfied (no matching extension; major
  mismatch; installed minor too old; malformed version).
- A variable id is reserved or duplicates another `var`.
- A schema declaration has no elements or a malformed `info=` value.
- A bus path references an unknown element or kind.
- An operation has the wrong number of arguments.

Error messages name the file path (if known), the line number, and a short
explanation.

## Platform-side errors

These are defined in `qprogram` but **never raised by it**. Platforms
(QiliLab, qblox-platform, ...) use them to report errors back to users so
the catch surface is uniform across backends.

### `UnsupportedOperationError`

The platform cannot lower an operation to its hardware. Examples: a vendor
operation this backend does not implement, a control-flow construct the
compiler does not support.

### `BusNotAvailableError`

The program references a bus name the backend does not expose. The program
is structurally well-formed; it just does not fit this particular platform.

### `WaveformResolutionError`

A string waveform alias was still unresolved when execution started.
Typically a missing entry in `with_waveforms` (or in whatever calibration
system feeds it).

### `CompilationError`

A backend-internal failure produced an invalid lowered representation:
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
| `.qp` file missing a `require` declaration                  | `ParseError`                          |
| `require qblox 99.0` against an older install                | `ParseError`                          |
| Platform missing the bus you wrote                          | `BusNotAvailableError` (platform side)|
| Forgot to call `with_waveforms` and the platform ran        | `WaveformResolutionError` (platform side)|
| Anything else from a platform you do not recognise          | `QProgramError`                        |

## Why two parents on some classes

`InvalidVariableIdError` and `UnassignedVariableError` originated in
`qprogram.variable` and predate the exception hierarchy. They keep
`ValueError` as a second parent so existing `except ValueError` code keeps
working for those specific cases. New code should prefer the
`QProgramError` family.

## `Diagnostic` is not an exception

The validator surface lives next door, but it is **not** part of this
hierarchy. `qp.validate(program, caps)` returns a `list[Diagnostic]`
rather than raising — the list comes back, the caller decides what to do.
A `Diagnostic` is a frozen dataclass with `severity`, `code`, `message`,
`node`, `capability`, and `limit` fields. Platforms typically translate a
non-empty list into one of the platform-side exceptions above
(`UnsupportedOperationError` is the usual choice) so end users see one
consistent error class regardless of which axis tripped.

See [Capabilities, diagnostics, and profiles](../guide/capabilities.md)
for the validator walkthrough.
