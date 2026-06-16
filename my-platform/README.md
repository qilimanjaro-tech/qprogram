# my-platform — a worked example of QProgram per-bus capabilities

This package is a **teaching demo**, not a real device driver. It implements an
imaginary QPU — **MyPlatform** — that wires *different capabilities onto different bus
types* and shows what the QProgram capability/analyse layer does with them.

> If you only read one file, read [`myplatform_demo.ipynb`](./myplatform_demo.ipynb). It walks
> through everything below interactively, with live validator / planner / `explain()`
> output.

## The device

MyPlatform is a flux-tunable-transmon chip (`BusSchema.flux_tunable_transmon()`), so each
qubit exposes three buses — `drive`, `readout` and `flux`. Each bus type is backed by a
different piece of (imaginary) hardware, assembled from the two **real** vendor packages
in this monorepo:

| bus kind  | back-end                       | domains (hw / sw) | capability profile        |
|-----------|--------------------------------|-------------------|---------------------------|
| `drive`   | qblox real-time generator      | **hw + sw**       | `qblox-default-v1`        |
| `readout` | qblox real-time generator      | **hw + sw**       | `myplatform-readout-v1`   |
| `flux`    | qdac slow DAC (no FPGA)        | **sw only**       | `myplatform-flux-v1`      |

`drive` reuses the qblox vendor profile verbatim. `readout` and `flux` are **ad-hoc
profiles** defined in [`profiles.py`](./src/my_platform/profiles.py) that `extend` the
vendor profiles to tighten a per-slot limit. The platform-level slot (blocks, sweeps,
expressions, bus-less ops) is filled by the core `qprogram-base-v1` profile.

## Why this is interesting

Capabilities are declared **per `(element, bus_kind)` slot**, and each slot has an
independent **hardware** and **software** half (`BusCapabilities(hw, sw)`). Because the
flux bus has `hw=None` (the qdac has no FPGA), the validator's domain classifier reaches
different conclusions about the *same* control-flow construct depending on which bus it
drives:

* A `for_loop` that sweeps a **drive amplitude** stays real-time hardware — `[hw|sw]`.
* A `for_loop` that sweeps a **flux bias** is forced to software dispatch — `[sw]`, with a
  `forced-software` warning explaining why.
* A **qblox** op on the flux bus (or a **qdac** op on the drive bus) is a hard error —
  the buses speak different vendor dialects.
* Per-bus **limits and predicates** differ too: a sub-16 ns `Wait` is rejected on readout
  but fine on drive, and a `qdac.play` below MyPlatform's 200 ns dwell floor is caught by a
  platform-authored predicate.

These three knobs — capability **tokens**, numeric **limits**, and AST-shape
**predicates** — are the three axes a `CompilerCapabilities` carries, and the notebook
exercises all of them.

## Layout

```
my-platform/
├── pyproject.toml                  # platform package; editable deps on qprogram + both vendors
├── README.md                       # this file
├── myplatform_demo.ipynb               # the guided walkthrough (start here)
└── src/my_platform/
    ├── __init__.py                 # activates qblox + qdac, registers ad-hoc profiles
    ├── profiles.py                 # myplatform-readout-v1, myplatform-flux-v1 (ad-hoc)
    └── platform.py                 # MyPlatform(PlatformProtocol) — the capabilities live here
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
from my_platform import MyPlatform           # importing also activates qblox + qdac

platform = MyPlatform()
schema = platform.get_bus_schema()

prog = QProgram(label="rabi", schema=schema)
amp = prog.variable("amp", units="a.u.")
with prog.average(1000), prog.for_loop(amp, 0.0, 1.0, 0.05):
    prog.play(schema.q[0].drive, IQDrag(amplitude=amp, duration=40, sigma=10, beta=0.5))
    prog.measure(schema.q[0].readout, "readout_pulse", "weights", name="m0")

print(platform.explain(prog))                 # drive sweep -> [hw|sw], real time
```

See the notebook for the flux-sweep (forced-software), cross-vendor (hard-error), and
per-bus limit/predicate cases.
