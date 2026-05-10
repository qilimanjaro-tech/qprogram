# QProgram DSL Specification (Draft)

> **Source:** https://www.notion.so/qilimanjaro/QProgram-DSL-Specification-Draft-32f7eec14c53815a8290d85478cdcaec
> **Fetched:** 2026-05-07
> **Status:** Draft (specification — code may not yet match)

---

# 1. Introduction
QProgram is a domain-specific language (DSL) for pulse-level quantum programming. It provides a hardware-agnostic way to describe pulse sequences, parameter sweeps, and control flow for quantum experiments. Any platform can implement a compiler to translate QProgram into its hardware-specific instruction set.
## 1.1 Design Principles
- **Hardware-agnostic.** QProgram describes *what* the user wants, not *how* the hardware should do it. The compiler decides hardware vs software execution.
- **Single program model.** Users write one QProgram that can mix pulse operations with parameter sweeps. No need to choose between "hardware program" and "software experiment".
- **Minimal dependencies.** The QProgram library depends only on numpy/xarray. No coupling to any vendor SDK, instrument driver, or platform.
- **Portable.** Programs can be serialized to `.qp` files and executed on any platform that implements the protocol.
- **Discoverable.** Platforms expose which buses and parameters they support. Users can query this at runtime.
---
# 2. Programs
A `QProgram` is the top-level container. It holds a tree of blocks and operations, a set of declared variables, and optional metadata.
```python
import qprogram as qp

program = qp.QProgram(label="rabi", description="Rabi oscillation experiment")
```
## 2.1 Properties
<table header-row="true">
<tr>
<td>**Property**</td>
<td>**Type**</td>
<td>**Description**</td>
</tr>
<tr>
<td>`label`</td>
<td>`str`</td>
<td>Human-readable name for the program</td>
</tr>
<tr>
<td>`description`</td>
<td>`str | None`</td>
<td>Optional description</td>
</tr>
<tr>
<td>`body`</td>
<td>`Block`</td>
<td>Root block containing all elements (read-only)</td>
</tr>
<tr>
<td>`buses`</td>
<td>`set[str]`</td>
<td>Set of bus names referenced by operations (read-only)</td>
</tr>
<tr>
<td>`variables`</td>
<td>`list[Variable]`</td>
<td>All declared variables (read-only)</td>
</tr>
</table>
## 2.2 Mappings
Programs reference buses and waveforms by logical name. Both can be remapped, returning a new QProgram (the original is unchanged).
**Bus mapping** — remap bus references:
```python
mapped = program.with_bus_mapping({"drive_q0": "drive_q1", "readout_q0": "readout_q1"})
```
**Waveform mapping** — resolve string waveform aliases to concrete waveforms:
```python
resolved = program.with_waveforms({
    "pi_pulse": IQDrag(0.5, 40, 2.5, 0.1),
    "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
})
```
This is how calibration data is applied to a program. The QProgram itself uses string aliases (e.g. `"pi_pulse"`); concrete waveform values are provided externally — by the platform, the calibration system, or the user — and mapped in via `with_waveforms()`. This keeps the program definition stable across calibration runs.
## 2.3 Bus References
Every operation in QProgram targets a bus by name (e.g. `"drive_q0"`, `"readout_q0"`, `"flux_q0"`). Raw strings work, but they are error-prone (typos are silent until runtime), non-discoverable (no tab-completion), and platform-coupled (each platform uses different naming conventions).
The `BusSchema` system provides typed, validated, discoverable bus references that resolve to plain strings at the AST level.
### The problem
Different platforms name their buses differently. One platform might use `"q0/drive"`, another `"drive_q0_bus"`. Different qubit types expose different bus types: a transmon has `drive` and `readout`, a flux-tunable transmon adds `flux`, a fluxonium has `flux_x` and `flux_z` instead.
Users shouldn't need to memorize string conventions. They should get tab-completion, validation, and clear errors.
### BusSchema
A `BusSchema` declares which bus types each element kind has and their properties via `BusInfo`. It does **not** define how many qubits or couplers exist — any index is accepted and the schema constructs the string.
Each bus type has a `BusInfo` with:
- `channel`: `"single"` (real-valued, accepts `Waveform`) or `"IQ"` (complex, accepts `IQWaveform`)
- `acquires`: whether the bus has an ADC and supports `measure()` operations
Convenience constants: `IQ`, `IQ_ACQUIRES`, `SINGLE`.
When a bus is referenced through the schema, `play()` validates waveform type and `measure()` validates acquisition support at program-construction time.
There are two modes:
1. **Presets** — return fully typed subclasses. IDE autocomplete works on `.q`, `.c`, `.drive`, `.readout`, etc. Should cover 99% of the cases.
2. **Dynamic** — use `add_element()` for custom topologies. Works at runtime but no static typing.
```python
from qprogram.buses import BusSchema, BusInfo, IQ, IQ_ACQUIRES, SINGLE

# Typed presets — full IDE autocomplete
schema = BusSchema.transmon()                        # .q only
schema = BusSchema.transmon_coupled()                # .q + .c
schema = BusSchema.flux_tunable_transmon()            # .q with flux
schema = BusSchema.flux_tunable_transmon_coupled()   # .q with flux + .c
schema = BusSchema.fluxonium()                       # .q with flux_x, flux_z
schema = BusSchema.fluxonium_coupled()               # .q with flux_x, flux_z + .c

# Dynamic (untyped) — for exotic topologies
schema = BusSchema()
schema.add_element("q", buses={"drive": IQ, "readout": IQ_ACQUIRES, "charge": SINGLE})
schema.add_element("resonator", buses={"probe": IQ_ACQUIRES})
```
### Usage
Access bus names through the schema with `element[index].bus_type` syntax:
```python
schema = BusSchema.flux_tunable_transmon_coupled()
q = schema.q
c = schema.c

program.play(q[0].drive, pulse)            # -> "q0/drive"
program.measure(q[0].readout, wf, wts)    # -> "q0/readout"
program.set_offset(q[0].flux, 0.5)        # -> "q0/flux"
program.set_offset(c[0,1].flux, 0.3)      # -> "c0_1/flux"
program.sync([q[0].readout, q[1].readout])
q[42].drive                                # -> "q42/drive" (any index accepted)
```
### BusRef is a string
`q[0].drive` returns a `BusRef`, which subclasses `str`. This means:
- It works everywhere a string works — operations, serialization, `.qp` files
- The QProgram AST stores plain strings — zero changes to internals
- But it also carries metadata for tooling and validation
```python
bus = q[0].drive
print(bus)              # "q0/drive"
isinstance(bus, str)    # True
bus.element             # "q"
bus.index               # 0
bus.bus_type            # "drive"
bus.info                # BusInfo(IQ)
bus.channel_type        # "IQ"   (shortcut for bus.info.channel)
bus.acquires            # False  (shortcut for bus.info.acquires)

q[0].readout.acquires   # True   (readout has ADC)
```
### Validation
When a bus is referenced through the schema, operations validate at program-construction time:
**Waveform channel type** — `play()` checks waveform type matches the bus channel:
```python
program.play(q[0].drive, IQDrag(0.5, 40, 2.5, 0.1))   # OK: IQ waveform on IQ bus
program.play(q[0].flux, FlatTop(0.5, 200, 20))         # OK: single waveform on single bus
program.play(q[0].drive, Square(0.5, 100))              # TypeError: single waveform on IQ bus
program.play(q[0].flux, IQDrag(0.5, 40, 2.5, 0.1))     # TypeError: IQ waveform on single bus
```
**Acquisition support** — `measure()` checks the bus has an ADC:
```python
program.measure(q[0].readout, readout, weights)   # OK: readout bus has acquires=True
program.measure(q[0].drive, readout, weights)      # TypeError: drive bus has acquires=False
program.measure(q[0].flux, readout, weights)       # TypeError: flux bus has acquires=False
```
**Bypass** — raw string buses and string alias waveforms skip all validation:
```python
program.play(q[0].drive, "pi_pulse")               # OK: string alias, no waveform validation
program.play("raw_string", Square(0.5, 100))        # OK: raw string, no validation at all
program.measure("raw_string", "readout", "weights") # OK: raw string, no acquires validation
```
### Validation
The schema validates **bus types** (not indices — any index is accepted):
```python
# Bus type validation
q[0].flux_x   # AttributeError: 'q' has no bus 'flux_x'. Available: drive, readout, flux

# Element type validation
schema.resonator  # AttributeError: No element 'resonator' in schema. Available: q, coupler

# Any index is accepted — the schema doesn't know how many qubits exist
q[0].drive     # OK -> "q0/drive"
q[99].drive    # OK -> "q99/drive"
c[3,7].flux    # OK -> "coupler3_7/flux"
```
### Presets
<table header-row="true">
<tr>
<td>**Preset**</td>
<td>**`.q`**** buses**</td>
<td>**`.c`**</td>
<td>**Use case**</td>
</tr>
<tr>
<td>`transmon()`</td>
<td>drive (IQ), readout (IQ, acquires)</td>
<td>no</td>
<td>Fixed-frequency transmons</td>
</tr>
<tr>
<td>`transmon_coupled()`</td>
<td>drive (IQ), readout (IQ, acquires)</td>
<td>flux (single)</td>
<td>Fixed-frequency transmons + couplers</td>
</tr>
<tr>
<td>`flux_tunable_transmon()`</td>
<td>drive (IQ), readout (IQ, acquires), flux (single)</td>
<td>no</td>
<td>Tunable transmons</td>
</tr>
<tr>
<td>`flux_tunable_transmon_coupled()`</td>
<td>drive (IQ), readout (IQ, acquires), flux (single)</td>
<td>flux (single)</td>
<td>Tunable transmons + couplers</td>
</tr>
<tr>
<td>`fluxonium()`</td>
<td>drive (IQ), readout (IQ, acquires), flux_x (single), flux_z (single)</td>
<td>no</td>
<td>Fluxonium qubits</td>
</tr>
<tr>
<td>`fluxonium_coupled()`</td>
<td>drive (IQ), readout (IQ, acquires), flux_x (single), flux_z (single)</td>
<td>flux (single)</td>
<td>Fluxonium qubits + couplers</td>
</tr>
</table>
All presets accept an optional `naming` argument. Coupled variants add `.c` for couplers. The presets return typed subclasses — IDE autocomplete works for `.q`, `.c`, and all bus type properties.
### Custom naming conventions
Different platforms can plug in their own naming pattern:
```python
from qprogram.buses import BusNaming

# Default: "q0/drive"
schema = BusSchema.flux_tunable_transmon()

# QiliLab-style: "drive_q0_bus"
schema = BusSchema.flux_tunable_transmon(
    naming=BusNaming("{bus_type}_{element}{index}_bus")
)
```
### Platform-provided schemas
Platforms can provide their schema via the `PlatformProtocol`:
```python
schema = platform.get_bus_schema()
q = schema.q

# Tab-completion shows exactly the bus types this chip supports
program.play(q[0].drive, pulse)
```
### Defining custom typed schemas
For qubit types not covered by the presets, you can define your own typed schema with full IDE support. This follows the same pattern the presets use internally:
```python
from qprogram.buses import (
    BusSchema, BusRef, BusInfo, BusNaming,
    _TypedElementAccessor, _TypedElementFactory,
    IQ, IQ_ACQUIRES, SINGLE, CouplerFactory,
)

# 1. Define a bus accessor — one @property per bus type
class MyQubitBuses(_TypedElementAccessor):
    @property
    def drive(self) -> BusRef:
        return self._ref("drive", IQ)

    @property
    def readout(self) -> BusRef:
        return self._ref("readout", IQ_ACQUIRES)

    @property
    def charge(self) -> BusRef:
        return self._ref("charge", SINGLE)

# 2. Define a factory — returns the accessor on subscript
class MyQubitFactory(_TypedElementFactory):
    _accessor_cls = MyQubitBuses

    def __getitem__(self, index: int) -> MyQubitBuses:
        return MyQubitBuses(self._element, index, self._naming)

# 3. Define the typed schema — one @property per element type
class MyChipSchema(BusSchema):
    @property
    def q(self) -> MyQubitFactory:
        return MyQubitFactory("q", self._naming)

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming)

# Usage — full IDE autocomplete
schema = MyChipSchema()
schema.q[0].drive     # autocomplete shows: .drive, .readout, .charge
schema.q[0].charge    # -> "q0/charge"
schema.c[0, 1].flux   # -> "c0_1/flux"
```
### Three levels of bus referencing
<table header-row="true">
<tr>
<td>**Approach**</td>
<td>**Setup**</td>
<td>**Validation**</td>
<td>**Tab-completion**</td>
</tr>
<tr>
<td>Raw strings: `"drive_q0"`</td>
<td>None</td>
<td>None</td>
<td>No</td>
</tr>
<tr>
<td>Schema presets: `q[0].drive`</td>
<td>One line</td>
<td>Bus type only</td>
<td>Yes</td>
</tr>
<tr>
<td>Platform schema: `platform.get_bus_schema()`</td>
<td>From platform</td>
<td>Bus type + platform naming</td>
<td>Yes</td>
</tr>
</table>
All three produce the same thing at the AST level: a string. **Zero changes to QProgram internals.**
---
# 3. Variables and Expressions
QProgram has a small AST for **symbolic expressions** — anywhere an operation accepts a numeric value, it also accepts a variable, an expression, or any composition of them. The expression system is the standard tree-of-nodes pattern used in compilers.
## 3.1 The expression hierarchy
<table header-row="true">
<tr>
<td>**Class**</td>
<td>**Role**</td>
<td>**Equality**</td>
<td>`Expression`</td>
<td>Abstract base. Anything usable where a number is expected.</td>
<td>—</td>
</tr>
<tr>
<td>`Variable`</td>
<td>Leaf. Symbolic placeholder identified by label. **Holds a value** — initially `UNASSIGNED`, set via `set_value()`.</td>
<td>**Identity-based** — each `Variable("freq")` is distinct.</td>
<td>`Constant`</td>
<td>Leaf. A concrete numeric value.</td>
<td>**Structural** — `Constant(5) == Constant(5)`.</td>
</tr>
<tr>
<td>`BinaryOp`</td>
<td>Internal node. Operator (`+`, `-`, `*`, `/`) over two expressions.</td>
<td>**Structural** — same op, same operands.</td>
<td>`UnaryOp`</td>
<td>Internal node. Unary operator (`-`, `+`) over one expression.</td>
<td>**Structural**.</td>
</tr>
</table>
Variables and Constants are **leaves**; BinaryOp and UnaryOp are **internal nodes**. Together they form an expression tree. Literal `int`/`float` values used in arithmetic with an `Expression` are auto-wrapped to `Constant` by the operators.
Variables carry no type or domain information — the compiler validates that the runtime values from loops are compatible with the operations using them (e.g. a variable used in `set_frequency(bus, freq)` should receive Hz values).
## 3.2 Declaring variables
```python
freq = program.variable("freq")
duration = program.variable("duration")
gain = program.variable("gain")
```
There is a single `Variable` type. No `IntVariable`, `FloatVariable`, or `Domain` enum.

