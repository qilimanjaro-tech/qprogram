# Running programs — the reference executor

Core qprogram ships a complete software platform: `ReferencePlatform`, plus the
one-liner `qp.run()`. It validates, interprets the AST in pure Python, and
returns a real `QProgramResult` of xarray `DataArray`s — making every demo
end-to-end and serving as the **reference semantics** vendor compilers are
tested against.

## The quickest path

```python
import numpy as np
import qprogram as qp
from qprogram import MockMeasurementModel
from qprogram.waveforms import Gaussian, IQPair, Square

model = MockMeasurementModel(
    response=lambda bus, env: np.sin(np.pi * env["g"] / 2) ** 2 + 0j,
    noise=0.02,
    seed=7,
)

p = qp.QProgram(label="rabi")
g = p.variable("g")
with p.average(1000), p.for_loop(g, 0.0, 1.0, 0.01):
    p.play("drive_q0", Gaussian(amplitude=g, duration=40, sigma=8))
    p.sync()
    p.measure("readout_q0", IQPair(Square(1.0, 2000), Square(0.0, 2000)), "weights")

result = qp.run(p, model=model)
da = result.get("m0")          # dims ("g", "IQ"), coords from the sweep
da.sel(IQ="I").plot()          # a noisy Rabi oscillation
```

## Result shapes (spec §8, pinned)

- One record per `measure()`; dims are the enclosing `for_loop`/`loop` sweeps
  (outermost first), named by the loop variable's id, with the sweep values as
  coordinates. Parallel loops share one `"a|b"` dimension carrying both
  variables' coords.
- `average(shots)` contributes **no** dimension: `iq`/`raw` are means over
  shots; `state` becomes the excited-state **population** (0/1 outside
  averaging).
- Multiple return tokens live side by side: `result.get("m0")` is the primary
  array (the `"iq"` field), `result.get("m0", field="state")` has no IQ dim,
  `result.get("m0", field="raw")` adds a `"time"` dimension.
- A measurement inside a conditional arm is **NaN** at sweep points where the
  arm never executed.

## Measurement models

The executor asks a `MeasurementModel` for one `sample(bus, env)` per shot.
`env` holds the currently bound loop variables (by id) and the platform
parameters (by `"alias.parameter"`), so the simulated response can follow the
sweep — a Lorentzian for spectroscopy, `sin²` for Rabi, anything. The default
`MockMeasurementModel` is deterministic given its `seed`:

| Argument | Meaning |
|---|---|
| `response(bus, env) -> complex` | Noiseless IQ point (default `0j`). |
| `p_excited(bus, env) -> float` | Excited-state probability for `state`. |
| `noise` | Gaussian σ added per quadrature per shot. |
| `raw_samples` | Length of the `raw` time trace. |
| `seed` | One RNG drives everything — same seed, same result. |

## The platform

`ReferencePlatform(schema=None, model=None, parameters=None)` implements the
full `PlatformProtocol`. `execute()` follows the documented convention:
fragments expand first, error diagnostics raise `UnsupportedOperationError`,
warnings surface as `ExecutionWarning` via `warnings.warn` (a swept
`set_parameter` triggers the `forced-software` warning here too), info passes
through. Its capabilities cover **every registered token** — vendor operations
execute generically (measurement ops record results; other ops validate their
expressions and are otherwise no-ops; there is no timing simulation) — while
`set_parameter`/`get_parameter`/`set_crosstalk` stay software-only, so
`platform.explain(p)` shows meaningful hw/sw plans.

State feedback works by construction: each measurement writes its classified
state onto the shared `MeasurementHandle`, so `with p.if_(m.state == 1): ...`
branches per shot exactly as the runtime contract promises.

```python
platform = qp.ReferencePlatform(parameters={"cluster.lo": 5e9})
print(platform.explain(p))     # the plan tree
result = platform.execute(p)
```

## See also

- [Measurements and results](measurements.md) — handles, names, `QProgramResult`.
- [Capabilities](capabilities.md) — validation, plans, `explain()`.
- [Fragments](fragments.md) — composed programs expand before execution.
