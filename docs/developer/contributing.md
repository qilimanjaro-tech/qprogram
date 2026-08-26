# Contributing

Open a small pull request, run the linter, the type checker, and the tests, and
bring the docs along for anything that changes user-visible behavior. What
follows is the detail behind that.

## Before you start

Skim [Architecture](architecture.md) first, which explains the AST builder
pattern, the vendor-extension hooks, and the serialization registry; most
changes touch one of these. Then read the reference section:
[The `.qp` file format](../reference/qp-format.md) is the normative description
of the text format, `src/qprogram/grammar/qp.lark` is its machine-readable
form, and the [API reference](../reference/api-qprogram.md) is generated from
the docstrings under `src/`, so the code and the reference move together.

A change that alters user-visible behavior changes the docs in the same pull
request. Say so in the description, and name the guide pages you touched.

## Development workflow

1. **Fork and clone.** The repository root is the package root.

   ```bash
   git clone https://github.com/qilimanjaro-tech/qprogram
   cd qprogram
   ```

2. **Install the package.** The `dev` dependency group installs by default, so
   this one command gets the linter, the type checker, pytest, hypothesis, and
   lark alongside the package and its extras.

   ```bash
   uv sync --all-extras
   ```

3. **Install the docs environment**, if you are changing docs.

   ```bash
   uv sync --all-extras --group docs
   uv run --all-extras --group docs zensical serve
   ```

   `--all-extras` matters here: mkdocstrings imports the package to render the
   API reference, so the project and its optional dependencies have to be
   installed, not just the docs tooling.

4. **Make your change.**

5. **Lint and format.** Both run from `pyproject.toml` and neither imports the
   code.

   ```bash
   uv run ruff check .
   uv run ruff format .
   ```

6. **Type-check.** `[tool.ty.src]` sets `include = ["src"]`, so this covers the
   package and not the test suite.

   ```bash
   uv run ty check
   ```

7. **Run the tests.** See [Testing](testing.md) for what the suite covers and
   what a new feature needs from it.

   ```bash
   uv run pytest
   uv run pytest --cov=qprogram
   ```

8. **Update the docs.** Anything user-visible needs an entry in the relevant
   guide page. New operations, waveforms, and sweep sources also need a mention
   in [`docs/reference/qp-format.md`](../reference/qp-format.md), and a new
   public symbol needs a `::: qprogram.<Name>` directive in
   [`docs/reference/api-qprogram.md`](../reference/api-qprogram.md). A new page
   is unreachable until it appears in the nav in `zensical.toml`.

9. **Run the docs checker** on the pages you touched.

   ```bash
   uv run python .claude/skills/qprogram-docs/scripts/check_docs.py docs/guide/waveforms.md
   ```

   It parses every Python fence and checks each `qp.` attribute against the
   installed package, so a renamed symbol is caught; it parses `.qp` fences
   whose first line is `#!QProgram` with the real parser; it resolves relative
   links, their anchors, and the nav; and it reports house-style drift. Broken
   examples, links, and nav entries are errors and set the exit status. Style
   findings are warnings, and `--strict` promotes them, which is the mode to run
   before opening the pull request. The one warning `--strict` leaves alone is
   `qp-vendor`, which fires when a `.qp` example needs a vendor extension that
   is not installed locally. A fence that is not meant to be valid Python is
   exempted with `<!-- check: skip -->` on the line above it.

10. **Build the site**, if you touched docstrings or a reference page.

    ```bash
    uv run --all-extras --group docs zensical build --strict
    ```

    `--strict` turns a warning into a failure. The one that matters is an
    unresolved mkdocstrings cross-reference, which the docs checker cannot see
    and which would otherwise ship as a dead link. This is the same command
    `docs.yml` runs.

11. **Add a changelog entry.** Anything a user would notice gets one news
    fragment under `changelog/`, named `<pr-number>.<type>.md`. The types are
    `added`, `changed`, `fixed`, `removed`, and `misc`; the first four render
    their text under a heading of the same name, while `misc` is configured with
    `showcontent = false`, so a `misc` fragment contributes only its pull
    request link.

    ```bash
    uv run towncrier create 123.added.md
    ```

    That writes a file containing the placeholder `Add your info here`, which
    you then replace with one or two sentences about what changed for somebody
    using the library; `--content "..."` sets the text in the same command. A
    fragment written before the pull request has a number takes a `+` prefix and
    any name, as in `+lazy-waveform-binding.added.md`; towncrier renders such a
    fragment without a link, so rename it once the number exists. Internal
    refactors, test-only changes, and docs corrections do not need one.

