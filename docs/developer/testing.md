# Testing

`tests/` holds 1582 tests in 34 files. They cover 95.5% of the 5214 statements
and 1584 branches under `src/qprogram`, run in about four seconds without
coverage and six with it, and need no hardware, no network, and no files on
disk beyond what they write to `tmp_path`. The suite is also the specification
for the parts of the library that have no other one: the serialization
round-trip, the capability protocol, and the reference executor.

## Running tests

```bash
uv sync --all-extras
uv run pytest
uv run pytest --cov=qprogram                  # with coverage
uv run pytest tests/test_round_trip.py -v     # one file
uv run pytest -k "set_phase"                  # by keyword
uv run pytest --durations=10                  # the ten slowest tests
```

`uv sync --all-extras` is enough on its own, because the `dev` dependency group
installs by default and carries pytest, `pytest-cov`, `pytest-mock`,
`hypothesis`, and `lark`. `testpaths = ["tests"]` means a bare `pytest`
collects that directory and nothing else, and
`addopts = ["-ra", "--strict-markers", "--strict-config"]` applies on every
run. `minversion = "9.0"` makes an older pytest refuse to start rather than
fail somewhere confusing.

`tests/` has no `__init__.py`, so pytest prepends the directory to `sys.path`.
That is what lets `tests/test_grammar.py` write
`from test_round_trip_property import fragment_programs, programs` and lets
every file that needs the in-tree vendor write `import _dummy_vendor`.

## What is covered

The suite is function-style, with no test classes anywhere, and its file layout
mirrors the source layout.

| Files | Tests | What they pin |
|---|---|---|
| `test_buses.py`, `test_variable.py`, `test_waveforms.py`, `test_operations.py`, `test_blocks.py`, `test_sweeps.py` | 510 | One module each: schemas and the `BusRef`s they produce, symbolic expressions, waveform shapes, operation leaves, block containers, sweep sources. |
| `test_qprogram.py`, `test_sweep_builder.py`, `test_conditional.py` | 187 | The builder surface: the methods on `QProgram`, the `sweep(var).from_*` builder, and the `if_` / `elif_` / `else_` chain. |
| `test_writer.py`, `test_parser.py`, `test_specs.py`, `test_registry.py` | 306 | Serialization feature by feature, plus the registries the writer and parser dispatch through. |
| `test_round_trip.py` | 36 | `dumps` to `loads` to `dumps` byte stability across feature combinations. |
| `test_round_trip_property.py` | 5 | The same guarantee over generated programs. |
| `test_grammar.py` | 24 | `src/qprogram/grammar/qp.lark` against the production parser, in both directions. |
| `test_protocol.py`, `test_required_capabilities.py`, `test_validation.py`, `test_paths.py`, `test_explain.py` | 141 | Capability tokens and profiles, the per-node token declarations, the validator and domain classifier, diagnostic paths, and the plan renderer. |
| `test_reserved.py`, `test_errors.py`, `test_structural.py`, `test_coverage_gaps.py` | 179 | Reserved keywords, the error hierarchy, the `ast_eq` / `ast_hash` helpers, and narrow paths with no other home. |
| `test_fragments.py`, `test_fragments_serialization.py` | 72 | Fragment definition, argument binding, expansion, and the `.qp` form. |
| `test_executor.py` | 32 | The reference executor's semantics and result shapes. |
| `test_result.py` | 32 | `MeasurementHandle`, `MeasurementResult`, and `QProgramResult`. |
| `test_vendor.py`, `test_vendor_discovery.py` | 24 | The vendor namespace and entry-point activation, driven by `tests/_dummy_vendor.py`. |
| `test_waveform_library.py` | 17 | Per-bus waveform resolution and the `.wfl` text format. |
| `test_lsp.py` | 14 | `check_text()`, the `check` and `explain` CLI modes, and `create_server()`, which builds the server the `serve` mode starts; the stdio loop itself is marked `pragma: no cover`. |
| `test_docstring_style.py` | 3 | That no module under `src/` carries a reStructuredText cross-reference role, and that no Markdown cross-reference lost its target. |

Two files are property-based. `tests/test_round_trip_property.py` builds
programs with hypothesis and asserts both structural equality and byte
stability after a round trip: `test_round_trip_structural_equality` and
`test_round_trip_byte_stability` run 60 examples each,
`test_fragment_round_trip_property` runs 40, and
`test_round_trip_long_sweeps_property` runs 25 over `Values` arrays of 51 to
200 floats, comparing the reloaded array element by element rather than through
structural equality. The strategies are adversarial about the fragile spots:
quotes, backslashes, `#`, and newlines in metadata text, path-shaped raw bus
strings such as `q[0].drive` that must not be promoted to bus references on
reload, ints against floats, and deep block nesting. Every generator sets
`deadline=None`, since a slow first example otherwise fails the test on a
loaded machine.

