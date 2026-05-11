# EuroSciPy 2026 — Poster Proposal

**Submission URL:** https://pretalx.com/euroscipy-2026/submit/K6SKY6/info/
**Deadline:** 2026-05-24 23:59 (Poland)
**Note:** The CFP is **poster-only** this year. Reviews are anonymous, so the
abstract and description below are written without personal/affiliation
information; add author info via the speaker fields in pretalx.

---

## Field 1: Title (10–200 characters)

> QProgram: A Python DSL for Pulse-Level Quantum Programming

(58 characters)

### Alternative titles

- "Pulse-Level Quantum Programming with QProgram, a Python DSL" (60)
- "Designing a Hardware-Agnostic Pulse Programming DSL in Python" (62)
- "QProgram: Symbolic Expressions, Custom File Formats, and Vendor Extensions in Pure Python" (90)

---

## Field 2: Track

**Primary fit:** *Computational Tools and Scientific Python Infrastructure*

(QProgram is a programming-language tool built using scientific Python idioms;
the poster focuses on DSL design and Python-tooling tradeoffs more than on the
quantum-physics application.)

**Secondary alternative:** *Physical Sciences and Engineering* — if the
reviewers prefer to weight the application domain (quantum computing).

---

## Field 3: Abstract (200–1500 characters)

```text
QProgram is a Python domain-specific language (DSL) for pulse-level quantum
programming — the layer below circuits, where individual microwave pulses
drive qubits. While quantum computing frameworks like Qiskit and Cirq operate
at the gate level, calibrating real hardware requires direct control over
pulses on each control line. Existing tools tend to be either vendor-specific
or low-level, leaving experimentalists to write platform-coupled scripts.

QProgram applies modern Python idioms — context managers for control flow,
operator overloading for symbolic expressions, abstract base classes for AST
nodes, and TypeAlias unions for typed extensibility — to build a hardware-
agnostic DSL. Programs are composed from waveforms, operations, and nested
loops; numeric parameters can be literal values or symbolic expressions;
vendor-specific operations are added via a plugin mechanism with full IDE
autocomplete; results are returned as xarray.DataArray.

This poster walks through the design decisions: why Python made a good host
for the DSL, the AST design for variables and expressions, the bus-schema
system for typed hardware references, and the custom .qp text file format
with a pure-Python parser.
```

(Roughly 1240 characters with newlines collapsed; well within 200–1500.)

---

## Field 4: Description (400–50,000 characters, Markdown OK)

```markdown
## Background

Quantum computing is typically programmed at the *circuit level* — sequences
of high-level gates like H, CNOT, RZ. Underneath, every gate decomposes into
precisely shaped microwave pulses on dedicated control lines: a drive line
for each qubit, a flux line for tunable couplers, a readout line for state
discrimination. Calibrating these pulses is where real experimental physics
happens, and where tooling has lagged behind circuit-level frameworks.

## The problem

Existing pulse-programming tools tend to be tightly coupled to specific
instrument vendors. Users write platform-specific code: bus names hardcoded
as strings, parameters typed as enums tied to one runcard format, "hardware
loops" separated from "software loops" because of how that vendor's FPGA
works. Programs become non-portable, difficult to share, and hard to unit-
test.

## QProgram: a hardware-agnostic DSL

QProgram is a pure-Python domain-specific language designed to invert this.
A QProgram describes *what* a user wants — "play this pulse, sweep this
frequency, measure this bus" — not *how* a particular vendor's hardware
should do it. Compilers translate the program into vendor instructions; the
DSL itself stays portable.

A small example:

```python
import qprogram as qp

schema = qp.BusSchema.flux_tunable_transmon()
q = schema.q

program = qp.QProgram(label="rabi", description="Rabi oscillation")
duration = program.variable("Duration (ns)")

