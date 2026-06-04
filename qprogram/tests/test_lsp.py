"""The .qp checker (``qprogram.lsp``): check_text core, CLI modes, and the LSP server."""

from __future__ import annotations

import json
import subprocess
import sys

from qprogram.lsp import FileDiagnostic, check_text, create_server, main

_WARNY = (
    "#!QProgram 1.0\n"
    "\n"
    "body:\n"
    "  var v\n"
    "\n"
    "  average 10:\n"
    "    for v in range(0, 1, 0.5):\n"
    '      set_frequency "d" v\n'
    '      set_parameter "c" "lo" v\n'
    '      measure "r" "wf" "w"\n'
)


# ---------------------------------------------------------------------------
# check_text core
# ---------------------------------------------------------------------------


def test_clean_program_has_no_diagnostics():
    assert check_text('#!QProgram 1.0\n\nbody:\n  play "d" "p"\n') == []


def test_parse_error_lands_on_its_line():
    text = '#!QProgram 1.0\n\nbody:\n  play "d" "p"\n  bogus_op "x"\n'
    diagnostics = check_text(text)
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity == "error"
    assert d.code == "parse-error"
    assert d.line == 4  # 0-based: the bogus_op line
    assert "bogus_op" in d.message
    assert d.lsp_severity == 1


def test_validation_warning_mapped_via_source_map():
    diagnostics = check_text(_WARNY)
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity == "warning"
    assert d.code == "forced-software"
    assert _WARNY.splitlines()[d.line].strip() == "average 10:"  # the highest forced block's line
    assert d.lsp_severity == 2


def test_no_validate_skips_capability_checks():
    assert check_text(_WARNY, validate=False) == []


def test_whole_file_parse_error_lands_on_line_zero():
    diagnostics = check_text('body:\n  play "d" "p"\n')  # missing header
    assert diagnostics[0].line == 0
    assert diagnostics[0].severity == "error"


def test_validation_error_is_reported():
    # Conditional on m0.state while the measurement doesn't request state classification:
    # parses fine, fails capability validation.
    text = '#!QProgram 1.0\n\nbody:\n  measure "r" "wf" "w" name="m0" returns="iq"\n  if m0.state == 0:\n    sync\n'
    diagnostics = check_text(text)
    assert any(d.code == "missing-classification" and d.severity == "error" for d in diagnostics)


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def _run_cli(*args: str, stdin: str) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "qprogram.lsp", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def test_cli_check_outputs_json_and_exit_code():
    code, out = _run_cli("check", "-", stdin='#!QProgram 1.0\n\nbody:\n  nope "x"\n')
    assert code == 1  # errors -> non-zero
    payload = json.loads(out)
    assert payload[0]["code"] == "parse-error"
    assert set(payload[0]) == {"line", "end_line", "severity", "code", "message"}


def test_cli_check_clean_file_exits_zero(tmp_path):
    f = tmp_path / "ok.qp"
    f.write_text("#!QProgram 1.0\n\nbody:\n  sync\n", encoding="utf-8")
    code, out = _run_cli("check", str(f), stdin="")
    assert code == 0
    assert json.loads(out) == []


def test_cli_check_warnings_exit_zero():
    code, out = _run_cli("check", stdin=_WARNY)
    assert code == 0  # warnings don't fail the check
    assert json.loads(out)[0]["severity"] == "warning"


def test_cli_explain_renders_plan_tree():
    code, out = _run_cli("explain", stdin=_WARNY)
    assert code == 0
    assert "average 10:" in out
    assert "~ forced-sw:" in out


def test_cli_explain_reports_parse_error():
    code, out = _run_cli("explain", stdin="not a qp file")
    assert code == 1
    assert "Missing #!QProgram header" in out


def test_main_callable_directly(tmp_path, capsys):
    f = tmp_path / "p.qp"
    f.write_text("#!QProgram 1.0\n\nbody:\n  sync\n", encoding="utf-8")
    assert main(["check", "--no-validate", str(f)]) == 0
    assert json.loads(capsys.readouterr().out) == []


# ---------------------------------------------------------------------------
# LSP server (pygls present in the dev environment)
# ---------------------------------------------------------------------------


def test_create_server_builds_with_handlers():
    server = create_server()
    assert server.name == "qp-language-server"


def test_file_diagnostic_severity_mapping():
    d = FileDiagnostic(line=0, end_line=0, severity="info", code="x", message="m")
    assert d.lsp_severity == 3
