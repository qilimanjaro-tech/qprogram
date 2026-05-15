# Measurements and results

Every `measure(...)` call (and every vendor measurement op exposed under a
`program.<vendor>.*` namespace) returns a `MeasurementHandle`. The handle is
just a stable name. It is the contract between the program description and
the result you get back after running.

## The lifecycle of a measurement

1. **Construction.** You call `measure(...)`. QProgram appends a `Measure`
   node and returns a `MeasurementHandle` holding the chosen name.
2. **Serialization.** The name lands in the `.qp` file as part of the
   `measure` line. Round-tripping through `loads`/`dumps` preserves it.
3. **Execution.** The platform tags every result it produces with the same
   name.
4. **Retrieval.** `result.get(handle)` looks the result up by name.

```python
m0 = program.measure(q[0].readout, "readout", "weights")
# ... later ...
data = result.get(m0)                  # xarray.DataArray
```

## Naming rules

If you do not pass `name=`, QProgram picks the name for you.

| Bus kind                        | Auto-name format                                |
|---------------------------------|-------------------------------------------------|
| Schema-backed `q[0].readout`    | `q0_m<counter>` (`q0_m0`, `q0_m1`, ...)         |
| Schema-backed `c[0, 1].readout` | `c0_1_m<counter>` (the index flattens with `_`) |
| Raw string `"readout_q0"`       | `m<counter>`, global across raw-string buses    |

Counters are per-element-index (so q0 and q1 each have their own
`m<counter>`), and they always start at 0. The second measurement on q0
becomes `q0_m1` regardless of how many measurements happened on q1 in
between.

The counter is recomputed by walking the AST. There is no hidden state on
the program, so `copy.deepcopy`, `with_waveforms`, and `qp.loads(qp.dumps(...))`
all work without surprises.

### Explicit names

You can pass `name=` to force a specific value:

```python
m_custom = program.measure(q[0].readout, "readout", "weights", name="t1_ref")
```

Duplicate names raise `ValidationError`.

## Recovering handles after a round-trip

`measure()` returns a handle, but the Python local that captured it is gone
after a `.qp` reload. To get a fresh handle list from a loaded program:

```python
program = qp.load("rabi.qp")
handles = program.measurement_handles()       # list, in declaration order
data    = result.get(handles[0])
```

Handles use structural equality, so a fresh `MeasurementHandle("q0_m0")`
constructed anywhere compares equal to the one produced at the original
`measure` call site:

```python
MeasurementHandle("q0_m0") == m0      # True if m0 was assigned that name
```

## The `returns` field

`measure` and vendor measurement ops accept a `returns` argument that tells
the platform what data to produce:

```python
program.measure(q[0].readout, "readout", "weights", returns="iq")           # default
program.measure(q[0].readout, "readout", "weights", returns="iq,raw")
program.measure(q[0].readout, "readout", "weights", returns=["iq", "raw"])
```

Recognised tokens today:

| Token   | Meaning                                                         |
|---------|-----------------------------------------------------------------|
| `"iq"`  | Integrated I and Q values. The historical default.              |
| `"raw"` | Raw ADC trace alongside the integrated values.                  |

Platforms decide which tokens they support; QProgram itself only normalises
input into a canonical `tuple[str, ...]` and serialises it. The token set is
open: future additions like `"state"` (classified outcome) will land without
breaking older files.

## The result object

`platform.execute(program)` returns a `QProgramResult`. Each measurement
inside is an `xarray.DataArray`:

```python
result = platform.execute(program)
data = result.get(m0)

data.dims               # ("freq", "gain", "IQ") for a 2D sweep
data.shape              # (2001, 101, 2)
data.coords["freq"]     # xarray coordinate, values are np.array([4e9, ...])
data.coords["IQ"]       # ["I", "Q"]
```

Three lookup forms work:

```python
result.get(m0)                  # by handle (preferred)
result.get("q0_m0")             # by name string
result.get(0)                   # by integer position
result.get(0, bus="readout_q0") # the same, filtered to one bus
```

`data.sel(IQ="I")` peels off the I trace; `data.sel(freq=5e9, method="nearest")`
slices into the named dimensions. Anything `xarray` does on `DataArray`s
works here.

## Parallel loops produce composite dimensions

When a measurement sits inside a parallel composition, the loops share a
single dimension named after every variable:

```python
with program.for_loop(freq, 4e9, 6e9, 1e6) | program.loop(amp, custom):
    program.measure(q[0].readout, "readout", "weights")

# data.dims == ("freq|amp", "IQ")
# data.coords["freq"].values  -> numpy array
# data.coords["amp"].values   -> numpy array
```

## Multiple measurements

A program can have any number of `measure` calls. They appear in `result`
in declaration order:

```python
m0 = program.measure(q[0].readout, "r", "w")
m1 = program.measure(q[1].readout, "r", "w")
m2 = program.measure(q[0].readout, "r", "w")    # q0_m1

result.get(m0)              # first
result.get(m1)              # second
result.get(m2)              # third
result.get(measurement=0, bus="q0/readout")     # first measurement on q0
result.get(measurement=1, bus="q0/readout")     # third overall, second on q0
```

## When you might call `measurement_handles()` yourself

Three cases:

1. After `qp.load(...)` to get handles back without re-running the program.
2. In test code that wants to assert against named measurements.
3. When you serialise a program, hand it to another process, and need to
   match results to operations later.

In every other case, hold on to the handle returned by `measure`.
