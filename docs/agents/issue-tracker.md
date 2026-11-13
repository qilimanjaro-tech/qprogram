# Issue tracker: GitHub Issues and Linear team QHC

TODO(human): confirm the tracker with the maintainers. This file records what the repository shows today; it is not yet an agreed rule.

## What the repository shows

- **GitHub Issues** is the public intake and backlog. `.github/ISSUE_TEMPLATE/` holds three forms, titled `[Bug]: `, `[Feature]: ` and `[Task]: `, which set the GitHub issue type. Open bugs, features and tasks live there, labelled by area (`serialization`, `waveforms`, `vendor` and the rest of the purple area labels).
- **Linear, team QHC** tracks the work the QHC team picks up. Those PRs branch as `qhc-####-<topic>` and are titled `[QHC-####] Name`.
- Other PRs use short descriptive branch names and imperative titles.
- One open GitHub task, #46, also names a Linear team SDK ticket in its body, so SDK work may be mirrored in Linear too.

## Conventions

- **Read an issue:** `gh issue view <number> --json number,title,body,labels,comments`.
- **List issues:** `gh issue list --state open --json number,title,labels` with `--label` filters.
- **Read a Linear ticket:** use the Linear tools (for example the Linear MCP server's `get_issue`) and refer to it by identifier (`QHC-1846`).
- **Create, comment on, label or close** a GitHub issue or a Linear ticket only after the human has seen the title and body and said yes. The repository is public: an issue body carries no internal links, hostnames or customer names.
- **Branches and PRs:** see `## Branches and PRs` in `AGENTS.md`. A PR links its GitHub issue, its Linear ticket, or both.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Ask the human which tracker the work belongs to, until the maintainers confirm the split. Public bugs and feature requests default to a GitHub issue that follows the matching template in `.github/ISSUE_TEMPLATE/`; work the QHC team takes on goes to Linear team QHC.

## When a skill says "fetch the relevant ticket"

A `#<n>` reference is a GitHub issue or PR (`gh issue view <n>`, falling back to `gh pr view <n>`); a `QHC-####` reference is a Linear ticket.