### Label, long_name, units, description

Every `Variable` carries a mandatory **`label`** plus three optional pieces of metadata for documentation and downstream tooling (axis names, plot titles, results coordinates):

```python
freq = program.variable(
    "freq",
    long_name="Drive Frequency",
    units="Hz",
    description="Carrier frequency swept across the qubit transition",
)
```

| Field         | Type            | Required | Purpose                                                                                          |
|---------------|-----------------|----------|--------------------------------------------------------------------------------------------------|
| `label`       | `str`           | yes      | Short identifier. Doubles as the identifier in the `.qp` file format.                            |
| `long_name`   | `str \| None`   | no       | Free-form human-readable name (used for axis labels, plot titles, result-array dimension names). |
| `units`       | `str \| None`   | no       | Unit string (e.g. `"Hz"`, `"ns"`, `"V"`). Carried into result coordinates.                       |
| `description` | `str \| None`   | no       | Longer description of what the variable represents.                                              |

**`label` rules.** Because the label is also the identifier in `.qp` files, it is restricted to a Python-style identifier:

- Must match `[A-Za-z_][A-Za-z0-9_]*` — letters, digits, underscores only; cannot start with a digit; no spaces or punctuation.
- Must be unique within a single `QProgram`. `program.variable("freq")` raises `ValueError` if `"freq"` is already declared.
- An invalid label raises `InvalidVariableLabelError` (a `ValueError` subclass) at construction time.

