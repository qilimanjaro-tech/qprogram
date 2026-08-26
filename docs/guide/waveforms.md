# Waveforms

A waveform describes a pulse envelope. It is pure data; nothing about it
involves hardware. The same `Gaussian(0.5, 40, 8)` shape can be a flux pulse
on one platform and a charge-line pulse on another, depending on the bus it
ends up on.

Waveforms are the one vocabulary in the package with no top-level re-export,
so they are always reached through the submodule:

```python
import qprogram as qp

pulse = qp.waveforms.Gaussian(amplitude=0.5, duration=40, sigma=8)
```

The one thing a bus cares about is the channel count: a single-channel bus
takes a `Waveform`, an IQ bus takes an `IQWaveform`. `play()` and `measure()`
check this, but only when the bus is a schema-bound `BusRef`, since a raw
string bus carries no channel metadata to check against. Playing a `Gaussian`
on the IQ `drive` bus of a transmon schema raises:

```
ValidationError: Bus 'q0/drive' is an IQ channel but received a single-channel Waveform (Gaussian). Use an IQWaveform (e.g. IQPair, IQDrag) instead.
```

The mirror case, an `IQDrag` on the single-channel `flux` bus, names `Square`
and `FlatTop` as the shapes that belong there. Both checks run again inside
`with_waveforms()`, when a string alias is replaced by a concrete shape, so a
mismatch introduced by calibration data is caught at substitution rather than
in the platform compiler. `measure()` is narrower still: both its `waveform`
and its `weights` must be `IQWaveform`s or aliases.

## Two base classes

`Waveform` is the base for single-channel (real) shapes. Two methods are
abstract, so every subclass supplies them:

| Method                   | What it returns                                                |
|--------------------------|----------------------------------------------------------------|
| `envelope(resolution=1)` | a 1-D numpy array of `int(duration / resolution)` amplitudes   |
| `get_duration()`         | the duration in nanoseconds, as an `int`                       |

`IQWaveform` is the base for IQ pairs, and asks for three:

| Method           | What it returns                          |
|------------------|------------------------------------------|
| `get_I()`        | the in-phase `Waveform`                  |
| `get_Q()`        | the quadrature `Waveform`                |
| `get_duration()` | the duration in nanoseconds, as an `int` |

Both bases derive the same measures from those, so any shape, built-in or your
own, answers them without extra code:

| Method                         | What it returns                                                                                                                                                                                              |
|--------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `area(resolution=1)`           | `numpy.trapezoid(envelope, dx=resolution)`, in nanosecond-amplitude. Trapezoidal, so a rectangle integrates to `amplitude * (duration - resolution)`: `Square(0.5, 100).area()` is `49.5`, not `50.0`.        |
| `peak_amplitude(resolution=1)` | `max(abs(envelope))`, and for an IQ shape the peak magnitude `max(abs(I + 1j*Q))`.                                                                                                                           |
| `rms_amplitude(resolution=1)`  | the root mean square of the samples, or of the complex magnitudes for an IQ shape.                                                                                                                            |
| `spectrum(resolution=1)`       | a `(frequencies_hz, complex_spectrum)` pair. Real shapes use `numpy.fft.rfft`, so 64 samples at `resolution=1` give 33 one-sided bins up to 500 MHz; IQ shapes use a `fftshift`ed two-sided `numpy.fft.fft`.  |
| `plot(resolution=1, ...)`      | a matplotlib `Axes`, or an `(I_axes, Q_axes)` pair for an IQ shape.                                                                                                                                           |

`envelope()` resolves every symbolic parameter before it samples anything, by
calling `Expression.evaluate_or_raise()` on it. A variable with no value
therefore fails at the point of use rather than somewhere inside numpy:

```
UnassignedVariableError: Cannot evaluate expression Variable('amp'): unassigned variable(s) {Variable('amp')}
```

Every measure above is computed from `envelope()`, so they all raise the same
error under the same conditions.

