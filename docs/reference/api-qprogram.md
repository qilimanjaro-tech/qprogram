# API reference

Auto-generated reference for the `qprogram` package. Names are linked into
the narrative guides where helpful.

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

`program.sweep(variable)` — with the source left out — returns a source builder;
`program.sweep(variable, source)` returns the loop context straight away. Neither
type is constructed directly, but their methods are part of the public surface.

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
        - repeat
        - rotate

## Sweep sources

What a `Sweep` iterates over. Immutable value objects with structural
equality, registered by class name, and extensible without a core change.

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

## Vendor protocol

::: qprogram.VendorNamespace
    options:
      show_root_full_path: false

::: qprogram.serialization.registry.register_vendor_operation
::: qprogram.serialization.registry.register_block
::: qprogram.serialization.registry.register_vendor_block
::: qprogram.serialization.registry.register_vendor_version
::: qprogram.serialization.registry.register_waveform
::: qprogram.serialization.registry.try_activate_vendor

## Serialization

::: qprogram.serialization.writer.dumps
::: qprogram.serialization.writer.save
::: qprogram.serialization.parser.loads
::: qprogram.serialization.parser.load

## Platform protocol

::: qprogram.PlatformProtocol
    options:
      show_root_full_path: false

### Reference platform

The software platform core qprogram ships: the executable definition of the
result shapes every backend has to reproduce. See
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

::: qprogram.validate

::: qprogram.explain.explain

::: qprogram.optimize

### Diagnostic paths

Every node-bearing `Diagnostic` carries a structural `path`. These helpers
build one, resolve one back to its node, and render one for display.

::: qprogram.AstPath

::: qprogram.node_path

::: qprogram.resolve_path

::: qprogram.format_path

### Registries and helpers

::: qprogram.register_profile

::: qprogram.resolve_profile

::: qprogram.register_capability_tokens

::: qprogram.register_waveform_token

::: qprogram.protocol.validate_tokens

::: qprogram.protocol.waveform_token

::: qprogram.protocol.expression_tokens

## Reserved keywords

::: qprogram.RESERVED_KEYWORDS

## Errors

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