For anything richer than a short identifier — spaces, units, full sentences — use `long_name` and `description`. Examples:

```python
program.variable("freq", long_name="Drive frequency (q0)", units="Hz")
program.variable("t_pi", long_name="π-pulse duration", units="ns")
program.variable("phi",  long_name="Phase", units="rad", description="NCO phase offset for echo sequence")
```

The runtime executor still uses `label` for identity-bearing operations; `long_name`/`units`/`description` are pure metadata and never affect program semantics.
## 3.3 Building expressions
All arithmetic operators are supported on any `Expression`. Literals are auto-wrapped:
```python
t = program.variable("t")
amp = program.variable("amp")

100 + t                    # BinaryOp("+", Constant(100), t)
t - 50                     # BinaryOp("-", t, Constant(50))
amp * 2                    # BinaryOp("*", amp, Constant(2))
(t + 100) / 2              # BinaryOp("/", BinaryOp("+", t, Constant(100)), Constant(2))
-amp                       # UnaryOp("-", amp)
```
Supported operators: `+`, `-`, `*`, `/`, unary `-`, unary `+`. Operators compose freely: `freq * 2 - 50 + duration` produces a nested `BinaryOp` tree.
## 3.4 Where expressions are accepted
Anywhere an operation accepts `int | Expression` or `float | Expression`, you can pass a literal, a variable, or any expression:
```python
program.wait(bus, 100 + t)                    # int | Expression
program.set_frequency(bus, 5e9 + t * 1e6)     # float | Expression
program.set_gain(bus, amp / 2)
program.set_offset(bus, -amp)
program.play(bus, Gaussian(amplitude=amp, duration=40 + t, num_sigmas=2.5))
```
Waveform parameters accept Expressions on the same basis (Section 4.2).
## 3.5 Variables hold values; evaluating expressions
Each `Variable` carries its own current value. Initially every variable is `UNASSIGNED`. The runtime executor sets values per loop iteration via `set_value()`; expressions then evaluate by reading the current value of each variable.
```python
freq = program.variable("freq")
freq.value                # -> UNASSIGNED  (singleton sentinel, falsy)
freq.set_value(5e9)
freq.value                # -> 5e9
freq.evaluate()           # -> 5e9
freq.reset()
freq.value                # -> UNASSIGNED
```
`evaluate()` takes **no arguments** and returns a numeric value, or `UNASSIGNED` if any variable in the expression is currently unbound:
```python
expr = freq * 2 + 100
expr.evaluate()           # -> UNASSIGNED  (freq has no value)
freq.set_value(50)
expr.evaluate()           # -> 200
freq.reset()
expr.evaluate()           # -> UNASSIGNED again
```
The `UNASSIGNED` sentinel propagates through `BinaryOp` and `UnaryOp`: if any operand evaluates to `UNASSIGNED`, the whole expression does too.
## 3.6 evaluate_or_raise()
For code that needs a concrete numeric value (e.g. computing a waveform envelope), `Expression` provides a second method:
```python
def evaluate_or_raise(self) -> int | float
```
It calls `evaluate()` and either returns the numeric result or raises `UnassignedVariableError` (a `ValueError` subclass) if any variable is unassigned. This is the natural method to use when an `UNASSIGNED` result would not be meaningful:
```python
from qprogram import UnassignedVariableError

expr = freq * 2 + 100
expr.evaluate()              # UNASSIGNED  (if freq has no value)
expr.evaluate_or_raise()     # raises UnassignedVariableError

freq.set_value(50)
expr.evaluate()              # 200
expr.evaluate_or_raise()     # 200
```
The error carries the expression and its free variables for debugging:
```python
try:
    expr.evaluate_or_raise()
except UnassignedVariableError as e:
    print(e.expression)        # the offending expression
    print(e.free_variables)    # set of unassigned variables
```
Waveforms use `evaluate_or_raise()` internally on each parameter, so users can build them with symbolic parameters and call `.envelope()` once values are bound:
```python
amp = program.variable("amp")
g = Gaussian(amplitude=amp, duration=40, num_sigmas=2.5)

g.envelope()              # UnassignedVariableError: amp is UNASSIGNED
amp.set_value(0.7)
g.envelope()              # works — returns numpy array with peak —0.7
amp.set_value(1.0)
g.envelope()              # re-evaluates with the new value
```
No external helper function is required — every `Expression` instance carries both `evaluate()` and `evaluate_or_raise()` directly.
## 3.7 Free variables
`expression.variables()` returns the set of free variables that appear in an expression (recursively). The compiler uses this to determine which variables an operation depends on — independent of whether they currently hold a value:
```python
(freq + 100).variables()                          # -> {freq}
(freq + duration * 2 - 50).variables()            # -> {freq, duration}
Constant(5).variables()                           # -> set()
```
## 3.8 Identity
Variables use **identity-based** equality: each `Variable` instance is distinct from every other, regardless of label. Each gets an auto-assigned integer ID for hashing (not UUID, for performance).

