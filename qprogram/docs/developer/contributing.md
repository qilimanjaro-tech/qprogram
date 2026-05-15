# Contributing

The short version: open a small PR, run the linter and the tests, write
the docs for anything that changes user-visible behaviour.

## Before you start

Skim two things first.

1. **The specs.** `.specs/qprogram-dsl.md` and `.specs/qp-file-format.md` are
   the source of truth for intent. The code may not match exactly; the
   specs are the target.
2. **The architecture overview.** [Architecture](architecture.md) explains
   the AST builder pattern, the three vendor-extension hooks, and the
   serialization registry. Most changes touch one of these.

If the change you want to make would alter user-visible behaviour, the spec
might be the right place to update too. Flag the mismatch in your PR
description.

## Development workflow

1. **Fork and clone.**

   ```bash
   git clone https://github.com/qilimanjaro-tech/qprogram
   cd qprogram
   ```

2. **Install the package.**

   ```bash
   cd qprogram
   uv sync
   ```

3. **Install the docs environment** (optional, only if you change docs).

   ```bash
   uv sync --group docs
   uv run mkdocs serve
   ```

4. **Make your change.**

5. **Lint and format.**

   ```bash
   uv run ruff check .
   uv run ruff format .
   ```

6. **Run the tests.**

   ```bash
   uv run pytest
   uv run pytest --cov          # check coverage on touched files
   ```

7. **Update the docs.** Anything user-visible needs an entry in the
   relevant guide page. New operations and waveforms also need a mention in
   [`docs/reference/qp-format.md`](../reference/qp-format.md).

8. **Open the PR.**

## What "small PR" means

One concept per PR. A new operation plus a new waveform plus a bug fix in
the parser is three PRs. Each one is easier to review, easier to revert, and
easier to bisect against.

If you find yourself in a long branch with many concepts, split it. The
maintainers will ask you to anyway.

## Style notes

These are the rules the project actually enforces; they are not exhaustive.

- **Ruff with `select = ["ALL"]` and a curated ignore list.** Expect the
  formatter and linter to push back on most external code. The configured
  ignores live in `qprogram/pyproject.toml`.
- **Two-space indentation in `.qp` files.** Match this in test fixtures.
- **Docstrings: Google style.** mkdocstrings parses them when building this
  site. Keep them concise and concrete; explain why where the why is
  non-obvious.
- **Type hints everywhere.** `ty` (Astral's type checker) is part of the
  `qprogram` dev deps and runs against `src/`.
- **Function-style tests.** No test classes. Use fixtures and
  parametrization.
- **No new top-level dependencies** without discussion. `qprogram` is
  numpy + xarray; that is on purpose.

## What goes where

Use this when you are not sure which package or which file to touch.

| Change kind                                                | Where it lands                                                                |
|------------------------------------------------------------|-------------------------------------------------------------------------------|
| New core operation                                          | `qprogram/src/qprogram/operations/<name>.py` + method on `QProgram`.          |
| New core waveform                                           | `qprogram/src/qprogram/waveforms/<name>.py` + registration.                   |
| Parser change                                               | `qprogram/src/qprogram/serialization/parser.py`.                              |
| Writer change                                               | `qprogram/src/qprogram/serialization/writer.py`.                              |
| New vendor operation                                        | The vendor's own package. See [Building a vendor extension](vendor-extensions.md). |
| New vendor package                                          | A separate package depending on `qprogram`. Same guide.                        |
| Docs                                                        | `docs/` (this site).                                                          |
| Specs                                                       | `.specs/`.                                                                    |

## Commit messages

Short, imperative, explanatory. The body is more important than the title;
explain *why*, not *what*. The recent history in `git log` is a good
template.

## License and attribution

QProgram is open source under the terms specified in the repository root
LICENSE file (TBD; check the file before contributing). By opening a PR you
agree to license your contribution under the same terms.

## Where to ask

- **Bug reports and feature requests.** Open a GitHub issue. Include a
  minimal program that reproduces the problem and the `.qp` text for it.
- **Design discussion.** Reference the relevant section of `.specs/` in the
  issue, propose a change, and link to the affected code.
- **Quick questions.** Open a discussion if the project has them enabled,
  or raise it in the relevant issue thread.

Pull requests with a clear scope and tests are the fastest path to a merge.
