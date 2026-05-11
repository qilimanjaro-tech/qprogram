# EuroSciPy 2026 — Poster Proposal (v3: experiment-intent framing)

**Submission URL:** https://pretalx.com/euroscipy-2026/submit/K6SKY6/info/
**Deadline:** 2026-05-24 23:59 (Poland)
**Note:** The CFP is **poster-only** this year. Reviews are anonymous, so the
abstract and description below are written without personal/affiliation
information; add author info via the speaker fields in pretalx.

This version frames QProgram as a story about **scientific-software
abstraction**: experiments as software artifacts, separation of experimental
intent from hardware, and the resulting wins for reproducibility, sharing,
and maintenance. The implementation patterns are mentioned where relevant
but are not the lead.

---

## Field 1: Title (10–200 characters)

> QProgram: A Python DSL for Pulse-Level Quantum Programming

(58 characters)

### Alternative titles

- "QProgram: Separating Experimental Intent from Hardware in Pulse-Level Quantum Programming" (90)
- "Reproducible Pulse-Level Quantum Experiments with QProgram" (58)
- "QProgram: A Python DSL for Portable, Reproducible Pulse-Level Quantum Experiments" (81)

---

## Field 2: Track

**Primary fit:** *Computational Tools and Scientific Python Infrastructure*

The poster is framed around scientific-software design choices —
reproducibility, portability, separation of intent from instrument — rather
than on the application physics, so this track is the strongest fit for
EuroSciPy reviewers.

**Secondary alternative:** *Physical Sciences and Engineering* — if reviewers
prefer to weight the application domain (quantum computing).

---

## Field 3: Abstract (200–1500 characters)

```text
Quantum experiments are often written tightly coupled to a specific control
stack: the scientific question, timing assumptions, calibration choices,
and vendor-specific control instructions all live in the same layer of
code. This makes experiments hard to share, reproduce, review, and move
between setups.

QProgram is a Python DSL for describing pulse-level quantum experiments at
the level of experimental intent. Instead of tying an experiment directly
to one control system, QProgram lets users express what should happen and
defer hardware-specific details to backend extensions and compilation
layers. The same experiment description can be inspected, serialised to a
versioned file format, remapped to a different setup, or extended with
vendor-specific operations when those are genuinely needed.

This poster presents why this abstraction matters for quantum research
workflows, how QProgram separates portable experiment logic from
device-specific operations, and how this design supports reproducibility,
collaboration, and long-term maintainability in scientific-Python
quantum-control software.
```

---

## Field 4: Description (400–50,000 characters, Markdown OK)

```markdown
## Why pulse-level experiments are hard to share

Quantum experiments are software artifacts as much as laboratory
procedures. A pulse-level experiment typically encodes the scientific
question, the measurement protocol, calibration assumptions, hardware
channels, waveform choices, acquisition settings, synchronisation details,
and vendor-specific control features. When all of this is written directly
against a single hardware API, the resulting code can work well in one lab
while becoming difficult to reuse, compare, or reproduce elsewhere.

## A separation of concerns

QProgram is designed around a simple idea: **the experiment should be
represented independently from the control system that eventually runs
it.** Researchers should be able to describe the intent of an experiment
in Python, keep that description inspectable and serialisable, and only
later bind it to concrete hardware details.

This separation matters because quantum hardware changes quickly. A lab
may upgrade control electronics, compare vendors, maintain several device
generations, or run similar experiments across different cryostats.
Without an abstraction layer, each such change can force experiment code
to be rewritten. With QProgram, the stable part of the workflow is the
experiment description; setup-specific details are handled through
mappings and backend extensions.

## A small example

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

The example contains no vendor-specific control instructions; the same
description can be compiled against different control stacks once a
backend and a calibration are bound.

## How QProgram achieves this in practice

- A user writes a quantum experiment in Python using a compact
  pulse-programming interface.
- The experiment is stored as a structured, inspectable representation
  rather than as an opaque script — the program object can be walked,
  diffed, validated, and unit-tested.
- Hardware names and calibrated pulse definitions can be attached or
  remapped separately from the experiment logic, so the same program can
  run against different qubits or different chip layouts by changing the
  mapping rather than the experiment.
- The program can be serialised to a readable, versioned `.qp` text
  format for review, storage, or exchange between teams. Required vendor
  extensions are declared in the file header so missing dependencies
  fail fast.
- Vendor-specific features (markers, conditional reset, vendor-specific
  acquisition modes, …) are added through extension namespaces, so
  backend capabilities remain available without making the core
  experiment description vendor-dependent.

For example, a generic experiment can express "drive the qubit, measure
the response, repeat over a sweep" without committing the whole program
to one hardware vendor. If a particular backend exposes extra
capabilities, those can be accessed through an extension while preserving
the same overall programming model.

## What the poster shows

The poster illustrates the separation of concerns with concrete examples:
the same experiment description, paired with different mappings,
backends, and extensions. It walks through three design decisions in
detail (symbolic parameters and sweeps, typed hardware references,
plugin-based vendor extensions); the remaining choices (the `.qp` file
format itself, the xarray-based result objects) are summarised at the
bottom of the poster and discussed verbally during the session.

Two reusable patterns are highlighted for attendees building their own
scientific-Python libraries:

- **Typed identifiers that remain strings.** A pattern for human-readable
  identifiers that gain IDE autocomplete and static type-checking while
  remaining ordinary strings for downstream consumers — applicable any
  time a library has stringly-typed keys it wants to make safer without
  breaking compatibility.
- **Plugin-based extensions with type safety.** Each plugin package
  exports a small typed entry point; users (or a platform library)
  compose them to get type-safe access to all installed plugins with no
  central registry — applicable to any extensible library where IDE
  ergonomics matter.

## Why this matters

The poster's broader message is a scientific-software lesson:
**abstractions are useful when they preserve the language of the
experiment while isolating the parts that change across instruments,
calibrations, and vendors.** QProgram applies this principle to
pulse-level quantum programming, aiming to make quantum experiments
easier to reproduce, port, and maintain in Python-based research
workflows. The same principle applies to many hardware-near
scientific-Python libraries; attendees building such tools should leave
with patterns they can apply directly.

## Audience

Anyone interested in scientific-software design, reproducibility,
extensibility, plugin architectures, custom file formats, or
quantum-computing tooling should find practical patterns applicable to
their own work. No prior quantum-computing background is required —
domain concepts (qubit, drive line, readout) are introduced in passing.
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
