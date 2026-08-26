# Reference

Four pages of normative material: the two text formats the package reads and
writes, the identifiers the DSL holds back, the exception hierarchy, and the
generated API listing. Where one of these pages disagrees with a guide page,
this section is the one to trust. Where one of them disagrees with `src/`, the
source wins, and the page is a bug.

| Page | What it fixes |
|---|---|
| [.qp file format](qp-format.md) | Every production of format version 1.0: sections, the eleven core operation keywords, inline waveform constructors, sweep sources, expressions, the grammar summary, and what the parser rejects with which message; then the `.wfl` waveform library format, which is the other file the package reads. |
| [Reserved keywords](reserved.md) | The 29 names in `qp.RESERVED_KEYWORDS`, which construction sites check them, and the wider rule that applies to vendor namespaces. |
| [Errors](errors.md) | The `QProgramError` hierarchy, which call raises which, and the two families of argument error that stay outside it as a plain `TypeError`. |
| [API reference](api-qprogram.md) | Signatures and docstrings for the names in `qprogram.__all__`, plus the submodule classes and extension points the guides name, rendered from `src/` by mkdocstrings. |

Three of the four are checkable against the package at runtime. The format
version comes from `qprogram.serialization._format.FORMAT_VERSION`, currently
`"1.0"`, and is what the writer emits in the `#!QProgram` header and what the
parser compares a file's major version against. The canonical grammar ships as
`src/qprogram/grammar/qp.lark` and is readable with
`qprogram.grammar.grammar_text()`. The reserved set is `qp.RESERVED_KEYWORDS`.
The API page is generated from one mkdocstrings directive per symbol, so a new
public name needs a directive rather than a hand-written signature.

These pages describe the observable surface and not the reasoning behind it.
For how the AST, the capability protocol, and the serializer are put together,
read the [developer guide](../developer/index.md), starting with
[Architecture](../developer/architecture.md).
