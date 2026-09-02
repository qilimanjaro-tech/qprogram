# API reference

Every entry below is generated from a docstring in `src/` by mkdocstrings.
Signatures, defaults, and type annotations are read from the code rather than
written out here, and each heading carries a fold with the source it came from.
Members are listed in the order they appear in their file rather than
alphabetically, and private names are hidden apart from `__init__`, whose
parameters are folded into the class heading. Every heading is anchored by
dotted path, so another page can link to a single member:
`api-qprogram.md#qprogram.QProgram.play`.

The supported surface is `qprogram.__all__`, the names that resolve directly on
the package after `import qprogram as qp`. Three other kinds of name appear
here under a longer dotted path. The waveform, operation, block, and plotting
classes live in submodules the top level does not re-export, so they are
written `qp.waveforms.Gaussian`, `qp.operations.Play`, `qp.blocks.Sweep`, and
`qp.plotting.Style`. A few
names the top level does re-export are grouped with the submodule that defines
them instead, because they read better next to related material: `Call` and
`MeasurementField` sit with the rest of `qprogram.operations`, `UNASSIGNED` and
the expression helpers (`qp.eq`, `qp.sin`, and so on) sit with
`qprogram.variable`, and `dumps`/`save` sit with `qprogram.serialization.writer`
next to `loads`/`load`. The rest are extension points an integrator needs and
a program author does not, reached through their submodule:
`qp.serialization.register_operation`, `qp.protocol.validate_tokens`,
`qp.sweeps.validate_source`. For the reasoning behind any of these names, read
the [user guide](../guide/index.md); this page is the lookup.

## Top-level

::: qprogram.QProgram
    options:
      show_root_full_path: false
      members:
        - body
        - schema
        - buses
        - variables
        - variable
        - measurement_handles
        - fragments
        - source_map
        - register_vendor
        - play
        - measure
        - wait
        - sync
        - set_frequency
        - set_phase
        - reset_phase
        - set_gain
        - set_offset
        - set_parameter
        - get_parameter
        - sweep
        - average
        - block
        - if_
        - elif_
        - else_
        - call
        - expand
        - rebind
        - with_waveforms

### Sweep builders

`program.sweep(variable)`, with the source left out, returns a source builder;
`program.sweep(variable, source)` returns the loop context straight away. Both
are private classes that user code never constructs, but their methods are part
of the public surface, so they are documented here. Entering a builder before a
`from_*` call has picked any values raises `ValidationError` rather than
sweeping nothing. The context managers behind `average`, `block`, `if_`,
`elif_`, and `else_` add nothing to the context-manager protocol, so they have
no entries of their own.

::: qprogram.qprogram._SweepBuilder
    options:
      show_root_full_path: false
      members:
        - from_range
        - from_linspace
        - from_logspace
        - from_values
        - from_file
        - __getattr__

::: qprogram.qprogram._LoopContext
    options:
      show_root_full_path: false
      members:
        - __or__
        - repeat
        - rotate

## Sweep sources

What a `Sweep` iterates over: a description of the values, never a producer of
them. Every source answers `length()` and `values()` without the program
running, declares a `KIND` of `"linear"` or `"arbitrary"` along with its own
`sweep.<name>` capability token, and compares and hashes structurally over its
public attributes, which are treated as immutable once the source is in a
program. `register_sweep_source` puts a subclass in the registry under its own
class name, which is what makes it parseable from a `.qp` file and spellable as
`sweep(variable).from_<name>(...)`; `validate_source` checks the `length()` and
`values()` invariants for a new one.

::: qprogram.SweepSource
    options:
      show_root_full_path: false

::: qprogram.Range
    options:
      show_root_full_path: false

::: qprogram.Linspace
    options:
      show_root_full_path: false

::: qprogram.Logspace
    options:
      show_root_full_path: false

::: qprogram.Values
    options:
      show_root_full_path: false

::: qprogram.File
    options:
      show_root_full_path: false

::: qprogram.Repeat
    options:
      show_root_full_path: false

::: qprogram.Rotate
    options:
      show_root_full_path: false

::: qprogram.Concat
    options:
      show_root_full_path: false

::: qprogram.serialization.registry.register_sweep_source

