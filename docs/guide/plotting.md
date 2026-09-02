# Plotting results

`QProgramResult.plot` draws one measurement. It looks the array up exactly the
way `get` does, works out what kind of figure its shape asks for, and hands the
drawing to a renderer:

```python
result = qp.simulate(program)

result.plot(m0)  # a line per quadrature
result.plot(m0, channels="magnitude")  # hypot(I, Q)
result.plot(m0, field="state")  # the classified outcome
```

The default renderer is matplotlib, which comes with the `viz` extra and is
imported the first time something is drawn, so `import qprogram` never pulls in
a plotting library. It returns the `Axes` it drew on, which is the point: a
figure is a starting position, not a finished picture, and everything the call
does not decide is one method away on the object that comes back.

```python
ax = result.plot(m0)
ax.axvline(0.5, linestyle="--")
ax.set_ylabel("Readout response")
```

In a notebook that axes is also the cell's value, so a `result.plot(m0)` on a
line of its own shows `<Axes: ...>` beside the figure. Bind it the way the
snippet above does, or end the call with a semicolon.

Behind that call are two halves that never meet. `qp.plotting.build_figure`
reads the array and returns a `Figure`: marks holding numpy arrays, two axis
labels, and nothing about colour or canvas. A renderer takes that figure and a
`Style` and draws it. The seam is what lets a second backend exist, and what
lets a test check the shape of a figure without a display attached.

## What the shape decides

Every dimension except `IQ` is a plot dimension. `IQ` is the one that never
becomes an axis: it holds the two quadratures of a single measured point, so it
becomes the series of a line figure or the two axes of a scatter. `time` is an
ordinary plot dimension, which is why a raw trace draws against it.

| Plot dimensions | Figure          | Example                                     |
|-----------------|-----------------|---------------------------------------------|
| one             | lines           | a Rabi sweep, `("gain", "IQ")`               |
| two             | a heatmap       | a chevron, `("amp", "dur", "IQ")`            |
| none            | `ValidationError` | an unswept measurement, `("IQ",)`          |
| three or more   | `ValidationError` | select one down with `data.sel()` first    |

`kind=` overrides the inference, and `kind="scatter"` is the one shape that is
never inferred: plotting I against Q is a choice no dimension count implies. It
puts I on one axis and Q on the other and flattens every other dimension into
the cloud.

```python
result.plot(shots, kind="scatter")
```

Its axes are settled by what they are, so `x`, `y`, and `channels` all raise
there rather than being quietly ignored. `y` raises on a line figure for the
same reason: only a heatmap has a second dimension to put on an axis.

## The two quadratures

`channels=` says what to make of the `IQ` dimension.

| `channels`    | What is drawn                                       |
|---------------|-----------------------------------------------------|
| `"iq"`        | one line per quadrature, labeled `I` and `Q`        |
| `"i"`, `"q"`  | that quadrature alone                               |
| `"magnitude"` | `hypot(I, Q)`, the reading a rotation cannot change |
| `"phase"`     | `arctan2(Q, I)`, in radians                         |

A line figure takes both quadratures by default, since that is the pair the
measurement produced. A heatmap colours one surface and has to reduce them, so
it takes the magnitude instead; `channels="iq"` on a heatmap raises rather than
picking a quadrature for you. An array with no `IQ` dimension, a `state` field
for instance, is already one number per point and rejects `channels` outright.

## Axis labels and which axis is which

An axis labels itself from the coordinate. A variable declared with a `label`
and `units` carries both onto its coordinate, and the axis reads
`Drive amplitude (V)` with nothing typed out:

```python
gain = program.variable("gain", label="Drive amplitude", units="V")
```

