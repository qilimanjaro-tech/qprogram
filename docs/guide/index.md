# User guide

These pages are ordered the way most readers need them: the vocabulary a
program is written in first, then the operations and blocks built out of it,
then what a platform makes of the finished tree. Each page stands on its own, so
starting in the middle costs only the terms it borrows, and those are linked
where they are used.

| Page | What it documents |
|---|---|
| [Core ideas](concepts.md) | The AST every other part of the library reads: operations as leaves, blocks as containers, `elements` and `walk()`, the block stack that the `with` statements push and pop, the structural equality that makes a reloaded program's body compare equal to the original's, and why the same `sweep` runs real-time on one platform and host-side on another. |
| [Buses and schemas](buses.md) | Bus names as plain strings and as `BusRef`s from a `BusSchema`: the six presets, `BusNaming` patterns, dynamically built and combined schemas, the channel and acquisition checks that run while the program is built, what a raw string skips, and `rebind` for re-resolving a finished program onto other qubits, another chip's schema, or another naming pattern. |
| [Variables and expressions](variables.md) | `program.variable` and its identifier rules, the expression nodes the operators build, `qp.where` and the math functions, `evaluate()` against `evaluate_or_raise()`, and why `==` on a `Variable` returns a bool rather than a comparison. |
| [Waveforms](waveforms.md) | The `qp.waveforms` shapes with their parameters and defaults, the `Waveform` and `IQWaveform` split that the single-channel-versus-IQ check reads, variables as shape parameters, string aliases, and registering a shape of your own. |
| [Operations](operations.md) | The twelve core builder calls, each with its signature, its `.qp` statement form, and the capability tokens it requires; the arguments they share; and which instrument-specific work is left to a vendor extension instead. |
| [Control flow](control-flow.md) | `sweep` and the eight built-in sweep sources, `average`, `block`, the `if_` / `elif_` / `else_` chain, parallel composition with `\|`, what a condition may reference, and the nesting rules. |
| [Fragments](fragments.md) | `@fragment`, `Fragment`, `Parameter`, and `call`: defining a parameterized sub-program once, what a `Call` node keeps at the call site, the `fragment` section it serializes to, and when to `expand()`. |
| [Measurements and results](measurements.md) | The `measure` signature and its `fields` argument, how names are allocated and how they survive a `.qp` round trip, and `QProgramResult` access by handle, by name, and by index, with the dimensions a result carries. |
| [Capabilities, diagnostics, and profiles](capabilities.md) | `PlatformCapabilities`, the routing that decides which slot checks a node, the ten diagnostic codes and what produces each, the `ExecutionPlan` and `explain()`, numeric limits, predicates, and `Profile` bundles. |
| [Running programs](execution.md) | `qp.simulate` and `ReferencePlatform`: the result shapes a run produces, measurement models and the mock default, what the reference executor does not model, and what implementing `PlatformProtocol` involves. |
| [Plotting results](plotting.md) | `QProgramResult.plot`: the figure a result's shape asks for, the `channels` argument that decides what becomes of the `IQ` dimension, where an axis label comes from, the `Style` and `Theme` dataclasses, and registering a renderer of your own. |
| [Saving and loading](serialization.md) | `dumps`, `loads`, `save`, and `load`: what the round trip preserves and what it drops, the format version and `require` lines, vendor activation at parse time, the normalizations the writer applies, and the `WaveformLibrary` that quoted aliases resolve through, with its own `.wfl` file. |

Two worked programs, each given in full from the builder calls to the result
array, are in [Examples](../examples/index.md). The grammar behind the wire
forms quoted on these pages is in [.qp file format](../reference/qp-format.md),
and the exceptions they raise are cataloged in
[Errors](../reference/errors.md). For adding a whole instrument vocabulary to
the language, [Building a vendor extension](../developer/vendor-extensions.md)
works through a package end to end, and the
[developer guide](../developer/index.md) has the in-tree recipes for a core
operation and for a new waveform.