`plot()` takes the axes to draw on: `Waveform.plot(resolution=1, ax=None)`
returns one `Axes`, and
`IQWaveform.plot(resolution=1, axes=None)` returns two stacked axes sharing an
x axis, labeled `I` and `Q`. Passing `None` creates a fresh figure. matplotlib
is imported inside the call, from the `qprogram[viz]` extra, which keeps the
rest of the package importable without it; without matplotlib installed the
call raises `ModuleNotFoundError`. Both bases also define `_repr_html_`, which
returns the same plot as an inline SVG, so a bare waveform renders in a Jupyter
cell without an explicit `plot()`.

## Single-channel built-ins

Every duration and width below is in nanoseconds, every frequency is in hertz,
and every phase offset is in radians.

| Constructor                                                       | Samples it renders                                                        |
|-------------------------------------------------------------------|---------------------------------------------------------------------------|
| `Square(amplitude, duration)`                                     | every sample at `amplitude`                                               |
| `Gaussian(amplitude, duration, sigma)`                            | a Gaussian peaked at the center of the window                             |
| `GaussianDragCorrection(amplitude, duration, sigma, beta)`        | the Gaussian's derivative, scaled by `beta`                               |
| `Sech(amplitude, duration, tau)`                                  | `amplitude / cosh((t - center) / tau)`                                    |
| `Tukey(amplitude, duration, alpha=0.5)`                           | a rectangle with cosine-tapered edges                                     |
| `FlatTop(amplitude, duration, smooth_duration, buffer=0)`         | a rectangle with erf-shaped edges, optionally zero-padded                 |
| `Ramp(from_amplitude, to_amplitude, duration)`                    | a linear interpolation between the two amplitudes                         |
| `SuddenNetZero(amplitude, duration, b, t_phi)`                    | a positive segment, a zero hold, then a negative segment                  |
| `Sine(amplitude, duration, frequency, phase=0.0)`                 | `amplitude * sin(2π * frequency * t + phase)`                             |
| `Cosine(amplitude, duration, frequency, phase=0.0)`               | `amplitude * cos(2π * frequency * t + phase)`                             |
| `Arbitrary(samples)`                                              | the samples you gave it                                                   |
| `Chained(waveforms)`                                              | its children's envelopes, concatenated in order                           |

```python
import numpy as np
import qprogram as qp

readout = qp.waveforms.Square(amplitude=0.5, duration=2000)
pi_pulse = qp.waveforms.Gaussian(amplitude=0.5, duration=40, sigma=8)
flux_step = qp.waveforms.FlatTop(amplitude=0.5, duration=200, smooth_duration=20)
cz_pulse = qp.waveforms.SuddenNetZero(amplitude=0.5, duration=100, b=0.4, t_phi=20)
measured = qp.waveforms.Arbitrary(samples=np.linspace(0.0, 1.0, 64))
```

`Square` renders with `numpy.full`, so its dtype follows its amplitude and an
integer amplitude yields an integer array. `Tukey` does the same in its two
untapered cases, and `Arbitrary` keeps whatever dtype its input had. Every
other shape computes in floating point.

`Gaussian` is not truncation-corrected. The peak sits at the center of the
sample window, so an even sample count straddles it and the largest sample
falls slightly below `amplitude`: `Gaussian(0.5, 40, 8).envelope().max()` is
`0.4990`, not `0.5`. The tails are clipped wherever the window ends rather than
forced to zero, so the same shape starts and ends at `0.0513 * amplitude`.
Widening the
window to `duration=60` at the same `sigma` adds tail without changing the
pulse. `GaussianDragCorrection` inherits all three parameters and differs only
in the envelope: it is antisymmetric about the center, where it crosses zero.
Its derivative is taken with respect to sample index rather than time, so its
amplitude scales with `resolution` while the Gaussian's does not.

`Tukey` splits `alpha` between the two edges: the flat top is
`(1 - alpha) * duration` wide and each cosine ramp is
`(alpha / 2) * duration`. `alpha=0` is a rectangle and `alpha=1` is a Hann
window that reaches zero at both endpoints; the default is `0.5`. It matches
the `alpha` of `scipy.signal.windows.tukey`, and needs no `erf` evaluation,
which is the reason to prefer it to `FlatTop` when either edge shape will do.

