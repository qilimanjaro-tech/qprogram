# Waveforms

A waveform describes a pulse envelope. It is pure data; nothing about it
involves hardware. The same `Gaussian(0.5, 40, 8)` shape can be a flux pulse
on one platform and a charge-line pulse on another, depending on the bus it
ends up on. The one thing a bus cares about is the channel count: a
single-channel bus takes a `Waveform`, an IQ bus takes an `IQWaveform`. That
is why `Gaussian` on an IQ drive bus raises `ValidationError` — `IQDrag` is
the shape that belongs there.

## Two base classes

`Waveform` is the base for single-channel (real) shapes. Two methods are
abstract, so every subclass supplies them:

| Method                              | What it returns                        |
|-------------------------------------|----------------------------------------|
| `envelope(resolution=1)`            | numpy array of amplitudes per sample   |
| `get_duration()`                    | duration in nanoseconds                |

`IQWaveform` is the base for IQ pairs, and asks for three:

| Method                              | What it returns                        |
|-------------------------------------|----------------------------------------|
| `get_I()`                           | the in-phase `Waveform`                |
| `get_Q()`                           | the quadrature `Waveform`              |
| `get_duration()`                    | duration in nanoseconds                |

Both bases then derive the same set of read-only measures from those, so any
shape — built-in or your own — answers them for free:

| Method                              | What it returns                                    |
|-------------------------------------|----------------------------------------------------|
| `area(resolution=1)`                | the integrated envelope, in nanosecond-amplitude   |
| `peak_amplitude(resolution=1)`      | `max(abs(envelope))`                               |
| `rms_amplitude(resolution=1)`       | the root-mean-square amplitude                     |
| `spectrum(resolution=1)`            | a `(frequencies_hz, complex_spectrum)` pair — one-sided for a real envelope, two-sided for an IQ one |
| `plot(resolution=1, ...)`           | a matplotlib `Axes` (a pair of them for IQ shapes) |

`plot()` needs matplotlib, which ships in the `qprogram[viz]` extra; the rest
are pure numpy.

Schema validation in `play()` and `measure()` uses the static class to decide
whether an IQ waveform is allowed on a given bus. `IQ` buses accept
`IQWaveform`; `single` buses accept `Waveform`.

## Single-channel built-ins

```python
from qprogram.waveforms import (
    Arbitrary,
    Chained,
    Cosine,
    FlatTop,
    Gaussian,
    GaussianDragCorrection,
    Ramp,
    Sech,
    Sine,
    Square,
    SuddenNetZero,
    Tukey,
)

Square(amplitude=0.5, duration=100)
Gaussian(amplitude=0.5, duration=40, sigma=8)
GaussianDragCorrection(amplitude=0.5, duration=40, sigma=8, beta=0.1)
Sech(amplitude=0.5, duration=100, tau=12.5)
Tukey(amplitude=0.5, duration=100, alpha=0.4)
Ramp(from_amplitude=0.0, to_amplitude=1.0, duration=200)
FlatTop(amplitude=0.5, duration=200, smooth_duration=20)
SuddenNetZero(amplitude=0.5, duration=100, b=0.4, t_phi=20)
Sine(amplitude=0.5, duration=200, frequency=50e6)
Cosine(amplitude=0.5, duration=200, frequency=50e6)
Arbitrary(samples=my_numpy_array)
Chained(waveforms=[Square(1.0, 50), Gaussian(0.5, 40, 8)])
```

`FlatTop` accepts an optional `buffer=` for zero-padding on each side, and its
`smooth_duration` is the length of each erf-shaped edge. `Sine` and `Cosine`
take an optional `phase=` in radians. `SuddenNetZero` plays a positive square
segment, a zero hold of width `t_phi`, then a negative segment scaled by `b`;
the two segments are meant to cancel the net integrated flux. The
cancellation is exact only when `b` is 1 and the samples left over after the
hold split evenly between the segments — `b` is detuned from 1 to null
whatever residual the flux line adds, so the `b=0.4` above integrates to a
deliberate non-zero area. `Arbitrary` and `Chained` are escape hatches:
provide samples directly, or concatenate other waveforms (`a + b` builds a
`Chained` too, flattening as it goes).

### What the shape parameters mean

Every duration and width is in **nanoseconds**, and every width is the real
width of the shape — not a ratio against `duration`.

| Parameter                                                     | Meaning                                                                                     |
|---------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| `Gaussian.sigma`, `GaussianDragCorrection.sigma`, `IQDrag.sigma` | The Gaussian standard deviation σ, in nanoseconds.                                        |
| `Sech.tau`                                                     | The sech width τ, in nanoseconds — the `sigma` analogue for `amplitude · sech((t - center) / tau)`. |
| `IQDrag.beta`, `GaussianDragCorrection.beta`                   | The DRAG coefficient β: the weight on the derivative term. Typically small (below 0.5) and tuned per qubit. |
| `Tukey.alpha`                                                  | The taper fraction, in `[0, 1]`: the share of `duration` taken by the rise and fall combined. `0` is a rectangle, `1` is a Hann window. |