Within a `QProgram`, labels must be unique — `program.variable("freq")` raises `ValueError` if `"freq"` was already declared, so two distinct variables on the same program never share a label. (At the bare `Variable` constructor level, you can still create two `Variable("freq")` instances; identity-based equality keeps them distinct.)

All other expression nodes (Constant, BinaryOp, UnaryOp) use **structural** equality — two structurally identical expressions compare as equal.
---
# 4. Waveforms
Waveforms define pulse shapes. They are pure data objects — they describe an envelope, not a hardware command.
## 4.1 Base Types
**`Waveform`** — abstract base for single-channel (real) waveforms.
- `envelope(resolution: int = 1) -> np.ndarray` — returns the amplitude at each time step
- `get_duration() -> int` — duration in nanoseconds
**`IQWaveform`** — abstract base for IQ (two-channel) waveforms.
- `get_I() -> Waveform` — in-phase component
- `get_Q() -> Waveform` — quadrature component
- `get_duration() -> int` — duration in nanoseconds
## 4.2 Variable-Aware Parameters
Waveform parameters accept both literal values and Variables. This allows waveform parameters to be swept inside loops — the compiler decides whether the sweep runs in hardware (if the parameter maps to a hardware register) or software (if it requires re-uploading the waveform).
```python
amp = program.variable("amp")

with program.for_loop(amp, start=0.0, stop=1.0, step=0.01):
    # amplitude is a Variable — swept each iteration
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, num_sigmas=2.5))
```
All numeric parameters in waveform constructors also accept `Variable`.
## 4.3 Built-in Waveforms
### Single-channel
**Square** — constant amplitude
```python
Square(amplitude: float | Variable, duration: int | Variable)
```
**Gaussian** — Gaussian-shaped pulse
```python
Gaussian(amplitude: float | Variable, duration: int | Variable, num_sigmas: float | Variable)
```
**GaussianDragCorrection** — derivative of Gaussian (DRAG Q component)
```python
GaussianDragCorrection(amplitude: float | Variable, duration: int | Variable, num_sigmas: float | Variable, drag_coefficient: float | Variable)
```
**Ramp** — linear interpolation between two amplitudes
```python
Ramp(from_amplitude: float | Variable, to_amplitude: float | Variable, duration: int | Variable)
```
**FlatTop** — square pulse with smoothed (erf) edges
```python
FlatTop(amplitude: float | Variable, duration: int | Variable, smooth_duration: int | Variable, buffer: int = 0)
```
**SuddenNetZero** — SNZ pulse shape for two-qubit gates
```python
SuddenNetZero(amplitude: float | Variable, duration: int | Variable, b: float | Variable, t_phi: int | Variable)
```
**Arbitrary** — user-provided sample array
```python
Arbitrary(samples: np.ndarray)
```
**Chained** — sequential concatenation of waveforms
```python
Chained(waveforms: list[Waveform])
```
### IQ waveforms
**IQPair** — pairs any two single-channel waveforms as I and Q
```python
IQPair(I: Waveform, Q: Waveform)   # I and Q must have same duration
```
**IQDrag** — DRAG pulse (Gaussian I + GaussianDragCorrection Q)
```python
IQDrag(amplitude: float | Variable, duration: int | Variable, num_sigmas: float | Variable, drag_coefficient: float | Variable)
```
## 4.4 Extensibility
Users can define custom waveforms by subclassing `Waveform` or `IQWaveform` and implementing the required abstract methods.
---
# 5. Operations
Operations are the instructions of the language. They are appended to the current active block when called on a QProgram.
## 5.1 Pulse Operations
These target a specific bus.
**`play(bus, waveform)`** — output a waveform on a bus
```python
program.play(bus: str, waveform: Waveform | IQWaveform | str)
```
If `waveform` is a string, it is an alias that must be resolved via `with_waveforms()` or by the platform before execution.
**`measure(bus, waveform, weights, save_adc=False)`** — play a readout pulse and acquire the result
```python
program.measure(bus: str, waveform: IQWaveform | str, weights: IQWaveform | str, save_adc: bool = False)
```
**`wait(bus, duration)`** — idle for a given duration (ns)
```python
program.wait(bus: str, duration: int | Variable)
```
**`sync(buses=None)`** — synchronize buses (all buses if None)
```python
program.sync(buses: list[str] | None = None)
```
## 5.2 Parameter Control Operations
These modify real-time parameters on a bus.
**`set_frequency(bus, frequency)`** — set NCO/oscillator frequency (Hz)
```python
program.set_frequency(bus: str, frequency: float | Variable)
```
**`set_phase(bus, phase)`** — set NCO phase (radians)
```python
program.set_phase(bus: str, phase: float | Variable)
```
**`reset_phase(bus)`** — reset NCO phase to zero
```python
program.reset_phase(bus: str)
```
**`set_gain(bus, gain)`** — set output gain
```python
program.set_gain(bus: str, gain: float | Variable)
```
**`set_offset(bus, offset_path0, offset_path1=None)`** — set DC offset
```python
program.set_offset(bus: str, offset_path0: float | Variable, offset_path1: float | Variable | None = None)
```
## 5.3 Platform Parameter Operations
These interact with the platform's configuration. They are not hardware-realtime — the compiler may execute them in software.
**`set_parameter(alias, parameter, value)`** — set a platform parameter
```python
program.set_parameter(alias: str, parameter: str, value: int | float | bool | Variable, channel_id: int | None = None)
```
`parameter` is a **string** (not an Enum). Each platform defines its own supported parameter names. Users can discover available parameters via the platform's API.
**`get_parameter(alias, parameter)`** — read a platform parameter into a variable
```python
var = program.get_parameter(alias: str, parameter: str) -> Variable
```
**`set_crosstalk(crosstalk)`** — apply a crosstalk correction matrix
```python
program.set_crosstalk(crosstalk: CrosstalkMatrix)
```
## 5.4 Vendor Extensions
The QProgram library provides a registration mechanism for vendor-specific operations. Core operations (play, measure, wait, etc.) are always available. Vendor operations are registered under a namespace and accessed via `program.<vendor>.<operation>()`.
### Architecture
The extension system has three layers:
1. **Operation classes** — subclass `Operation`, hold the data that goes into the AST
2. **VendorNamespace classes** — subclass `VendorNamespace`, provide typed methods that instantiate operations and append them to the active block
3. **Registration** — `QProgram.register_vendor()` connects a namespace to a name
### Step 1: Define Operation classes
Each vendor operation is a concrete `Operation` subclass with typed attributes. These are the nodes that live in the QProgram's block tree and get serialized to `.qp` files.
```python
from qprogram import Operation

class Acquire(Operation):
    def __init__(self, bus: str, weights: IQWaveform | str, save_adc: bool = False):
        self.bus = bus
        self.weights = weights
        self.save_adc = save_adc

    def get_variables(self) -> set[Variable]:
        return {v for v in [self.weights] if isinstance(v, Variable)}

class SetMarkers(Operation):
    def __init__(self, bus: str, mask: str):
        self.bus = bus
        self.mask = mask

class MeasureReset(Operation):
    def __init__(self, bus: str, waveform: IQWaveform | str, weights: IQWaveform | str,
                 control_bus: str, reset_pulse: IQWaveform | str,
                 trigger_address: int = 1, save_adc: bool = False):
        self.bus = bus
        self.waveform = waveform
        self.weights = weights
        self.control_bus = control_bus
        self.reset_pulse = reset_pulse
        self.trigger_address = trigger_address
        self.save_adc = save_adc
```
### Step 2: Define a typed VendorNamespace
The namespace class provides the typed methods that users call. Each method instantiates the corresponding Operation and appends it to the program's active block via `self._append()`. This is where **strong typing lives** — IDE autocompletion and mypy validation work because the methods have explicit signatures.
```python
from qprogram import VendorNamespace

class QbloxNamespace(VendorNamespace):
    def acquire(self, bus: str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        """Qblox-specific acquisition without play."""
        self._append(Acquire(bus=bus, weights=weights, save_adc=save_adc))

    def set_markers(self, bus: str, mask: str) -> None:
        """Set 4-bit marker mask."""
        self._append(SetMarkers(bus=bus, mask=mask))

    def measure_reset(self, bus: str, waveform: IQWaveform | str, weights: IQWaveform | str,
                      control_bus: str, reset_pulse: IQWaveform | str,
                      trigger_address: int = 1, save_adc: bool = False) -> None:
        """Active reset with conditional feedback."""
        self._append(MeasureReset(
            bus=bus, waveform=waveform, weights=weights, control_bus=control_bus,
            reset_pulse=reset_pulse, trigger_address=trigger_address, save_adc=save_adc
        ))
```
### Step 3: Register the vendor and its protocol version
```python
from qprogram import QProgram, register_vendor_version

QProgram.register_vendor("qblox", QbloxNamespace)
register_vendor_version("qblox", "0.1.0")
```
This is typically done at import time by the vendor library (e.g. in `qprogram_qblox/__init__.py`).
**About the version.** The string passed to `register_vendor_version` is the **vendor protocol version** — i.e. the version of the operation set this extension exposes. It is what `.qp` files refer to when they declare `require qblox 0.1`. The parser uses major.minor for compatibility checks (same major required; installed minor must be ≥ the file's minor); patch is informational. In practice, the vendor extension reads its own version once via `importlib.metadata.version("qprogram-qblox")` and registers that, so the version stays in `pyproject.toml` as the single source of truth.
### QProgram internals
The QProgram library provides the base classes and the dispatch mechanism:
```python
class VendorNamespace:
    """Base class for vendor operation namespaces."""
    def __init__(self, program: "QProgram"):
        self._program = program

    def _append(self, operation: Operation) -> None:
        """Append an operation to the program's active block."""
        self._program._active_block.append(operation)

class QProgram:
    _vendor_registry: ClassVar[dict[str, type[VendorNamespace]]] = {}

    @classmethod
    def register_vendor(cls, name: str, namespace_cls: type[VendorNamespace]) -> None:
        """Register a vendor namespace. Called at import time by vendor libraries."""
        cls._vendor_registry[name] = namespace_cls

    def __getattr__(self, name: str) -> VendorNamespace:
        if name in self._vendor_registry:
            # Lazy init + cache on the instance
            ns = self._vendor_registry[name](self)
            object.__setattr__(self, name, ns)
            return ns
        raise AttributeError(f"No vendor namespace '{name}' registered")
```
### Typing and distribution
Vendor extensions live outside the core QProgram library. They are distributed as separate packages (e.g. `pip install qprogram-qblox`) or bundled into a platform library (e.g. `pip install qililab`).
At runtime, `program.qblox` is resolved via `__getattr__` on the base `QProgram`. This works but provides no static typing. For **IDE autocomplete and mypy**, each vendor package provides two things:
**A typed mixin** — a class with a single `@property` returning the typed namespace:
```python
# In qprogram_qblox/mixin.py
class QbloxMixin:
    @property
    def qblox(self) -> QbloxNamespace: ...
```
**A pre-combined QProgram** — the mixin applied to the base:
```python
# In qprogram_qblox/__init__.py
class QProgram(QbloxMixin, qp.QProgram):
    pass
```
### Usage — single vendor
The simplest way: import `QProgram` from the vendor package instead of from `qprogram`:
```python
from qprogram_qblox import QProgram

program = QProgram(label="example")
program.play(q[0].drive, pulse)                # core operation — always typed
program.qblox.acquire(q[0].readout, weights)   # vendor operation — typed via mixin
program.qblox.set_markers(q[0].drive, "0001")  # IDE autocomplete works
```
### Usage — multiple vendors
When a platform uses multiple vendors (e.g. Qblox + QDAC), the vendor packages each export a mixin. The platform (or user) combines them:
```python
from qprogram_qblox import QbloxMixin
from qprogram_qdac import QdacMixin
from qprogram import QProgram as BaseQProgram

class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
    pass

program = QProgram()
program.qblox.acquire(...)   # typed
program.qdac.play(...)       # typed
```
In practice, the platform library (e.g. QiliLab) provides this combined class so users don't need to do the inheritance themselves.
### Package structure
A vendor extension package has four files:
```javascript
qprogram-qblox/
——— src/qprogram_qblox/
    ——— __init__.py      # Registration + pre-combined QProgram
    ——— operations.py    # Operation subclasses (AST nodes)
    ——— namespace.py     # QbloxNamespace (typed VendorNamespace)
    ——— mixin.py         # QbloxMixin (typed @property)
```
**`operations.py`** — `Operation` subclasses with typed attributes. These are the AST nodes:
```python
class Acquire(Operation):
    def __init__(self, bus: str, weights: IQWaveform | str, save_adc: bool = False): ...

class SetMarkers(Operation):
    def __init__(self, bus: str, mask: str): ...
```
**`namespace.py`** — `VendorNamespace` subclass with typed methods. IDE autocomplete comes from here:
```python
class QbloxNamespace(VendorNamespace):
    def acquire(self, bus: str, weights: IQWaveform | str, save_adc: bool = False) -> None:
        self._append(Acquire(bus=bus, weights=weights, save_adc=save_adc))

    def set_markers(self, bus: str, mask: str) -> None:
        self._append(SetMarkers(bus=bus, mask=mask))
```
**`mixin.py`** — single `@property` for static typing:
```python
class QbloxMixin:
    @property
    def qblox(self) -> QbloxNamespace: ...
```
**`__init__.py`** — performs all registration on import:
```python
# Register vendor namespace (runtime)
QProgram.register_vendor("qblox", QbloxNamespace)

# Register operations with .qp serializer
register_vendor_operation("qblox", "acquire", Acquire)
register_vendor_operation("qblox", "set_markers", SetMarkers)

# Register the protocol version (read from package metadata)
from importlib.metadata import version
register_vendor_version("qblox", version("qprogram-qblox"))

# Pre-combined typed QProgram
class QProgram(QbloxMixin, BaseQProgram):
    pass
```
### Serialization
The Operation class registry powers `.qp` serialization. Each vendor Operation is registered with its `(vendor, name)` pair:
- `Acquire` registered as `("qblox", "acquire")` serializes to `qblox.acquire "bus" "weights"`
- On deserialization, the parser looks up `("qblox", "acquire")` in the registry to reconstruct the typed object
### Portability
A `.qp` file containing vendor operations declares its dependencies via versioned `require` declarations (e.g. `require qblox 0.1`). The parser validates these upfront before parsing the body:
- if the vendor is not registered in the current environment, parsing fails with a clear error;
- if the major version doesn't match, parsing fails;
- if the installed minor is older than the file's minor, parsing fails.
This means a `.qp` file is a complete, executable contract: any environment that can parse it without error is guaranteed to recognise every operation referenced by it. See the .qp File Format Specification for the full syntax and compatibility rules. See the .qp File Format Specification for details.
---
# 6. Control Flow
Control flow is expressed via Python context managers that create nested blocks in the program tree.
## 6.1 Block Types
**`for_loop(variable, start, stop, step=1)`** — parametric sweep
```python
freq = program.variable("freq")
with program.for_loop(freq, start=4e9, stop=6e9, step=1e6):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", pulse)
    program.measure("readout_q0", readout, weights)
```
The compiler decides whether this runs as a hardware loop or software loop.
**`loop(variable, values)`** — sweep over an arbitrary array
```python
amp = program.variable("amp")
with program.loop(amp, values=np.linspace(0.0, 1.0, 100)):
    program.set_gain("drive_q0", amp)
    program.play("drive_q0", pulse)
```
**Parallel loops via ****`|`**** operator** — run multiple loops concurrently (replaces the current `parallel()` API)
Loops can be combined with the `|` operator to run in parallel. This works with any combination of loop types (`for_loop`, `loop`) as long as they have the same number of iterations.
```python
freq = program.variable("freq")
gain = program.variable("gain")
with program.for_loop(freq, 4e9, 6e9, 1e6) | program.for_loop(gain, 0.0, 1.0, 0.01):
    program.set_frequency("drive_q0", freq)
    program.set_gain("drive_q0", gain)
    program.play("drive_q0", pulse)
```
Different loop types can be combined freely:
```python
freq = program.variable("freq")
amp = program.variable("amp")
with program.for_loop(freq, 4e9, 6e9, 1e6) | program.loop(amp, values=custom_array):
    program.set_frequency("drive_q0", freq)
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, num_sigmas=2.5))
```
Chaining is supported: `a | b | c` creates a parallel block with three concurrent loops.
**`average(shots)`** — repeat and average results
```python
with program.average(shots=1000):
    program.play("drive_q0", pulse)
    program.measure("readout_q0", readout, weights)
```
**`block()`** — generic scope (for grouping)
```python
with program.block():
    program.play("drive_q0", pulse)
    program.wait("drive_q0", 100)
```
## 6.2 Nesting
Blocks can be arbitrarily nested. The program's `body` is the root block; all operations and blocks are descendants of it.
```python
with program.average(shots=1000):
    with program.for_loop(freq, 4e9, 6e9, 1e6):
        program.set_frequency("drive_q0", freq)
        program.play("drive_q0", pulse)
        program.measure("readout_q0", readout, weights)
```
## 6.3 Hardware vs Software Execution
The QProgram language makes **no distinction** between hardware and software loops. The compiler analyzes the block tree and decides:
- Blocks containing only pulse/timing operations **may** be compiled to hardware loops
- Blocks containing `set_parameter`, `get_parameter`, or `set_crosstalk` **require** software orchestration
- The same `for_loop` may run in hardware on one platform and in software on another.
This is intentional. Users describe *what* they want; the compiler decides *how*.
---
# 7. CrosstalkMatrix (?)
A `CrosstalkMatrix` models flux crosstalk between buses. It can be applied at runtime via `set_crosstalk()`.
```python
xtalk = qp.CrosstalkMatrix()
xtalk["flux_q0"] = {"flux_q0": 1.0, "flux_q1": 0.03}
xtalk["flux_q1"] = {"flux_q0": 0.02, "flux_q1": 1.0}
```
Methods: `to_array()`, `inverse()`, `from_array(buses, matrix)`, `from_buses(dict)`, `set_offset(dict)`, `set_resistances(dict)`.
---
# 8. Results
A `QProgramResult` is the in-memory object returned by a platform after executing a QProgram. It is provided by the QProgram library so that all platforms return a consistent result type.
## 8.1 Design
`QProgramResult` is an **in-memory Python object** by default — it is not tied to any file format or database. The current `ExperimentResults` class (which is coupled to HDF5 files) is refactored into this cleaner separation:
- **In-memory first.** `QProgramResult` stores all measurement data as `xarray.DataArray` objects in memory. Dimensions, coordinates, and labels are built into the data structure. No file<br>I/O happens unless explicitly requested.
- **Persistence is opt-in.** Saving to HDF5, database, or other formats are explicitly opted-in and configured via `QililabSettings (PydanticSettings)`, either programmatically or through environment variables.
```python
# Execute returns an in-memory result
result = platform.execute(program)

# Access data as xarray.DataArray                                                                                                                                                   
da = result.get(measurement=0)
da.dims          # ("freq", "gain", "IQ")                                                                                                                                           
da.coords        # freq: [4e9, ...], gain: [0.0, ...], IQ: ["I", "Q"]

# Select and slice with named dimensions                                                                                                                                            
I_values = da.sel(IQ="I")              # xarray.DataArray without IQ dim                                                                                                            
Q_values = da.sel(IQ="Q")                                                                                                                                                           
complex_s21 = I_values + 1j * Q_values
at_5ghz = da.sel(freq=5e9, method="nearest")                                                                                                                                        
                                                                                                                                                                                        
# Convert to other formats                                                                                                                                                          
da.to_numpy()        # raw numpy array                                                                                                                                              
da.to_dataframe()    # pandas DataFrame                                                                                                                                             
da.to_dict()         # dict
  

# Opt-in persistence — configured via QililabSettings (PydanticSettings)
# Can be set programmatically or via environment variables
from qililab import get_settings
settings = get_settings()
settings.result_save_hdf5 = True
settings.result_save_db = True
settings.result_hdf5_path = "/data/results/"
# Or via environment variables:
# QILILAB_RESULT_SAVE_HDF5=true
# QILILAB_RESULT_SAVE_DB=true
# QILILAB_RESULT_HDF5_PATH=/data/results/
```
## 8.2 Measurement Array Structure
For each `measure()` operation in the program, an `xarray.DataArray` is produced. The dimensions are determined by the loops the measurement is nested in:
```javascript
shape = (loop_1_iterations, loop_2_iterations, ..., loop_N_iterations, 2)
```
- **Dimensions 0 to N-1**: each corresponds to one enclosing loop, in nesting order (outermost first)
- **Last dimension**: always `2`, for I (in-phase) and Q (quadrature) values
	- `data[..., 0]` = I values
	- `data[..., 1]` = Q values
**Example:** a measurement inside two nested loops:
```python
freq = program.variable("freq")
gain = program.variable("gain")
with program.for_loop(freq, 4e9, 6e9, 1e6):         # 2001 iterations
    with program.for_loop(gain, 0.0, 1.0, 0.01):    # 101 iterations
        program.play("drive_q0", pulse)
        program.measure("readout_q0", readout, weights)
```
Produces a DataArray with:<br>- dims = ("freq", "gain", "IQ")<br>- shape = (2001, 101, 2)<br>- coords = \{freq: \[4e9, 4.000001e9, ...\], gain: \[0.0, 0.01, ...\], IQ: \["I", "Q"\]\}
## 8.3 Dimension Metadata
Since `xarray` carries dimension names and coordinates natively, there is no separate `DimensionInfo` class. All metadata is part of the `DataArray`:
```python
da = result.get(measurement=0)                                                                                                                                                      
da.dims                    # ("freq", "gain", "IQ")
da.coords["freq"].values   # np.array([4e9, 4.000001e9, ...])                                                                                                                       
da.coords["gain"].values   # np.array([0.0, 0.01, ...])                                                                                                                             
da.coords["IQ"].values     # np.array(["I", "Q"])
```
For parallel loops (via `|`), both variables share a single dimension. The dimension is named after both variables and both coordinate arrays are attached:
```python
da.dims                    # ("freq|gain", "IQ")                                                                                                                                    
da.coords["freq"].values   # np.array([...])
da.coords["gain"].values   # np.array([...]) 
```
## 8.4 Multiple Measurements
If the program contains multiple `measure()` calls (on the same or different buses), each produces its own array. Results are indexed by measurement order:
```python
da_m0 = result.get(measurement=0)   # first measure()
da_m1 = result.get(measurement=1)   # second measure()
```
Results can also be accessed by bus:
```python
result.get(bus="readout_q0", measurement=0)     # first measurement on readout_q0
```
---
# 9. Platform Protocol
The QProgram library defines a `PlatformProtocol` — a common interface that any execution backend must implement. This covers both **discovery** (what the platform supports) and **execution** (running a QProgram and returning results).
## 9.1 Discovery
Users can query the platform to inspect available resources before writing a program. Parameters are strings — each platform defines its own (replacing the old `Parameter` enum).
## 9.2 Execution
The platform compiles and executes a QProgram, returning a `QProgramResult`. Internally it is responsible for analyzing the block tree, deciding hardware vs software execution, allocating hardware resources, resolving calibrated references, and reporting errors for unsupported operations.
```python
class PlatformProtocol(ABC):
    def get_bus_schema(self) -> BusSchema: ...
    def get_buses(self) -> list[str]: ...
    def get_parameters(self, bus: str) -> list[str]: ...
    def get_global_parameters(self) -> list[str]: ...
    def execute(self, qprogram: QProgram, **kwargs) -> QProgramResult: ...
```
---
# 10. File Format (`.qp`)
QProgram defines its own text-based serialization format for portability. Programs can be saved and loaded without depending on any external serialization library.
```python
qp.save(program, "experiment.qp")    # save to file
program = qp.load("experiment.qp")   # load from file
text = qp.dumps(program)             # serialize to string
program = qp.loads(text)             # parse from string
```
The `.qp` format is an indentation-based language with sections: `metadata` and `body`. It maps 1:1 to the Python API.
Example:
```javascript
#!QProgram 1.0

metadata:
  label: "rabi"
  description: "Rabi oscillation"

body:
  var gain

  average 1000:
    for gain in range(0.0, 1.0, 0.01):
      set_gain "drive_q0" gain
      play "drive_q0" "pi_pulse"
      sync
      measure "readout_q0" "readout" "weights"
```
See the full specification: .qp File Format Specification (Draft)
---
# 11. Complete Example
```python
import qprogram as qp
from qprogram.buses import BusSchema
from qprogram.waveforms import IQDrag, IQPair, Square

# Setup bus schema for a flux-tunable transmon chip
schema = BusSchema.flux_tunable_transmon()
q = schema.q

# Create program using typed bus references and waveform aliases
program = qp.QProgram(label="rabi", description="Rabi oscillation")
gain = program.variable("gain")

with program.average(shots=1000):
    with program.for_loop(gain, start=0.0, stop=1.0, step=0.01):
        program.set_gain(q[0].drive, gain)
        program.play(q[0].drive, "pi_pulse")
        program.sync()
        program.measure(q[0].readout, "readout", "weights")

# Save to file
qp.save(program, "rabi.qp")

# Resolve waveform aliases with concrete values (e.g. from calibration data)
resolved = program.with_waveforms({
    "pi_pulse": IQDrag(0.5, 40, 2.5, 0.1),
    "readout": IQPair(Square(1.0, 2000), Square(0.0, 2000)),
    "weights": IQPair(Square(1.0, 2000), Square(1.0, 2000)),
})

# Execute on a platform — returns in-memory QProgramResult
result = platform.execute(resolved)

# Access results as xarray.DataArray
da = result.get(measurement=0)
I_values = da.sel(IQ="I")
Q_values = da.sel(IQ="Q")
print(I_values)
```
---
# 12. Open Questions
- [x] **`.qp`**** file format**: **Resolved** — custom text-based indentation language. See .qp File Format Specification subpage.
- [x] **Waveform parametrization in loops**: **Resolved** — waveforms are Variable-aware. Users can pass Variables as waveform parameters (see Section 4.2).
- [x] **Trigger operations**: **Resolved** — `set_trigger`, `wait_trigger`, `set_markers` move to `qblox.*` vendor namespace via the extension mechanism (Section 5.4).
- [x] **MeasureReset**: **Resolved** — `measure_reset` is a `qblox.*` vendor extension, not a core operation.
- [x] **`set_offset`**** dual path**: **Resolved** — core `set_offset` keeps a generic signature; Qblox-specific dual-path behavior is handled by the compiler.
- [x] **Variable arithmetic**: **Resolved** — expressions support `+`, `-`, `*`, `/`, and unary `-` via the `Expression` AST (Section 3).
- [ ] **Error model**: How should the compiler report unsupported operations? Exceptions? Warnings? A validation pass before compilation?
