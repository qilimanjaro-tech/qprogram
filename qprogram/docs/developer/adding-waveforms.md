# Adding a waveform

This guide shows two scenarios:

1. Adding a **built-in** waveform to `qprogram` itself.
2. Adding a **user-registered** waveform from your own code or a downstream
   package.

Both go through the same `Waveform` / `IQWaveform` interface; the only
difference is where the registration call lives.

## What a waveform must provide

For single-channel shapes, subclass `Waveform` and implement two methods:

```python
import numpy as np
from qprogram.waveforms.waveform import Waveform


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

`Waveform` carries structural equality and hashing via the `_StructuralEqMixin`
base, so `HalfSine(0.5, 100) == HalfSine(0.5, 100)` works automatically.

For IQ waveforms, subclass `IQWaveform`:

```python
from qprogram.waveforms.waveform import IQWaveform, Waveform


class MyIQShape(IQWaveform):
    def __init__(self, amplitude: float, duration: int) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def get_I(self) -> Waveform:
        return Square(self.amplitude, self.duration)

    def get_Q(self) -> Waveform:
        return Square(0.0, self.duration)

    def get_duration(self) -> int:
        return self.duration
```

`IQWaveform.get_I` and `IQWaveform.get_Q` must produce a `Waveform` each of
the same duration.

## Variable-aware parameters

If a parameter is meant to be sweepable, accept `float | Expression` (or
`int | Expression`) in the signature and call `evaluate_or_raise()` inside
`envelope()` / `get_I()` / `get_Q()`:

```python
from qprogram.variable import Expression


class HalfSine(Waveform):
    def __init__(self, amplitude: float | Expression, duration: int | Expression) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amp = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        dur = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        n = dur // resolution
        t = np.arange(n) / n * np.pi
        return amp * np.sin(t)

    def get_duration(self) -> int:
        return self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
```

There is a tiny helper in `qprogram.variable` that does the same in one
call (`resolve(...)` if you find it), but the explicit form makes the
boundary between symbolic and concrete obvious.

## Registering a built-in (in `qprogram`)

Built-in waveforms are registered in `qprogram/src/qprogram/serialization/registry.py`,
inside `_register_builtins()`:

```python
def _register_builtins() -> None:
    register_waveform(Square)
    register_waveform(Gaussian)
    register_waveform(GaussianDragCorrection)
    register_waveform(Ramp)
    register_waveform(FlatTop)
    register_waveform(SuddenNetZero)
    register_waveform(Arbitrary)
    register_waveform(Chained)
    register_waveform(IQPair)
    register_waveform(IQDrag)
    # add yours here:
    register_waveform(HalfSine)
```

Then export it from `qprogram/src/qprogram/waveforms/__init__.py`:

```python
from qprogram.waveforms.half_sine import HalfSine

__all__ = [..., "HalfSine"]
```

After this, `HalfSine(0.5, 100)` appears in `.qp` files as
`HalfSine(amplitude=0.5, duration=100)` and round-trips automatically.

## Registering a user waveform

If you do not own `qprogram` and you just want to plug a custom shape into
your program, decorate the class with `@qp.register_waveform`:

```python
import numpy as np
import qprogram as qp
from qprogram.waveforms import Waveform


@qp.register_waveform
class MyPulse(Waveform):
    def __init__(self, amplitude: float, duration: int, knob: float) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.knob = knob

    def envelope(self, resolution: int = 1) -> np.ndarray:
        ...

    def get_duration(self) -> int:
        return self.duration
```

After import, the parser knows the class. `MyPulse(0.5, 100, 3.14)` in a
`.qp` file rebuilds an instance using the constructor signature.

The serializer walks `vars(wf)` to figure out which arguments to emit. The
rule of thumb: if you store an `__init__` argument on `self` with the same
name, the round-trip works for free.

## How the writer emits a waveform

```
Gaussian(amplitude=0.5, duration=40, sigma=8)
```

Algorithm (in `writer.py`):

1. Look up the class in the waveform registry to get its public name.
2. Walk `inspect.signature(cls.__init__).parameters` in order.
3. For each parameter, fetch `getattr(wf, name)` and emit it as a `key=value`
   pair (or positional, if it has no default).
4. Skip private attributes (anything beginning with `_`).
5. Recurse into expression nodes and nested waveforms.

This means:

- Parameters with default values appear only if they differ from the default.
- A keyword-only parameter on the `__init__` will round-trip as a keyword.
- A computed attribute that is not an `__init__` argument will still get
  emitted unless you prefix it with `_`.

## How the parser reads a waveform

```
parser._parse_waveform_expr("Gaussian(amplitude=0.5, duration=40, sigma=8)")
```

1. Split off the class name; look it up in the registry.
2. Parse the argument list into positional args and keyword args.
3. Coerce numeric tokens to numbers, identifier tokens to `Variable`
   references (when known), `Expression` subtrees, or nested waveforms.
4. Call `cls(*args, **kwargs)`.

If `cls.__init__` does not accept what the parser produced, you get a
`ParseError` naming the field. The cure is usually to make sure your
constructor accepts both literal numbers and `Expression`s where applicable.

## Testing a new waveform

Add tests in `qprogram/tests/test_waveforms.py`:

```python
def test_half_sine_envelope():
    wf = HalfSine(amplitude=1.0, duration=100)
    env = wf.envelope()
    assert env.shape == (100,)
    assert env[0] == pytest.approx(0.0)
    assert env[50] == pytest.approx(1.0, abs=0.05)


def test_half_sine_round_trip():
    p = QProgram()
    p.play("drive_q0", HalfSine(0.5, 100))
    text = qp.dumps(p)
    reloaded = qp.loads(text)
    assert qp.dumps(reloaded) == text
```

## Documenting a new waveform

Add a short paragraph to [`docs/guide/waveforms.md`](../guide/waveforms.md)
and a constructor line in
[`docs/reference/qp-format.md`](../reference/qp-format.md). The auto-generated
API reference picks up the class from the package docstring; make sure your
class docstring explains the shape and its parameters.
