# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""``.qp`` checker and language server — the production parser behind editor squiggles.

A grammar-derived checker could only validate *syntax*; this module instead exposes the real
toolchain to editors: `check_text` runs `qprogram.loads` (line-tagged ``ParseError``,
registry and schema-aware) and, when the file parses, [`qprogram.validate`][] against the
[`ReferencePlatform`][qprogram.ReferencePlatform] capabilities — mapping each diagnostic's structural
`path` to its ``.qp`` line through the parser-recorded
[`source_map`][qprogram.QProgram.source_map].

Three front-ends share that core:

- ``python -m qprogram.lsp check [file]`` — one-shot JSON diagnostics (file argument or stdin).
  Zero dependencies beyond qprogram itself, which is what an editor integration spawns it for.
- ``python -m qprogram.lsp explain [file]`` — the `qprogram.explain` plan tree for a file.
- ``python -m qprogram.lsp serve`` — a Language Server Protocol server over stdio (requires the
  ``qprogram[lsp]`` extra, i.e. ``pygls``), usable from any LSP-capable editor.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from qprogram.qprogram import QProgram

_SEVERITY_TO_LSP: dict[str, int] = {"error": 1, "warning": 2, "info": 3}


@dataclass(frozen=True)
class FileDiagnostic:
    """One editor-facing diagnostic: a 0-based line span plus the toolchain's message.

    Attributes:
        line (int): 0-based line the diagnostic points at (``0`` for whole-file diagnostics).
        end_line (int): 0-based line the span ends on, inclusive. The checker reports whole lines,
            so it equals `line`; the field is separate so a multi-line span needs no change
            on the editor side.
        severity (Literal["error", "warning", "info"]): Severity tier — the same three tiers as
            [`Diagnostic`][qprogram.Diagnostic].
        code (str): Machine-readable code (``"parse-error"`` or the validator's diagnostic code).
        message (str): Human-readable message, verbatim from the parser / validator.
    """

    line: int
    end_line: int
    severity: Literal["error", "warning", "info"]
    code: str
    message: str

    @property
    def lsp_severity(self) -> int:
        """The LSP ``DiagnosticSeverity`` integer (1=Error, 2=Warning, 3=Information)."""
        return _SEVERITY_TO_LSP[self.severity]


def check_text(text: str, *, validate: bool = True) -> list[FileDiagnostic]:
    """Check ``.qp`` source and return editor-facing diagnostics.

    First the production parser runs (``loads``): a `ParseError` becomes a
    single error diagnostic on its source line. When the file parses and ``validate`` is true,
    capability validation runs against the reference platform; node-bearing diagnostics map to
    lines via the program's [`source_map`][qprogram.QProgram.source_map] (structural-path keyed),
    node-less ones land on line 0. Programs with fragments are validated on their expansion —
    those diagnostics also land on line 0 (the expansion's paths don't exist in the file).

    Args:
        text (str): The ``.qp`` source.
        validate (bool): Whether to run reference-platform capability validation after a successful
            parse. ``False`` reports syntax problems only.

    Returns:
        Diagnostics sorted by line, errors first within a line.
    """
    # The package exposes the parser lazily through its own __getattr__, so importing it here
    # keeps this module importable without pulling the parser in.
    from qprogram import loads  # ruff: ignore[import-outside-top-level]
    from qprogram.serialization.parser import ParseError  # ruff: ignore[import-outside-top-level]

    try:
        program = loads(text)
    except ParseError as e:
        line = max(e.line_num - 1, 0)
        return [FileDiagnostic(line=line, end_line=line, severity="error", code="parse-error", message=str(e))]
    if not validate:
        return []
    return _validation_diagnostics(program)


def _validation_diagnostics(program: QProgram) -> list[FileDiagnostic]:
    """Return capability diagnostics for a freshly parsed program, mapped onto source lines.

    Args:
        program (QProgram): A program straight out of ``loads``, whose
            [`source_map`][qprogram.QProgram.source_map] still relates structural paths to lines.

    Returns:
        Diagnostics sorted by line, errors first within a line. A diagnostic with no node, or whose
        path is absent from the source map, lands on line 0.
    """
    from qprogram.executor import reference_capabilities  # ruff: ignore[import-outside-top-level]
    from qprogram.validation import validate as _validate  # ruff: ignore[import-outside-top-level]

    source_map = program.source_map
    diagnostics, _plan = _validate(program, reference_capabilities())
    out: list[FileDiagnostic] = []
    for diag in diagnostics:
        line = 0
        if diag.path is not None and diag.path in source_map:
            line = source_map[diag.path] - 1
        out.append(
            FileDiagnostic(
                line=line,
                end_line=line,
                severity=diag.severity,
                code=diag.code,
                message=diag.message,
            ),
        )
    out.sort(key=lambda d: (d.line, d.lsp_severity))
    return out


# ---------------------------------------------------------------------------
# One-shot CLI modes (zero extra dependencies)
# ---------------------------------------------------------------------------


