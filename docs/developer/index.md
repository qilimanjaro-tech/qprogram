# Developer guide

These pages cover the internals: how the package is laid out, the extension
points it exposes, and what changing each one involves.

| Page | What it documents |
|---|---|
| [Architecture](architecture.md) | The source tree, what each module owns, the direction the imports run, the builder and its block stack, the shared structural equality, the three vendor hooks, and which file a given kind of change belongs in. |
| [Adding operations](adding-operations.md) | Adding a core operation in seven ordered steps: the node class, the export, the builder method, the serializer registration, the capability token, the tests, and the documentation, closing with every place `set_phase` appears as a worked audit trail. |
| [Adding waveforms](adding-waveforms.md) | The waveform contract, parameters that accept an expression, and registering a shape either inside the package or from user code. |
| [Building a vendor extension](vendor-extensions.md) | A whole vendor package: operation classes, the typed namespace and mixin, a capability profile, the registration glue, packaging, and tests. |
| [Capability protocol internals](capability-protocol.md) | Where capabilities are declared, the token and profile registries, waveform-class dispatch, predicates, and how the validator consumes them. |
| [Serialization internals](serialization-internals.md) | The registries the writer and parser dispatch through, how each side is structured, and what the round trip guarantees. |
| [Testing](testing.md) | How the suite is organized, the shared fixtures, the coverage settings, and what is worth a test. |
| [Contributing](contributing.md) | The development loop, the checks CI runs, changelog fragments, and the release steps. |

Which page you need depends on whether the change is inside `qprogram` or
outside it. A waveform registered with `qp.register_waveform`, a sweep source
registered with `qp.register_sweep_source`, and everything a vendor package
registers on import need no change to the package at all, so those pages
describe a contract to satisfy. A new core operation, a new core block kind, or
a new capability token changes the package and the `.qp` format together, so
those pages read as ordered checklists, down to the tests and the
documentation that keep the code and the format description in agreement.

The [Reference](../reference/index.md) section holds the normative material
these pages build on: the [`.qp` file format](../reference/qp-format.md), the
[reserved keywords](../reference/reserved.md), the
[error hierarchy](../reference/errors.md), and the generated
[API reference](../reference/api-qprogram.md). The machine-readable grammar
ships with the package as `src/qprogram/grammar/qp.lark`, and
`tests/test_grammar.py` checks it against the hand-written parser so the two
cannot drift.
