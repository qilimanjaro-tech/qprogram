# QProgram

QProgram is a Python DSL for describing pulse-level quantum experiments. A
program says what the chip should do; a platform decides how to run it. The
package is the language plus everything that can be settled without an
instrument attached: the AST, the `.qp` text format, the capability protocol a
platform validates programs against, a reference executor written in Python,
and the hooks vendor packages register themselves through. Its only runtime
dependencies are `numpy` and `xarray`.

## A first program

```python
import qprogram as qp

schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
gain = program.variable("gain", units="V")

with program.average(shots=1000):
    with program.sweep(gain).from_range(0.0, 1.0, 0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        m0 = program.measure(q[0].readout, "readout", "weights")

# Plug in calibrated waveforms at the very end.
resolved = program.with_waveforms(
    {
        "pi_pulse": qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1),
        "readout": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
        "weights": qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(1.0, 2000)),
    }
)

# Run it. `qp.simulate` is the reference software executor that ships with the
# package; hardware platforms implement the same `PlatformProtocol` interface.
result = qp.simulate(resolved)
data = result.get(m0)  # xarray.DataArray with named dimensions
```

`data` comes back with dimensions `("gain", "IQ")` and shape `(101, 2)`.
Dimensions are named after the enclosing loops, outermost first, so the sweep
over `gain` becomes an axis of 101 points; the trailing `IQ` axis carries
coordinates `["I", "Q"]`. The 1000 shots of the `average` block are reduced
rather than kept.

## What the package does and does not do

QProgram compiles nothing and talks to no instrument. It builds a description
of an experiment, checks that description against what a platform says it can
do, and hands it over. Lowering to instrument code, scheduling, and
calibration all sit on the platform side of `qp.PlatformProtocol`.

The same `QProgram` therefore runs on any platform that implements that
protocol. What portability costs is that the core vocabulary can only be the
part every platform can be asked to support. Instrument-specific work
(markers, active reset, triggers, slow-control parameters) lives in optional
vendor packages that register their operations at import time, so adding an
instrument does not change `qprogram`, and a program that uses one runs only
where that package is installed. Its `.qp` file records the dependency as a
`require` line and refuses to load without it, which is the trade-off taken on
purpose: a loud `ParseError` rather than a file that loads with an operation
silently missing.

Programs are ordinary Python objects. Blocks are containers, operations are
leaves, both are nodes, and `program.body.walk()` walks them, so a program can
be assembled by a function, a loop, or a comprehension and inspected
afterwards without a parser in the way. The cost is that Python control flow
runs while the program is being built and leaves no trace in the AST: a Python
`for` unrolls into repeated nodes, a loop that has to survive into execution is
`program.sweep(...)` or `program.average(...)`, and a branch on a measurement
result is `program.if_(...)`. The transformers (`rebind`, `with_waveforms`,
`expand`) deep-copy the program rather than mutate it, so a node held from
before a transform is not a node of the result.

`qp.save(program, "exp.qp")` writes a line-oriented text file that reviews and
diffs like source, and `qp.load` reads it back. The round trip is pinned by the
test suite in two directions: `qp.loads(qp.dumps(program))` carries the same
label, description, variables, and body under structural equality, and
re-emitting that program reproduces the text byte for byte. Numbers are written
through `repr` and arrays are never truncated, so the values that come back are
the values that went in. What the file preserves is the program, not the
document: the parser strips comments and the writer emits none, so a
hand-edited `.qp` file loses its annotations on the next save, and measurement
handles come back as new objects to be looked up by name through
`QProgram.measurement_handles`. Anything the format has no representation for
is refused rather than approximated. An unregistered operation class, a 2-D
array, and a `Fragment` handed to `qp.dumps` directly each raise
`SerializationError`.

A bus is addressed by a `BusRef` that a `BusSchema` produces.
`qp.BusSchema.transmon()` and the other presets return typed subclasses, so
`schema.q[0].drive` completes in an editor and a kind the element does not
expose fails while the program is being built rather than at execution: a
dynamically built schema reports `'q' has no bus 'flux'. Available: drive,
readout`. The schema also owns the mapping from element, index, and kind to the
bus string, through `qp.BusNaming` (the default pattern
`"{element}{index}/{kind}"` gives `q0/drive`), so no naming convention is built
into the language. Presets are typed but fixed; schemas built with
`add_element` or composed with `schema_a + schema_b` work at run time and carry
no static type. Plain strings remain valid buses, and a platform validates them
against its default bus profile instead of a per-bus one.

Capabilities are declared per slot, a slot being a `(bus, domain)` pair, with
the domains real-time (`rt`) and host-side (`host`).
`qp.validate(program, caps)` returns the `Diagnostic`s together with an
`ExecutionPlan` recording which domains each node can run in, and
`qp.explain(program, caps)` renders the same result as a tree with a
`[rt|host]`, `[rt]`, `[host]`, or `[--]` column per node, so an unsupported
operation is reported against the node that carries it instead of as one
rejection of the whole program. The check is static and no better than the
descriptor behind it: a `Profile` bundles capability tokens, numeric limits,
and predicates, the validator ignores limit keys it does not recognize, and
nothing in it looks at calibration, so a program can validate clean and still
fail on the device.

