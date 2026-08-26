# Errors

QProgram has a single-rooted exception hierarchy. Everything the library raises
about a program, and everything a platform raises through the contract, is a
subclass of `QProgramError`, so one `except` covers the lot. Catch the level of
granularity you need.

Argument types are the exception, and they raise a plain `TypeError` on purpose,
because a value with no expression or waveform form is a Python type error
rather than a fact about a program. Two families are documented. Expression
construction rejects an operand it cannot represent, so a `bool` where an
`Expression` belongs, or a `Variable` in a context that calls `bool()` on it,
comes back as a `TypeError` whose message names the alternative to write; see
[Comparisons and logical combination](../guide/variables.md#comparisons-and-logical-combination).
A malformed inline waveform constructor in a `.qp` file escapes as the waveform
class's own `TypeError`, described under
[Parse-time errors](#parse-time-errors).

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

Every class here is defined in `src/qprogram/errors.py` except `ParseError`,
which lives in `qprogram.serialization.parser` because it is part of the
parser's own surface. All twelve are re-exported at the top level, so
`qp.ValidationError` and `qp.ParseError` both resolve. `qp.loads`, `qp.load`,
and `qp.ParseError` come through a module-level `__getattr__` on first
attribute access rather than at import time, because the parser imports
`QProgram` and importing it eagerly from `qprogram/__init__.py` would close a
cycle. `qp.errors` reaches the module holding the other eleven;
`qp.errors.ParseError` does not exist.

## Choosing what to catch

| What you write                            | What it catches                                                      |
|-------------------------------------------|----------------------------------------------------------------------|
| `except qp.QProgramError`                 | Anything QProgram-related, core or platform.                         |
| `except qp.ValidationError`               | Construction-time validation, including its two subclasses.          |
| `except qp.ParseError`                    | A `.qp` or `.wfl` document that does not parse.                      |
| `except qp.SerializationError`            | A program or waveform library the writer cannot represent.           |
| `except qp.VendorActivationError`         | A vendor extension that is installed but fails to activate.          |
| `except qp.UnsupportedOperationError`     | An error diagnostic, or an operation a backend cannot lower.         |
| `except (qp.CompilationError, qp.HardwareError)` | Backend compile failures and instrument failures.             |
| `except ValueError`                       | `InvalidVariableIdError` and `UnassignedVariableError`, nothing else. |

## Construction-time validation

### `ValidationError`

Raised while a program is being assembled, whenever an operation, block, or
sweep source rejects its arguments. The checks run in the constructor or the
builder method, not in a later pass, so the traceback points at the line that
built the offending node.

| Module | What it rejects |
|---|---|
| `qprogram.qprogram` | A waveform whose channel count does not match the bus, `measure()` on a bus with `acquires=False`, a duplicate variable id, a `BusRef` from a different `BusSchema`, an empty or duplicate measurement name, a `sweep()` that never picked values, `sync([])`, a `call()` that is a self-call or names a fragment built against another schema, a broken `if_`/`elif_`/`else_` chain, a conditional condition that is not a comparison over a measurement-state ref and int literals, and `rebind(naming=...)` on a program with no schema or a `rebind()` that leaves raw-string buses unported |
| `qprogram.fragments` | A fragment name that is malformed or reserved, a parameter colliding with a local variable, the wrong number of call arguments, an unknown or duplicated keyword, an unsupported argument type, a call cycle, and an expansion result that is not a bus or a waveform |
| `qprogram.sweeps.builtin` | Non-numeric or non-finite bounds, `num < 1`, a zero step, a step pointing away from `stop`, non-positive `Logspace` bounds, and a `Values` or `File` array that is empty or not 1-D |
| `qprogram.sweeps.combinators` | A `Repeat` count below 1, a non-integer `Rotate` offset, `Concat` given a single source or none, and any combinator argument that is a callable rather than a `SweepSource` |
| `qprogram.blocks` | A `Sweep` source that is a callable or not a 1-D sequence, a `Parallel` with fewer than two loops or with mismatched iteration counts, `Average(shots)` below 1, and appending directly to a `Conditional` |
| `qprogram.operations.operation` | A `fields=` that is a bare string, not iterable, empty, or names a field no capability token registers |
| `qprogram.waveforms.iq_pair` | An `IQPair` whose I and Q channels have different concrete durations |
| `qprogram.result` | An empty `MeasurementHandle` name, and `QProgramResult.get(field=None)` |
| `qprogram.waveform_library` | An empty waveform name, and a `WaveformLibrary.set()` whose `element`/`idx`/`kind` combination matches none of the three tiers |

A message names the offending value and the fix rather than the rule that was
broken:

```python
import qprogram as qp

schema = qp.BusSchema.flux_tunable_transmon()
q = schema.q
program = qp.QProgram(schema=schema)

program.play(q[0].drive, qp.waveforms.Square(0.5, 100))
# ValidationError: Bus 'q0/drive' is an IQ channel but received a
# single-channel Waveform (Square). Use an IQWaveform (e.g. IQPair, IQDrag)
# instead.
```

Three more, from a `measure()` on a drive bus, a `|` composition whose loops
run different numbers of iterations, and an unknown measurement field:

```
ValidationError: Bus 'q0/drive' does not support acquisition
(acquires=False). measure() can only be called on buses with an ADC
(e.g. readout buses).

ValidationError: parallel loops must have the same number of iterations to
advance in lockstep; got Sweep('a'): 11, Sweep('b'): 4

ValidationError: unknown measurement field(s) ['bogus']. Known fields:
['iq', 'raw', 'state']. A vendor extension adds its own by registering
`measure.fields.<name>` via qprogram.protocol.register_capability_tokens.
```

The base `ValidationError` does not extend `ValueError`. Construction
validation is common enough in this library that inheriting `ValueError`
would turn a generic `except ValueError` into an accidental catch-all for it.
Catch `qp.ValidationError` or `qp.QProgramError` instead.

### `InvalidVariableIdError`

A `Variable.id` fails the identifier rules. Two flavors share the class: a
pattern failure, where the id does not match `[A-Za-z_][A-Za-z0-9_]*`, and a
reserved keyword, where the id matches the pattern but is one of
[the reserved keywords](reserved.md). The `reserved` attribute distinguishes
them, and `id` carries the offending string:

```python
import qprogram as qp

program = qp.QProgram()
try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    print((e.id, e.reserved))  # ('if', True)
```

Both messages suggest the fix. The reserved one proposes appending `_var` and
points at the optional `label` argument for the human-readable name; the
pattern one spells the regular expression out:

```
Variable id 'if' is reserved for future QProgram syntax (see
qprogram.RESERVED_KEYWORDS). Pick a non-reserved id such as 'if_var', or
carry the original name in the optional `label` argument.

Variable id '1freq' is invalid: must match [A-Za-z_][A-Za-z0-9_]* (letters,
digits, underscores only; cannot start with a digit, no spaces or special
characters). Use the optional `label` for human-readable names.
```

The class also subclasses `ValueError`, so `except ValueError` around
variable construction catches an invalid identifier as well.

### `UnassignedVariableError`

`Expression.evaluate_or_raise()` ran while at least one variable in the
expression was still unbound. The error carries the expression and the set of
free variables:

```python
import qprogram as qp

program = qp.QProgram()
freq = program.variable("freq")
expr = freq * 2 + 100

try:
    expr.evaluate_or_raise()
except qp.UnassignedVariableError as e:
    e.expression  # the offending expression
    e.free_variables  # {freq}
```

The message reads
`Cannot evaluate expression <repr>: unassigned variable(s) <set>`. The same
error comes out of `qp.simulate` when an operation holds an expression that no
enclosing loop binds, since the reference executor evaluates every operand
before it runs the operation. Like `InvalidVariableIdError`, this class
subclasses `ValueError` too.

## Write-time errors

QProgram writes two formats, and both go through the same exception.
`qp.dumps` and `qp.save` write a program as `.qp`;
[`WaveformLibrary`](api-qprogram.md#qprogram.WaveformLibrary) has its own
`dumps` and `save`, which write a calibration library as `.wfl`.

### `SerializationError`

Raised instead of emitting output that is lossy or would not parse back. On
the `.qp` side that covers an operation or block class that was never
registered with the serialization registry, a vendor operation whose
extension never called `register_vendor_version`, an attribute value with no
`.qp` representation (a dict with non-string keys, an array with more than
one dimension), a fragment passed to `dumps` directly instead of the program
that calls it, a fragment call with an unbound parameter, two different
fragments with the same name reachable from one program, and a measurement
name that cannot survive the unquoted `name.field` wire form of a conditional
reference. On the `.wfl` side it covers an entry whose waveform is not
concrete.

Arrays are never truncated: `Arbitrary` samples and `Values` sweeps are
written in full, and the text reparses to an equal program.

```
SerializationError: Cannot serialize operation class 'MyOp': it is not
registered with the .qp serializer. Core ops register in
qprogram.serialization._specs; vendor ops must call
register_vendor_operation(...) at import time.

SerializationError: measurement name 'my readout' is referenced in a
conditional but contains characters that don't survive the unquoted
`<name>.<field>` wire form (whitespace, quotes, dots, brackets, '#', or ',').
Pass a token-safe name= to measure() when you intend to branch on the result.

SerializationError: cannot serialize the waveform stored under name 'pi': a
WaveformLibrary must hold concrete waveforms (no Variables / symbolic
parameters). Underlying error: 'v'
```

A clean `dumps` is not by itself a promise that the text parses back. The
writer emits an expression wherever the AST holds one, including inside a
waveform or sweep-source constructor argument, and the parser does not accept
an expression in that position:

```python
import qprogram as qp

program = qp.QProgram()
phi = program.variable("phi")
pulse = qp.waveforms.Gaussian(amplitude=qp.sin(phi), duration=40, sigma=8)
program.play("drive_q0", pulse)

text = qp.dumps(program)
# play "drive_q0" Gaussian(amplitude=sin(phi), duration=40, sigma=8)

qp.loads(text)
# ParseError: Unknown waveform or sweep source type: sin
```

`Gaussian(amplitude=(phi * 2), ...)` fails in the same place, with an empty
class name in the message: any argument containing a `(` is routed to the
constructor parser, and the class name it reads is the text before the
opening bracket, which here is nothing at all. Keep constructor
arguments to numbers, quoted strings, and bare variable references, the
shapes [the format documents](qp-format.md#inline-waveform-constructors), and
a file that writes without a `SerializationError` parses back into an equal
program. Nothing in the writer checks this for you.

## Parse-time errors

`ParseError` is what `qp.load` and `qp.loads` raise on a `.qp` document that
does not follow the grammar or fails a compatibility check, and what
`WaveformLibrary.load` and `WaveformLibrary.loads` raise on a `.wfl`
document. Compatibility accounts for the first group of `.qp` cases: a
missing `#!QProgram` header, a header whose major version differs from the
parser's, a `require` declaration that cannot be satisfied, and `require`
lines that do not sit directly after the header. The rest are grammar:
a second `schema:` declaration, a schema with no elements or a malformed
`info=` value, a bus path that does not resolve against the schema, a
duplicate `var` id, a fragment defined after `body:` or called before it is
defined, an `elif` or `else` without a matching `if`, an unknown operation or
block keyword, an unknown sweep source, and an argument list that does not fit
the signature.

```
ParseError: Line 1: Missing #!QProgram header
ParseError: Line 1: Unsupported format version 9.0
ParseError: Line 5: duplicate variable id 'x'
ParseError: Line 2: file requires vendor 'nosuchvendor' 1.0 but no matching
extension is registered in this environment — install the package that
declares the 'qprogram.vendors' entry point for 'nosuchvendor', or import the
extension before loading
ParseError: Line 2: file requires myvendor 99.0 (major 99); installed
myvendor is 1.2.0 (major 1) — major versions must match
ParseError: Line 2: file requires myvendor 1.9 or compatible; installed
myvendor is 1.2.0 — minor version too old
```

Majors must match exactly, the file's minor must be no newer than the
installed extension's, and a patch component is read but ignored.

An id declared in a `.qp` file is checked twice, and the two failures come
back differently. A malformed id is rejected by the parser's own pattern
check, so `var 1x` raises a `ParseError` carrying the line number. A
reserved id passes that check and is rejected by the `Variable` constructor
instead, so `var if` raises `InvalidVariableIdError` with no line
information:

```python
import qprogram as qp

qp.loads("#!QProgram 1.0\n\nbody:\n  var 1x\n")
# ParseError: Line 4: variable id '1x' is invalid: must match
# [A-Za-z_][A-Za-z0-9_]* (no spaces or special characters)

qp.loads("#!QProgram 1.0\n\nbody:\n  var if\n")
# InvalidVariableIdError: Variable id 'if' is reserved for future QProgram
# syntax ...
```

Inline constructors are where an argument-list mistake leaves the hierarchy
entirely. The parser hands the arguments it read straight to the waveform
class, so a missing or misspelled constructor argument surfaces as that
class's own `TypeError`:

```python
import qprogram as qp

qp.loads('#!QProgram 1.0\n\nbody:\n  play "b" Gaussian(amplitude=0.5)\n')
# TypeError: Gaussian.__init__() missing 2 required positional arguments:
# 'duration' and 'sigma'
```

A sweep source nested inside a combinator's argument list escapes the same
way, because it reaches the class through the same argument parser. Only the
outermost sweep-source constructor has its `TypeError` wrapped, so
`for x in Range(start=0):` is a `ParseError` carrying the line number while
`for x in Concat(sources=[Range(start=0)]):` is a bare `TypeError` naming the
missing `stop` argument.

Catch `(qp.ParseError, TypeError)` around `load` and `loads` if you are
parsing files you did not write.

Most messages name the 1-based line number and carry it separately as
`ParseError.line_num`, and the string form gains a `Line N: ` prefix when
`line_num` is non-zero. Two raise sites in the parser omit it, both in
helpers that run below the line loop and have no view of the cursor: the
unknown-class check in `_parse_waveform_expr`, and the operand promotion in
`_to_expression`. Everything else does carry a line, including the sweep-source
lookup on a `for` header, which is the near twin of the waveform lookup. So two
almost identical mistakes read differently:

```python
import qprogram as qp

qp.loads('#!QProgram 1.0\n\nbody:\n  play "b" Bogus(amplitude=0.5)\n')
# ParseError: Unknown waveform or sweep source type: Bogus
# ... with line_num == 0, even though the offending line is line 4

qp.loads("#!QProgram 1.0\n\nbody:\n  var x\n  for x in Bogus(start=1):\n    sync\n")
# ParseError: Line 5: unknown sweep source 'Bogus'; registered sources are
# ['Concat', 'File', 'Linspace', 'Logspace', 'Range', 'Repeat', 'Rotate',
# 'Values']
# ... with line_num == 5

qp.loads('#!QProgram 1.0\n\nbody:\n  var x\n  set_phase "b" ("a" + x)\n')
# ParseError: cannot use 'a' (_QuotedStr) as an expression operand
# ... with line_num == 0
```

So treat `line_num == 0` as "no line attributed", not as "whole-file error".

## Vendor extension activation

`VendorActivationError` is raised by `qp.try_activate_vendor(name)` when a
`qprogram.vendors` entry point claims `name` but its import target raises, or
imports without calling `register_vendor_version`. The extension is installed
and broken, which is a different failure from not being installed at all:
`try_activate_vendor` returns `False` in that case and leaves the decision to
the caller.

```
VendorActivationError: vendor extension for 'myvendor' is installed (entry
point 'qprogram_myvendor:activate') but failed to import: ImportError: ...

VendorActivationError: vendor extension for 'myvendor' imported from entry
point 'qprogram_myvendor:activate' but did not register a protocol version;
the package must call register_vendor_version('myvendor', '<x.y.z>') on
import
```

Reading a `.qp` file whose `require` line names a vendor triggers activation
by default, and the parser wraps any `VendorActivationError` in a
`ParseError` carrying the `require` line's number. Passing
`auto_activate=False` to `qp.loads` or `qp.load` turns the discovery off, in
which case an unregistered vendor is a `ParseError` whose hint asks you to
import the extension yourself.

## Platform-side errors

These five classes give platforms one hierarchy to report failures through,
so the catch surface is uniform across backends. Four of them
(`BusNotAvailableError`, `WaveformResolutionError`, `CompilationError`, and
`HardwareError`) are defined in `qprogram` and raised only by platforms; no
core code path raises them. `UnsupportedOperationError` is the exception:
core raises it too.

### `UnsupportedOperationError`

The platform cannot run an operation as written. Core raises it from
`ReferencePlatform.execute()`, the engine behind `qp.simulate`, on any
`severity="error"` diagnostic the validator reports, listing every one in the
message:

```python
import qprogram as qp

square = qp.waveforms.Square(0.5, 100)
pulse = qp.waveforms.IQPair(square, qp.waveforms.Square(0.0, 100))

program = qp.QProgram()
handle = program.measure("readout_q0", pulse, pulse, fields=[qp.MeasurementField.IQ])
with program.if_(handle.state == 0):
    program.sync()

qp.simulate(program)
# UnsupportedOperationError: program is not executable on the reference
# platform:
# [error] missing-classification: Conditional references m0.state, but the
# measurement does not request state classification (add
# MeasurementField.STATE to fields=) (at body[1])
```

Each line is a `Diagnostic` rendered as `[severity] code: message (at path)`;
the ten codes the validator emits are tabulated with their severities and the
condition that produces each under
[Diagnostics](../guide/capabilities.md#diagnostics).

A hardware backend raises it for the same reason, and for anything it cannot
lower: a vendor operation it does not implement, a control-flow construct its
compiler does not support.

### `BusNotAvailableError`

The program references a bus name the backend does not expose. The program is
structurally well-formed; it just does not fit this particular platform. A
bus problem caught while the program is being built is a `ValidationError`
instead.

### `WaveformResolutionError`

A string waveform alias reached execution without a concrete waveform behind
it, usually a name missing from `QProgram.with_waveforms` or from the
`WaveformLibrary` that fed it. The reference platform does not raise this,
because it models measurements only and never reads waveform content:
`qp.simulate` on a program full of unresolved aliases returns a
`QProgramResult` as usual.

### `CompilationError`

A backend-internal failure produced an invalid lowered representation:
timing constraints not satisfied, resource over-allocation, code-generation
bugs, anything that surfaces during compilation but does not fit the other
classes.

### `HardwareError`

Runtime failure at the instrument level: driver errors, SCPI failures, lost
trigger pulses. Anything raised during execution rather than at compile or
validate time.

## Which error to expect

| Situation                                                     | Error                                            |
|---------------------------------------------------------------|--------------------------------------------------|
| Wrong waveform channel count for the bus                      | `ValidationError`                                |
| `measure()` on a bus with `acquires=False`                    | `ValidationError`                                |
| Duplicate variable id, or a `BusRef` from another schema      | `ValidationError`                                |
| Sweep bounds, step, or length that yield no iterations        | `ValidationError`                                |
| `qp.Variable("if")` or `qp.Variable("1freq")`                 | `InvalidVariableIdError`                         |
| `expr.evaluate_or_raise()` with an unbound variable           | `UnassignedVariableError`                        |
| `qp.simulate` on an operation no loop binds a variable for    | `UnassignedVariableError`                        |
| `var if` in a `.qp` file                                      | `InvalidVariableIdError`, no line number         |
| `var 1x` in a `.qp` file                                      | `ParseError` with the line number                |
| `require myvendor 1.0` with nothing installed                 | `ParseError`                                     |
| `require myvendor 99.0` against an older install              | `ParseError`                                     |
| Vendor extension installed but raising on import              | `VendorActivationError`, wrapped in `ParseError` during `loads` |
| Bad argument list in an inline `.qp` waveform constructor      | `TypeError`, outside the hierarchy               |
| `qp.and_(cond, True)`, or a `Variable` where Python calls `bool()` | `TypeError`, outside the hierarchy           |
| Unregistered operation or block class in `qp.dumps`           | `SerializationError`                             |
| `WaveformLibrary` entry holding a `Variable`                  | `SerializationError`                             |
| Any error diagnostic under `qp.simulate` or a platform `execute` | `UnsupportedOperationError`                    |
| Platform missing the bus you wrote                            | `BusNotAvailableError` (platform side)           |
| A waveform alias still unresolved when execution starts       | `WaveformResolutionError` (platform side)        |
| Anything else from a platform you do not recognize            | `QProgramError`                                  |

## Why two parents on some classes

`InvalidVariableIdError` and `UnassignedVariableError` inherit from both
`ValidationError` and `ValueError`. Each reports a value that is wrong on its
own terms: an identifier that is not a legal identifier, an expression with
no number to compute. That is exactly what `ValueError` means in Python, so
both spellings catch them. Every other class in the hierarchy descends from
`QProgramError` alone.

## `Diagnostic` is not an exception

The validator surface lives next door, but it is not part of this hierarchy.
`qp.validate(program, caps)` returns a tuple `(list[Diagnostic],
ExecutionPlan)` rather than raising. The list comes back, the caller decides
what to do. A `Diagnostic` is a frozen dataclass with `severity`
(`"error"`, `"warning"`, or `"info"`), `code`, `message`, `node`, `path`,
`capability`, `limit`, and `domain` fields.

Platforms typically translate any `severity="error"` diagnostic into one of
the platform-side exceptions above, `UnsupportedOperationError` being the
usual choice, so end users see one consistent error class regardless of which
axis tripped. A `severity="warning"` diagnostic means the program runs but in
a degraded way; `ReferencePlatform.execute` passes those to
`warnings.warn` as a `qp.ExecutionWarning` rather than raising, which is how
the `"forced-host"` notice on a block that lost real-time dispatch reaches
the caller. `severity="info"` diagnostics, such as the
`"reorderable-averaging"` hint, are neither raised nor warned; they come back
in the list as advisory output.

See [Capabilities, diagnostics, and profiles](../guide/capabilities.md)
for the validator walkthrough, and
[Diagnostics](../guide/capabilities.md#diagnostics) for the ten codes with
their severities and producing conditions.