`tests/test_grammar.py` imports `programs()` and `fragment_programs()` from
that file and reruns them, 40 and 25 examples, through the reference Lark
parser. It also keeps a hand-built corpus covering each feature family on the
positive side and a curated corpus of syntactically malformed inputs on the
negative side, which must be rejected by both the grammar and the production
parser. Semantic errors are out of the grammar's scope: it over-approximates
unknown operations, duplicate variables, and unresolvable bus paths as valid
syntax, and the parser rejects them after parsing.

## Fixtures

Shared fixtures live in `tests/conftest.py`.

| Fixture | What it returns |
|---|---|
| `transmon_schema` | `BusSchema.transmon()`, one element `q` with a drive and a readout. |
| `flux_tunable_schema` | `BusSchema.flux_tunable_transmon()`, adding a flux bus. |
| `fluxonium_schema` | `BusSchema.fluxonium()`, with `flux_x` and `flux_z`. |
| `coupled_schema` | `BusSchema.transmon_coupled()`, which adds a `c` element with a flux bus. |
| `custom_naming_schema` | `BusSchema.transmon(naming=BusNaming("{kind}_{element}{index}_bus"))`. |
| `dynamic_schema` | A bare `BusSchema()` with one `add_element("q", ...)` call, no preset. |
| `empty_program` | `QProgram(label="empty")`, no schema and an empty body. |
| `schema_program` | `QProgram(label="schema_program", schema=transmon_schema)`. |
| `freq_var`, `gain_var` | `Variable("freq")` and `Variable("gain")`, attached to no program. |
| `square_pulse`, `gaussian_pulse`, `iq_pulse`, `iq_pair_pulse` | `Square(0.5, 100)`, `Gaussian(0.5, 40, 8)`, `IQDrag(0.5, 40, 8, 0.1)`, and an `IQPair` of two squares. |
| `rabi_program` | A Rabi-shaped program: `average(1000)` around `sweep(gain, Range(0.0, 1.0, 0.01))`, with `set_gain`, `play`, `sync`, and `measure` inside. |
| `array_values` | `np.array([0.0, 0.1, 0.3, 0.5, 0.7, 1.0])`, for a `Values` sweep. |
| `dummy_vendor` | Activates the in-tree vendor extension for one test, then tears it down. |

`rabi_program` is the one to reach for when a test needs a program with more
than one feature in it; `tests/test_writer.py` and `tests/test_parser.py` use
it as the payload for the file-level `save` and `load` cases. A test takes a
fixture as a parameter and needs no import beyond the package itself:

```python
import qprogram as qp


def test_round_trip_is_byte_stable(rabi_program):
    text = qp.dumps(rabi_program)
    assert qp.dumps(qp.loads(text)) == text
```

`tests/_dummy_vendor.py` is a complete vendor extension with no external
dependency: a namespace, a typed mixin, a pre-combined `DummyQProgram`, six
operations, and a profile bundle. Its `activate()` registers the namespace, the
vendor version, the operations, and the profile; its `deactivate()` pops every
one of those back out of the global registries. Because those registries are
process-wide, a test that installed the vendor through an import side effect
would leak it into every test that ran afterwards, so use the `dummy_vendor`
fixture and let the teardown run.

Four fixtures are local to the file that needs them rather than shared.
`isolated_registry` in `tests/test_protocol.py` monkeypatches
`qprogram.protocol.PROFILE_REGISTRY` to an empty dict so profile registration
tests do not pollute the real one. `vendor_source` in
`tests/test_sweep_builder.py` defines and registers an out-of-core
`SweepSource` subclass, the way a vendor extension would. `dummy_inactive` in
`tests/test_vendor_discovery.py` guarantees the dummy vendor is not registered
and clears the discovery cache, so entry-point discovery has to do the work
itself. `toy_program` in `tests/test_vendor.py` registers a throwaway `toy`
namespace on `QProgram` for one test and pops it again afterwards.

## Coverage configuration

Coverage measures the `qprogram` package with branch tracking on, prints
missing lines, and does not skip fully covered files:

```toml
[tool.coverage.run]
source = ["qprogram"]
branch = true

[tool.coverage.report]
exclude_also = [
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod",
    "pragma: no cover",
    "raise NotImplementedError",
    "\\.\\.\\.",
]
show_missing = true
skip_covered = false
precision = 1
```

There is no `omit` list and no `fail_under`, so every module appears in the
report and a local `pytest --cov` never fails on the number alone. Regressions
show up in Codecov instead, which the 3.13 job in `tests.yml` uploads to after
running:

```bash
pytest --cov=qprogram --cov-report=xml --cov-report=term-missing \
       --junitxml=junit.xml -o junit_family=legacy
```

