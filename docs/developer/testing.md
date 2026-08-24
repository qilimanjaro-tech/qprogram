# Testing

The `qprogram` package ships an extensive test suite: **1579 tests** covering
about 95% of `src/qprogram`, with branch tracking on. The whole suite runs in
well under ten seconds.

## Running tests

```bash
uv sync --all-extras
uv run pytest
uv run pytest --cov                           # with coverage
uv run pytest tests/test_round_trip.py -v     # one file
uv run pytest -k "set_phase"                  # by keyword
```

## What is covered

The suite is function-style (no test classes). The structure mirrors the
source layout.

- `test_buses.py`, `test_variable.py`, `test_waveforms.py`,
  `test_operations.py`, `test_blocks.py`, `test_sweeps.py`: unit tests per
  module.
- `test_qprogram.py`, `test_sweep_builder.py`, `test_conditional.py`: the
  builder methods on `QProgram`, the `sweep(var).from_*` builder, and the
  `if_` / `elif_` / `else_` chain.
- `test_fragments.py`, `test_fragments_serialization.py`: fragment
  definition, binding, expansion, and its `.qp` form.
- `test_result.py`: `MeasurementHandle`, `MeasurementResult`,
  `QProgramResult`.
- `test_writer.py`, `test_parser.py`, `test_specs.py`, `test_registry.py`:
  serialization per feature, plus the registries that drive it.
- `test_round_trip.py`: end-to-end byte stability across every feature.
- `test_round_trip_property.py`: hypothesis-generated programs, asserting
  both structural equality and byte stability after a round trip.
- `test_grammar.py`: the cross-check that keeps `grammar/qp.lark` and the
  production parser from drifting — the writer corpus and the hypothesis
  strategies must parse under the grammar, and syntactic negatives must
  fail under both.
- `test_protocol.py`, `test_required_capabilities.py`, `test_validation.py`,
  `test_paths.py`, `test_explain.py`: the capability protocol, the
  per-node token declarations, the validator and classifier, diagnostic
  paths, and the plan renderer.
- `test_executor.py`: the reference executor's result shapes and semantics.
- `test_vendor.py`, `test_vendor_discovery.py`: the vendor namespace and
  entry-point activation, driven by `tests/_dummy_vendor.py`.
- `test_waveform_library.py`: per-bus waveform resolution and the `.wfl`
  format.
- `test_lsp.py`: `check_text()` and the `check` / `explain` / `serve` CLI
  modes the editor integration drives.
- `test_reserved.py`, `test_errors.py`, `test_structural.py`,
  `test_coverage_gaps.py`: hardening tests.

## Fixtures

Common fixtures live in `tests/conftest.py`. The most useful ones:

| Fixture                  | What it returns                                                       |
|--------------------------|-----------------------------------------------------------------------|
| `transmon_schema`        | `BusSchema.transmon()`                                                |
| `flux_tunable_schema`    | `BusSchema.flux_tunable_transmon()`                                   |
| `fluxonium_schema`       | `BusSchema.fluxonium()`                                               |
| `coupled_schema`         | `BusSchema.transmon_coupled()`                                        |
| `custom_naming_schema`   | A transmon schema with a custom `BusNaming` pattern.                   |
| `dynamic_schema`         | A `BusSchema()` built with `add_element` (no presets).                 |
| `empty_program`          | `QProgram(label="empty")` — no schema, empty body.                     |
| `schema_program`         | `QProgram(schema=transmon_schema)`.                                    |
| `freq_var`, `gain_var`   | Single `Variable` instances.                                           |
| `square_pulse`, `gaussian_pulse`, `iq_pulse`, `iq_pair_pulse` | Stock waveforms. |
| `rabi_program`           | A small but feature-rich program for round-trip tests.                 |
| `array_values`           | A numpy array suitable for a `Values` sweep.                           |
| `dummy_vendor`           | Activates the in-tree vendor extension for one test, then tears it down. |

`tests/_dummy_vendor.py` is a complete vendor extension: namespace, mixin,
pre-combined `QProgram`, operations, and a profile bundle. Its
`activate()` / `deactivate()` pair is what keeps the global registries clean
between tests, so no test should rely on import side effects to install it —
use the `dummy_vendor` fixture.

