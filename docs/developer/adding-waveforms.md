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
base, so `HalfSine(0.5, 100) == HalfSine(0.5, 100)` works automatically. The
base class also derives `peak_amplitude()`, `rms_amplitude()`, `area()`,
`spectrum()`, and `plot()` from `envelope()`, so implementing the two
abstract methods gets you the whole surface.

For IQ waveforms, subclass `IQWaveform`:

```python
from qprogram.waveforms.square import Square
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
import numpy as np
from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


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

Every built-in waveform is written exactly this way: the explicit
`isinstance` guard keeps the boundary between symbolic and concrete
obvious, and `evaluate_or_raise()` is what turns an unassigned variable into
an `UnassignedVariableError` at the point of use rather than a confusing
numpy failure further down.

## Registering a built-in (in `qprogram`)

Built-in waveforms are listed in
`src/qprogram/serialization/registry.py`, inside
`_register_builtin_waveforms()` — the registry is keyed by class name:

```python
def _register_builtin_waveforms() -> None:
    from qprogram.waveforms import (
        Arbitrary,
        Chained,
        ...,
        HalfSine,   # add your import
    )

    for cls in [
        Square,
        Gaussian,
        ...,
        HalfSine,   # and add it here
    ]:
        _waveform_registry[cls.__name__] = cls
```

Export it from `src/qprogram/waveforms/__init__.py`:

```python
from qprogram.waveforms.half_sine import HalfSine

__all__ = [..., "HalfSine"]
```

And give it a capability token, so a platform can advertise that its
compiler knows how to lower the shape. Core waveforms are mapped in
`_register_builtin_waveform_tokens()` in `src/qprogram/protocol.py`, and
the token goes in `protocol._BASE_TOKENS` alongside the other
`waveform.*` entries:

```python
WAVEFORM_TOKEN.update(
    {
        ...,
        HalfSine: "waveform.half_sine",
    }
)
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

    def envelope(self, resolution: int = 1) -> np.ndarray: ...

    def get_duration(self) -> int:
        return self.duration
```

After import, the parser knows the class. `MyPulse(0.5, 100, 3.14)` in a
`.qp` file rebuilds an instance using the constructor signature.
Registering a *different* class under a name already taken raises
`ValueError` — it would silently change how every existing file parses that
constructor.

To let profiles advertise the shape, pair the registration with
`qp.register_waveform_token(MyPulse, "waveform.my_pulse")`, which adds the
token to the capability registry for you. Without a token the waveform
contributes only its channel-kind token (`waveform.single` / `waveform.iq`)
and skips the per-class refinement, so any profile advertising that channel
kind accepts it.

## How the writer emits a waveform

```
Gaussian(amplitude=0.5, duration=40, sigma=8)
```

Algorithm (`_Writer.serialize_waveform`):

1. Take the class name verbatim — it is the registry key.
2. Walk `vars(wf)` in assignment order.
3. Skip anything whose name begins with `_`.
4. Emit every remaining attribute as a `key=value` pair, recursing through
   `serialize_value` for expression nodes and nested waveforms.
5. Never truncate. An `Arbitrary` sample array is emitted in full, because
   the parser has no way to recover dropped samples.

This means:

- Constructor parameters are recovered from attributes, so store each one on
  `self` under the same name.
- A stored attribute that is *not* an `__init__` parameter is still emitted,
  and the reload will fail on it. Prefix computed attributes with `_`.
- Defaults are emitted explicitly rather than omitted, which keeps the file
  readable and its meaning stable if a default ever changes.

## How the parser reads a waveform

```
parser._parse_waveform_expr("Gaussian(amplitude=0.5, duration=40, sigma=8)")
```

1. Split off the class name; look it up in the waveform registry (falling
   back to the sweep-source registry, which shares the `Name(args)` shape).
2. Parse the argument list into positional args and keyword args.
3. Coerce numeric tokens to numbers, identifier tokens to `Variable`
   references (when the identifier is declared), `Expression` subtrees, or
   nested waveforms.
4. Call `cls(**kwargs)` when any argument was named, `cls(*args)`
   otherwise — the writer emits every waveform argument as a keyword, so the
   keyword path is the one round-trips take. The two are never combined, so a
   hand-written call that mixes them drops its positional arguments.

Only the lookup failure is wrapped: a name registered as neither a waveform
nor a sweep source raises `ParseError: Unknown waveform or sweep source type:
<name>`. The construction itself is a bare `cls(**kwargs)` / `cls(*args)`, so
an argument list the constructor rejects surfaces as the constructor's own
`TypeError`, unwrapped and with no line number. Loading

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, bogus=8)
```

raises `TypeError: Gaussian.__init__() got an unexpected keyword argument
'bogus'`, not a `ParseError`. Read that as a signature mismatch and check the
constructor: the usual cure is to make sure it accepts both literal numbers
and `Expression`s where applicable.

## Testing a new waveform

Add tests in `tests/test_waveforms.py`:

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
[`docs/reference/qp-format.md`](../reference/qp-format.md). The generated
API reference picks the class up from the package, so the class docstring
carries the description of the shape and an `Args:` entry per parameter.
