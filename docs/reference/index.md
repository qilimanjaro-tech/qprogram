# Reference

Authoritative material: the file format grammar, the reserved keyword set,
the exception hierarchy, and the generated API documentation.

| Topic                                                  | What you find there                                              |
|--------------------------------------------------------|-----------------------------------------------------------------|
| [.qp file format](qp-format.md)                        | The full grammar, examples, compatibility rules.                 |
| [Reserved keywords](reserved.md)                       | Identifiers QProgram will not let you use as variable ids.       |
| [Errors](errors.md)                                    | The exception hierarchy and what raises which.                   |
| [API reference](api-qprogram.md)                       | Auto-generated reference for the `qprogram` package.             |

These pages describe the observable surface. For the reasoning behind it —
how the AST, the capability protocol, and the serializer are put together —
read the [developer guide](../developer/index.md), starting with
[Architecture](../developer/architecture.md).
