# my-platform — a worked example of QProgram per-bus capabilities (and a vendor extension)

This package is a **teaching demo**, not a real device driver. It implements an
imaginary QPU — **MyPlatform** — that wires *different capabilities onto different bus
types* and shows what the QProgram capability/analyse layer does with them.

It wears **two hats**. It is a *platform* (declares per-bus capabilities, validates/explains/
executes programs against them) **and** a *vendor extension*: it ships a brand-new `BusSchema`
(an RF-switch matrix), the `myplatform` namespace (`program.myplatform.*`), two vendor
operations, and its own capability profiles — all without touching the core library. That makes
it a single package that exercises *both* extension surfaces this monorepo demonstrates.

> If you only read one file, read [`myplatform_demo.ipynb`](./myplatform_demo.ipynb). It walks
> through everything below interactively, with live validator / planner / `explain()`
> output.

## The device

MyPlatform is a flux-tunable-transmon chip **combined with an RF-switch matrix**. It builds that
combined topology with the core schema-composition operator —
`BusSchema.flux_tunable_transmon() + RFSwitchSchema()` — where `RFSwitchSchema` is the new schema
this package defines. Each qubit exposes three buses — `drive`, `readout`, `flux` — and each switch
exposes one `rf` control line. Every bus type is backed by a different piece of (imaginary) hardware:

| bus kind  | back-end                       | domains (hw / sw) | capability profile        |
|-----------|--------------------------------|-------------------|---------------------------|
| `drive`   | qblox real-time generator      | **hw + sw**       | `qblox-default-v1`        |
| `readout` | qblox real-time generator      | **hw + sw**       | `myplatform-readout-v1`   |
| `flux`    | qdac slow DAC (no FPGA)        | **sw only**       | `myplatform-flux-v1`      |
| `rf`      | MyPlatform RF switch (fast)    | **hw + sw**       | `myplatform-rfswitch-v1`  |

`drive` reuses the qblox vendor profile verbatim. `readout` and `flux` are **ad-hoc
profiles** defined in [`profiles.py`](./src/my_platform/profiles.py) that `extend` the
vendor profiles to tighten a per-slot limit; `flux` additionally publishes MyPlatform's own
`set_crosstalk` op. `rf` is a fresh profile carrying MyPlatform's own `set_rf_switch` op plus the
core timing ops (`op.sync` / `op.wait`) so the switch can be aligned with the real-time pulse
program. The platform-level slot (blocks, sweeps, expressions, bus-less ops) is filled by the core
`qprogram-base-v1` profile.

