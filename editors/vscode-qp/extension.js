// QProgram (.qp) VS Code extension — diagnostics and plan explainer.
//
// Deliberately dependency-free (no node_modules, no build step): the checker is the *real*
// qprogram toolchain, spawned as `python -m qprogram.lsp check -` with the document text on
// stdin. That returns the production parser's line-tagged errors plus reference-platform
// capability diagnostics (forced-software warnings land on the exact line via qprogram's
// source maps). The same module also speaks full LSP (`python -m qprogram.lsp serve`) for
// other editors; here a thin spawn keeps the extension a single reviewable file.

"use strict";

const vscode = require("vscode");
const cp = require("child_process");

const LANGUAGE_ID = "qp";

let diagnosticCollection;
let outputChannel;
let warnedAboutPython = false;
const debounceTimers = new Map();

function config() {
  const cfg = vscode.workspace.getConfiguration("qp");
  return {
    python: cfg.get("python", "python3"),
    validate: cfg.get("validate", true),
    checkOnChange: cfg.get("checkOnChange", true),
    debounceMs: cfg.get("debounceMs", 500),
  };
}

/** Spawn `python -m qprogram.lsp <args>` feeding `stdinText`; resolve {code, stdout, stderr}. */
function runChecker(args, stdinText) {
  const { python } = config();
  return new Promise((resolve, reject) => {
    const proc = cp.spawn(python, ["-m", "qprogram.lsp", ...args], {
      cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("error", reject); // spawn failure (python not found)
    proc.on("close", (code) => resolve({ code, stdout, stderr }));
    proc.stdin.write(stdinText);
    proc.stdin.end();
  });
}

const SEVERITY = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  info: vscode.DiagnosticSeverity.Information,
};

async function checkDocument(document) {
  if (document.languageId !== LANGUAGE_ID) {
    return;
  }
  const { validate } = config();
  const args = ["check", "-"];
  if (!validate) {
    args.push("--no-validate");
  }
  try {
    const { stdout } = await runChecker(args, document.getText());
    const payload = JSON.parse(stdout || "[]");
    const diagnostics = payload.map((d) => {
      const line = Math.min(d.line, document.lineCount - 1);
      const range = document.lineAt(Math.max(line, 0)).range;
      const diagnostic = new vscode.Diagnostic(range, d.message, SEVERITY[d.severity] ?? SEVERITY.info);
      diagnostic.source = "qprogram";
      diagnostic.code = d.code;
      return diagnostic;
    });
    diagnosticCollection.set(document.uri, diagnostics);
  } catch (err) {
    diagnosticCollection.delete(document.uri);
    if (!warnedAboutPython) {
      warnedAboutPython = true;
      const pick = await vscode.window.showWarningMessage(
        `qp: could not run the checker (${err.message}). Set "qp.python" to an interpreter with qprogram installed.`,
        "Open Settings",
      );
      if (pick === "Open Settings") {
        vscode.commands.executeCommand("workbench.action.openSettings", "qp.python");
      }
    }
  }
}

function scheduleCheck(document) {
  const key = document.uri.toString();
  clearTimeout(debounceTimers.get(key));
  debounceTimers.set(
    key,
    setTimeout(() => checkDocument(document), config().debounceMs),
  );
}

async function explainActiveEditor() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== LANGUAGE_ID) {
    vscode.window.showInformationMessage("qp: open a .qp file first.");
    return;
  }
  try {
    const { stdout } = await runChecker(["explain", "-"], editor.document.getText());
    outputChannel.clear();
    outputChannel.appendLine(stdout.trimEnd());
    outputChannel.show(true);
  } catch (err) {
    vscode.window.showErrorMessage(`qp: explain failed — ${err.message}`);
  }
}

function activate(context) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection("qprogram");
  outputChannel = vscode.window.createOutputChannel("QProgram");
  context.subscriptions.push(diagnosticCollection, outputChannel);

  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument(checkDocument),
    vscode.workspace.onDidSaveTextDocument(checkDocument),
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (config().checkOnChange) {
        scheduleCheck(event.document);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((document) => diagnosticCollection.delete(document.uri)),
    vscode.commands.registerCommand("qp.check", () => {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        checkDocument(editor.document);
      }
    }),
    vscode.commands.registerCommand("qp.explain", explainActiveEditor),
  );

  // Check anything already open when the extension activates.
  vscode.workspace.textDocuments.forEach(checkDocument);
}

function deactivate() {
  for (const timer of debounceTimers.values()) {
    clearTimeout(timer);
  }
}

module.exports = { activate, deactivate };