`FlatTop` builds each edge from an error function of width
`smooth_duration / 3`, and multiplies the rising and falling edges together
rather than splicing them, which keeps the envelope smooth when the two
overlap. The rise crosses half amplitude `smooth_duration` ns into the pulse
and is flat to within a part in 10⁵ by twice that, so a `duration` that is not
comfortably longer than `2 * smooth_duration` never reaches full amplitude.
`buffer` adds zero padding on each side, on top of `duration` rather than
inside it: `FlatTop(0.5, 200, 20, buffer=10).get_duration()` is `220`.

`Ramp` samples with `numpy.linspace`, so both endpoints are hit exactly once
the window holds at least two samples, and the step between samples is
`(to_amplitude - from_amplitude) / (n - 1)` rather than anything derived from
`resolution` alone. A one-sample window yields `from_amplitude` alone, a
window holding less than one sample yields an empty array, and a negative
duration raises
`ValueError: Number of samples, -5, must be non-negative.` from `linspace`.

`SuddenNetZero` plays a positive square segment, a zero hold of width `t_phi`,
then a negative segment scaled by `b`. The two segments are meant to cancel
the net integrated flux, and the cancellation is exact only when `b` is 1 and
the samples left over after the hold divide evenly between the segments. The
positive segment takes `(duration - t_phi) // 2` samples and the negative one
takes the rest, so an odd remainder gives the negative segment the extra
sample: at `duration=101, t_phi=20, b=1` the envelope sums to `-amplitude`
instead of zero. In practice `b` is detuned from 1 to null whatever residual
the flux line adds, so the `b=0.4` above integrates to a non-zero area.

`Sine` and `Cosine` express their sample times in seconds, which is what pairs
with a `frequency` in Hz: 200 ns at 50 MHz is ten cycles. Neither tapers to
zero at the endpoints, so pair one with a window shape when the discontinuity
matters. `Sech` is the analogue of `Gaussian` for adiabatic passage: paired
with a quadratic phase ramp it gives analytically solvable population
transfer. Its `tau` plays the role `sigma` plays for a Gaussian.

`Arbitrary` and `Chained` are the escape hatches. `Arbitrary` takes a
sequence or an ndarray through `numpy.asarray`, so the stored dtype follows
the input, and an ndarray is adopted rather than copied: do not mutate an array
you have handed to it, because waveforms are compared and hashed by value.
`envelope()` returns a copy for the same reason, and ignores `resolution`
entirely, since the samples are already the envelope at one per nanosecond.
`Chained` passes whatever resolution it is asked for down to each child and
sums their durations. `a + b` builds a `Chained` too, flattening as it goes, so
`a + b + c` is one three-element chain rather than nested pairs; a non-waveform
operand yields `NotImplemented`, which surfaces as `TypeError`. An empty chain
reports `get_duration() == 0` but its `envelope()` raises
`ValueError: need at least one array to concatenate`.

### What the shape parameters mean

Every width is the real width of the shape, not a ratio against `duration`.

| Parameter                                                        | Meaning                                                                                                                                |
|------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `Gaussian.sigma`, `GaussianDragCorrection.sigma`, `IQDrag.sigma` | The Gaussian standard deviation σ, in nanoseconds.                                                                                     |
| `Sech.tau`                                                       | The sech width τ, in nanoseconds: the `sigma` analogue for `amplitude / cosh((t - center) / tau)`.                                      |
| `IQDrag.beta`, `GaussianDragCorrection.beta`                     | The DRAG coefficient β from the Motzoi parameterization: the weight on the derivative term. Typically below 0.5 and tuned per qubit.    |
| `Tukey.alpha`                                                    | The taper fraction, in `[0, 1]`: the share of `duration` taken by the rise and fall combined.                                           |
| `FlatTop.smooth_duration`                                        | The length of each erf-shaped edge, in nanoseconds. The erf width itself is a third of it.                                              |
| `SuddenNetZero.b`                                                | The ratio of the negative segment's amplitude to the positive one's, normally near 1.                                                   |
| `SuddenNetZero.t_phi`                                            | The width of the zero hold between the two segments, in nanoseconds.                                                                    |