## Coverage configuration

The package uses `coverage.py` with branch tracking enabled. The exclusion
list in `pyproject.toml` covers the conventional cases:

```toml
[tool.coverage.report]
exclude_also = [
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod",
    "pragma: no cover",
    "raise NotImplementedError",
    "\\.\\.\\.",
]
```

`src/qprogram/platform.py` is omitted from the report on top of that. It is
not purely abstract, though: `get_bus_schema`, `get_buses`, `get_parameters`,
`get_global_parameters`, `capabilities`, and `execute` are abstract, but
`validate`, `plan`, and `explain` carry concrete bodies that delegate to
`qprogram.validation.validate` and `qprogram.explain`, and `stream` raises
`NotImplementedError`. `ReferencePlatform` inherits all four, and
`tests/test_executor.py` calls the inherited `explain`. So the omission hides
real, partly exercised code — read that file's absence from the report as a
gap, not as proof there is nothing to cover.

What is left uncovered is mostly unreachable from a normal call: guards the
public API makes impossible, the language-server handler bodies in `lsp.py`
(they need a live editor client), and the `lark`-backed reference parser in
`grammar/`, which is only built when that optional dependency is installed.
`tests/test_coverage_gaps.py` collects the narrow cases worth pinning down,
and is the place to add more.

## Style and idioms

- **Function-style tests, not class-style.** Even when several tests share
  setup, prefer fixtures over `TestX:` classes.
- **`@pytest.mark.parametrize` is encouraged** for the same test against a
  list of inputs. Use `pytest.param(..., id="...")` to keep diagnostic
  output readable.
- **`pytest-mock`** is available but rarely needed; most tests construct
  real objects and assert on them.
- **`filterwarnings = ["error"]`** is set, so any new warning fails the
  test that emitted it.
- **`xfail_strict = true`** keeps `@pytest.mark.xfail` honest.
- **`--strict-markers` and `--strict-config`** are in `addopts`, so a typo
  in a marker or a config key is an error rather than a silent no-op.

## What to write a test for

| Change                                          | Tests to add                                                      |
|-------------------------------------------------|-------------------------------------------------------------------|
| New operation                                   | Construction, introspection, `QProgram` method, `required_capabilities()`, round-trip. |
| New waveform                                    | `envelope` / `get_I` / `get_Q`, registration, round-trip.         |
| New sweep source                                | `length()`, `values()`, `KIND`, tokens, round-trip.               |
| New schema preset                               | Bus paths, validation, round-trip.                                |
| Bug fix                                         | A regression test that pins the correct behavior.                 |
| Vendor extension                                | In the vendor's own package; mirror the pattern from this suite.  |

## Speed

The suite is fast on purpose. The slowest tests are the hypothesis-driven
round-trip properties and the `lsp` CLI tests that spawn a subprocess, each
still under a second. If your additions push the total past a few seconds,
profile before defending the slowdown. `pytest --durations=10` prints the
ten slowest tests.

## CI

Three workflows under `.github/workflows/` gate a pull request.

- **`tests.yml`** runs `pytest` on every supported Python version (a pull
  request runs the oldest and newest, a push to `main` fills in the middle),
  and uploads coverage and test results to Codecov from the 3.13 job.
- **`code_quality.yml`** has two independent jobs and an aggregating gate.
  `lint` runs `ruff check` and `ruff format --diff` once, on Python 3.13.
  `types` loops `ty check --python-version <v>` over 3.11, 3.12, 3.13, and
  3.14 — the type checker resolves the standard library for the version it is
  told to assume, so one environment covers all four. The third job fails the
  workflow if either of the other two does.
- **`docs.yml`** builds this site with `zensical build`, and deploys it to
  GitHub Pages on a push to `main`.

Locally, the same contract is:

- `uv run ruff check .` and `uv run ruff format --check .` pass.
- `uv run ty check` passes.
- `uv run pytest` passes.
- `uv run pytest --cov` shows no regression in coverage on the touched
  files.
