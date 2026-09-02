# Adding a waveform

There are two scenarios, and they share everything except where the registration
call lives. A built-in waveform is a module inside `src/qprogram/waveforms/`,
listed in the package's own registries, and can be advertised by a capability
token that core ships. A user waveform is a class in your own code or a
downstream package, registered at import time through the public API. Both
subclass the same two bases and are written to `.qp` files by the same code.

Snippets whose first line is a `# src/qprogram/...` path comment are package
source, and they keep their intra-package imports (`from
qprogram.waveforms.waveform import Waveform`): inside the package, `import
qprogram` would close an import cycle. The snippets without that comment are
user code, and follow the convention the rest of the documentation uses, a
single `import qprogram as qp` with everything else reached through `qp.`. The
snippets in [Testing a new waveform](#testing-a-new-waveform) are a third case:
they are bodies lifted from files under `tests/`, which import the names they
use directly (`from qprogram import QProgram`), so nothing in them is prefixed
either.

## What a waveform must provide

A single-channel shape subclasses `Waveform` and implements two abstract
methods:

```python
# src/qprogram/waveforms/half_sine.py
from __future__ import annotations

import numpy as np

from qprogram.waveforms.waveform import Waveform


class HalfSine(Waveform):
    """Half period of a sine, rising from zero and returning to it.

    Args:
        amplitude (float): Peak amplitude, reached at the midpoint.
        duration (int): Pulse duration in nanoseconds.
    """

    def __init__(self, amplitude: float, duration: int) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        """Return the envelope sampled at ``resolution``-ns steps.

        Args:
            resolution (int, optional): Sample period in nanoseconds.

        Returns:
            A 1-D array of ``duration / resolution`` samples.
        """
        n = self.duration // resolution
        t = np.arange(n) / n * np.pi
        return self.amplitude * np.sin(t)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds.

        Returns:
            The duration in nanoseconds.
        """
        return self.duration
```

`envelope(resolution)` returns `duration / resolution` samples, with
`resolution` in nanoseconds and `1` meaning one sample per nanosecond. Shape
parameters that carry a time (a `sigma`, a rise time) are converted to samples
inside `envelope`, so the shape stays the same at every resolution; `Gaussian`
divides `sigma` by `resolution` for exactly that reason. The array dtype follows
the parameters, so an integer amplitude produces an integer array, which
`Square` documents on `envelope`. `Arbitrary` is the one built-in that ignores
`resolution`: its samples are the envelope already, one per nanosecond.

`Waveform` implements the rest of its surface in terms of those two, so a
subclass inherits all of it and writes none of it. `peak_amplitude()` is
`max(|envelope|)`, `rms_amplitude()` the root mean square of the samples,
`area()` the trapezoidal integral in nanosecond-amplitude units
(`np.trapezoid(env, dx=resolution)`), and `spectrum()` a one-sided `np.fft.rfft`
paired with frequencies in Hz. `plot()` describes the envelope as a
`qp.plotting.Figure` and hands it to a renderer, and the Jupyter `_repr_html_`
draws it once per surface; both reach matplotlib by default, which ships in the
`viz` extra and is imported the first time something draws with it, so the
package stays importable without it.

Nothing in core calls `envelope()`. Validation and serialization work on the
constructor arguments alone, so samples are rendered only when someone asks for
them: the analysis helpers above, a plot, or a platform compiler lowering the
program. A shape whose `envelope` is expensive costs nothing until then.

Equality and hashing come from `_StructuralEqMixin`, which compares `vars(self)`
through `ast_eq`, so `HalfSine(0.5, 100) == HalfSine(0.5, 100)` holds and a
waveform can be a dictionary key. The mixin exists because symbolic parameters
and numpy arrays do not compose under Python's default identity equality. A
waveform is a value: once it has been hashed, do not mutate its attributes.

An IQ shape subclasses `IQWaveform` and supplies its two channels:

```python
# src/qprogram/waveforms/half_sine_iq.py
from __future__ import annotations

from qprogram.waveforms.half_sine import HalfSine
from qprogram.waveforms.square import Square
from qprogram.waveforms.waveform import IQWaveform, Waveform


class HalfSineIQ(IQWaveform):
    """A half-sine on I, silence on Q.

    Args:
        amplitude (float): Peak amplitude of the I channel.
        duration (int): Pulse duration in nanoseconds.
    """

    def __init__(self, amplitude: float, duration: int) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def get_I(self) -> Waveform:
        """Return the in-phase channel."""
        return HalfSine(self.amplitude, self.duration)

    def get_Q(self) -> Waveform:
        """Return the quadrature channel, silent for this shape."""
        return Square(0.0, self.duration)

    def get_duration(self) -> int:
        """Return the pulse duration in nanoseconds."""
        return self.duration
```

The analysis helpers on `IQWaveform` work on the complex envelope `I + jQ`, and
its `spectrum()` is a two-sided `np.fft.fft` with the zero frequency shifted to
the middle, because a complex envelope carries information at negative
frequencies that a one-sided transform would fold away.

Equal channel durations are an invariant each class enforces for itself, not a
base-class check. `IQPair`, which takes its two channels as arguments, compares
them in `__init__` and raises `ValidationError: IQPair channels must have equal
durations; got I=10 ns, Q=20 ns`, deferring the check when a duration is still
symbolic. A shape that builds its own channels, as `HalfSineIQ` does, has no
such check to make. Get it wrong and the failure arrives from numpy the first
time the complex envelope is assembled: `ValueError: operands could not be
broadcast together with shapes (10,) (20,)`.

Choose the base class deliberately, because two behaviors follow from it and
neither is configurable. `Play.required_capabilities()` reads
`isinstance(waveform, IQWaveform)` to decide between the `waveform.iq` and
`waveform.single` channel-kind tokens, and `QProgram.play` and `QProgram.measure`
check the same thing against the bus's declared channel:

```
ValidationError: Bus 'q0/drive' is an IQ channel but received a single-channel
Waveform (Square). Use an IQWaveform (e.g. IQPair, IQDrag) instead.
```

`register_waveform` does not enforce the hierarchy, so a class outside it
registers and serializes. What it loses is both of the checks above plus
variable collection: `Operation.variables()` descends into an attribute only
when it is an `Expression`, a `Waveform`, an `IQWaveform`, or a list of those, so
a swept parameter held by a class outside the hierarchy is invisible to the
program that contains it.

## Variable-aware parameters

A parameter meant to be swept is annotated `float | Expression` (or
`int | Expression`) and resolved with `evaluate_or_raise()` at the point of use:

```python
# src/qprogram/waveforms/half_sine.py
from __future__ import annotations

import numpy as np

from qprogram.variable import Expression
from qprogram.waveforms.waveform import Waveform


class HalfSine(Waveform):
    def __init__(self, amplitude: float | Expression, duration: int | Expression) -> None:
        self.amplitude = amplitude
        self.duration = duration

    def envelope(self, resolution: int = 1) -> np.ndarray:
        amplitude = self.amplitude.evaluate_or_raise() if isinstance(self.amplitude, Expression) else self.amplitude
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        n = int(duration) // resolution
        t = np.arange(n) / n * np.pi
        return amplitude * np.sin(t)

    def get_duration(self) -> int:
        duration = self.duration.evaluate_or_raise() if isinstance(self.duration, Expression) else self.duration
        return int(duration)
```

Every built-in is written this way. The explicit `isinstance` guard keeps the
boundary between symbolic and concrete visible at each use, and
`evaluate_or_raise()` turns an unbound variable into a message that names it,
`UnassignedVariableError: Cannot evaluate expression Variable('amp'):
unassigned variable(s) {Variable('amp')}`, rather than a numpy failure further
down. `get_duration()` has to do the same resolution, because it is called
independently of `envelope()`: `IQPair` uses it to compare channels, and
`Chained` sums it across children.

Only parameters annotated with `Expression` accept one. A parameter that must be
a plain number, such as `Arbitrary.samples`, keeps its concrete annotation, and
`qprogram.waveforms`'s module docstring states that rule for readers of the API
reference.

## Registering a built-in

A built-in shape is named in three registries inside the package, exported from
the waveform subpackage, and listed in the API reference. Start with the export,
in `src/qprogram/waveforms/__init__.py`, whose import block and `__all__` are
both alphabetical:

```python
# src/qprogram/waveforms/__init__.py
from qprogram.waveforms.half_sine import HalfSine

__all__ = [..., "HalfSine", ...]
```

Then the serialization registry, in `_register_builtin_waveforms()` in
`src/qprogram/serialization/registry.py`. The import sits inside the function to
break the cycle with `qprogram.waveforms`, and the loop keys each class by its
own `__name__`:

```python
# src/qprogram/serialization/registry.py
def _register_builtin_waveforms() -> None:
    from qprogram.waveforms import HalfSine

    for cls in [..., HalfSine]:
        _waveform_registry[cls.__name__] = cls
```

Keying by `__name__` makes the class name the constructor name on the wire, so
renaming the class is a format change. The capability token is separate and need
not match: `SuddenNetZero` is spelled that way in a `.qp` file and carries the
token `waveform.snz`.

Then the capability side, in `src/qprogram/protocol.py`: the token string joins
the other `waveform.*` entries in `_BASE_TOKENS`, and the class-to-token mapping
joins `_register_builtin_waveform_tokens()`.

```python
# src/qprogram/protocol.py
WAVEFORM_TOKEN.update(
    {
        HalfSine: "waveform.half_sine",
    },
)
```

`WAVEFORM_TOKEN` is populated lazily, on the first call to `waveform_token()`,
because `qprogram.protocol` cannot import `qprogram.waveforms` at module level.

Last, add the class to the `members:` list under `::: qprogram.waveforms` in
[`docs/reference/api-qprogram.md`](../reference/api-qprogram.md). That list is
explicit, so a class missing from it does not appear in the API reference at all,
however complete its docstring.

After those edits, `HalfSine(0.5, 100)` writes as
`HalfSine(amplitude=0.5, duration=100)` and reads back to an equal instance.

## Registering a user waveform

From outside the package, one decorator does the serialization half:

```python
import numpy as np
import qprogram as qp


@qp.register_waveform
class MyPulse(qp.waveforms.Waveform):
    def __init__(self, amplitude: float, duration: int, knob: float) -> None:
        self.amplitude = amplitude
        self.duration = duration
        self.knob = knob

    def envelope(self, resolution: int = 1) -> np.ndarray:
        n = self.duration // resolution
        return self.amplitude * np.sin(np.arange(n) / n * np.pi) ** self.knob

    def get_duration(self) -> int:
        return self.duration


qp.register_waveform_token(MyPulse, "waveform.my_pulse")
```

`register_waveform` returns the class, which is what lets it be used as a
decorator, and registers it under `cls.__name__`. After the module is imported,
`MyPulse(0.5, 100, 3.14)` in a `.qp` file rebuilds an instance from the
constructor signature. Registering the same class again is a no-op; registering
a *different* class under a taken name raises `ValueError: waveform name
'MyPulse' is already registered to ...`, because it would change how every
existing file parses that constructor.

`register_waveform_token(cls, token)` writes the class-to-token mapping and adds
the token to `CAPABILITY_REGISTRY` in one call, so a profile can list it without
a separate `register_capability_tokens`. Whether to pair the two calls is a real
choice. Without a token, the shape contributes only its channel kind, so every
profile that accepts `waveform.single` accepts it, including platforms that have
never heard of it. With a token, a platform that has not advertised the shape
refuses the program up front:

```
[error] missing-capability: 'Play' requires capability 'waveform.my_pulse' which is not supported by 'dummy-default-v1' (rt) / 'dummy-default-v1' (host) (at body[0])
```

A vendor extension puts both calls in the module its entry point loads, so
installing the package is what makes the shape parseable and advertisable. See
[Building a vendor extension](vendor-extensions.md).

## How the writer emits a waveform

```
Gaussian(amplitude=0.5, duration=40, sigma=8)
```

`_Writer.serialize_waveform` takes the class name verbatim, since it is the
registry key, then walks `vars(wf)` in assignment order, skips any name starting
with `_`, and emits every remaining attribute as `key=value`, recursing through
`serialize_value` for expression nodes and nested waveforms. Sample arrays are
written in full: truncating an `Arbitrary` would leave the parser with no way to
recover the dropped samples.

Three consequences follow. Constructor parameters are recovered from attributes,
so each one has to be stored on `self` under the same name. Defaults are written
explicitly rather than omitted, which keeps a file's meaning stable if a default
later changes. And a public attribute that is *not* a constructor parameter is
emitted too, which makes the file unloadable:

```
play "d" Leaky(amplitude=0.5, duration=20, n_samples=20)
# TypeError: Leaky.__init__() got an unexpected keyword argument 'n_samples'
```

Prefix computed attributes with `_` to keep them out of the file. Operations
behave differently here: they serialize from the constructor signature, so a
leftover public attribute is dropped silently rather than written out.

## How the parser reads a waveform

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, sigma=8)
```

Reading that line, the parser hands the constructor call to
`_parse_waveform_expr`. The class name is split off and looked up in the
waveform registry, falling back to the sweep-source registry, which shares the
`Name(args)` shape and the same key-by-class-name design. The argument list is
then split on top-level commas,
respecting quotes and every bracket kind, and each argument is decoded to a
number, a quoted string, a `Variable` reference (when the identifier is
declared), an `Expression` subtree, or a nested waveform. Finally the class is
called: `cls(**kwargs)` when any argument was named, `cls(*args)` otherwise.

Positional and keyword arguments are never combined. The writer spells every
waveform argument as a keyword, so a written file always takes the keyword path;
a hand-written call that mixes the two loses its positional values.

Only the lookup failure is wrapped. A name registered as neither a waveform nor
a sweep source raises `ParseError: Unknown waveform or sweep source type:
<name>`. The construction itself is a bare call, so an argument list the
constructor rejects surfaces as the constructor's own `TypeError`, unwrapped and
with no line number. Loading

```
play "drive_q0" Gaussian(amplitude=0.5, duration=40, bogus=8)
```

raises `TypeError: Gaussian.__init__() got an unexpected keyword argument
'bogus'` rather than a `ParseError`. Read that as a signature mismatch and check
the constructor; the usual cure is accepting both literal numbers and
`Expression`s where the parameter is meant to be swept.

## Testing a new waveform

`tests/test_waveforms.py` is organized by shape, with a comment banner per
class. The shapes that are worth pinning are the ones the existing tests pin:
the sample count and the values at the interesting points
(`test_square_envelope`, `test_gaussian_peak_at_center`), the duration
(`test_square_get_duration`), the behavior at a resolution other than 1
(`test_square_resolution_changes_envelope_length`), a symbolic parameter with a
value bound (`test_square_with_expression_amplitude`), and the same parameter
left unbound (`test_square_with_unassigned_expression_raises`).

```python
def test_half_sine_envelope():
    wf = HalfSine(amplitude=1.0, duration=100)
    env = wf.envelope()
    assert env.shape == (100,)
    assert env[0] == pytest.approx(0.0)
    assert env[50] == pytest.approx(1.0, abs=0.05)
