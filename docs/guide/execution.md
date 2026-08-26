# Running programs

Core qprogram ships one execution back-end. `ReferencePlatform` validates a
program against a permissive capability descriptor, walks the AST in pure
Python, and returns a `QProgramResult` of `xarray.DataArray`s. Nothing is
compiled and no instrument is contacted, so a program runs anywhere the package
is installed. What comes back is the reference semantics vendor compilers are
tested against, which is why the result shapes below are a contract rather than
an implementation detail.

The executor simulates results, not devices: measurement outcomes come from a
pluggable measurement model, and pulse and timing operations have their
expressions evaluated and then do nothing.
[What the reference executor does not model](#what-the-reference-executor-does-not-model)
draws the boundary in full.

## Simulating a program

`qp.simulate(program, *, model=None, schema=None, parameters=None)` builds a
one-off `ReferencePlatform`, executes `program` on it, and returns the result.
Everything after `program` is keyword-only.

| Argument | Meaning |
|---|---|
| `program` | The program to run. Fragment calls are expanded first, on a copy, so the program you hold is left as it was. |
| `model` | The `MeasurementModel` consulted once per measurement shot. `None` builds a default `MockMeasurementModel()`: every shot lands on `0j`, no noise, every state classified as `0`. |
| `schema` | The `BusSchema` the throwaway platform would report from `get_bus_schema()`. Execution reads bus references off the program itself, so this has no effect on the result. |
| `parameters` | Initial parameter store, keyed `"bus.parameter"`. Copied, so the caller's dict is untouched, and the platform is discarded afterwards, so writes a run performs are not readable anywhere. Construct a `ReferencePlatform` when you want them back. |

A Rabi sweep with a response function, noise, and a fixed seed:

```python
import numpy as np
import qprogram as qp

model = qp.MockMeasurementModel(
    response=lambda bus, env: np.sin(np.pi * env["g"] / 2) ** 2 + 0j,
    noise=0.02,
    seed=7,
)

p = qp.QProgram(label="rabi")
g = p.variable("g")
with p.average(1000), p.sweep(g, qp.Range(0.0, 1.0, 0.01)):
    p.play("drive_q0", qp.waveforms.Gaussian(amplitude=g, duration=40, sigma=8))
    p.sync()
    p.measure(
        "readout_q0",
        qp.waveforms.IQPair(qp.waveforms.Square(1.0, 2000), qp.waveforms.Square(0.0, 2000)),
        "weights",
    )

result = qp.simulate(p, model=model)
da = result.get("m0")  # dims ("g", "IQ"), coords from the sweep
da.sel(IQ="I").plot()  # a noisy Rabi oscillation (needs matplotlib, the `viz` extra)
```

`simulate` raises rather than returning a partial result. A program that
validation rejects raises `UnsupportedOperationError`, and an operation whose
expression references a variable no enclosing loop binds raises
`UnassignedVariableError`.

## Result shapes

One record is produced per measurement operation, in the order a setup walk over
the AST finds them, which is declaration order. Each record carries one
`DataArray` per requested measurement field, plus a primary array: the `iq`
field when the measurement requested it, otherwise the first requested field in
canonical order (`state`, `iq`, `raw`).

Dimensions are the `Sweep` and `Parallel` blocks enclosing the measurement,
outermost first. A `Sweep` contributes a dimension named after its variable's id
with the sweep values as its coordinate. A parallel composition contributes one
shared dimension named by joining every composed variable's id with `|`, and
attaches one coordinate per variable to it. `Average` and `Conditional`
contribute no dimension of their own.

For an averaged program with no sweep, a single sweep, and two nested sweeps:

```python
import qprogram as qp

readout = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100))

flat = qp.QProgram()
with flat.average(200):
    flat.measure("readout_q0", readout, "weights")
qp.simulate(flat).get("m0").dims  # ("IQ",), averaging adds nothing

one = qp.QProgram()
g = one.variable("g")
with one.average(200), one.sweep(g, qp.Range(0.0, 1.0, 0.25)):
    one.measure("readout_q0", readout, "weights")
qp.simulate(one).get("m0").dims  # ("g", "IQ"), shape (5, 2)

two = qp.QProgram()
freq = two.variable("freq")
gain = two.variable("gain")
with two.sweep(freq, qp.Range(4e9, 5e9, 0.5e9)), two.sweep(gain, qp.Range(0.0, 1.0, 0.25)):
    two.measure("readout_q0", readout, "weights")
qp.simulate(two).get("m0").dims  # ("freq", "gain", "IQ"), shape (3, 5, 2)
```

Parallel loops collapse into one axis, so a composition of a three-point and a
three-point sweep is three points, not nine:

```python
import qprogram as qp

readout = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100))
p = qp.QProgram()
a = p.variable("a")
b = p.variable("b")
with p.sweep(a, qp.Range(0.0, 1.0, 0.5)) | p.sweep(b, qp.Range(10.0, 20.0, 5.0)):
    p.measure("readout_q0", readout, "weights")

da = qp.simulate(p).get("m0")
da.dims  # ("a|b", "IQ"), shape (3, 2)
da.coords["a"].values  # [0.0, 0.5, 1.0]
da.coords["b"].values  # [10.0, 15.0, 20.0]
```

The trailing dimensions depend on which field you ask for. Writing `*sweeps` for
the loop dimensions above:

| Field | Dims | Content under `average(shots)` |
|---|---|---|
| `iq` | `(*sweeps, "IQ")`, coords `["I", "Q"]` | Mean of the per-shot I and Q values. |
| `state` | `(*sweeps)` | Excited-state population, the mean of the per-shot `0`/`1` classifications. Outside averaging it is the single shot's `0` or `1`. |
| `raw` | `(*sweeps, "time", "IQ")` | Mean trace, with `time` coordinates `0 .. raw_samples - 1`. |

`get` reads these through its `field` argument, which defaults to
`qp.MeasurementField.IQ`, matching the default of `measure(..., fields=)`. It
never substitutes a field the measurement did not request, the default included,
so a state-only measurement needs the field spelled out or `get` raises
`KeyError`. A vendor-registered field is legal
to request against the reference platform, since its capabilities cover every
registered token, but the reference model produces only the three core fields:
an accepted vendor field comes back as zeros.

Averaging works by summing into an accumulator and dividing by a shot count kept
per sweep point, shared across fields. A sweep point where a measurement never
ran has a count of zero and holds NaN rather than the zero a plain division
would leave. That is what a measurement inside a conditional arm looks like at
the points where the branch selected a different arm:

```python
import numpy as np
import qprogram as qp

readout = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100))
model = qp.MockMeasurementModel(p_excited=lambda bus, env: float(env["g"] >= 0.5))

p = qp.QProgram()
g = p.variable("g")
with p.sweep(g, qp.Range(0.0, 1.0, 0.25)):
    m = p.measure("readout_q0", readout, "weights", fields=("iq", "state"))
    with p.if_(m.state == 1):
        p.measure("readout_q1", readout, "weights")

arm = qp.simulate(p, model=model).get("m1").sel(IQ="I").values
# [nan, nan, 0.0, 0.0, 0.0]: the arm ran only where g >= 0.5
```

## Measurement models

The executor asks the model for one sample per measurement shot. The interface is
`MeasurementModel`, a runtime-checkable protocol with a single method:

```python
from collections.abc import Mapping

import qprogram as qp


def sample(self, bus: str, env: Mapping[str, float]) -> qp.MeasurementSample: ...
```

`bus` is the measurement's bus as a plain string, or `""` for a measurement
operation that carries no bus attribute. `env` holds the currently bound loop
variables keyed by variable id, plus the platform parameters keyed
`"bus.parameter"`. Parameter keys always contain a dot and variable ids never
do, so the two can never collide. Only variables with a numeric value are
present: an unbound variable is absent from `env` rather than present with a
placeholder, so a model that indexes it fails with `KeyError` instead of
returning something plausible and wrong.

`MeasurementSample` is a frozen dataclass with four fields: `i` and `q` are the
shot's in-phase and quadrature floats, `state` is the classified outcome as `0`
or `1`, and `raw` is a `numpy` array of shape `(raw_samples, 2)` holding I and Q
per time sample. The executor reads the trace length from the model itself, as
`getattr(model, "raw_samples", 16)`, and allocates its accumulator from that
before the first sample arrives, so a model that returns traces of a different
length fails on the accumulation rather than reshaping quietly.

The protocol is runtime-checkable, so `isinstance(model, qp.MeasurementModel)`
answers whether an object satisfies it.

`MockMeasurementModel` is what `simulate` and `ReferencePlatform` fall back to
when no model is given. Its five arguments cover a response curve, a
classification probability, and the noise around both:

| Argument | Meaning |
|---|---|
| `response(bus, env) -> complex` | Noiseless IQ point. Omitted, every shot lands on `0j`. |
| `p_excited(bus, env) -> float` | Excited-state probability, sampled per shot as a Bernoulli draw. Omitted, every shot classifies as `0`. |
| `noise` | Standard deviation of the gaussian noise added per quadrature, per shot, and per raw time sample. Defaults to `0.0`, which skips the draws rather than drawing zero-width ones. |
| `raw_samples` | Length of the `raw` trace. Defaults to `16`. |
| `seed` | Seed for the model's private `numpy.random.default_rng`. Defaults to `0`. |

One generator drives both the noise and the state draws, so a program run twice
against two models built from the same seed gives identical arrays, and a
different seed gives different draws wherever a draw reaches the result. The
draws happen in execution order, so editing the program changes the sequence
even when the seed does not.

Anything with a `sample` method is a model. Writing one directly is the way to
simulate a response the two callbacks cannot express, or to return a raw trace
with real structure instead of a replicated IQ point:

```python
import numpy as np
import qprogram as qp


class LorentzianModel:
    """A resonance peak centered on a platform parameter, no noise."""

    raw_samples = 32

    def sample(self, bus: str, env: dict[str, float]) -> qp.MeasurementSample:
        detuning = env["freq"] - env.get("readout_q0.resonance", 6.5e9)
        amplitude = 1.0 / (1.0 + (detuning / 1e6) ** 2)
        raw = np.tile([amplitude, 0.0], (self.raw_samples, 1))
        return qp.MeasurementSample(i=amplitude, q=0.0, state=0, raw=raw)


readout = qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100))
p = qp.QProgram()
freq = p.variable("freq")
with p.sweep(freq, qp.Range(6.495e9, 6.505e9, 1e6)):
    p.measure("readout_q0", readout, "weights", fields=("iq", "raw"))

result = qp.simulate(p, model=LorentzianModel(), parameters={"readout_q0.resonance": 6.5e9})
result.get("m0").sel(IQ="I").values  # peaks at 1.0 on resonance
```

## The reference platform

`qp.ReferencePlatform(schema=None, model=None, parameters=None,
vendor_op_handlers=None)` is what `simulate` builds internally, and constructing
it yourself is how you keep the platform between runs. `schema` is returned by
`get_bus_schema()`, which raises `ValueError` when the platform was built
without one, and drives `get_buses()`, which renders one name per
`(element, bus kind)` pair with `*` in the index position because a schema names
kinds rather than enumerating indices.

`parameters` is copied once into the public `platform.parameters` dict. That copy
is read by `get_parameter`, written by `set_parameter`, and exposed to the
measurement model through `env`, so a run's writes persist on the platform and
are visible to the next `execute` call:

```python
import qprogram as qp

platform = qp.ReferencePlatform(parameters={"cluster.lo": 3.0})
p = qp.QProgram()
lo = p.get_parameter("cluster", "lo")
p.set_frequency("drive_q0", lo)
p.set_parameter("cluster", "lo", 7.5)
p.measure(
    "readout_q0",
    qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100)),
    "weights",
)

platform.execute(p)
platform.parameters  # {"cluster.lo": 7.5}
platform.get_global_parameters()  # ["cluster.lo"], sorted, fully qualified
platform.get_parameters("cluster")  # ["lo"]
```

Reading a key the store does not hold yields `0.0` rather than raising, and
`get_parameters` reports what has been set rather than what a bus accepts, since
the reference platform keeps one flat store and validates no parameter names.
`get_global_parameters` returns those same `"bus.parameter"` keys, not the
bus-less parameters the name suggests.

`vendor_op_handlers` maps a vendor `Operation` subclass to a callable
`handler(op, parameters)` that runs when an instance of that class executes. A
handled operation skips the interpreter's eager expression evaluation entirely,
which is what lets a get-style vendor operation write its own output variable
without that variable being force-evaluated first; such a handler evaluates any
value expression itself, with the loop variables already bound. Vendor
operations without a handler execute generically, and a vendor measurement
operation records a result like any other measurement.

### Capabilities and the execution convention

`platform.capabilities` returns `qp.reference_capabilities()`, recomputed on
every access so that a vendor extension imported after the platform was
constructed still has its tokens honored. Every token in the live capability
registry is supported, core and vendor alike, with one deliberate hole: the
bus-scoped parameter operations `op.set_parameter` and `op.get_parameter` appear
in each bus slot's `host` half and are absent from its `rt` half, and the
platform slot carries them in neither half. Setting or reading a platform
parameter is a configuration action rather than a real-time sequencer
instruction, and keeping that restriction here is what makes plans,
`forced-host` warnings, and `explain()` meaningful against the reference
platform instead of uniformly permissive.

The descriptor also carries one predicate. A `set_parameter` whose value is a
bound variable emits a `DomainConstraint` excluding `rt` from the loop that
binds the variable, with the reason
`parameter '<name>' is swept via set_parameter (host-side dispatch per iteration)`.
The constraint targets the loop, not the operation, so the operation stays
real-time capable while the loop around it drops to host-side.

`execute` follows the convention the protocol documents. It expands fragment
calls, validates, raises `UnsupportedOperationError` listing every
`severity="error"` diagnostic, re-emits every `severity="warning"` diagnostic
through `warnings.warn` under the `qp.ExecutionWarning` category, drops
`severity="info"` diagnostics, and only then interprets. It accepts and ignores
any `**kwargs`, so a call written for a real back-end still runs here.
`ExecutionWarning` subclasses `UserWarning`, so
`warnings.simplefilter("error", qp.ExecutionWarning)` turns a forced-host
fallback into a failure in a test suite.

A swept `set_parameter` is the common way to see the warning path:

```python
import warnings
import qprogram as qp

p = qp.QProgram()
v = p.variable("v")
with p.average(10), p.sweep(v, qp.Range(0.0, 1.0, 0.5)):
    p.set_parameter("cluster", "lo", v)
    p.measure(
        "readout_q0",
        qp.waveforms.IQPair(qp.waveforms.Square(1.0, 100), qp.waveforms.Square(0.0, 100)),
        "weights",
    )

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    result = qp.simulate(p)
caught[0].category  # qp.ExecutionWarning
```

```
[warning] forced-host: Block 'Average' falls back to host-side execution: contains host-side-only sub-block 'Sweep' (parameter 'lo' is swept via set_parameter (host-side dispatch per iteration)). (at body[0])
```

Validation also emits an info-severity `reorderable-averaging` hint for this
program, because the average encloses a host-side sweep whose measurement
sequence could run in a real-time inner average. Because it is info severity,
`execute` drops it: reach for `qp.validate(p, platform.capabilities)` or
`platform.explain(p)` to see it, and `qp.optimize(p, platform.capabilities)`
to apply the rewrite it suggests. The hint fires only where the rewrite is
actually possible, which means an average whose sole child is a flat sweep
whose body opens with a contiguous run of host-side-only operations and
continues with real-time-capable ones, at least one of which affects averaging.

`platform.validate`, `platform.plan`, and `platform.explain` are the protocol's
inherited defaults and go through `qp.validate` without interpreting anything,
so they cost a validation walk and no shots:

```python
import qprogram as qp

platform = qp.ReferencePlatform(parameters={"drive_q0.lo_frequency": 5e9})
p = qp.QProgram(label="ref")
p.play("drive_q0", "pi")
print(platform.explain(p))
```

```
plan for 'ref' — errors: 0 · warnings: 0 · info: 0
body
└─ play "drive_q0" "pi"  [rt|host]
```

### State feedback

Each measurement writes its classified state onto the shared
`MeasurementHandle` before the shot is accumulated, so a conditional later in
the same iteration reads the outcome that measurement just produced and
`with p.if_(m.state == 1): ...` branches per shot. The conditional runs the
first arm whose condition holds, or the `else` body when none does. This works
because every reference to a measurement holds the same handle instance; nothing
else is wired up.

## What the reference executor does not model

There is no timing simulation and no waveform physics. `play`, `wait`, `sync`,
`reset_phase`, and the bus-level `set_*` operations (`set_frequency`,
`set_phase`, `set_gain`, `set_offset`) have every `Expression` reachable from
their public attributes force-evaluated, which is what makes an unbound variable
raise `UnassignedVariableError` at execution time instead of producing nonsense
downstream, and then they do nothing. Durations, phases, amplitudes, and
alignment have no effect on the numbers that come back. Whatever structure the
results carry comes from the measurement model, not from the pulses.

String waveform names are not resolved. A program that plays `"pi"` runs, and the
operation is a no-op, so the result is the same as it would be with the name
resolved. Real platforms need the concrete waveform, so resolve names with
`program.with_waveforms(library)` before handing a program to hardware rather
than relying on the reference platform's tolerance.

The measurement model is consulted per shot and knows nothing about the state
the device would be in, so state preparation, decoherence, and crosstalk are
absent unless a model computes them from `env`. Vendor operations run
generically: their expressions are evaluated, a measurement operation records a
result, and everything else is a no-op unless a `vendor_op_handlers` entry gives
it an effect. Streaming is not implemented, so `platform.stream(p)` raises
`NotImplementedError("Streaming not supported by this platform")` from the
protocol's default.

## Implementing a platform

`qp.PlatformProtocol` is an abstract base class, and a hardware back-end
subclasses it. Six members are abstract:

| Member | Contract |
|---|---|
| `get_bus_schema()` | The `BusSchema` for the platform's chip. |
| `get_buses()` | Every bus name, spelled the way a program would reference it. |
| `get_parameters(bus)` | The parameter names `set_parameter` and `get_parameter` accept on that bus. |
| `get_global_parameters()` | The parameter names not bound to any bus. |
| `capabilities` | A `PlatformCapabilities` property: per-`(element, bus_kind)` bus profiles, a platform-level profile for blocks, expressions, and bus-less operations, and a default bus profile that raw-string buses fall back to. |
| `execute(qprogram)` | Run the program and return one record per measurement. |

`validate`, `plan`, and `explain` come with working defaults that delegate to
`qp.validate` and `qp.explain` against `self.capabilities`, so a subclass gets
them for free and overrides only to prepend device-specific predicates or to
short-circuit on the first error. `validate` and `plan` each discard half of
what `qp.validate` returns; an `execute` that gates on diagnostics and then
compiles against the plan should call `qp.validate` directly rather than paying
for two walks. `stream` is optional and raises `NotImplementedError` by default.

The convention `execute` is expected to follow is the one `ReferencePlatform`
implements: validate first, raise `UnsupportedOperationError` on any
`severity="error"` diagnostic, and surface warnings and info without raising.
Nothing enforces it, but a platform that skips the check hands its users a
vendor compiler's error text in place of a structured diagnostic that names the
offending node.

```python
import qprogram as qp


class MyPlatform(qp.PlatformProtocol):
    def __init__(self, schema: qp.BusSchema, capabilities: qp.PlatformCapabilities) -> None:
        self._schema = schema
        self._capabilities = capabilities

    def get_bus_schema(self) -> qp.BusSchema:
        return self._schema

    def get_buses(self) -> list[str]:
        return ["q0/drive", "q0/readout"]

    def get_parameters(self, bus: str) -> list[str]:
        return ["lo_frequency", "gain"]

    def get_global_parameters(self) -> list[str]:
        return ["reference_clock"]

    @property
    def capabilities(self) -> qp.PlatformCapabilities:
        return self._capabilities

    def execute(self, qprogram: qp.QProgram) -> qp.QProgramResult:
        diagnostics, plan = qp.validate(qprogram, self.capabilities)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            raise qp.UnsupportedOperationError("\n".join(str(d) for d in errors))
        # compile against `plan`, run, and fill a QProgramResult
        return qp.QProgramResult()
```

The capability descriptor is the part that takes real work, since it decides
which programs the platform accepts and which nodes fall back to host-side
dispatch. `qp.reference_capabilities()` is a readable starting point and
[Capabilities](capabilities.md) covers the token vocabulary.

## See also

- [Measurements and results](measurements.md): handles, names, `QProgramResult`.
- [Capabilities](capabilities.md): validation, plans, `explain()`.
- [Fragments](fragments.md): composed programs expand before execution.
- [Capability protocol](../developer/capability-protocol.md): building a
  `PlatformCapabilities` descriptor for a real device.