::: qprogram.serialization.registry.known_sweep_sources

::: qprogram.sweeps.source.validate_source

## Bus schemas

::: qprogram.BusSchema
    options:
      show_root_full_path: false

::: qprogram.BusNaming
    options:
      show_root_full_path: false

::: qprogram.BusRef
    options:
      show_root_full_path: false

::: qprogram.buses.ElementSchema
    options:
      show_root_full_path: false

### Typed schemas

Each preset factory on `BusSchema` returns one of these subclasses, whose
element properties are declared rather than resolved through `__getattr__`, so
an editor can complete the bus kinds. The classes are reachable under their own
names for a type annotation or for `combine`, which takes a class as readily as
an instance.

::: qprogram.buses.TransmonSchema
    options:
      show_root_full_path: false

::: qprogram.buses.TransmonCoupledSchema
    options:
      show_root_full_path: false

::: qprogram.buses.FluxTunableTransmonSchema
    options:
      show_root_full_path: false

::: qprogram.buses.FluxTunableTransmonCoupledSchema
    options:
      show_root_full_path: false

::: qprogram.buses.FluxoniumSchema
    options:
      show_root_full_path: false

::: qprogram.buses.FluxoniumCoupledSchema
    options:
      show_root_full_path: false

### Typed element accessors

`TransmonSchema.q`, `FluxTunableTransmonSchema.q`, and `FluxoniumSchema.q`
return one of these factories; indexing one returns the matching accessor,
whose properties are the element's typed bus refs.
`FluxTunableTransmonQubitBuses` and `FluxoniumQubitBuses` subclass
`TransmonQubitBuses` to add their extra flux buses rather than repeating
`drive` and `readout`.

::: qprogram.buses.TransmonQubitBuses
    options:
      show_root_full_path: false

::: qprogram.buses.TransmonQubitFactory
    options:
      show_root_full_path: false

::: qprogram.buses.FluxTunableTransmonQubitBuses
    options:
      show_root_full_path: false

::: qprogram.buses.FluxTunableTransmonQubitFactory
    options:
      show_root_full_path: false

::: qprogram.buses.FluxoniumQubitBuses
    options:
      show_root_full_path: false

::: qprogram.buses.FluxoniumQubitFactory
    options:
      show_root_full_path: false

### Typed schema base classes