def _read_source(file_arg: str | None) -> str:
    """Return the ``.qp`` source named by a command-line file argument.

    Args:
        file_arg (str | None): Path to read; ``None`` or ``"-"`` selects standard input, which is
            how an editor hands over an unsaved buffer.

    Returns:
        The document text.

    Raises:
        OSError: When ``file_arg`` names a file that cannot be read.
        UnicodeDecodeError: When the bytes read are not valid UTF-8.
    """
    if file_arg in {None, "-"}:
        return sys.stdin.read()
    return Path(file_arg).read_text(encoding="utf-8")


def _cmd_check(args: argparse.Namespace) -> int:
    """Write the diagnostics for one document to stdout as JSON.

    Args:
        args (argparse.Namespace): Parsed ``check`` arguments — ``file`` and ``no_validate``.

    Returns:
        The process exit status: ``0`` when nothing error-severity was found, ``1`` otherwise.
    """
    diagnostics = check_text(_read_source(args.file), validate=not args.no_validate)
    json.dump([asdict(d) for d in diagnostics], sys.stdout)
    sys.stdout.write("\n")
    return 0 if not any(d.severity == "error" for d in diagnostics) else 1


def _cmd_explain(args: argparse.Namespace) -> int:
    """Write the execution-plan tree for one document to stdout.

    Args:
        args (argparse.Namespace): Parsed ``explain`` arguments — ``file``.

    Returns:
        The process exit status: ``0`` on success, ``1`` when the document does not parse (the
        parse error is printed in place of the tree).
    """
    from qprogram import loads  # ruff: ignore[import-outside-top-level]
    from qprogram.executor import reference_capabilities  # ruff: ignore[import-outside-top-level]
    from qprogram.explain import explain  # ruff: ignore[import-outside-top-level]
    from qprogram.serialization.parser import ParseError  # ruff: ignore[import-outside-top-level]

    try:
        program = loads(_read_source(args.file))
    except ParseError as e:
        sys.stdout.write(f"{e}\n")
        return 1
    sys.stdout.write(explain(program, reference_capabilities()))
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# LSP server (requires the qprogram[lsp] extra)
# ---------------------------------------------------------------------------


def create_server():  # ruff: ignore[missing-return-type-undocumented-public-function] — pygls.lsp.server.LanguageServer; annotating would force the import
    """Build the ``.qp`` LSP server (publishes `check_text` diagnostics on open/change/save).

    Returns:
        A ``pygls`` ``LanguageServer`` with the three document handlers registered, ready for
        ``start_io()``.

    Raises:
        ModuleNotFoundError: When ``pygls`` is not installed — install ``qprogram[lsp]``.
    """
    try:
        from lsprotocol import types  # ruff: ignore[import-outside-top-level]
        from pygls.lsp.server import LanguageServer  # ruff: ignore[import-outside-top-level]
    except ModuleNotFoundError as e:  # pragma: no cover — exercised only without the extra
        msg = "the .qp language server needs the 'pygls' package — install qprogram[lsp]"
        raise ModuleNotFoundError(msg) from e

    server = LanguageServer("qp-language-server", "0.1.0")

    def publish(uri: str) -> None:
        document = server.workspace.get_text_document(uri)
        diagnostics = [
            types.Diagnostic(
                range=types.Range(
                    start=types.Position(line=d.line, character=0),
                    end=types.Position(line=d.end_line + 1, character=0),
                ),
                severity=types.DiagnosticSeverity(d.lsp_severity),
                code=d.code,
                source="qprogram",
                message=d.message,
            )
            for d in check_text(document.source)
        ]
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics),
        )

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(params: types.DidOpenTextDocumentParams) -> None:
        publish(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(params: types.DidChangeTextDocumentParams) -> None:
        publish(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(params: types.DidSaveTextDocumentParams) -> None:
        publish(params.text_document.uri)

    return server


def _cmd_serve(_args: argparse.Namespace) -> int:  # pragma: no cover — interactive stdio loop
    """Run the language server over stdio until the client disconnects.

    Args:
        _args (argparse.Namespace): Parsed ``serve`` arguments; the subcommand takes none.

    Returns:
        ``0`` once the stdio loop exits.
    """
    create_server().start_io()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the ``python -m qprogram.lsp`` command line.

    Args:
        argv (list[str] | None): Argument vector to parse; ``None`` reads `sys.argv`.

    Returns:
        The exit status of the chosen subcommand.

    Raises:
        SystemExit: When the arguments are malformed or ``--help`` is requested — raised by
            `argparse`.
    """
    cli = argparse.ArgumentParser(prog="qprogram.lsp", description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="one-shot JSON diagnostics for a .qp file (or stdin)")
    check.add_argument("file", nargs="?", help=".qp file path, or '-' / omitted for stdin")
    check.add_argument("--no-validate", action="store_true", help="parse only; skip capability validation")
    check.set_defaults(handler=_cmd_check)

    explain = sub.add_parser("explain", help="render the execution-plan tree for a .qp file (or stdin)")
    explain.add_argument("file", nargs="?", help=".qp file path, or '-' / omitted for stdin")
    explain.set_defaults(handler=_cmd_explain)

    serve = sub.add_parser("serve", help="run the LSP server over stdio (requires qprogram[lsp])")
    serve.set_defaults(handler=_cmd_serve)

    args = cli.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
