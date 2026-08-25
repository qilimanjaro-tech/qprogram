# Changelog

Notable changes to QProgram, newest first. Each entry starts life as a news
fragment under `changelog.d/`, and `towncrier build` assembles the fragments
into a release section here. See
[Contributing](https://qilimanjaro-tech.github.io/qprogram/developer/contributing.html)
for how to add one.

<!-- towncrier release notes start -->

## 0.1.0 (2026-08-25)

### Added

- First release. QProgram is a hardware-agnostic Python DSL for pulse-level
  quantum experiments: a fluent builder that assembles a typed AST of
  operations, symbolic expressions, waveforms, and control flow. The core knows
  nothing about any particular instrument, and its runtime dependencies are
  numpy and xarray.
- `BusSchema` and the `BusRef` values it produces, for addressing drives,
  readouts, fluxes, and couplers without committing to a naming convention.
- A capability protocol platforms validate programs against. Capabilities are
  declared per slot, where a slot is a `(bus, domain)` pair over the real-time
  (`rt`) and host-side (`host`) domains. Validation reports `Diagnostic`s and an
  `ExecutionPlan`, and `qp.explain` shows which part of a program a backend
  cannot run and why.
- A reference software executor behind `qp.simulate`, which is the executable
  definition of the language's semantics. Results come back as labeled `xarray`
  arrays whose axes are named by the sweeps that produced them.
- The `.qp` text file format, with `src/qprogram/grammar/qp.lark` as its
  normative grammar and explicit `require` lines recording what a file needs
  from whatever reads it.
- Three hooks for vendor extensions: a runtime namespace, a typed mixin for
  autocompletion, and a serialization registry entry. Extensions are discovered
  through the `qprogram.vendors` entry-point group, so a `.qp` file that names a
  vendor resolves it without the caller importing that package first.
- Two optional extras: `qprogram[viz]` adds `Waveform.plot()`, and
  `qprogram[lsp]` adds the language server behind editor diagnostics.
