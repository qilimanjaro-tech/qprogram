# QProgram (.qp) — VS Code extension

Syntax highlighting, live diagnostics, and an execution-plan explainer for
[QProgram](https://github.com/qilimanjaro-tech/qprogram) `.qp` files.

## What you get

- **Highlighting** — TextMate grammar for the whole format: sections, `require`
  lines, fragments, control flow, operations (core and vendor-dotted), bus
  paths (`q[0].drive`), expressions, waveform constructors, snippets.
- **Diagnostics** — squiggles from the *real* qprogram toolchain, not a
  regex linter: the production parser's line-tagged errors plus
  reference-platform capability validation (`forced-software` warnings land on
  the exact line through qprogram's source maps). Runs on open, save, and
  (debounced) while typing.
- **`qp: Explain execution plan`** — renders the hw/sw plan tree for the
  current file in an output panel.

## Requirements

A Python environment with `qprogram` installed. Point the extension at it via
the `qp.python` setting (default `python3`):

```jsonc
// .vscode/settings.json
{ "qp.python": "${workspaceFolder}/qprogram/.venv/bin/python" }
```

No Node dependencies, no build step — the extension is a single plain-JS file
that spawns `python -m qprogram.lsp check -`.

## Install (local development)

Symlink (or copy) this folder into your VS Code extensions directory and
reload:

```bash
ln -s "$(pwd)/editors/vscode-qp" ~/.vscode/extensions/qilimanjaro.qp-language-0.1.0
```

Or package a `.vsix` with [`vsce`](https://github.com/microsoft/vscode-vsce):

```bash
npx @vscode/vsce package   # then: code --install-extension qp-language-0.1.0.vsix
```

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `qp.python` | `python3` | Interpreter of an env with `qprogram` installed. |
| `qp.validate` | `true` | Capability validation on top of parsing. |
| `qp.checkOnChange` | `true` | Debounced checking while typing. |
| `qp.debounceMs` | `500` | The debounce interval. |

## Other editors

The same checker speaks full LSP: install `qprogram[lsp]` and run
`python -m qprogram.lsp serve` over stdio from any LSP client (Neovim, Helix,
Emacs `eglot`, ...).
