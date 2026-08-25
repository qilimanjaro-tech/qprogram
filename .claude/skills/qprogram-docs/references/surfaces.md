# Documentation surfaces

Three places in this repo hold prose, and they do not share conventions. Work
out which one you are editing before you start.

## The documentation site

`docs/` is the user-facing site. It is built with **zensical**, not mkdocs,
configured in `zensical.toml`:

```bash
uv sync --group docs --all-extras
uv run zensical serve      # or: uv run zensical build --strict
```

The `--all-extras` matters: mkdocstrings imports the package to render the API
reference, so the optional extras have to be installed, not just the docs
tooling. `.github/workflows/docs.yml` does the same and then deploys to GitHub
Pages from `main`.

The tree is `index.md`, `getting-started.md`, then four sections: `guide/` for
task-oriented explanation, `examples/` for complete worked experiments,
`reference/` for the wire format, reserved keywords, errors, and the generated
API, and `developer/` for the extension points and internals.

Where a change lands depends on who needs it. User-visible behaviour goes in the
matching `guide/` page. Anything that changes the `.qp` wire format also goes in
`reference/qp-format.md`. New extension points go in `developer/`.

The nav in `zensical.toml` is explicit and is the only way a page becomes
reachable. Adding a file to `docs/` does not put it in the site. The checker's
link group reports pages that exist on disk but are absent from the nav, and nav
entries that point at files that do not exist.

`reference/api-qprogram.md` is generated. It is a list of mkdocstrings
directives, one per public symbol:

```markdown
::: qprogram.QProgram
```

Add a directive for a new public symbol rather than hand-writing its signature.
The handler is configured in `zensical.toml` to read Google-style docstrings
from `src/`, show the source, order members as they appear in the file, and hide
private names apart from `__init__`.

CI builds with `zensical build --strict`, which turns a warning into a failure.
The warning that matters is an unresolved cross-reference, which would otherwise
ship as a dead link on the published page.

The site config enables admonitions, but no page uses them. Do not introduce
them for a single page; a short paragraph carries a caveat as well as a coloured
box does, and the tree stays consistent.

## The README

`README.md` is the repository front page and, through `readme` in
`pyproject.toml`, the PyPI long description. Relative links to files in the repo
do not resolve on PyPI, so link to the published docs site or to absolute URLs
for anything a package page needs to reach.

Keep it to what someone needs before they decide to use the library: what it is,
how to install it, one worked example, and where to read more. Everything else
belongs in `docs/`, and duplicating a guide page here means two copies to keep
true.

## Docstrings

Google convention, enforced by ruff's pydocstyle rules. `D1` is in the ignore
list, so a symbol may have no docstring at all, but a docstring that exists must
be well formed. `ruff format` reformats code inside docstrings, so any example
in a docstring has to be valid Python.

Docstrings are rendered into the API reference page, which makes them part of
the site. Write them for a reader who has the signature in front of them: what
the call does, what the arguments mean where the names do not already say it,
and why the behaviour is what it is where that is not obvious.

Cross-references use the mkdocstrings Markdown form, with the display text in
backticks and the target as a full dotted path:

```
[`Sweep`][qprogram.blocks.Sweep]
[`Play`][qprogram.operations.Play]
```

Sphinx roles (`` :class:`~qprogram.blocks.Sweep` ``) do not resolve and render
as literal text. They were converted across the package and should not come
back. An unresolved target fails the CI docs build rather than degrading
quietly, so build locally after editing docstrings.