`duration` is independent of the width: it is the window the shape is rendered
into, so it controls truncation and nothing else. Widening the window from
`Gaussian(amp, duration=40, sigma=8)` to `Gaussian(amp, duration=60, sigma=8)`
adds 20 ns of tail without changing the pulse shape — which is what makes
`sigma` the knob a calibration sweep reaches for, since a Gaussian's rotation
angle goes with its area, roughly `amplitude · sigma · sqrt(2π)`.

## IQ built-ins

```python
from qprogram.waveforms import IQDrag, IQPair, IQRotation, IQZero, Modulated, Square

IQPair(I=Square(1.0, 2000), Q=Square(0.0, 2000))
IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
IQZero(envelope=Square(1.0, 2000))
Modulated(envelope=Square(1.0, 200), frequency=50e6)
IQRotation(base=IQDrag(0.5, 40, 8, 0.1), phase=1.5708)
```

`IQPair` is the generic pair; you can put any two `Waveform`s in it as long
as they have the same duration. `IQDrag` is the standard DRAG shape: a
Gaussian on I plus its derivative on Q, scaled by `beta`. The other three
adapt an existing shape: `IQZero` puts a single-channel envelope on I and
silence on Q, `Modulated` lifts one onto an IQ bus at an intermediate
frequency, and `IQRotation` rotates an IQ pair's channels by a phase.

## Variable-aware parameters

Every numeric parameter accepts a `Variable` or an `Expression`. This is how
you sweep pulse parameters inside a loop:

```python
amp = program.variable("amp")

with program.sweep(amp, qp.Range(0.0, 1.0, 0.01)):
    program.play("drive_q0", Gaussian(amplitude=amp, duration=40, sigma=8))
```

The waveform stores the symbolic parameter. The platform's compiler then
decides whether to update an amplitude register on the fly (cheap) or
re-upload the waveform on each iteration (more expensive). You write the
same thing either way.

You can evaluate a waveform locally for plotting if you bind its variables:

```python
amp.set_value(0.7)
samples = Gaussian(amp, 40, 8).envelope()
amp.reset()
```

## Structural equality

Waveforms compare equal by structure:

```python
Gaussian(0.5, 40, 8) == Gaussian(0.5, 40, 8)  # True
IQPair(Square(1.0, 100), Square(0.0, 100)) == ...  # True if both halves match
```

This is what makes program-level structural equality work after a `.qp`
round-trip.

## Custom waveforms

Subclass `Waveform` or `IQWaveform` and implement the abstract methods. To
make the new shape serializable, register it with the parser:

```python
import numpy as np
import qprogram as qp
from qprogram.waveforms import Waveform


@qp.register_waveform
class HalfSine(Waveform):
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
files using the constructor syntax, and the parser reconstructs the object
on load. The serializer walks `vars(wf)` to figure out which arguments to
emit, so anything stored on `self` and matching the constructor signature
round-trips for free. See
[Adding waveforms](../developer/adding-waveforms.md) for the developer-side
details.

## Picking the right shape

| When you need                                                    | Reach for                          |
|-------------------------------------------------------------------|------------------------------------|
| A simple square pulse                                             | `Square`                           |
| A Gaussian envelope                                               | `Gaussian`                         |
| A DRAG pulse on an IQ bus                                         | `IQDrag`                           |
| A specific I and Q pairing                                        | `IQPair(I=..., Q=...)`             |
| A single-channel envelope on an IQ bus                            | `IQZero` / `Modulated`             |
| Smooth-edged square (flux ramp)                                   | `FlatTop`                          |
| Cosine-tapered square (a window function)                         | `Tukey`                            |
| An adiabatic-passage envelope                                     | `Sech`                             |
| A continuous drive tone                                           | `Sine` / `Cosine`                  |
| Net-zero flux pulse for two-qubit gates                           | `SuddenNetZero`                    |
| Linear ramp                                                       | `Ramp`                             |
| Sample array you already have                                     | `Arbitrary`                        |
| Two waveforms played back to back                                 | `Chained`                          |
| Anything else                                                     | Subclass `Waveform` / `IQWaveform` |

For programs that go through calibration, you usually pass string aliases
(`"pi_pulse"`, `"readout"`) and let the platform substitute the concrete
waveform from its calibration store. See [Operations](operations.md) for the
`play` and `measure` signatures and
[Saving and loading](serialization.md) for how aliases survive the `.qp`
round-trip.
