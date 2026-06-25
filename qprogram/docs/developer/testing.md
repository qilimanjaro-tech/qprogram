# Testing

The `qprogram` package ships an extensive unit test suite. The numbers below
are current as of writing:

| Package           | Tests | Coverage |
|-------------------|-------|----------|
| `qprogram`        | 966   | 98.7%    |

The whole suite runs in well under a second.

## Running tests

```bash
cd qprogram
uv sync
uv run pytest
uv run pytest --cov                          # with coverage
uv run pytest tests/test_round_trip.py -v     # one file
uv run pytest -k "set_phase"                  # by keyword
```

## What is covered

The suite is function-style (no test classes). The structure mirrors the
source layout.

- `test_buses.py`, `test_variable.py`, `test_waveforms.py`,
  `test_operations.py`, `test_blocks.py`: unit tests per module.
- `test_qprogram.py`: the builder methods on `QProgram`, validation, and
  the context managers.
- `test_result.py`: `MeasurementHandle`, `QProgramResult`.
- `test_writer.py`, `test_parser.py`, `test_specs.py`, `test_registry.py`:
  serialization round-trip per feature.
- `test_round_trip.py`: end-to-end byte-stability across every feature.
- `test_reserved.py`, `test_errors.py`, `test_structural.py`,
  `test_coverage_gaps.py`: hardening tests.

## Fixtures

Common fixtures live in `tests/conftest.py`. The most useful ones:

| Fixture                  | What it returns                                                       |
|--------------------------|-----------------------------------------------------------------------|
| `transmon_schema`        | `BusSchema.transmon()`                                                |
| `flux_tunable_schema`    | `BusSchema.flux_tunable_transmon()`                                   |
| `fluxonium_schema`       | `BusSchema.fluxonium()`                                               |
| `coupled_schema`         | `BusSchema.flux_tunable_transmon_coupled()`                           |
| `custom_naming_schema`   | A transmon schema with a custom `BusNaming` pattern.                   |
| `dynamic_schema`         | A `BusSchema()` built with `add_element` (no presets).                 |
| `empty_program`          | `QProgram()` with nothing in it.                                       |
| `schema_program`         | `QProgram(schema=transmon_schema)`.                                    |
| `freq_var`, `gain_var`   | Single `Variable` instances.                                           |
| `square_pulse`, `gaussian_pulse`, `iq_pulse`, `iq_pair_pulse` | Stock waveforms. |
| `rabi_program`           | A small but feature-rich program for round-trip tests.                 |
| `array_values`           | A numpy array used in `loop` fixtures.                                 |

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

`src/qprogram/platform.py` is also omitted, since that file is declarative
ABC scaffolding only.

The remaining uncovered lines are defensive paths in the parser (malformed
inputs that no caller currently produces) and one or two narrow conditional
branches. They are tracked in `tests/test_coverage_gaps.py` if you want to
push the number higher.

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

## What to write a test for

| Change                                          | Tests to add                                                      |
|-------------------------------------------------|-------------------------------------------------------------------|
| New operation                                   | Construction, introspection, `QProgram` method, round-trip.       |
| New waveform                                    | `envelope` / `get_I` / `get_Q`, registration, round-trip.         |
| New schema preset                               | Bus paths, validation, round-trip.                                |
| Bug fix                                         | A regression test that fails on the old behaviour.                |
| Vendor extension                                | In the vendor's own package; mirror the pattern from this suite.  |

## Speed

The suite is fast on purpose. If your additions push it past a couple of
seconds, profile before defending the slowdown. `pytest --durations=10`
prints the ten slowest tests.

## CI

There is no CI configuration committed yet. Locally, the contract is:

- `uv run ruff check .` and `uv run ruff format --check .` pass.
- `uv run pytest` passes.
- `uv run pytest --cov` shows no regression in coverage on the touched
  files.
