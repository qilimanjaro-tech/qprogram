# qprogram coding standards

Shared core v0.2 (Qilimanjaro Software), a draft until the software leads agree it. Copied verbatim, followed by qprogram additions. Mechanical rules live in the tools configured in `pyproject.toml` and in CI; this file holds the judgement they cannot make. The review contract is `REVIEW.md`; findings cite the section they apply.

## Tests

Tests are behaviour tests: they drive the public interface at an agreed seam and assert what a caller observes.

- **Seams.** Test at the seams listed in the additions below. A new seam is named in the PR description before tests target it.
- **Observable outcomes.** Assert returned values, raised errors, emitted programs and waveforms, and results read back through the public reader.
- **Independent expected values.** Take expected values from a literal, a worked example, the specification, a published reference or a hardware-validated fixture, never recomputed the way the code computes them.
- **Boundary mocks.** Mock instrument drivers and hardware execution, network, databases, the file system when needed, time and randomness. The package's own classes and functions run for real.
- **Public reach.** When a behaviour is visible only through a `_private` name, add a public accessor or move the test to the seam above.
- **Regressions.** A bug fix starts with a test that fails on the bug.
- **Coverage is evidence.** A line counts as covered when a behaviour test exercises it. Hardware-only code goes in the coverage `omit` list with a reason.

## Code

- **Design.** Prefer deep modules: a small interface over a lot of behaviour. Name things with the terms in `GLOSSARY.md`; a new concept gets a glossary entry first.
- **Units.** Physical quantities carry their unit in the name, a type alias or the docstring (Hz, ns, V, rad, dBm), fixtures included, in the convention the additions state.
- **Suppressions.** Every suppression (`# noqa`, `# type: ignore`, `# ty: ignore`, `# pragma: no cover`, a coverage `omit`) names its rule code and a one-line reason.
- **Comments.** Explain why. A comment that excuses a workaround becomes a type, a lint or a test.
- **Dependencies.** A dependency on another Qilimanjaro package names the released version that carries the needed behaviour.
- **Compatibility.** Public API changes go through a deprecation release unless the PR links an accepted decision to break compatibility.

## Change hygiene

- User-facing changes add a towncrier fragment in `changes/`, in repos that use towncrier.
- Interface or architecture decisions link their decision record; proposals stay labelled as proposals until accepted.

## qprogram additions

- **Seams:** the public `qprogram` API, imported as `import qprogram as qp`: the builder methods on `QProgram`; `qp.dumps` and `qp.loads` (round trips in `tests/test_round_trip.py`, generated programs in `tests/test_round_trip_property.py`); `qp.validate` and `qp.explain` against a `PlatformCapabilities`; `qp.simulate` and the results it returns; a waveform's `envelope()`. Vendor hooks are tested through `tests/_dummy_vendor.py` with the `dummy_vendor` fixture. `docs/developer/testing.md` lists the tests each kind of change needs.
- **Boundary mocks:** none beyond the core: the suite builds real objects and needs no hardware. `monkeypatch` replaces the profile registry (`tests/test_protocol.py`) and the entry-point lookup (`tests/test_vendor_discovery.py`). Vendor, profile and registry state is process-wide, so a test registers it through a fixture that tears it down, never through an import side effect.
- **Units:** durations and the waveform sample period (`resolution`) are integer ns (`Wait` in `src/qprogram/operations/wait.py`, `get_duration()` in `src/qprogram/waveforms/waveform.py`); frequencies are Hz (`src/qprogram/operations/set_frequency.py`); phases are rad (`src/qprogram/operations/set_phase.py`); gain and offset are dimensionless (`docs/guide/operations.md`). Sample times become seconds only inside an envelope that pairs them with a frequency in Hz (`src/qprogram/waveforms/cosine.py`). A `Variable`'s `units` is free-form metadata for result coordinates and plots (`src/qprogram/variable.py`), not a conversion; the examples label gain and amplitude sweeps `units="V"` (`docs/examples/rabi.md`), which disagrees with "dimensionless". The core has no QDAC operation; instrument units belong to the vendor packages. Waveform amplitude has no documented range. TODO(human): document the amplitude convention and settle the gain label.
- **Coverage:** no `fail_under` in `pyproject.toml`; Codecov tracks coverage from the Python 3.13 job in `.github/workflows/tests.yml`. TODO(human): agree a coverage floor.
- **Changelog:** this repo's towncrier directory is `changelog/`, not `changes/`; a fragment is named `<PR number>.<type>.md` (`[tool.towncrier]` in `pyproject.toml`).
- **Tooling:** a ruff exemption goes into the `ignore` list in `pyproject.toml` by rule name with a comment giving the reason, because the config is where the reasons are read. Tests are functions with fixtures and `pytest.mark.parametrize`, not test classes, because a fixture carries shared setup across files.
