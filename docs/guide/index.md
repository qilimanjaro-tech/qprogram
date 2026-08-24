# User guide

These pages walk through QProgram in the order you usually need it. Each page
is self-contained and ends with cross-references.

| Topic                                                  | What it covers                                                                                |
|--------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| [Core ideas](concepts.md)                              | The mental model: blocks, operations, the AST, real-time vs. host-side loops.                |
| [Buses and schemas](buses.md)                          | Typed bus references, presets, custom naming, validation, raw strings.                       |
| [Variables and expressions](variables.md)              | Declaring variables, building expressions, evaluating them.                                   |
| [Waveforms](waveforms.md)                              | Built-in pulse shapes, IQ pairs, variable-aware parameters, custom waveforms.                |
| [Operations](operations.md)                            | Every core operation, in one place.                                                          |
| [Control flow](control-flow.md)                        | `sweep` and its sources, `average`, conditionals, parallel loops, generic blocks, nesting.  |
| [Fragments](fragments.md)                              | Reusable parameterized sub-programs: `@fragment`, `Fragment`, `call`, `expand`.               |
| [Measurements and results](measurements.md)            | `measure`, `MeasurementHandle`, `QProgramResult`, name allocation rules.                     |
| [Capabilities, diagnostics, and profiles](capabilities.md) | What a platform supports, how to ask, what diagnostics look like.                          |
| [Running programs](execution.md)                       | The reference executor: `qp.simulate`, `ReferencePlatform`, measurement models, result shapes.     |
| [Saving and loading](serialization.md)                 | `dumps`, `loads`, `save`, `load`, round-trip guarantees.                                      |

If you are looking for the exact wire format, the `.qp` grammar lives in
[reference/qp-format.md](../reference/qp-format.md). If you want to build a
new vendor extension that adds custom operations, the
[developer guide](../developer/vendor-extensions.md) has a step-by-step
walkthrough.
