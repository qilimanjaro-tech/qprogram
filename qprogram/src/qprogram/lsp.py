"""``.qp`` checker and language server — the production parser behind editor squiggles.

A grammar-derived checker could only validate *syntax*; this module instead exposes the real
toolchain to editors: :func:`check_text` runs :func:`qprogram.loads` (line-tagged ``ParseError``,
registry and schema-aware) and, when the file parses, :func:`qprogram.validate` against the
:class:`~qprogram.ReferencePlatform` capabilities — mapping each diagnostic's structural
:attr:`~qprogram.Diagnostic.path` to its ``.qp`` line through the parser-recorded
:attr:`~qprogram.QProgram.source_map`.

Three front-ends share that core:

- ``python -m qprogram.lsp check [file]`` — one-shot JSON diagnostics (file argument or stdin).
  Zero dependencies beyond qprogram itself; this is what the VS Code extension spawns.
- ``python -m qprogram.lsp explain [file]`` — the :func:`qprogram.explain` plan tree for a file.
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
        line: 0-based line the diagnostic points at (``0`` for whole-file diagnostics).
        end_line: 0-based line the span ends on (inclusive; equals :attr:`line` today).
        severity: ``"error"`` / ``"warning"`` / ``"info"`` — same tiers as
            :class:`~qprogram.Diagnostic`.
        code: Machine-readable code (``"parse-error"`` or the validator's diagnostic code).
        message: Human-readable message, verbatim from the parser / validator.
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

    First the production parser runs (``loads``): a :class:`~qprogram.ParseError` becomes a
    single error diagnostic on its source line. When the file parses and ``validate`` is true,
    capability validation runs against the reference platform; node-bearing diagnostics map to
    lines via the program's :attr:`~qprogram.QProgram.source_map` (structural-path keyed),
    node-less ones land on line 0. Programs with fragments are validated on their expansion —
    those diagnostics also land on line 0 (the expansion's paths don't exist in the file).

    Args:
        text: The ``.qp`` source.
        validate: Also run reference-platform capability validation after a successful parse.

    Returns:
        Diagnostics sorted by line, errors first within a line.
    """
    from qprogram import loads  # noqa: PLC0415 — lazy: package __init__ exposes it via __getattr__
    from qprogram.serialization.parser import ParseError  # noqa: PLC0415

    try:
        program = loads(text)
    except ParseError as e:
        line = max(e.line_num - 1, 0)
        return [FileDiagnostic(line=line, end_line=line, severity="error", code="parse-error", message=str(e))]
    if not validate:
        return []
    return _validation_diagnostics(program)


def _validation_diagnostics(program: QProgram) -> list[FileDiagnostic]:
    from qprogram.executor import reference_capabilities  # noqa: PLC0415
    from qprogram.validation import validate as _validate  # noqa: PLC0415

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
    if file_arg in (None, "-"):
        return sys.stdin.read()
    return Path(file_arg).read_text(encoding="utf-8")


def _cmd_check(args: argparse.Namespace) -> int:
    diagnostics = check_text(_read_source(args.file), validate=not args.no_validate)
    json.dump([asdict(d) for d in diagnostics], sys.stdout)
    sys.stdout.write("\n")
    return 0 if not any(d.severity == "error" for d in diagnostics) else 1


def _cmd_explain(args: argparse.Namespace) -> int:
    from qprogram import loads  # noqa: PLC0415
    from qprogram.executor import reference_capabilities  # noqa: PLC0415
    from qprogram.explain import explain  # noqa: PLC0415
    from qprogram.serialization.parser import ParseError  # noqa: PLC0415

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


def create_server():  # noqa: ANN201 — pygls.lsp.server.LanguageServer; annotating would force the import
    """Build the ``.qp`` LSP server (publishes :func:`check_text` diagnostics on open/change/save).

    Raises:
        ModuleNotFoundError: When ``pygls`` is not installed — install ``qprogram[lsp]``.
    """
    try:
        from lsprotocol import types  # noqa: PLC0415
        from pygls.lsp.server import LanguageServer  # noqa: PLC0415
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
    create_server().start_io()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m qprogram.lsp``."""
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
