# Developer guide

These pages document the internals you need to extend or contribute to
QProgram.

| Topic                                                  | What you find there                                              |
|--------------------------------------------------------|-----------------------------------------------------------------|
| [Architecture](architecture.md)                        | Repository layout, key design choices, the AST builder pattern.  |
| [Adding operations](adding-operations.md)              | Step-by-step for a new core operation.                           |
| [Adding waveforms](adding-waveforms.md)                | Step-by-step for a new built-in or user-registered waveform.     |
| [Building a vendor extension](vendor-extensions.md)    | The whole story for a new vendor package.                         |
| [Capability protocol internals](capability-protocol.md) | Distributed declaration, the token registry, predicates, profiles. |
| [Serialization internals](serialization-internals.md)  | How the writer and parser work, registry-driven dispatch.        |
| [Testing](testing.md)                                   | How tests are organized, fixtures, coverage targets.              |
| [Contributing](contributing.md)                        | Workflow, style, what to read before opening a PR.                |

The [Reference](../reference/index.md) section holds the normative material
these pages build on: the [`.qp` file format](../reference/qp-format.md), the
[reserved keywords](../reference/reserved.md), the
[error hierarchy](../reference/errors.md), and the generated
[API reference](../reference/api-qprogram.md). The machine-readable grammar
ships with the package as `src/qprogram/grammar/qp.lark`.