```

`tests/test_registry.py` covers registration: `test_waveform_builtins_registered`
asserts that `get_waveform_class("Square")` resolves, and
`test_register_waveform_decorator` and
`test_register_waveform_rejects_different_class_under_taken_name` cover the
user-facing decorator. `tests/test_protocol.py` covers the token side, through
`test_waveform_token_returns_canonical_token_for_known_classes` and
`test_register_waveform_token_extends_registry_and_dispatch`.

`tests/test_round_trip.py` covers serialization with the module's
`_assert_byte_stable` helper, which asserts that `dumps` after `loads` after
`dumps` is identical text:

```python
def test_round_trip_half_sine():
    p = QProgram()
    p.play("drive", HalfSine(0.5, 100))
    _assert_byte_stable(p)
```

`tests/test_round_trip_property.py` generates random programs with hypothesis.
Its `single_waveforms` strategy draws a shape name from a `sampled_from` list
and dispatches on it, and `iq_waveforms` picks between `IQPair` and `IQDrag` on a
boolean draw, so a new shape joins the property tests by adding a branch that
builds it from adversarial parameters. A shape with a stored array belongs there
in particular, since `single_waveforms` draws `Arbitrary` arrays
past any plausible truncation cutoff. `tests/conftest.py` holds the stock
waveform fixtures (`square_pulse`, `gaussian_pulse`, `iq_pulse`,
`iq_pair_pulse`) that the rest of the suite builds programs from. See
[Testing](testing.md).

## Documenting a new waveform

The class docstring carries the description of the shape and an `Args:` entry
per parameter, and it is what the API reference renders, so that is where the
detail belongs. Four pages need an edit as well.

[`docs/guide/waveforms.md`](../guide/waveforms.md) has a table of the
single-channel built-ins and one of the IQ built-ins, a section explaining what
the shape parameters mean, and a "Picking the right shape" table whose third
column names the property that decides between two candidates.

[`docs/reference/qp-format.md`](../reference/qp-format.md) has the constructor
table under "Inline waveform constructors", listing each class with its
parameters and their defaults.

[`docs/reference/api-qprogram.md`](../reference/api-qprogram.md) needs the
`members:` entry described under
[registering a built-in](#registering-a-built-in).

[`docs/guide/capabilities.md`](../guide/capabilities.md) lists the
`waveform.<class>` tokens in its token-prefix table.