12. **Open the pull request.** Three of the workflows under
    `.github/workflows/` run on it, and each skips while the pull request is a
    draft: `tests.yml` runs the suite on Python 3.11 and 3.14 and uploads
    coverage from the 3.13 job on `main`; `code_quality.yml` runs `ruff check`
    and `ruff format --diff` once on Python 3.13, then `ty check` once per
    supported version from 3.11 through 3.14; and `docs.yml` builds this site
    with `zensical build --strict`. [Testing](testing.md#ci) describes the jobs
    in more detail.

## What "small PR" means

One concept per pull request. A new operation plus a new waveform plus a bug fix
in the parser is three pull requests. Each one is easier to review, easier to
revert, and easier to bisect against.

If you find yourself in a long branch with many concepts, split it. The
maintainers will ask you to anyway.

## Style notes

Most of this is configured in `pyproject.toml` and enforced by ruff or by a
test; the rest is a review convention. The list is not exhaustive.

Ruff runs with `preview = true` and `select = ["ALL"]`, minus an ignore list
written as rule *names* rather than codes, so the reason for each exemption
reads off the config. Line length is 120 and the formatter owns it, which is why
`line-too-long` is one of the exemptions. `tests/**` carries its own
per-file-ignores, dropping the annotation and docstring families and the
security rules, so a test may reach into a private helper and skip its type
hints. Expect the linter to push back on code brought in from elsewhere.

Docstrings are enforced under the Google convention. Preview mode means both the
`D` and the `DOC` families run, so every parameter gets a
`name (type): Description.` entry, where the parenthesized type is house style
even though the signature is annotated. A non-`None` return needs a `Returns:`
section and every exception a caller can observe needs a `Raises:` entry.
Constructor arguments are documented on the **class** docstring, not on
`__init__`, which is why `undocumented-public-init` is in the ignore list.
`docstring-code-format = true` means the formatter reformats code inside
docstrings, so an example in one has to be valid Python.

Cross-references in docstrings are Markdown, not Sphinx roles. Write
`` [`Variable`][qprogram.Variable] `` for a target the
[API reference](../reference/api-qprogram.md) documents, and plain
`` `Variable` `` for anything it does not: a builtin, a stdlib name, a private
helper no page renders. `` [`qprogram.Range`][] `` is the shorthand when the
text is already the full path. mkdocstrings reads a docstring as Markdown and
has no reStructuredText reader, so a role such as
`` :class:`~qprogram.Variable` `` would reach the page as literal text.
`tests/test_docstring_style.py` scans every module under `src/` and fails the
suite on one, and it also catches a cross-reference that lost its target.

Every file carries the Apache header: the standard 13-line notice with
`Copyright 2026 Qilimanjaro Quantum Tech`. Ruff's `missing-copyright-notice`
rule reads the expected author from
`[tool.ruff.lint.flake8-copyright]` and fails the lint on a file without it.

Type hints go everywhere. `ty` checks `src` only, and one rule is switched off
there: `unused-ignore-comment`, because a `Values(...)` call needs an
`invalid-argument-type` suppression on Python 3.12 and later, where numpy's
`ArrayLike` does not admit the narrowed operand type, and that suppression is
then reported as unused on 3.11.

`.qp` files use two-space indentation, in test fixtures as well as in examples.
Tests are functions, not methods on a class, and use fixtures and
parametrization for shared setup.

New runtime dependencies need discussion first. `qprogram` depends on
`numpy>=2.1` and `xarray>=2026.4.0` and nothing else, which is what lets it
install next to whatever a lab already has; anything heavier belongs in an
extra, the way `matplotlib` sits behind `qprogram[viz]` and `pygls` behind
`qprogram[lsp]`. Supported Python versions are 3.11 through 3.14, so anything
that only works on a newer interpreter needs a fallback.

## What goes where

Use this when you are not sure which file to touch.

| Change kind | Where it lands |
|---|---|
| New core operation | `src/qprogram/operations/<name>.py`, exported from `operations/__init__.py`, a method on `QProgram`, and a `register_operation` line in `_register_core_specs()` in `src/qprogram/serialization/_specs.py`. |
| New core waveform | `src/qprogram/waveforms/<name>.py`, exported from `waveforms/__init__.py`, and added to the class list in `_register_builtin_waveforms()` in `src/qprogram/serialization/registry.py`. |
| New sweep source | `src/qprogram/sweeps/builtin.py` or `combinators.py`, exported from `sweeps/__init__.py`, and added to the `register_sweep_source` loop in `src/qprogram/serialization/_specs.py`. That call also registers the class's `TOKEN` with the capability registry. |
| Parser change | `src/qprogram/serialization/parser.py`. |
| Writer change | `src/qprogram/serialization/writer.py`. |
| Grammar change | `src/qprogram/grammar/qp.lark`, kept in step with the parser by `tests/test_grammar.py`. |
| New vendor operation | The vendor's own package. See [Building a vendor extension](vendor-extensions.md). |
| New vendor package | A separate package depending on `qprogram`. Same guide. |
| Docs | `docs/`, with the nav in `zensical.toml`. |
| Changelog entry | One fragment in `changelog/`. Never edit `CHANGELOG.md` by hand. |

## Releasing

`CHANGELOG.md` is assembled from the fragments in `changelog/`, so it is written
once per release rather than edited per pull request. A release goes out from
its own pull request:

1. Branch from an up-to-date `main`.
2. Set the new version. This writes both `pyproject.toml` and `uv.lock`; nothing
   else holds the literal, since `qprogram.__version__` is read from the
   installed metadata.

   ```bash
   uv version 0.2.0
   uv sync
   ```

3. Assemble the changelog. Pass the version explicitly. Left to guess, towncrier
   reads the *installed* metadata and can render a stale number into a heading
   that is never regenerated.

   ```bash
   uv run towncrier build --draft --version "$(uv version --short)"   # preview
   uv run towncrier build --version "$(uv version --short)" --yes
   ```

   The second command writes a `## <version> (<date>)` section into
   `CHANGELOG.md` under the `<!-- towncrier release notes start -->` marker and
   deletes the fragments it consumed. Each entry carries a
   `[PR #<n>](https://github.com/qilimanjaro-tech/qprogram/pull/<n>)` link built
   from the fragment's file name.

4. Read the rendered section and edit it. Fragments are written weeks apart by
   different people and rarely read as one voice when they land together.
5. Open the release pull request, and merge it once CI is green.
6. Create the GitHub Release on the merge commit, with a tag matching the
   version now in `pyproject.toml`, and use the new changelog section as the
   release body.

Publishing the release triggers `publish.yml`. Its `build` job runs `uv build`,
which produces one wheel and one sdist; `qprogram` is pure Python, so a single
wheel covers every interpreter and platform, and a package with compiled
extensions would need a build matrix here instead. The publish job downloads
those artifacts, lists them, validates them with `twine check`, and uploads them
with `uv publish --trusted-publishing always`. The `--check-url` pointing at
`https://pypi.org/simple/qprogram/` lets a retried run skip files that already
landed. Pre-releases publish the same way, so a version such as `0.2.0rc1`
reaches PyPI and `pip` installs it only when asked with `--pre`.

The upload job runs in the `pypi` GitHub environment, so any protection rule on
that environment (a required reviewer, a wait timer) gates the upload. PyPI
never lets a file be replaced, so that gate is the last point at which a wrong
version can be stopped. The workflow's concurrency group is keyed on the release
tag with `cancel-in-progress: false`, because a publish cancelled mid-upload can
leave an index in a state that is hard to recover from.

`publish.yml` can also be started by hand from the Actions tab, which is how the
first release goes out and how a run that failed on a transient error is
retried. A manual run takes three inputs. `platform` chooses between PyPI and
the `qilimanjaro` AWS CodeArtifact domain; the CodeArtifact path authenticates
through OIDC role chaining and uploads with `twine upload` against a
CodeArtifact authorization token rather than through trusted publishing.
`repository` names the CodeArtifact repository, and a dispatch that selects
`aws` without it fails in seconds in the `check-inputs` job rather than after
the distributions build. `dry_run` passes `--dry-run` to `uv publish` on the
PyPI path and skips the upload step entirely on the CodeArtifact path, so both
build and validate without publishing.

## Commit messages

Short, imperative, explanatory. The body is more important than the title;
explain *why*, not *what*. The history in `git log` is a good template.

## License and attribution

QProgram is licensed under the Apache License, Version 2.0. The full text is in
the `LICENSE` file at the repository root, and every source file carries the
matching 13-line header. By opening a pull request you agree to license your
contribution under the same terms.

## Where to ask

Bug reports, feature requests, and chores each have an issue template under
`.github/ISSUE_TEMPLATE/`. A bug report wants the shortest program that shows
the problem, and the `.qp` text for it if serialization is involved. A feature
request wants what you cannot do today and why the workaround is not good
enough; an operation, waveform, or sweep source that only one vendor's hardware
can run belongs in that vendor's extension package rather than here, and
[Building a vendor extension](vendor-extensions.md) covers the hooks. For design
discussion, open an issue that names the affected code, the behavior you expect,
and the guide page that documents it.