`duration` is independent of the width: it is the window the shape is rendered
into, so it controls truncation and nothing else. That is what makes `sigma`
the knob a calibration sweep reaches for, since a Gaussian's rotation angle
goes with its area, roughly `amplitude * sigma * sqrt(2π)`.

## IQ built-ins

| Constructor                                        | What it produces                                                             |
|----------------------------------------------------|------------------------------------------------------------------------------|
| `IQPair(I, Q)`                                     | the two waveforms you gave it, unchanged                                     |
| `IQDrag(amplitude, duration, sigma, beta)`         | a `Gaussian` on I and a `GaussianDragCorrection` on Q                        |
| `IQZero(envelope)`                                 | the envelope on I and zeros on Q                                             |
| `Modulated(envelope, frequency, phase=0.0)`        | the envelope multiplied by a cosine on I and a sine on Q                      |
| `IQRotation(base, phase)`                          | another IQ shape's channels mixed by a 2x2 rotation                          |

```python
import qprogram as qp

drive = qp.waveforms.IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
readout = qp.waveforms.IQPair(
    I=qp.waveforms.Square(1.0, 2000),
    Q=qp.waveforms.Square(0.0, 2000),
)
weights = qp.waveforms.IQZero(envelope=qp.waveforms.Square(1.0, 2000))
sideband = qp.waveforms.Modulated(envelope=qp.waveforms.Gaussian(0.5, 40, 8), frequency=50e6)
virtual_z = qp.waveforms.IQRotation(base=drive, phase=1.5708)
```

`IQPair` is the generic pair, and the only IQ shape that hands back exactly
the objects it was given: `get_I()` and `get_Q()` return them by identity. It
rejects a non-`Waveform` argument with
`TypeError: I and Q must be Waveform instances`, and unequal durations with

```
ValidationError: IQPair channels must have equal durations; got I=100 ns, Q=999 ns
```

The duration check is best-effort. When both durations are symbolic and still
unassigned, `get_duration()` raises `UnassignedVariableError`, the constructor
swallows it and accepts the pair, and the platform compiler verifies the match
once values are bound.

`IQDrag` is the standard DRAG shape, and stores its four parameters rather than
two child waveforms: `get_I()` and `get_Q()` build a `Gaussian` and a
`GaussianDragCorrection` on demand, so the channels cannot drift apart from
each other or from `get_duration()`.

The remaining three adapt an existing shape and validate its type in
`__init__`, naming what they got: `IQZero("pi_pulse")` raises
`TypeError: IQZero envelope must be a Waveform, got str`. `IQZero` puts a
single-channel envelope on I and silence on Q, which is how a calibrated
single-channel pulse reaches an IQ-typed bus without rewriting the program
around it. `Modulated` lifts one onto an IQ bus at an intermediate frequency,
producing `envelope * cos(2π * frequency * t + phase)` on I and the sine on Q.
`IQRotation` applies `I' = I * cos(phase) - Q * sin(phase)` and
`Q' = I * sin(phase) + Q * cos(phase)`, which is the shape a virtual-Z gate or
a software phase offset needs.

`Modulated` and `IQRotation` materialize both channels as `Arbitrary` waveforms
sampled at one per nanosecond, and `IQZero` does the same for its silent Q
channel while handing back its I channel unchanged. Materializing collapses the
parametric structure of what these shapes wrap to concrete samples, and since
`Arbitrary.envelope()` ignores `resolution`, they only behave correctly at
`resolution=1`. `Modulated` and `IQRotation` quietly return 1-ns samples for
any other value, and `IQZero` mixes a resolution-aware I channel with a
fixed-length Q channel, so
`IQZero(Square(0.5, 100)).peak_amplitude(resolution=2)` raises
`ValueError: operands could not be broadcast together with shapes (50,) (100,)`.
Where a coarser rendering is needed, prefer carrying the phase or frequency
through the underlying envelope's own parameters.

## Variables and expressions in parameters

