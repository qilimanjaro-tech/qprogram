# qprogram review contract

Shared core v0.2 (Qilimanjaro Software), a draft until the software leads agree it. Copied verbatim, followed by qprogram additions. Applies to humans and agents. AI review is advisory: a qualified human reads every line of the same revision before merge.

## Findings
- Each finding gives `path:line` and quotes the line, then the trigger, the consequence, the evidence and a concrete next step. No citation, no finding.
- Separate confirmed defects from questions and missing evidence. Recheck earlier findings on the current revision and state the inspected scope.
- Review on two axes: standards (`CODING_STANDARDS.md`, citing the section) and spec (the linked ticket, PRD or TRD: missing, extra, wrong; quote the spec line).
- Green CI is evidence, not a verdict: say what it covers and what it misses.

## Severity
Lead with the highest.
- **blocker**: wrong results, data loss, a security hole, or a broken invariant from AGENTS.md. Fix before merge.
- **major**: a likely bug, or a design problem the next change will trip over. Fix, or justify in the PR.
- **minor**: worth fixing, not blocking.
- **nit**: taste. At most 3 per review; drop the rest.

## qprogram additions
- **Format compatibility.** A change to the writer, parser or grammar keeps `dumps` after `loads` after `dumps` byte-stable (`tests/test_round_trip.py`, `tests/test_round_trip_property.py`) and keeps `src/qprogram/grammar/qp.lark` in step with the parser. A change that stops an existing `.qp` or `.wfl` file from loading needs a migration in `src/qprogram/serialization/migrations.py`; without one it is a blocker, unless no rewrite keeps the old meaning and the break is documented in `docs/reference/qp-format.md`, as the 0.3 waveform change was. A minor-version bump without a format change is a blocker too.
- **Where it lands.** Check the change against the "What goes where" table in `docs/developer/contributing.md`: a new operation, waveform or sweep source is registered in `src/qprogram/serialization/_specs.py` or `src/qprogram/serialization/registry.py` and reports its capability tokens. An operation only one vendor's hardware runs belongs in that vendor's package, not here.
- **Dependencies.** A new runtime dependency is a blocker unless the PR links the discussion that agreed it; optional ones go behind an extra.
- **Public API and docs.** A public API change goes through a deprecation or links an accepted decision; reference-executor result shapes count as public API. User-visible behavior changes the guide page in the same PR; a new public symbol gets its `::: qprogram.<Name>` line in `docs/reference/api-qprogram.md`.
- **Changelog.** A user-visible change carries a fragment in `changelog/`; internal refactors, test-only changes and docs corrections need none.
- **Public repository.** No internal links, hostnames, customer names or credentials in the diff, the PR text or the commit messages.
- **Units.** Values follow the convention in CODING_STANDARDS.md additions; check every conversion where a value leaves this repo (compiler, driver, instrument).
- **Parity.** Any numeric change ships a parity report: before and after on `tests/data/waveform_samples.json` (`test_envelope_matches_reference_samples` in `tests/test_waveforms.py`), within the tolerance that test sets (`atol=1e-12`), both named in the PR. Golden values and tolerances change only with sign-off from TODO(human): name who owns the reference samples, named in the PR. The reference set pins `Gaussian`, `IQDrag` (with its `GaussianDragCorrection` channel), `FlatTop` and `SuddenNetZero`; for other numeric code, ship before-and-after output on a synthetic input that reaches it (the envelope, or the `qp.simulate` result).
