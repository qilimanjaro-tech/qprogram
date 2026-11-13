# qprogram

QProgram is a hardware-agnostic Python DSL for pulse-level quantum programs: a builder, an AST, the `.qp` text format, a capability protocol platforms validate against, and the hooks vendor packages (qprogram-qblox, qprogram-qdac) extend. It is public, Apache-2.0, published on PyPI.

## Navigate by task
- Builder and AST: `src/qprogram/qprogram.py`; operations in `src/qprogram/operations/`, blocks in `src/qprogram/blocks/`, waveforms in `src/qprogram/waveforms/`, sweep sources in `src/qprogram/sweeps/`, fragments in `src/qprogram/fragments.py`.
- Variables and expressions: `src/qprogram/variable.py`. Buses and schemas: `src/qprogram/buses.py`.
- `.qp` format: writer, parser, registry and migrations in `src/qprogram/serialization/`; grammar `src/qprogram/grammar/qp.lark`; the normative spec is `docs/reference/qp-format.md`.
- Capability protocol and validation: `src/qprogram/protocol.py`, `src/qprogram/validation.py`, `src/qprogram/profiles.py`, `src/qprogram/explain.py`, `src/qprogram/optimization.py`.
- Execution and results: `src/qprogram/platform.py`, the reference executor `src/qprogram/executor.py`, `src/qprogram/result.py`, plotting in `src/qprogram/plotting/`.
- Vendor extension hooks: `src/qprogram/vendor.py`, explained in `docs/developer/vendor-extensions.md`. Language server: `src/qprogram/lsp.py`.
- Which files a change touches: the "What goes where" table in `docs/developer/contributing.md`. Which tests it needs: `docs/developer/testing.md`.
- Tests: `tests/`, one file per module; shared fixtures in `tests/conftest.py`, an in-tree vendor in `tests/_dummy_vendor.py`.
- Before writing or reviewing code or tests: read `CODING_STANDARDS.md` and `REVIEW.md`.
- Domain vocabulary: `GLOSSARY.md`; name code, tests and PRs with its terms. Decisions: `docs/adr/`. Tracker and triage: `docs/agents/`.
- Project knowledge base: TODO(human): link the domain wiki.

## Commands
- Setup: `uv sync --all-extras --group docs`. Every dependency is public.
- Focused test: `uv run pytest tests/test_round_trip.py`, or `uv run pytest -k set_phase`.
- Docs preview: `uv run --all-extras --group docs zensical serve`.
- Changelog fragment: `uv run towncrier create <PR number>.<type>.md --content "..."`; the types are in `[tool.towncrier]` in `pyproject.toml`.
- **Verify (run before calling work done):** `uv run ruff check . && uv run ruff format --check . && uv run ty check --python-version 3.11 && uv run ty check --python-version 3.12 && uv run ty check --python-version 3.13 && uv run ty check --python-version 3.14 && uv run pytest --cov=qprogram && uv run --all-extras --group docs zensical build --strict`
  (the gates of the Code Quality, Tests and Documentation workflows; the waveform reference samples run inside pytest).

## Boundaries
- Validate with synthetic inputs and offline tests; hardware, lab databases and production runcards are in scope only when the task names them.
- Report every gate you could not run and why.
- Keep credentials, runcards with real addresses and measurement data out of the repository and out of agent instructions.

## Critical invariants
- The repository is public: keep internal links, hostnames, customer names and credentials out of code, docs, commits and PRs, because anyone can read them.
- The core reaches no instrument and depends only on `numpy` and `xarray`. Anything heavier goes behind an extra (`viz`, `lsp`), and an operation, waveform or sweep source that only one vendor's hardware runs goes in that vendor's package, because the core has to install next to whatever a lab already runs.
- The minor version is the `.qp` and `.wfl` format version (`#!QProgram 0.3`). After a release `main` moves to the next patch as a dev version (`0.3.1.dev0`), and the minor moves only in the PR that changes the format, because files written from `main` must load in the release that follows.
- A change that stops an existing `.qp` file from loading registers a migration in `src/qprogram/serialization/migrations.py` under the version that ships it (or, when no rewrite keeps the old meaning, documents the break in `docs/reference/qp-format.md`), because saved programs must keep loading.
- The reference executor (`qp.simulate`) defines the language's semantics and its result shapes are a contract vendor compilers test against, so changing them is a public API change.
- A user-visible change updates the docs in the same PR, and a new page goes into the nav in `zensical.toml`, because the site is the user documentation.

<important if="you touch numeric code, units, golden values or tolerances">
- Units: durations are integer ns, frequencies Hz, phases rad; gain and offset are dimensionless. The core has no QDAC operations. The full convention and its exceptions are in CODING_STANDARDS.md additions.
- Every numeric change ships with the before-and-after evidence REVIEW.md asks for.
</important>

## Branches and PRs
Decision 2 (Qilimanjaro Software branch conventions), as this repo works today:
- Branch from `main`. Work on a Linear ticket uses the ticket's branch (`qhc-1846-<topic>`) and the PR title `[QHC-1846] Name`, with the ticket linked. Other work uses a short descriptive branch (`format-migrations`) and links its GitHub issue.
- One concept per PR, squash-merged, so the PR title becomes the commit subject on `main`.
- A user-visible change adds a towncrier fragment `changelog/<PR number>.<type>.md`; never edit `CHANGELOG.md` by hand outside a release.
- A release goes out from its own PR on `release-X.Y.Z` titled `Release X.Y.Z`; the GitHub Release on its merge commit carries the bare tag `X.Y.Z` and triggers `.github/workflows/publish.yml`. Steps: "Releasing" in `docs/developer/contributing.md`.

## Agent workflow
- Gardener: TODO(human) (owns the environment-change list after each retro)
- Trust rung: 1 (Pair), proposed: the human drives and reads every diff live. The waveform shapes are calibration baselines for the vendor packages.
- Scratch output (research, specs, evidence) goes to `.agents/artifacts/`, which is gitignored.

## Agent skills

### Issue tracker

Mixed, to confirm: GitHub Issues is the public intake and backlog; Linear team QHC tracks the work QHC picks up (`QHC-####`). See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, mapped onto this repo's existing `question` and `wontfix` labels; three are still to be created. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Completion
Work is ready for review when the verify command passes and its output is attached to the PR. The verify command runs the gates of the workflows that run on pull requests.