Without a label the axis falls back to the variable id, and without units it is
the label alone.
[Variables and expressions](variables.md#label-units-and-description) has what
the two strings are and where else they travel.

The other axis is the measured quantity, and there the result has less to go
on: a demodulated point is whatever the readout chain makes of it, and no unit
follows from the program. It is labeled from the channel by default, `Signal`
for a pair of quadratures and `Magnitude` for their hypotenuse, and `value=`
says what it really is. The same words label the colour bar of a heatmap.

```python
result.plot(m0, value=qp.plotting.Quantity("Readout response"))
```

For a heatmap the innermost sweep runs along the x axis and the outermost up
the y axis, matching the loop nesting: the variable that changes fastest goes
left to right. `x=` and `y=` override that, and naming one settles the other:

```python
result.plot(m0)  # x is "dur", the inner sweep
result.plot(m0, x="amp")  # x is "amp", so "dur" moves to y
```

## Two variables on one axis

A dimension built by a parallel composition carries one coordinate per composed
variable and none of its own, so there are two readings of every sample and no
reason to throw one away. Both are drawn: the first goes on the axis and the
second on a twin scale opposite it, which is matplotlib's `secondary_xaxis`,
the position-tracking form of `twiny`. The order is the order the dimension
name gives, which is the order the loops were written in, so `"freq|time"`
draws frequency along the bottom and time along the top.

```python
with program.sweep(freq, qp.Range(4e9, 5e9, 25e6)) | program.sweep(time, qp.Range(0, 400, 10)):
    m0 = program.measure(q[0].readout, "readout", "weights")

result.plot(m0)  # freq along the bottom, time along the top
```

The twin ticks at samples rather than at round numbers. The two loops advanced
in lockstep, so tick *i* and sample *i* are the same measurement, and putting a
tick anywhere else would mean interpolating between measured points to label a
position nothing was measured at. `Style(twin_ticks=...)` is how many to aim
for; a sweep shorter than that gets one per sample.

`x=` and `y=` name an axis to draw on its own, which is how an axis with
nothing above it is asked for:

```python
result.plot(m0, x="freq")  # frequency along the bottom, nothing on top
result.plot(m0, x="time")  # time along the bottom instead
result.plot(m0, x="freq|time")  # the sweep index, if that is what you meant
```

A heatmap twins each axis separately, so a chevron whose inner loop is a
composition reads its second variable across the top and a composition on the
outer loop reads up the right-hand side. A composition of three or more
variables draws the first two and leaves the rest: two scales on one axis is
already the most a reader can follow, and the coordinates are all still on the
array for a caller who wants a different one.

## Restating a quantity

A result carries hertz because the instrument takes hertz, and the figure of it
wants gigahertz. That is two changes at once, arithmetic on the numbers and a
new unit on the axis, and `Quantity` carries the pair so that neither can
travel without the other:

```python
from qprogram.plotting import Quantity

result.plot(
    m0,
    channels="magnitude",
    coords={"freq": Quantity(units="GHz", transform=lambda v: v / 1e9)},
    value=Quantity("Readout magnitude"),
)
```

`coords=` is keyed by the name the axis resolved to, which is the same string
`x=` takes: the coordinate on the axis, or the dimension when no coordinate is.
A twin scale is keyed by its own coordinate the same way.
A key that reaches no axis raises rather than doing nothing, since a figure
that ignored it would print the axis it was asked to change. `value=` is the
measured quantity wherever it lands: the y axis of a line, the colour bar of a
heatmap, both axes of a scatter.

| Field | What it does |
|---|---|
| `label` | Replaces the coordinate's `long_name`, or the name the channel implies. `None` keeps it. |
| `units` | Replaces the coordinate's `units`. `None` keeps it, `""` says the numbers now carry none. |
| `transform` | Arithmetic on the values. Gets a copy of the whole array that would have been drawn, and returns one real number per value. |

Read positionally the three are the sentence the axis makes:

```python
result.plot(m0, coords={"freq": Quantity("Detuning", "MHz", lambda f: (f - 5e9) / 1e6)})
```

A `Quantity` describes presentation only. `result.get(m0)` is still in hertz
after the figure of it has been drawn in gigahertz, which is what you want when
the next line fits a peak, and what to remember when the line after that draws
on the axes: everything you hand the returned `Axes` is in the figure's units,
so a frequency read back off the array needs the same `/ 1e9` the figure got.

### One rule, in both directions

A change of unit and a change of numbers travel together. Rescaling values that
carry a unit has to say what the unit is now, and a unit that contradicts the
one already there has to come with the arithmetic that earns it:

```python
# On a coordinate that declares units="Hz":
Quantity(transform=lambda v: v / 1e9)  # raises: the axis would read (Hz) over gigahertz
Quantity(units="GHz")  # raises: relabels the unit, changes no number
Quantity(units="GHz", transform=lambda v: v / 1e9)  # both halves, and the figure is drawn
Quantity(units="Hz", transform=lambda v: v - v[0])  # a shift keeps its unit, and says so
```

Both fire only where there is a claim to falsify, so a coordinate that declared
no unit, or a demodulated magnitude that has none to declare, takes either half
alone. That is also how you correct a unit the program never recorded:
`Quantity(units="V")` on an unlabeled coordinate is a statement, not a
contradiction.

A transform is checked for the things that produce a broken figure rather than
a wrong one: it must not raise, must return the shape it was given, must return
real numbers, and must not turn a finite value into an infinity or a NaN. A NaN
the measurement itself carries, from a grid point a conditional arm never
reached, passes through untouched. What cannot be checked is whether the
arithmetic matches the unit — `Quantity(units="GHz", transform=lambda v: v / 1e6)`
is a lie no check here can catch, because `Variable.units` is free-form text
that legitimately holds `arb`, `counts` and `shots`.

### Why this moves the data, not the tick labels

matplotlib would let a formatter rewrite the tick text and leave the numbers
alone, and that is what `EngFormatter` and `FuncFormatter` do. This does not,
for three reasons. The figure model is numpy and xarray only, so a formatter
would be a rendering contract smuggled into the description. A transform like
`v - v[0]` or `v / v.max()` reads the whole array, which no per-tick formatter
can see. And a ticks-only rescale leaves `ax.get_xlim()`, a fit, and any
`axvline` in the old unit while the axis reads the new one, which is the
mismatch this page spends its rules preventing. The numbers on the axis are the
numbers drawn.

## Themes

A `Style` is a palette plus the handful of settings that decide how heavy the
marks are. Two themes ship, `qp.plotting.LIGHT` and `qp.plotting.DARK`, and
both are frozen dataclasses, so a variant is one `dataclasses.replace` away and
a palette of your own is a constructor call.

```python
from dataclasses import replace

import qprogram as qp

result.plot(m0, style=qp.plotting.Style(theme=qp.plotting.DARK))
result.plot(m0, style=qp.plotting.Style(markers=True, legend=False))

house = replace(qp.plotting.LIGHT, series=("#3b6ea5", "#c1554a"))
result.plot(m0, style=qp.plotting.Style(theme=house))
```

`Style` carries `size`, `linewidth`, `markers`, `markersize`, `point_size`,
`point_alpha`, `grid`, `legend`, `colorbar`, and `twin_ticks` alongside `theme`.
`markers` is worth turning on for a coarse sweep, where the points are the
measurement and the line between them is interpolation.

`size` is the one field with no default of its own. `None` means the size that
suits what is being drawn, which is `qp.plotting.DEFAULT_SIZE` for a
measurement and `ENVELOPE_SIZE` or `IQ_ENVELOPE_SIZE` for a waveform, and it is
read only when the figure is made here: axes you pass as `target=` keep the size
they came with.

`Waveform.plot` takes the same `style`, `renderer` and `target`, which is most
of why the palette and the registry are objects of their own: a pi pulse and the
Rabi sweep it produced are one experiment, and a pair that speaks two visual
languages is a papercut. Its style defaults to `Style()` the way this one does,
and the only differences are the size a figure of a pulse comes out at and the
`(I, Q)` pair of panels an IQ shape wants for a `target`.
[Waveforms](waveforms.md) has the rest.

## Another renderer

A renderer is any callable taking a figure, a `Style`, and a surface to draw
on. Registering one works the way `register_sweep_source` works: one name, one
implementation, and a different object under a name already taken raises.

```python
import qprogram as qp
from qprogram.plotting import Line, register_renderer


def to_text(figure, style, target=None):
    """Print a figure instead of drawing it."""
    print(f"{figure.x_label} against {figure.y_label}")
    for mark in figure.marks:
        if isinstance(mark, Line):
            print(f"  {mark.label or 'series'}: {len(mark.x)} points")


register_renderer("text", to_text)
result.plot(m0, renderer="text")
qp.waveforms.Square(0.5, 100).plot(renderer="text")
```

`build_figure` is the half worth reading first when writing one. It returns a
`Figure` holding `Line`, `Points`, and `Mesh` marks, each a small frozen
dataclass of numpy arrays, and a renderer dispatches on their types. Nothing in
that half imports a plotting library, so a renderer for any backend reads the
same description.

A figure hands over everything a renderer needs to draw it and nothing about
how: the marks, the two labels, a title, either `Twin` scale, and `series`, the
palette slot its first mark takes. That last one is only ever set when a figure
is one panel of several that should not repeat a colour, which is what the `Q`
panel of an IQ envelope is; a renderer drawing in one colour ignores it.

## What it does not draw

`plot` returns composable axes rather than trying to be the whole figure. A
layout of several panels, a fit drawn over the data, an annotation pointing at
a peak: none of those follow from anything the result knows, and all of them
are ordinary calls on the axes that come back. The example pages that build one
keep their own plotting code for exactly that reason.

A result does not draw itself in a Jupyter cell the way a waveform does. A
waveform is one shape and has one picture; a result holds every measurement of
a run, and they need not share a field, a shape, or an axis. `repr` stays the
list of what is in there, and `plot` draws the one you name.