with program.average(shots=1000):
    with program.for_loop(wait_duration, start=0, stop=10_000, step=100):
        program.play(q[0].drive, qp.Gaussian(amplitude=1.0, duration=40, num_sigmas=2.5))
        program.wait(q[0].drive, duration)
        program.sync()
        program.measure(q[0].readout, "readout", "weights")
```

## Selected design decisions

The poster focuses on three design decisions in detail; the remaining
choices — a custom `.qp` text file format with a pure-Python recursive-descent
parser (no PyYAML/ruamel dependency), and `xarray.DataArray`-based result
objects — are summarised at the bottom of the poster and discussed verbally
during the session.

**Symbolic expressions as a small AST.** Variables, constants, and arithmetic
operators form a tree of `Expression` nodes. Variables carry their current
value and an `UNASSIGNED` sentinel; expressions provide both `evaluate()`
(returns numeric or `UNASSIGNED`) and `evaluate_or_raise()` (raises
`UnassignedVariableError`). This is enough to drive parameter sweeps in
waveform shapes — `Gaussian(amplitude=amp_var, duration=40, num_sigmas=2.5)`
works whether `amp_var` is bound or not.

**Typed hardware references.** The `BusSchema` system replaces stringly-typed
bus names like `"drive_q0_bus"` with `q[0].drive`, `c[0,1].flux`, and so on.
Schema presets (`transmon`, `flux_tunable_transmon`, `fluxonium`) ship typed
buses classes so IDE autocomplete works for `q[0].<TAB>`. `BusRef` subclasses
`str`, so internally everything stays a string — zero changes to the AST or
serializer. Channel-type metadata (single-channel vs IQ; ADC-equipped or not)
is carried on the `BusRef` and used to reject incompatible operations at
program-construction time.

**Vendor extensions with type safety.** Vendors extend the DSL by registering
operation classes and a `VendorNamespace`. A separate `qprogram-qblox`
package adds `program.qblox.acquire(...)`, `program.qblox.set_markers(...)`,
etc., with full IDE support via a mixin class. Multiple vendors compose via
multiple inheritance — `class QProgram(QbloxMixin, QdacMixin, BaseQProgram)`.
Importing the vendor package registers operation classes for serialization
in one go.

## What the poster shows

The poster walks through each of the three featured design decisions with
short code samples and one architecture diagram per decision, and highlights
two reusable Python patterns that EuroSciPy attendees may find applicable to
their own DSL or library design:

- **Typed `str` subclasses.** How to make a `str` subclass (`BusRef`)
  interoperate cleanly with static type checkers, so users get IDE
  autocomplete on a value that is still — at the AST and serializer level —
  just a string. Zero changes to downstream code; pure ergonomics.
- **Mixin-based vendor extensions.** Each vendor package exports a small
  mixin class with a single typed `@property`; users (or a platform library)
  compose them via multiple inheritance to get type-safe access to all
  installed vendors with no central registry — every vendor extension is
  self-contained and IDE-discoverable.

## Audience

Anyone interested in DSL design, type-safe APIs, plugin architectures, or
quantum computing tooling should find practical patterns they can apply to
their own scientific-Python projects. No prior quantum computing background
is required — the poster sticks to the Python-tooling angle and explains
domain concepts (qubit, drive line, readout) in passing.
```

(Roughly 4500 characters; well within 400–50,000.)

---

## Field 5: Notes for organizers (optional)

```text
The work described is publicly available open-source software; the poster
will reference repositories at the conference. The author is available
on-site for the full poster session.
```

---

## Submission checklist

- [ ] Copy title into pretalx
- [ ] Select track: Computational Tools and Scientific Python Infrastructure
- [ ] Paste abstract (verify char count is ≥ 200 and ≤ 1500)
- [ ] Paste description (verify char count is ≥ 400 and ≤ 50,000)
- [ ] Optionally fill notes for organizers
- [ ] Add speaker bio in the speaker section (separate from anonymous review fields)
- [ ] Submit before 2026-05-24 23:59 Poland time
