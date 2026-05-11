# EuroSciPy 2026 — Poster Proposal (v2: big-picture framing)

**Submission URL:** https://pretalx.com/euroscipy-2026/submit/K6SKY6/info/
**Deadline:** 2026-05-24 23:59 (Poland)
**Note:** The CFP is **poster-only** this year. Reviews are anonymous, so the
abstract and description below are written without personal/affiliation
information; add author info via the speaker fields in pretalx.

This version of the proposal focuses on **what QProgram does and why it
matters** — extensibility, portability, the versioned file format, hardware-
agnostic semantics — rather than on the Python implementation idioms used
to build it. Compare with `euroscipy_2026_poster_proposal.md` (v1).

---

## Field 1: Title (10–200 characters)

> QProgram: A Python DSL for Pulse-Level Quantum Programming

(58 characters)

### Alternative titles

- "QProgram: A Portable, Extensible DSL for Pulse-Level Quantum Programming" (73)
- "Hardware-Agnostic Pulse Programming for Quantum Computers" (57)
- "QProgram: Bringing Portability and Extensibility to Pulse-Level Quantum Programming" (84)

---

## Field 2: Track

**Primary fit:** *Computational Tools and Scientific Python Infrastructure*

(QProgram is a Python tool/library aimed at the scientific-computing audience;
the poster focuses on what the tool offers and how it composes with the
scientific-Python ecosystem, not on its application physics.)

**Secondary alternative:** *Physical Sciences and Engineering* — if the
reviewers prefer to weight the application domain (quantum computing).

---

## Field 3: Abstract (200–1500 characters)

```text
QProgram is a Python domain-specific language (DSL) for pulse-level quantum
programming — the layer below circuits, where individual microwave pulses
drive qubits. While quantum computing frameworks like Qiskit and Cirq
operate at the gate level, calibrating real hardware requires direct
control over pulses on each control line. Existing tools tend to be tightly
coupled to specific instrument vendors: programs become non-portable, hard
to share, and hard to test.

QProgram inverts this. The same program runs on any backend that ships a
compiler. Programs are extensible — vendors add their own operations
through plugin packages, users add their own waveform types — without
touching the core. Programs are portable: a versioned `.qp` text file
format with explicit dependency declarations lets one team write an
experiment and another team load it on different hardware. And programs
describe *what* a user wants, not *how* a particular FPGA realises it:
symbolic parameters and sweeps are first-class, and the compiler decides
whether each loop runs in hardware or software.

This poster walks through the design decisions that make this possible:
the symbolic expression system used for parameters, a schema-based system
for typed hardware references, plugin-based vendor extensions, and the
`.qp` file format itself.
```

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
instrument vendors. Bus names get hardcoded as strings tied to one runcard
format. Operations are split into "hardware loops" and "software loops"
because of how that vendor's FPGA happens to work — even though the user
shouldn't care. Programs become non-portable, difficult to share, and hard
to unit-test. Knowledge accumulated in one team rarely transfers to another.

## QProgram

QProgram is a hardware-agnostic Python DSL designed to make pulse programs
**portable, extensible, and self-documenting**. A small example:

```python
import qprogram as qp

schema = qp.BusSchema.flux_tunable_transmon()
q = schema.q

program = qp.QProgram(label="rabi", description="Rabi oscillation")
duration = program.variable("Duration (ns)")

with program.average(shots=1000):
    with program.for_loop(duration, start=0, stop=10_000, step=100):
        program.play(q[0].drive, qp.Gaussian(amplitude=1.0, duration=40, num_sigmas=2.5))
        program.wait(q[0].drive, duration)
        program.sync()
        program.measure(q[0].readout, "readout", "weights")
```

The DSL is built around four principles.

### 1. Extensibility from the start

Vendors add their own operations through plugin packages. Importing a
package such as `qprogram-qblox` registers vendor-specific operations
(`acquire`, `set_markers`, `measure_reset`, …) under a `program.qblox.*`
namespace, with full IDE autocomplete and type checking. Users likewise
add their own waveform types by subclassing the `Waveform` interface.
Multiple vendors compose cleanly, so a chip driven by Qblox + QDAC
instruments gets `program.qblox.*` and `program.qdac.*` on the same
program object without conflict, and without the core library having to
know about either vendor.

### 2. A versioned, portable file format

Programs are serialized to `.qp` — a small, human-readable, text-based
format with a versioned header (`#!qp 1.0`). Vendor operations declare
their dependencies upfront with `require qblox`-style lines, so a program
that uses `qblox.acquire` fails fast on a host that doesn't have the
qblox extension installed. The format is parsed by a pure-Python parser
with no YAML/JSON dependencies, and round-trips perfectly with the
in-memory representation. One team can write an experiment, save it to
`.qp`, and another team can load and run it on different hardware.

### 3. Symbolic parameters; hardware/software-agnostic loops

Parameters in a QProgram can be variables, expressions, or literals
interchangeably. A waveform amplitude can be a `Variable` whose value is
bound at runtime; a wait duration can be `100 + offset` where `offset`
is a swept variable. Loops are written without specifying whether they
should run on the FPGA or in Python — the compiler decides per backend.
This eliminates a class of leaky abstractions and lets the same source
program run efficiently on different hardware.

### 4. Type-safe hardware references

Bus names are typed: instead of error-prone strings like `"drive_q0_bus"`,
users write `q[0].drive`, with IDE autocomplete telling them which
control lines a chip exposes (`drive`, `readout`, `flux`, `flux_x`, …).
Schema presets ship for common qubit types (transmon, flux-tunable
transmon, fluxonium); custom chip topologies are easy to add. The bus
reference still behaves as a plain string everywhere downstream, so the
abstraction is zero-overhead.

## Standard scientific-Python data

`measure()` calls produce `xarray.DataArray` objects with named loop
dimensions and IQ coordinates. Standard scientific-Python tooling —
slicing by name, plotting, persistence — works without any custom result
API. Persistence to HDF5 is opt-in.

## What the poster shows

The poster focuses on three of the design decisions in detail (symbolic
parameters, typed hardware references, plugin-based vendor extensions),
with the remaining choices summarised at the bottom of the poster and
discussed verbally during the session. Each featured decision gets one
short code sample and one architecture diagram.

Two reusable patterns are highlighted for attendees building their own
libraries:

- **Typed identifiers that are still strings.** A pattern for
  human-readable identifiers that get IDE autocomplete and static
  type-checking, while remaining ordinary strings for downstream
  consumers — applicable any time a library has stringly-typed keys it
  wants to make safer without breaking compatibility.
- **Plugin-based extensions with type safety.** Each plugin package
  exports a small typed entry point; users (or a platform library)
  compose them to get type-safe access to all installed plugins with no
  central registry — applicable to any extensible library where IDE
  ergonomics matter.

## Audience

Anyone interested in extensible Python libraries, plugin architectures,
DSL design, custom file-format design, or quantum-computing tooling
should find practical patterns they can apply to their own
scientific-Python projects. No prior quantum-computing background is
required — the poster keeps to the design angle and introduces domain
concepts (qubit, drive line, readout) in passing.
```

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
