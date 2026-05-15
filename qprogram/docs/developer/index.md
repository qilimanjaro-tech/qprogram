# Developer guide

These pages document the internals you need to extend or contribute to
QProgram.

| Topic                                                  | What you find there                                              |
|--------------------------------------------------------|-----------------------------------------------------------------|
| [Architecture](architecture.md)                        | Repository layout, key design choices, the AST builder pattern.  |
| [Adding operations](adding-operations.md)              | Step-by-step for a new core operation.                           |
| [Adding waveforms](adding-waveforms.md)                | Step-by-step for a new built-in or user-registered waveform.     |
| [Building a vendor extension](vendor-extensions.md)    | The whole story for a new vendor package.                         |
| [Serialization internals](serialization-internals.md)  | How the writer and parser work, registry-driven dispatch.        |
| [Testing](testing.md)                                   | How tests are organised, fixtures, coverage targets.              |
| [Contributing](contributing.md)                        | Workflow, style, what to read before opening a PR.                |

Two specifications in `.specs/` are the long-form design documents. They
spell out intent in more detail than the code or these pages do. When they
disagree with the code, they are the target.