A parameter annotated `float | Expression` or `int | Expression` accepts a
`Variable` or an `Expression` in place of a number, and that covers every
numeric parameter of every built-in with one exception: `FlatTop.buffer` is
annotated plain `int` and takes a number only. This is how a pulse parameter
gets swept inside a loop:

```python
import qprogram as qp

program = qp.QProgram()
amp = program.variable("amp")

with program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):
    program.play("drive_q0", qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8))
```

The waveform stores the symbolic parameter and nothing else happens at
construction. The platform's compiler then decides whether to update an
amplitude register on the fly or re-upload the waveform on each iteration; you
write the same thing either way.

The structural parameters are the ones that must be concrete when the object
is built: `Arbitrary.samples`, the list in `Chained.waveforms`, `IQPair.I` and
`IQPair.Q`, `IQZero.envelope`, `Modulated.envelope`, and `IQRotation.base`.
Each of the last four is type-checked in `__init__`, so a mistake there is a
`TypeError` at the call site rather than a failure during rendering.

A waveform can be evaluated locally once its variables are bound, which is
what plotting a swept shape needs:

```python
import qprogram as qp

amp = qp.Variable("amp")
amp.set_value(0.7)
samples = qp.waveforms.Gaussian(amp, 40, 8).envelope()
amp.reset()
```

## Structural equality