## The layers a program passes through

The stages are building, serialization, validation, optimization, execution,
and result collection, of which only building and execution are compulsory.
Serialization is a detour off the AST rather than a stage every program passes
through: `qp.dumps` and `qp.loads` can be skipped entirely, or used as the only
interchange between the process that writes a program and the one that runs it.
Validation against a platform's capabilities comes next, and
`qp.optimize(program, caps)` is an opt-in rewrite that applies the one
reordering the validator otherwise reports as the `"reorderable-averaging"`
info hint, lifting a host-side sweep out of an `average` so that the averaging
itself can run in real time. It is opt-in because the rewrite groups all shots
of a sweep point together instead of interleaving passes, which changes nothing
for a stationary system and does change results under drift.

Execution is the stage QProgram does not own. `qp.PlatformProtocol` requires a
platform to supply resource discovery (`get_bus_schema`, `get_buses`,
`get_parameters`, `get_global_parameters`), a `PlatformCapabilities`
descriptor, and `execute`. Its `validate`, `plan`, and `explain` methods have
working defaults that delegate to the core validator, so a platform with no
opinion of its own reports the same diagnostics a user gets from
`qp.validate`, and `stream` raises `NotImplementedError` until a platform
overrides it. The convention is that `execute` validates first and raises
`UnsupportedOperationError` on any diagnostic of severity `"error"`; a platform
is not forced to, and one that skips the check surfaces its own compiler errors
in place of structured diagnostics. `qp.simulate` runs a `qp.ReferencePlatform`
over the program in Python and is the executable definition of the language's
semantics; whatever runs the program, results arrive as one record per
measurement in a `QProgramResult`.

## Vendor extensions

A vendor extension is a separate package that depends on `qprogram` and
registers itself on import through three independent hooks: a runtime namespace
(a `qp.VendorNamespace` subclass passed to `QProgram.register_vendor`, which is
what makes a call such as `program.fake_inst.beep(...)` resolve), a typed mixin
so that the same call completes in an editor, and serialization registry
entries (`qp.register_vendor_operation`, `qp.register_vendor_block`,
`qp.register_vendor_version`) that give its nodes a `.qp` form and a version
for the `require` line. Capability tokens and profiles are registered the same
way, through `qp.register_capability_tokens` and `qp.register_profile`. A
package that also declares a `qprogram.vendors` entry point can be activated by
the parser on demand, which is what lets a `.qp` file that names it load in a
fresh interpreter. The [Architecture](developer/architecture.md) and [Building
a vendor extension](developer/vendor-extensions.md) pages work through the
pattern.

## Versions and compatibility

The package is pre-1.0, so the Python API can change between releases without a
deprecation cycle. The `.qp` format carries its own version and is at `1.0`,
where only the major component is binding: the writer emits `#!QProgram 1.0`, a
`1.1` file still loads on this parser, and a `2.0` file raises `ParseError` with
`Unsupported format version 2.0`. Accepting a newer minor is deliberate, and the
cost is that a file using grammar this parser does not know fails somewhere in
its body instead of at the header. A file with no header at all fails
immediately with `Missing #!QProgram header`.

Vendor compatibility is checked one `require` line at a time, before any of the
body is built, so a rejected file leaves no partially loaded program: the majors
must match, the installed minor must be at least the one the file asks for, a
patch component is accepted and ignored, and a vendor that is installed but not
yet imported is activated through its `qprogram.vendors` entry point.
[Format version and the require line](guide/serialization.md#format-version-and-the-require-line)
has the message each failure produces and the argument that turns activation
off.

## Pages by task

The [API reference](reference/api-qprogram.md) is generated from the source,
[.qp file format](reference/qp-format.md) describes the on-disk grammar, and
`src/qprogram/grammar/qp.lark` is the normative machine-readable form of that
grammar, kept in step with the production parser by the test suite.

| If you want to ...                              | Read                                                                |
|-------------------------------------------------|---------------------------------------------------------------------|
| install QProgram and run something              | [Getting started](getting-started.md)                                |
| understand the moving parts                     | [Core ideas](guide/concepts.md)                                      |
| sweep parameters with loops                     | [Control flow](guide/control-flow.md)                                |
| run a program without hardware                  | [Running programs](guide/execution.md)                               |
| know which programs a platform will accept       | [Capabilities, diagnostics, and profiles](guide/capabilities.md)     |
| read the file format                            | [.qp file format](reference/qp-format.md)                            |
| build your own vendor package                   | [Building a vendor extension](developer/vendor-extensions.md)        |
| browse the full API                             | [API reference](reference/api-qprogram.md)                           |