The qubit buses read `q0/drive`, `q0/flux`, …; the switch buses read `switch0/rf`,
`switch1/rf`, … (qubits and switches share the schema's single naming convention).

## Why this is interesting

Capabilities are declared **per `(element, bus_kind)` slot**, and each slot has an
independent **hardware** and **software** half (`BusCapabilities(hw, sw)`). Because the
flux bus has `hw=None` (the qdac has no FPGA), the validator's domain classifier reaches
different conclusions about the *same* control-flow construct depending on which bus it
drives:

* A `for_loop` that sweeps a **drive amplitude** stays real-time hardware — `[hw|sw]`.
* A `for_loop` that sweeps a **flux bias** is forced to software dispatch — `[sw]`, with a
  `forced-software` warning explaining why.
* An `average` *enclosing* that flux sweep is software too — but only because of the nesting.
  Since averaging only accumulates **measurements** (the `AFFECTS_AVERAGING` op flag), the
  classifier flags it as **reorderable**, and `qp.optimize(program, caps)` rewrites the loops so
  the averaging runs in hardware.
* A **qblox** op on the flux bus (or a **qdac** op on the drive bus) is a hard error —
  the buses speak different vendor dialects. The same is true of MyPlatform's own ops:
  `myplatform.set_rf_switch` is legal only on a `switch/rf` bus, `myplatform.set_crosstalk`
  only on a flux bus.
* A `for_loop` that sweeps an **RF-switch channel** stays real-time hardware — `[hw|sw]` —
  because the switch is a fast device (its profile fills both domains), unlike the flux DAC.
* Per-bus **limits and predicates** differ too: a sub-16 ns `Wait` is rejected on readout
  but fine on drive, and a `qdac.play` below MyPlatform's 200 ns dwell floor is caught by a
  platform-authored predicate.

These three knobs — capability **tokens**, numeric **limits**, and AST-shape
**predicates** — are the three axes a `CompilerCapabilities` carries, and the notebook
exercises all of them.

## MyPlatform as a vendor extension

Beyond declaring capabilities, MyPlatform *adds* DSL surface — the other extension pattern
this monorepo demonstrates (see `qprogram-qblox` / `qprogram-qdac`):

* **A new schema.** [`schema.py`](./src/my_platform/schema.py) defines `RFSwitchSchema` (a
  fresh `BusSchema` whose `switch[i].rf` buses model a microwave routing matrix). MyPlatform
  **combines** it with the core flux-tunable-transmon preset using the schema-composition operator —
  `BusSchema.flux_tunable_transmon() + RFSwitchSchema()` (the `+` / `BusSchema.combine` mechanism is
  itself a small core addition; either operand may be a schema class or instance, and `+` chains).
* **Two vendor operations** ([`operations.py`](./src/my_platform/operations.py),
  [`namespace.py`](./src/my_platform/namespace.py)):
  * `program.myplatform.set_crosstalk(flux_bus, matrix)` — installs an `N×N` flux
    crosstalk-compensation matrix (a NumPy array). Valid on any flux-like bus: `flux` on a
    flux-tunable transmon, or `flux_x` / `flux_z` on a fluxonium — because the token lives on
    the flux profile, which a fluxonium platform can reuse on those slots.
  * `program.myplatform.set_rf_switch(switch_bus, channel)` — routes a switch to an output
    port; `channel` may be a swept expression.
* **Registration & auto-activation.** Importing `my_platform` registers the `myplatform`
  namespace, version, operations, tokens, and profiles. The package declares a
  `qprogram.vendors` entry point, so a `.qp` file carrying `require myplatform 0.1`
  auto-activates it on `loads()` — no explicit import needed.

Both ops round-trip through `.qp` (`set_crosstalk` ships a small custom serializer because its
matrix is 2-D; `set_rf_switch` uses the signature-driven default).

## Layout

```
my-platform/
├── pyproject.toml                  # platform + vendor package; editable deps on qprogram + both vendors
├── README.md                       # this file
├── myplatform_demo.ipynb           # the guided walkthrough (start here)
├── src/my_platform/
│   ├── __init__.py                 # activates qblox + qdac; registers myplatform vendor + profiles
│   ├── schema.py                   # RFSwitchSchema (new schema; combined with FTT via `+`)
│   ├── operations.py               # SetCrosstalk, SetRFSwitch (vendor ops)
│   ├── namespace.py                # MyPlatformNamespace — program.myplatform.*
│   ├── mixin.py                    # MyPlatformMixin (typed .myplatform for IDE autocomplete)
│   ├── profiles.py                 # myplatform-readout-v1 / -flux-v1 / -rfswitch-v1 (ad-hoc)
│   └── platform.py                 # MyPlatform(PlatformProtocol) — the capabilities live here
└── tests/                          # schema, vendor ops, serialization, capabilities, auto-activation
```

## Setup

```bash
cd my-platform
uv sync                 # creates .venv with qprogram, qprogram-qblox, qprogram-qdac (editable)
uv run jupyter lab      # open myplatform_demo.ipynb
```

## Quick taste

```python
from qprogram import QProgram
from qprogram.waveforms import IQDrag
from my_platform import MyPlatform           # importing also activates qblox + qdac + myplatform

platform = MyPlatform()
schema = platform.get_bus_schema()           # flux-tunable transmon + RF switch, combined

prog = QProgram(label="rabi", schema=schema)
amp = prog.variable("amp", units="a.u.")
prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])   # vendor op, flux bus
prog.myplatform.set_rf_switch(schema.switch[0].rf, 2)                        # vendor op, switch bus
with prog.average(1000), prog.for_loop(amp, 0.0, 1.0, 0.05):
    prog.play(schema.q[0].drive, IQDrag(amplitude=amp, duration=40, sigma=10, beta=0.5))
    prog.measure(schema.q[0].readout, "readout_pulse", "weights", name="m0")

print(platform.explain(prog))                 # set_crosstalk -> [sw], set_rf_switch -> [hw|sw]
```

See the notebook for the flux-sweep (forced-software), cross-vendor (hard-error),
per-bus limit/predicate, and RF-switch / crosstalk cases.
