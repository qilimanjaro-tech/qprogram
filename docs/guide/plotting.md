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
for a pair of quadratures and `Magnitude` for their hypotenuse, and
`value_label=` says what it really is. On a heatmap the same string labels the
colour bar.

```python
result.plot(m0, value_label="Readout response")
```

For a heatmap the innermost sweep runs along the x axis and the outermost up
the y axis, matching the loop nesting: the variable that changes fastest goes
left to right. `x=` and `y=` override that, and naming one settles the other:

```python
result.plot(m0)  # x is "dur", the inner sweep
result.plot(m0, x="amp")  # x is "amp", so "dur" moves to y
```

A dimension built by a parallel composition is the case where the default has
no answer. It composes several variables onto one dimension and carries one
coordinate per variable, so there is no single set of values to put on the
axis. Asking anyway raises, because the alternative is worse: `coords["a|b"]`
returns a plain integer range rather than failing, so a guess would produce a
wrong axis that looks entirely plausible.

```python
result.plot(m0)  # ValidationError: 'a|b' composes 2 swept variables
result.plot(m0, x="a")  # that variable's values, and its label
result.plot(m0, x="a|b")  # the sweep index, if that is what you meant
```

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
`point_alpha`, `grid`, `legend`, and `colorbar` alongside `theme`. `markers` is
worth turning on for a coarse sweep, where the points are the measurement and
the line between them is interpolation.

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
```

`build_figure` is the half worth reading first when writing one. It returns a
`Figure` holding `Line`, `Points`, and `Mesh` marks, each a small frozen
dataclass of numpy arrays, and a renderer dispatches on their types. Nothing in
that half imports a plotting library, so a renderer for any backend reads the
same description.

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