Waveforms compare the way every other AST node does, through the shared rule
[Core ideas](concepts.md#structural-equality) sets out: an exact type match,
then attribute-by-attribute comparison of `vars()`. Two consequences are
particular to waveforms.

```python
import numpy as np
import qprogram as qp

qp.waveforms.Gaussian(0.5, 40, 8) == qp.waveforms.Gaussian(0.5, 40, 8)
qp.waveforms.Gaussian(qp.Variable("amp"), 40, 8) == qp.waveforms.Gaussian(qp.Variable("amp"), 40, 8)
qp.waveforms.Arbitrary(np.array([1.0, 2.0])) == qp.waveforms.Arbitrary([1.0, 2.0])
```

All three are `True`. The second holds because a `Variable` compares by its
string id and the two objects share the id `"amp"`, so a waveform whose
amplitude is swept still compares equal across a rebuild. The third holds
because `Arbitrary` converts its argument with `numpy.asarray` before storing
it, which puts a list and the equivalent array in the same place.

Waveforms are usable as dictionary keys, on the condition that they are treated
as values and never mutated after being hashed. `Arbitrary` is the one shape
where equality and hashing disagree: hashing includes the array's dtype and
equality does not, so `Arbitrary(np.array([1, 2, 3]))` and
`Arbitrary(np.array([1.0, 2.0, 3.0]))` compare equal yet land in different
buckets of a `dict` or `set`.

Structural equality is what survives a `.qp` round trip. `QProgram` itself
does not define `__eq__`, so compare bodies:

```python
import qprogram as qp

program = qp.QProgram()
program.play("drive_q0", qp.waveforms.IQDrag(0.5, 40, 8, 0.1))

reloaded = qp.loads(qp.dumps(program))
assert reloaded.body == program.body
```

## On the wire

The writer emits a waveform as a constructor call: the class name verbatim,
then every attribute in `vars(wf)` that does not start with an underscore, as
a keyword argument. Defaults are written out rather than omitted, and sample
arrays are written in full, because the parser has no way to recover dropped
samples.

```
#!QProgram 1.0

body:
  play "drive_q0" IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
  play "flux_q0" Arbitrary(samples=[0.0, 0.5, 1.0])
  play "flux_q1" FlatTop(amplitude=0.5, duration=200, smooth_duration=20, buffer=10)
  play "flux_q2" Chained(waveforms=[Square(amplitude=1.0, duration=50), Gaussian(amplitude=0.5, duration=40, sigma=8)])
```

Each shape also carries a capability token, which is what lets a `Profile`
advertise the subset of shapes its compiler can lower. The token is
`waveform.` followed by the snake_case class name, with `SuddenNetZero` the
one abbreviation: it is `waveform.snz`, not `waveform.sudden_net_zero`. On top
of the per-class token, a `play` contributes the channel kind
(`waveform.single` or `waveform.iq`), and a string alias contributes
`waveform.alias` and no per-class token at all.

## Custom waveforms

Subclass `Waveform` or `IQWaveform` and implement the abstract methods. To
make the new shape serializable, register it with the parser:

```python
import numpy as np
import qprogram as qp


@qp.register_waveform
class HalfSine(qp.waveforms.Waveform):
    def __init__(self, amplitude: float, duration: int) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        n = self.duration // resolution
        t = np.arange(n) / n * np.pi
        return self.amplitude * np.sin(t)

    def get_duration(self) -> int:
        return self.duration
```

Once registered, `HalfSine(amplitude=0.5, duration=100)` appears in `.qp`
files using the constructor syntax, and the parser reconstructs the object on
load. Because the writer works from `vars(wf)`, anything stored on `self` under
the same name as a constructor parameter round-trips without further work, and
anything else stored on `self` is emitted too and will fail to reload; prefix
computed attributes with an underscore. Registering a different class under a
name already taken raises `ValueError` rather than silently changing how
existing files parse. Pair the registration with
`qp.register_waveform_token(HalfSine, "waveform.half_sine")` to give profiles
something to advertise; without a token the shape contributes only its channel
kind, and any profile accepting that kind accepts it. See
[Adding waveforms](../developer/adding-waveforms.md) for the developer-side
details.

## Picking the right shape

Two shapes often both fit a job. The third column is the property that decides
between them.

| When you need                                       | Reach for                          | Because                                                                                    |
|-----------------------------------------------------|------------------------------------|--------------------------------------------------------------------------------------------|
| A flat pulse with hard edges                        | `Square`                           | every sample sits at `amplitude`, and nothing is tapered                                    |
| A gate whose area you calibrate                     | `Gaussian`                         | area is close to `amplitude * sigma * sqrt(2π)`, so `sigma` and `amplitude` trade off       |
| Leakage suppression on a weakly anharmonic transmon | `IQDrag`                           | Q carries the Gaussian's derivative, weighted by `beta`                                     |
| A specific I and Q pairing                          | `IQPair(I=..., Q=...)`             | the two channels stay exactly what you passed                                               |
| A single-channel envelope on an IQ bus              | `IQZero`                           | I is the envelope untouched, Q is silence                                                   |
| The same, at an intermediate frequency              | `Modulated`                        | one envelope becomes a cos/sin pair at `frequency`                                          |
| A phase offset on an already calibrated pulse       | `IQRotation`                       | it mixes the existing channels instead of resampling the shape                               |
| A flat pulse whose edges are set in nanoseconds     | `FlatTop`                          | `smooth_duration` is an absolute edge length, and `buffer` adds dead time around it          |
| A flat pulse whose edges scale with its length      | `Tukey`                            | `alpha` is a fraction of `duration`, so the taper tracks it as the pulse is swept            |
| Zero net integrated flux for a two-qubit gate       | `SuddenNetZero`                    | the negative segment cancels the positive one, and `b` absorbs the residual                  |
| An adiabatic-passage envelope                       | `Sech`                             | sech plus a quadratic phase ramp has a closed-form transfer solution                        |
| A continuous drive tone                             | `Sine` / `Cosine`                  | the oscillation is the envelope, and neither shape tapers at the endpoints                   |
| A monotonic sweep of a bias point                   | `Ramp`                             | both endpoints are hit exactly                                                              |
| A shape you computed elsewhere                      | `Arbitrary`                        | samples are stored and emitted verbatim, at one per nanosecond                               |
| Two shapes played back to back                      | `Chained`, or `a + b`              | durations add and each child keeps its own parameters                                       |
| Anything else                                       | Subclass `Waveform` / `IQWaveform` | `envelope()` and `get_duration()` supply every derived measure                               |

For programs that go through calibration, you usually pass string aliases
(`"pi_pulse"`, `"readout"`) and let the platform substitute the concrete
waveform from its calibration store.

## Related pages

[Operations](operations.md) has the `play` and `measure` signatures.
[Buses](buses.md) covers the schemas whose `channel` field drives the
single-versus-IQ check. [Saving and loading](serialization.md) covers how
aliases survive the `.qp` round trip.
