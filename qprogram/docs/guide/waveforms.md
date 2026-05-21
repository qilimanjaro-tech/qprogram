# Waveforms

A waveform describes a pulse envelope. It is pure data; nothing about it
involves hardware. The same `Gaussian(0.5, 40, 8)` shape can drive a
qubit on one platform and a flux line on another, depending on the bus it
ends up on.

## Two base classes

`Waveform` is the base for single-channel (real) shapes.

| Method                              | What it returns                        |
|-------------------------------------|----------------------------------------|
| `envelope(resolution=1)`            | numpy array of amplitudes per sample   |
| `get_duration()`                    | duration in nanoseconds                |

`IQWaveform` is the base for IQ pairs:

| Method                              | What it returns                        |
|-------------------------------------|----------------------------------------|
| `get_I()`                           | the in-phase `Waveform`                |
| `get_Q()`                           | the quadrature `Waveform`              |
| `get_duration()`                    | duration in nanoseconds                |

Schema validation in `play()` and `measure()` uses the static class to decide
whether an IQ waveform is allowed on a given bus. `IQ` buses accept
`IQWaveform`; `single` buses accept `Waveform`.

## Single-channel built-ins

```python
from qprogram.waveforms import (
    Square, Gaussian, GaussianDragCorrection,
    Ramp, FlatTop, SuddenNetZero, Arbitrary, Chained,
)

Square(amplitude=0.5, duration=100)
Gaussian(amplitude=0.5, duration=40, sigma=8)
GaussianDragCorrection(amplitude=0.5, duration=40, sigma=8, beta=0.1)
Ramp(from_amplitude=0.0, to_amplitude=1.0, duration=200)
FlatTop(amplitude=0.5, duration=200, smooth_duration=20)
SuddenNetZero(amplitude=0.5, duration=100, b=0.4, t_phi=20)
Arbitrary(samples=my_numpy_array)
Chained(waveforms=[Square(1.0, 50), Gaussian(0.5, 40, 8)])
```

`FlatTop` accepts an optional `buffer=` for zero-padding on each side.
`Arbitrary` and `Chained` are escape hatches: provide samples directly or
concatenate other waveforms.

## IQ built-ins

```python
from qprogram.waveforms import IQPair, IQDrag, Square

IQPair(I=Square(1.0, 2000), Q=Square(0.0, 2000))
IQDrag(amplitude=0.5, duration=40, sigma=8, beta=0.1)
```

`IQPair` is the generic pair; you can put any two `Waveform`s in it as long
as they have the same duration. `IQDrag` is the standard DRAG shape: a
Gaussian on I plus its derivative on Q.

## Variable-aware parameters

Every numeric parameter accepts a `Variable` or an `Expression`. This is how
you sweep pulse parameters inside a loop:

```python
amp = program.variable("amp")

with program.for_loop(amp, 0.0, 1.0, 0.01):
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
Gaussian(0.5, 40, 8) == Gaussian(0.5, 40, 8)        # True
IQPair(Square(1.0, 100), Square(0.0, 100)) == ...        # True if both halves match
```

This is what makes program-level structural equality work after a `.qp`
round-trip.

## Custom waveforms

Subclass `Waveform` or `IQWaveform` and implement the abstract methods. To
make the new shape serialisable, register it with the parser:

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
| Smooth-edged square (flux ramp)                                   | `FlatTop`                          |
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
