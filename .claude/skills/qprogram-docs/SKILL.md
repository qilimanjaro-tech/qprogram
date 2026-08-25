---
name: qprogram-docs
description: Write or edit documentation in this repo. Use when touching docs/, the README, or a public docstring, and whenever a change to the DSL needs its documentation brought along. Carries the house writing style, the conventions of each documentation surface, the change checklist, and the docs checker.
---

# Writing QProgram documentation

Documentation here is held to the same standard as the code. It has to be
correct first and readable second. A page that reads well and describes
behaviour the library does not have is worse than no page at all.

## Check the claim before you write it

The code is the authority on what QProgram does, and the tests pin the parts
that are settled. Read the implementation before writing about its details, and
verify names and signatures against the source rather than from memory. The
checker described below catches attributes that no longer exist, but only
inside Python fences, and only for the `qprogram` module and `QProgram`
instances.

Where a page and the code disagree and you cannot tell which is wrong, say so
rather than picking one. A confident sentence about the wrong behaviour is the
expensive kind of documentation bug.

## The change checklist

A change to the DSL is not finished when the code is. These move together:

1. The code.
2. The tests, including the round-trip property tests when serialization is
   involved.
3. The grammar in `src/qprogram/grammar/qp.lark` when the wire format changes.
   It is normative, and `tests/test_grammar.py` fails when it drifts from the
   production parser.
4. The docs under `docs/`. Anything user-visible needs an entry in the matching
   guide page, and new operations, waveforms, and sweep sources also need a
   mention in `docs/reference/qp-format.md`.
5. The docstrings of anything that appears in `docs/reference/api-qprogram.md`,
   since that page is generated from them.

If you are asked to do only part of this, do that part and say plainly which
steps are still outstanding.

## Where each kind of documentation lives

| Surface | What it is | Read before editing |
|---|---|---|
| `docs/` | The user-facing site, built with zensical and deployed by CI. | [surfaces.md](references/surfaces.md#the-documentation-site) |
| `README.md` | The front page and the PyPI long description. | [surfaces.md](references/surfaces.md#the-readme) |
| Docstrings in `src/` | Google style, rendered into the API reference by mkdocstrings. | [surfaces.md](references/surfaces.md#docstrings) |

## House style

The full rules, with the reasoning and the word list, are in
[style.md](references/style.md). Read that file before writing prose. The short
version:

Write the way an experienced engineer writes when explaining something to a
colleague. Professional, direct, specific. Say what a thing does, why it exists,
and when to use it, without claiming it is powerful or elegant. Do not use em
dashes; a comma, a colon, parentheses, or two sentences will do. Do not use the
vocabulary that marks generated text, and do not open or close a section with
filler.

Prefer connected paragraphs. Use a bulleted list when the content is a genuine
enumeration such as parameters, options, or independent items, and a numbered
list only when the order matters. Do not turn every sentence into a bullet.

Keep headings descriptive, in sentence case, and do not stack three levels of
heading over four sentences of text. Do not say the same thing in an
introduction, the body, and a summary.

Avoid comparative claims unless the comparison is on the page. "Faster" needs a
baseline, "simpler" needs the thing it is simpler than.

Before handing anything back, reread it and cut what sounds generic, promotional,
or over-polished. That pass is part of the work, not an optional extra.

## Running the checker

`scripts/check_docs.py` checks three things: that Python and `.qp` examples
still parse and still name symbols that exist, that internal links and the site
nav resolve, and that prose follows the style rules. It needs the project's
environment for the code group:

```bash
uv run python .claude/skills/qprogram-docs/scripts/check_docs.py docs
```

Narrow it to the files you touched, which is the normal case:

```bash
uv run python .claude/skills/qprogram-docs/scripts/check_docs.py docs/guide/waveforms.md
```

Broken examples, broken links, and broken nav entries are reported as errors and
set the exit status. Style findings are warnings; `--strict` promotes them to
errors, which is what a gate would use. The one check `--strict` leaves alone is
`qp-vendor`, which fires when a `.qp` example needs a vendor extension that is
not installed here. That describes the environment, not the page. `--only
style`, `--only code`, and `--only links` run one group, and `--json` gives
machine-readable output.

A snippet that is deliberately not valid Python, such as a before-and-after
column layout, is exempted with an HTML comment on the line above the fence:

```markdown
<!-- check: skip -->
```

Snippets that use `...` for an elision or `<placeholder>` for a name are detected
as illustrative and skipped without a marker.

The checker is not a substitute for building the site. CI runs
`zensical build --strict`, which fails on an unresolved mkdocstrings
cross-reference, something the checker cannot see. Build locally before opening
a PR that touches docstrings or the reference pages.
