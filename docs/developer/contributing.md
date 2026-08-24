# Contributing

The short version: open a small PR, run the linter and the tests, write
the docs for anything that changes user-visible behavior.

## Before you start

Skim two things first.

1. **The architecture overview.** [Architecture](architecture.md) explains
   the AST builder pattern, the four vendor-extension hooks, and the
   serialization registry. Most changes touch one of these.
2. **The reference section.** [The `.qp` file format](../reference/qp-format.md)
   is the normative description of the text format, and
   `src/qprogram/grammar/qp.lark` is its machine-readable form. The
   [API reference](../reference/api-qprogram.md) is generated from the
   docstrings under `src/`, so the code and the reference move together.

A change that alters user-visible behavior changes the docs in the same PR.
Say so in the PR description, and name the guide pages you touched.

## Development workflow

1. **Fork and clone.** The repository root is the package root.

   ```bash
   git clone https://github.com/qilimanjaro-tech/qprogram
   cd qprogram
   ```

2. **Install the package.**

   ```bash
   uv sync --all-extras
   ```

3. **Install the docs environment** (optional, only if you change docs).

   ```bash
   uv sync --all-extras --group docs
   uv run --group docs zensical serve
   ```

   `mkdocstrings` imports the package to render the API reference, so the
   docs build needs the project and its extras installed, not just the docs
   tooling.

4. **Make your change.**

5. **Lint and format.**

   ```bash
   uv run ruff check .
   uv run ruff format .
   ```

6. **Type-check.**

   ```bash
   uv run ty check
   ```

7. **Run the tests.**

   ```bash
   uv run pytest
   uv run pytest --cov          # check coverage on touched files
   ```

8. **Update the docs.** Anything user-visible needs an entry in the
   relevant guide page. New operations and waveforms also need a mention in
   [`docs/reference/qp-format.md`](../reference/qp-format.md).

9. **Open the PR.** The workflows under `.github/workflows/` run on it:
   `tests.yml` runs the suite across the supported Python versions and
   uploads coverage; `code_quality.yml` runs `ruff check` and
   `ruff format --diff` once on Python 3.13, then `ty check` once per
   supported version (3.11 through 3.14); and `docs.yml` builds this site.

## What "small PR" means

One concept per PR. A new operation plus a new waveform plus a bug fix in
the parser is three PRs. Each one is easier to review, easier to revert, and
easier to bisect against.

If you find yourself in a long branch with many concepts, split it. The
maintainers will ask you to anyway.

## Style notes

These are the rules the project actually enforces; they are not exhaustive.
All of them are configured in `pyproject.toml`.

- **Ruff with `preview = true` and `select = ["ALL"]`**, minus a curated
  ignore list written as rule *names* rather than codes, so the reason for
  each exemption reads off the config. Line length is 120 and the formatter
  owns it. Expect the linter to push back on most external code.
- **Docstrings are enforced.** Preview mode means both the `D` and the `DOC`
  families run, under the Google convention. Every parameter gets a
  `name (type): Description.` entry — the parenthesized type is house style
  even though the signature is annotated. A non-`None` return needs a
  `Returns:` section, and every exception a caller can observe needs a
  `Raises:` entry. Constructor arguments are documented on the **class**
  docstring, not on `__init__`. mkdocstrings renders all of it into the
  [API reference](../reference/api-qprogram.md).
- **Every file carries the Apache header** — the standard 13-line notice
  with `Copyright 2026 Qilimanjaro Quantum Tech`. Ruff's
  `missing-copyright-notice` rule fails the lint on a file without it.
- **Type hints everywhere.** `ty` (Astral's type checker) is in the dev
  group and checks `src`.
- **Two-space indentation in `.qp` files.** Match this in test fixtures.
- **Function-style tests.** No test classes. Use fixtures and
  parametrization.
- **No new runtime dependencies** without discussion. `qprogram` is
  numpy + xarray; that is on purpose. Anything heavier belongs in an extra —
  `matplotlib` behind `qprogram[viz]`, `pygls` behind `qprogram[lsp]`.

## What goes where

Use this when you are not sure which file to touch.

| Change kind                                                | Where it lands                                                                |
|------------------------------------------------------------|-------------------------------------------------------------------------------|
| New core operation                                          | `src/qprogram/operations/<name>.py` + method on `QProgram`.                   |
| New core waveform                                           | `src/qprogram/waveforms/<name>.py` + registration.                            |
| New sweep source                                            | `src/qprogram/sweeps/builtin.py` (or `combinators.py`) + registration.        |
| Parser change                                               | `src/qprogram/serialization/parser.py`.                                       |
| Writer change                                               | `src/qprogram/serialization/writer.py`.                                       |
| Grammar change                                              | `src/qprogram/grammar/qp.lark`, kept in step with the parser by `tests/test_grammar.py`. |
| New vendor operation                                        | The vendor's own package. See [Building a vendor extension](vendor-extensions.md). |
| New vendor package                                          | A separate package depending on `qprogram`. Same guide.                        |
| Docs                                                        | `docs/` (this site); the nav lives in `zensical.toml`.                         |

## Commit messages

Short, imperative, explanatory. The body is more important than the title;
explain *why*, not *what*. The history in `git log` is a good template.

## License and attribution

QProgram is licensed under the Apache License, Version 2.0. The full text is
in the `LICENSE` file at the repository root, and every source file carries
the matching 13-line header. By opening a PR you agree to license your
contribution under the same terms.

## Where to ask

- **Bug reports and feature requests.** Open a GitHub issue. Include a
  minimal program that reproduces the problem and the `.qp` text for it.
- **Design discussion.** Open an issue that names the affected code, the
  behavior you expect, and the guide page that documents it.
- **Quick questions.** Open a discussion if the project has them enabled,
  or raise it in the relevant issue thread.

Pull requests with a clear scope and tests are the fastest path to a merge.