What is left uncovered is worth reading as a list of gaps rather than as proof
there is nothing there. `src/qprogram/operations/call.py` sits at 52.4%,
because a `Call`'s `variables()`, `buses()`, `required_capabilities()`, and
`__repr__` are never reached: validation expands fragments before it walks, so
nothing in the normal path introspects a call site.
`src/qprogram/platform.py` sits at 58.8%; `ReferencePlatform` inherits the
default `validate`, `plan`, and `explain`, and `tests/test_executor.py`
exercises the inherited `explain`, but nothing calls the `validate` or `plan`
defaults or the `stream` method that raises `NotImplementedError`.
`src/qprogram/optimization.py` sits at 78.9%, missing the early return for a
program with no `Average` in its body and the recursion into conditional arms
and parallel loop headers. `src/qprogram/grammar/__init__.py` sits at 77.3%
only because `parse_text()` is never called: `tests/test_grammar.py` builds
`parser()` once and calls `.parse()` on it directly.
`src/qprogram/lsp.py` sits at 80.0% for two separate reasons: the `explain` CLI
path and the read from standard input run only inside the subprocess the CLI
tests spawn, which the coverage run does not follow, and the document-handler
bodies need a live editor client. The rest is single guards that the public API
makes unreachable. `tests/test_coverage_gaps.py` is where the narrow cases worth
pinning down collect, and it is the right home for any of the above.

## Test style

Tests are functions, not methods on a `TestX` class, even when several of them
share setup; a fixture carries shared setup further than a class does, since it
can be reused across files. Parametrization is the normal way to run one
assertion over a list of inputs; pass `pytest.param(..., id="...")` when the
generated id would be unreadable. Those are also the only marks in the suite:
40 `@pytest.mark.parametrize` decorators and ten `@pytest.mark.usefixtures`.
No custom marker exists, and `--strict-markers` with no `markers` table in
`pyproject.toml` means the first one has to be registered before it can be
used.

`pytest-mock` is installed but no test uses `mocker`; the suite builds real
objects and asserts on them, and reaches for pytest's own `monkeypatch` in the
two files that need to replace a module-level name: the profile registry in
`tests/test_protocol.py` and the entry-point lookup in
`tests/test_vendor_discovery.py`.
`filterwarnings = ["error"]` turns any warning a test emits into a failure of
that test, which is why a deprecation in a dependency shows up here first.
`xfail_strict = true` fails a test that was marked `xfail` and then started
passing. `--strict-config` makes an unknown key in
`[tool.pytest.ini_options]` an error rather than a silent no-op.

## What to write a test for

| Change | Tests to add |
|---|---|
| New operation | Construction and introspection, the `QProgram` method that builds it, `required_capabilities()`, and a round-trip case in `tests/test_round_trip.py`. |
| New waveform | `envelope()` and, for an IQ shape, `get_I()` / `get_Q()`; that it appears in the waveform registry; a round-trip case. |
| New sweep source | `length()`, `values()`, `KIND`, `TOKEN`, and a round-trip case. |
| New schema preset | The bus paths it produces, validation against a profile, and a round-trip case. |
| Wire-format change | A writer test, a parser test, and a negative case in `tests/test_grammar.py` if the grammar moved with it. |
| Bug fix | A regression test that fails before the fix and pins the corrected behavior. |
| Vendor extension | In the vendor's own package, mirroring `tests/_dummy_vendor.py`. |

## Speed

A full run takes 4.3 seconds and a coverage run 5.9 on a developer machine, and
the whole suite is worth running on every save. The slowest tests are the
`tests/test_lsp.py` CLI cases, which spawn a subprocess and take about 0.4
seconds each, followed at around 0.2 by the hypothesis properties and by
`test_create_server_builds_with_handlers`, which pays for the `pygls` import.
Nothing else reaches 0.1. `pytest --durations=10` prints the ranking, and it is
the first thing to look at if a change pushes the total past a few seconds.

## CI

Three workflows run on a pull request. All of them skip while the pull request
is a draft, and all of them cancel an in-progress run when a new commit
arrives on the same branch.

`tests.yml` runs the suite on a matrix of Python versions. A pull request gets
3.11 and 3.14, the oldest and newest supported, on the reasoning that nothing
under `src/` branches on the Python version; a push to `main` fills in 3.12 and
3.13. The 3.13 job is the one that carries coverage, and it also uploads
JUnit-format test results to Codecov.

`code_quality.yml` splits into two independent jobs. `lint` runs
`ruff check --output-format=github .` and `ruff format --diff .` on Python 3.13,
in an environment installed with
`uv sync --frozen --only-group dev --no-install-project`: ruff takes its whole
configuration from `pyproject.toml` and never imports the code, so the project
itself does not need to be there. `types` installs the project with all extras and loops
`ty check --python-version <v>` over 3.11, 3.12, 3.13, and 3.14, which works
from one environment because `ty` resolves the standard library for the version
it is told to assume.

`docs.yml` installs the project with the `docs` group and all extras, builds
the site with `zensical build --strict`, and uploads the result. The deploy job
runs only on a push to `main`. `--strict` turns a warning into a failure, and
the warning that matters is an unresolved mkdocstrings cross-reference, which
would otherwise ship as a dead link.

Both `tests.yml` and `code_quality.yml` end in an aggregating job that fails if
any job it depends on failed or was cancelled, so a single required check
covers the whole matrix.

Locally the same contract is four commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest --cov=qprogram
```

## Related pages

[Contributing](contributing.md) has the full pull-request workflow, including
the changelog fragment and the docs checker.
[Adding operations](adding-operations.md) and
[Adding waveforms](adding-waveforms.md) list the tests each of those changes
needs.
