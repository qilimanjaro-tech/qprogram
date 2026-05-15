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
        - set_crosstalk
        - for_loop
        - loop
        - average
        - block
        - with_bus_mapping
        - with_waveforms

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
        - Arbitrary
        - Chained
        - IQPair
        - IQDrag

## Operations

::: qprogram.operations
    options:
      show_root_full_path: false
      members:
        - Operation
        - MeasurementOperation
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
        - SetCrosstalk
        - normalize_returns

## Blocks

::: qprogram.blocks
    options:
      show_root_full_path: false
      members:
        - Block
        - Average
        - ForLoop
        - Loop
        - Parallel

## Crosstalk

::: qprogram.CrosstalkMatrix
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
::: qprogram.serialization.registry.register_vendor_version
::: qprogram.serialization.registry.register_waveform

## Serialization

::: qprogram.serialization.writer.dumps
::: qprogram.serialization.writer.save
::: qprogram.serialization.parser.loads
::: qprogram.serialization.parser.load

## Platform protocol

::: qprogram.PlatformProtocol
    options:
      show_root_full_path: false

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