A chip type no preset covers gets a subclass built from the same three pieces
the presets use: an accessor carrying one property per bus kind, a factory that
turns an index into an accessor, and the schema carrying one property per
element. The two base classes hold the machinery for the first two, and
`CouplerBuses`/`CouplerFactory` are reusable as they stand, because a coupler's
single `flux` bus is the same in every preset that has one. [Defining your own
typed schema](../guide/buses.md#defining-your-own-typed-schema) walks through a
complete class.

::: qprogram.buses._TypedElementAccessor
    options:
      show_root_full_path: false
      members:
        - _ref

::: qprogram.buses._TypedElementFactory
    options:
      show_root_full_path: false
      members:
        - __getitem__

::: qprogram.buses.CouplerBuses
    options:
      show_root_full_path: false

::: qprogram.buses.CouplerFactory
    options:
      show_root_full_path: false

### Re-resolving a coordinate

`resolve_ref` is the one place an `(element, index, kind)` coordinate becomes a
`BusRef`: the `.qp` parser calls it for every `element[i].kind` path it reads,
and `QProgram.rebind` calls it for every ref it rewrites, which is what keeps a
re-indexed or ported program checked against the schema it lands on.
`naming_substituted_schema` covers the naming-only port, returning a dynamic
copy of the schema with the same elements declared under a new `BusNaming`.

::: qprogram.buses.resolve_ref

::: qprogram.buses.naming_substituted_schema

## Variables and expressions

::: qprogram.Variable
    options:
      show_root_full_path: false

::: qprogram.Expression
    options:
      show_root_full_path: false

::: qprogram.Constant
    options:
      show_root_full_path: false

::: qprogram.BinaryOp
    options:
      show_root_full_path: false

::: qprogram.UnaryOp
    options:
      show_root_full_path: false

::: qprogram.Comparison
    options:
      show_root_full_path: false

::: qprogram.LogicalBinaryOp
    options:
      show_root_full_path: false

::: qprogram.LogicalNot
    options:
      show_root_full_path: false

::: qprogram.MathFunc
    options:
      show_root_full_path: false

::: qprogram.Where
    options:
      show_root_full_path: false

::: qprogram.MeasurementRef
    options:
      show_root_full_path: false

::: qprogram.variable.UNASSIGNED

### Helper functions

Free functions that build expression nodes, all reached as `qp.eq`, `qp.sin`,
and so on. `eq` and `ne` are the only way to compare two expressions for
equality: `Variable.__eq__` compares ids and has to keep returning a `bool` so
that variables stay usable in sets and as dictionary keys. `and_`, `or_`, and
`not_` are function forms of `&`, `|`, and `~`, which `Expression` does
overload. The math functions, `minimum`, `maximum`, and `where` have no
operator form at all.

::: qprogram.variable.eq
::: qprogram.variable.ne
::: qprogram.variable.and_
::: qprogram.variable.or_
::: qprogram.variable.not_
::: qprogram.variable.sin
::: qprogram.variable.cos
::: qprogram.variable.tan
::: qprogram.variable.exp
::: qprogram.variable.log
::: qprogram.variable.sqrt
::: qprogram.variable.minimum
::: qprogram.variable.maximum
::: qprogram.variable.where

## Waveforms

::: qprogram.waveforms
    options:
      show_root_full_path: false
      members:
        - Waveform
        - IQWaveform
        - Square
        - Gaussian
        - GaussianDragCorrection
        - Ramp
        - FlatTop
        - SuddenNetZero
        - Sine
        - Cosine
        - Sech
        - Tukey
        - Arbitrary
        - Chained
        - IQPair
        - IQDrag
        - IQRotation
        - IQZero
        - Modulated

::: qprogram.WaveformLibrary
    options:
      show_root_full_path: false

## Operations

The AST leaves, each appended by the matching builder method on `QProgram`
rather than constructed at a call site. `MeasurementField` and
`normalize_fields` are documented here because they live in the same module: the
first is a `StrEnum` of the field names a measurement can request, and the
second sorts a `fields` argument into the canonical order those names are
compared, hashed, and serialized in.

::: qprogram.operations
    options:
      show_root_full_path: false
      members:
        - Operation
        - Play
        - Measure
        - Wait
        - Sync
        - SetFrequency
        - SetPhase
        - ResetPhase
        - SetGain
        - SetOffset
        - SetParameter
        - GetParameter
        - Call
        - MeasurementField

::: qprogram.operations.operation.MeasurementOperation
    options:
      show_root_full_path: false

::: qprogram.operations.operation.normalize_fields

## Blocks

::: qprogram.blocks
    options:
      show_root_full_path: false
      members:
        - Block
        - Average
        - Sweep
        - Parallel
        - Conditional

## Fragments

::: qprogram.fragment

::: qprogram.Fragment
    options:
      show_root_full_path: false

::: qprogram.Parameter
    options:
      show_root_full_path: false

## Results

::: qprogram.MeasurementHandle
    options:
      show_root_full_path: false

::: qprogram.MeasurementResult
    options:
      show_root_full_path: false

::: qprogram.QProgramResult
    options:
      show_root_full_path: false

## Plotting

`QProgramResult.plot` above is the front door. It runs `build_figure` to
describe the figure and a renderer to draw it, and the two halves are separate
so that a backend other than matplotlib is possible: everything down to
`Renderer` reads numpy and xarray only. See
[Plotting results](../guide/plotting.md) for the walkthrough. These names live
in `qprogram.plotting`, which the top level does not re-export.

::: qprogram.plotting.build_figure

::: qprogram.plotting.Quantity
    options:
      show_root_full_path: false

::: qprogram.plotting.Figure
    options:
      show_root_full_path: false

::: qprogram.plotting.Line
    options:
      show_root_full_path: false

::: qprogram.plotting.Points
    options:
      show_root_full_path: false

::: qprogram.plotting.Mesh
    options:
      show_root_full_path: false

::: qprogram.plotting.Style
    options:
      show_root_full_path: false

::: qprogram.plotting.Theme
    options:
      show_root_full_path: false

::: qprogram.plotting.LIGHT

::: qprogram.plotting.DARK

::: qprogram.plotting.Renderer
    options:
      show_root_full_path: false

::: qprogram.plotting.register_renderer

::: qprogram.plotting.resolve_renderer

::: qprogram.plotting.available_renderers

::: qprogram.plotting.matplotlib_renderer.render

## Vendor protocol

A vendor extension groups its operations as methods on a `VendorNamespace`
subclass, registers that subclass under a namespace name with
`QProgram.register_vendor`, and reaches the program through the two protected
helpers below; `program.<vendor>.<operation>(...)` then works without core
QProgram knowing the vendor exists.

The registration calls below all run at the extension's import time.
`register_operation` and `register_block` add core names unless given a
`vendor=`, while `register_vendor_operation` and `register_vendor_block` forward
to them with the vendor prefix already filled in; `register_waveform` keys a
waveform class by its own `__name__` with no prefix at all.
`register_vendor_version` records the extension's protocol version, and the
writer raises `SerializationError` on vendor content whose extension never
called it. `try_activate_vendor` is the discovery step:
it imports the package behind the `qprogram.vendors` entry point for a name and
returns `False` when no installed package claims that name.

::: qprogram.VendorNamespace
    options:
      show_root_full_path: false
      members:
        - _append
        - _append_measurement

::: qprogram.serialization.registry.register_operation
::: qprogram.serialization.registry.register_vendor_operation
::: qprogram.serialization.registry.register_block
::: qprogram.serialization.registry.register_vendor_block
::: qprogram.serialization.registry.register_vendor_version
::: qprogram.serialization.registry.register_waveform
::: qprogram.serialization.registry.try_activate_vendor

::: qprogram.serialization.registry.OperationSpec
    options:
      show_root_full_path: false

::: qprogram.serialization.registry.BlockSpec
    options:
      show_root_full_path: false

## Serialization

`dumps` and `save` write `.qp`; `loads` and `load` read it. Those two readers
and `ParseError` are resolved by the package's module-level `__getattr__` on
first access rather than at import time, because the parser imports `QProgram`
and importing them eagerly would close a cycle. Nothing about that shows at the
call site: `qp.loads` is read off the package like any other attribute.

::: qprogram.serialization.writer.dumps
::: qprogram.serialization.writer.save
::: qprogram.serialization.parser.loads
::: qprogram.serialization.parser.load

## Platform protocol

::: qprogram.PlatformProtocol
    options:
      show_root_full_path: false

### Reference platform

The software platform in this repository. It validates a program, interprets
the AST, and returns `xarray.DataArray` results with one dimension per
enclosing sweep (a `Parallel` composition contributing one shared dimension)
and none for an averaging block, and it is the semantics a vendor compiler is
tested against: an error diagnostic becomes `UnsupportedOperationError`, a
warning is raised through `warnings.warn` with category `ExecutionWarning`, and
info diagnostics pass silently. `simulate(program)` is the one-call form,
running the program on a throwaway platform whose measurement model defaults to
a deterministic, all-zero `MockMeasurementModel`. See
[Running programs](../guide/execution.md) for the walkthrough.

::: qprogram.simulate

::: qprogram.ReferencePlatform
    options:
      show_root_full_path: false

::: qprogram.reference_capabilities

::: qprogram.MeasurementModel
    options:
      show_root_full_path: false

::: qprogram.MockMeasurementModel
    options:
      show_root_full_path: false

::: qprogram.MeasurementSample
    options:
      show_root_full_path: false

::: qprogram.ExecutionWarning
    options:
      show_root_full_path: false

## Capability protocol

The data types and helpers that platforms use to declare which DSL features
they support. See [Capabilities, diagnostics, and profiles](../guide/capabilities.md)
for the narrative tour.

### Descriptors and bundles

::: qprogram.PlatformCapabilities
    options:
      show_root_full_path: false

::: qprogram.BusCapabilities
    options:
      show_root_full_path: false

::: qprogram.CompilerCapabilities
    options:
      show_root_full_path: false

::: qprogram.Profile
    options:
      show_root_full_path: false

::: qprogram.Diagnostic
    options:
      show_root_full_path: false

::: qprogram.DomainConstraint
    options:
      show_root_full_path: false

::: qprogram.Domain

::: qprogram.BusSelector

::: qprogram.ExecutionPlan

::: qprogram.ValidationContext
    options:
      show_root_full_path: false

::: qprogram.SweepKind

::: qprogram.Predicate
    options:
      show_root_full_path: false

::: qprogram.PredicateFn

::: qprogram.QPROGRAM_BASE_V1

### Validator

`validate(program, caps)` returns a list of `Diagnostic`s and an
`ExecutionPlan` covering every visited node except the root body, keyed by node
identity rather than by structural equality. `explain(program, caps)` renders
that same classification as a string: a header with the severity counts, then
one row per node carrying its `.qp` text, its domain, and any diagnostic on it.
`optimize(program, caps)` applies the one rewrite the validator only reports as
an info hint, lifting a host-side sweep out of an averaging block so that the
averaging itself can run in real time.

`validate` and `explain` expand fragment `Call` nodes before walking, so their
diagnostics reference nodes of that expansion rather than nodes the caller
holds. `optimize` expands only when the program's own body holds an `average`;
otherwise it returns a deep copy with the `Call` nodes intact.

::: qprogram.validate

::: qprogram.explain.explain

::: qprogram.optimize

### Diagnostic paths

Every node-bearing `Diagnostic` carries a structural `path`. `AstPath` is the
type, `node_path` builds one for a node, `resolve_path` walks one back to the
node it names, `format_path` renders one for display, and `iter_child_edges` is
the single ordered traversal that `node_path`, `resolve_path`, and the
validator all read the AST through.

::: qprogram.AstPath

::: qprogram.node_path

::: qprogram.resolve_path

::: qprogram.format_path

::: qprogram.paths.iter_child_edges

### Registries and helpers

Registration and lookup for the capability tokens and the named profiles.
`Profile` runs every token it is given through `validate_tokens` in its
`__post_init__`, so a token no core definition or registration call has put in
`CAPABILITY_REGISTRY` is rejected where the profile is defined rather than
surfacing later as a feature the platform silently lacks.

::: qprogram.protocol.CAPABILITY_REGISTRY

::: qprogram.register_profile

::: qprogram.resolve_profile

::: qprogram.register_capability_tokens

::: qprogram.register_waveform_token

::: qprogram.protocol.validate_tokens

::: qprogram.protocol.waveform_token

::: qprogram.protocol.expression_tokens

::: qprogram.protocol.measurement_field_token

::: qprogram.protocol.known_measurement_fields

## Reserved keywords

A `frozenset` of the identifiers the `.qp` grammar reserves. Variable ids,
fragment names, and vendor namespaces are all checked against it, and
[Reserved keywords](reserved.md) lists the words with what each one is kept
for.

::: qprogram.RESERVED_KEYWORDS

## Errors

Every error QProgram raises while a program is built, parsed, or run derives
from `QProgramError`, so one `except` covers all of it.
`UnsupportedOperationError`, `BusNotAvailableError`, `WaveformResolutionError`,
`CompilationError`, and `HardwareError` are the platform-side half of the
hierarchy, defined here so that user code can catch one class per failure mode
whichever backend is in use; of the five, only `UnsupportedOperationError` is
raised in this repository, by `ReferencePlatform.execute`. [Errors](errors.md)
covers when each one fires and what it carries.

::: qprogram.QProgramError
    options:
      show_root_full_path: false

::: qprogram.ValidationError
    options:
      show_root_full_path: false

::: qprogram.InvalidVariableIdError
    options:
      show_root_full_path: false

::: qprogram.UnassignedVariableError
    options:
      show_root_full_path: false

::: qprogram.serialization.parser.ParseError
    options:
      show_root_full_path: false

::: qprogram.SerializationError
    options:
      show_root_full_path: false

::: qprogram.VendorActivationError
    options:
      show_root_full_path: false

::: qprogram.UnsupportedOperationError
    options:
      show_root_full_path: false

::: qprogram.BusNotAvailableError
    options:
      show_root_full_path: false

::: qprogram.WaveformResolutionError
    options:
      show_root_full_path: false

::: qprogram.CompilationError
    options:
      show_root_full_path: false

::: qprogram.HardwareError
    options:
      show_root_full_path: false
