# QProgram

A hardware-agnostic language for pulse-level quantum programs: a program is a description that platforms validate and run, and vendor packages extend.

Terms shared across Qilimanjaro's stack are in the [shared glossary](https://github.com/qilimanjaro-tech/claude-skills/blob/main/onboarding/qilimanjaro-agent-starter-kit/org-context/qilimanjaro-glossary.md); this file holds QProgram's own. The guides under `docs/guide/` explain each term in depth.

## Language

### Programs

**QProgram**:
A labelled tree of blocks and operations, with its declared variables and optionally a bus schema. Building one runs nothing; it is handed to a platform.
_Avoid_: Sequence, circuit, experiment

**Operation**:
A leaf of the program tree, such as `Play`, `Measure`, `Wait` or `SetFrequency`.
_Avoid_: Instruction, command

**Block**:
A container node of the program tree that holds operations and other blocks: `Block`, `Sweep`, `Average`, `Parallel` or `Conditional`.
_Avoid_: Scope, loop (for blocks that do not loop)

**Fragment**:
A named, parameterized program template, defined once and placed into a program by a `Call` that stays in the tree until the program is expanded.
_Avoid_: Macro, subroutine, gate

**Variable**:
A named placeholder a program declares and a sweep binds; an expression built from variables and numbers can stand wherever a number can.
_Avoid_: Parameter (a fragment's argument), sweep variable

**Sweep source**:
What a sweep draws its values from, such as `Range` or `Values`; its kind (linear or arbitrary) is part of what a platform must support.
_Avoid_: Sweep range, value list

**Waveform**:
A pure-data description of a pulse envelope, sampled only when asked for its envelope. An IQ waveform carries two channels.
_Avoid_: Pulse shape, samples

**Waveform alias**:
A string standing for a waveform in a program, resolved later from calibration data through `with_waveforms`.
_Avoid_: Pulse name, placeholder

### Buses

**Bus**:
The named target of an operation, a plain string or a bus reference from a bus schema.
_Avoid_: Channel, line, port

**Bus schema**:
The declaration of a device's elements and their buses (drive, readout, flux), which lets the builder reject a wrong bus or waveform at the call site.
_Avoid_: Topology, chip layout

### Platforms and capabilities

**Platform**:
Whatever implements `PlatformProtocol` and runs programs; the core ships only the reference platform.
_Avoid_: Backend, device, runner

**Capability token**:
A string a node reports to say which language feature it needs (`op.play`, `block.sweep`); a platform declares the tokens it supports.
_Avoid_: Feature flag

**Domain**:
Where a node can run: real-time (`rt`) on the hardware sequencer, or host-side (`host`), dispatched per iteration from the host.
_Avoid_: Mode, execution context

**Profile**:
A named, versioned bundle of capability tokens, limits and predicates that a platform declares or extends.
_Avoid_: Capability set, config

**Reference executor**:
The pure-Python executor behind `qp.simulate`, which defines the language's semantics and the result shapes vendor compilers are tested against.
_Avoid_: Simulator (it simulates results, not devices), emulator

**Vendor extension**:
A separate package that adds instrument-specific operations, a namespace and profiles to QProgram, registering itself on import.
_Avoid_: Plugin, driver

### Files

**`.qp` file**:
The text form of a program, written by `dumps` and read by `loads`; its header carries the format version, and a `require` line names each vendor extension it uses.
_Avoid_: Program file, script

**Format version**:
The `major.minor` part of the library version, written into every `.qp` and `.wfl` header; files from an earlier format version load through migrations.
_Avoid_: Schema version, file version
