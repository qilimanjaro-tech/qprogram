# Variables and expressions

Anywhere QProgram accepts a number, it also accepts a `Variable` or an
`Expression`. This is how parameter sweeps work: the variable gets bound to a
different value on each loop iteration, and any expression built on top of it
re-evaluates with the new binding.

## Declaring variables

```python
freq = program.variable("freq", label="Drive Frequency", units="Hz")
dur  = program.variable("dur",  units="ns")
amp  = program.variable("amp")
```

`id` is the only required argument. The other three (`label`, `units`,
`description`) are pure metadata that flows into result coordinates, plot
labels, and `.qp` files.

### Rules for variable ids

The id doubles as the identifier inside `.qp` files (`for freq in range(...)`,
`get_parameter "cluster" "lo" -> lo_freq`). To keep the file format regular,
ids must:

- Match `[A-Za-z_][A-Za-z0-9_]*`. Letters, digits, underscores. Cannot start
  with a digit.
- Be unique within a single `QProgram`.
- Not be a reserved keyword (`if`, `while`, `where`, `repeat`, `true`, ...).
  The full list lives at [`qprogram.RESERVED_KEYWORDS`](../reference/reserved.md).

Anything richer than an identifier belongs in `label` and `description`:

```python
program.variable("phi", label="Phase offset", units="rad",
                 description="NCO phase for the echo arm")
```

Bad ids raise [`InvalidVariableIdError`](../reference/errors.md). The
`reserved` attribute on that error tells you which of the two rules tripped.

```python
try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    print(e.id, e.reserved)     # "if", True
```

## Variables hold a value

Each variable carries a current value. It starts as the singleton
`UNASSIGNED`:

```python
freq = program.variable("freq")
freq.value                  # UNASSIGNED (falsy)
freq.set_value(5e9)
freq.value                  # 5e9
freq.reset()
freq.value                  # UNASSIGNED again
```

The runtime executor sets and resets values per loop iteration, so the
program-side code rarely calls `set_value` directly. The exception is when
you want to evaluate an expression or build a waveform in pure Python for
plotting or debugging.

## Building expressions

The four arithmetic operators, plus unary minus and unary plus, work on any
`Expression`. Python literals get auto-wrapped:

```python
t   = program.variable("t")
amp = program.variable("amp")

100 + t                # BinaryOp("+", Constant(100), t)
t - 50                 # BinaryOp("-", t, Constant(50))
amp * 2                # BinaryOp("*", amp, Constant(2))
(t + 100) / 2          # BinaryOp("/", BinaryOp("+", t, Constant(100)), Constant(2))
-amp                   # UnaryOp("-", amp)
```

Operators compose freely. The whole tree is just data; nothing happens at
construction time.

### Comparisons and logical operators

You can compare and combine expressions:

```python
from qprogram import eq, ne, and_, or_, not_

amp < 0.5
freq >= 5e9
eq(t, 100)               # t == 100  (avoid Python's identity-based ==)
ne(freq, 0)
and_(amp < 0.5, t > 100)
or_(amp < 0.0, amp > 1.0)
not_(amp < 0.5)
```

These return `Comparison`, `LogicalBinaryOp`, and `LogicalNot` nodes. They
behave like the arithmetic ones: they are data, not booleans. Python's `==`
and `!=` operators still do identity-based equality on variables (which is
what AST equality wants), so use the `eq` / `ne` helpers when you mean the
expression-building form.

### Math functions and `where`

```python
from qprogram import sin, cos, tan, exp, log, sqrt, minimum, maximum, where

sin(freq * 2 * 3.14159)
sqrt(amp)
abs(t - 100)
minimum(amp, 0.5)
where(t > 100, amp, 0.0)        # tertiary, evaluates one branch
```

`MathFunc` and `Where` round-trip through `.qp` files alongside the
arithmetic ops.

## Where expressions are accepted

Every operation that takes a number takes an expression:

```python
program.wait(bus, 100 + t)                       # int or expression
program.set_frequency(bus, 5e9 + freq * 1e6)     # float or expression
program.set_gain(bus, amp / 2)
program.set_offset(bus, -amp)
program.play(bus, Gaussian(amplitude=amp, duration=40 + t, sigma=8))
```

Waveform constructors accept expressions on every numeric parameter; see
[Waveforms](waveforms.md).

## Evaluating expressions

`Expression.evaluate()` walks the tree, reads each variable's current value,
and returns a number (or `UNASSIGNED`):

```python
expr = freq * 2 + 100
expr.evaluate()              # UNASSIGNED (freq has no value)

freq.set_value(50)
expr.evaluate()              # 200

freq.reset()
expr.evaluate()              # UNASSIGNED again
```

`UNASSIGNED` propagates: if any variable in the tree is unbound, the whole
expression evaluates to `UNASSIGNED`.

### Forcing a numeric value

If you need a concrete number, call `evaluate_or_raise()`. It returns the
number or raises [`UnassignedVariableError`](../reference/errors.md):

```python
expr.evaluate_or_raise()       # raises UnassignedVariableError if freq is unbound

freq.set_value(50)
expr.evaluate_or_raise()       # 200

# The error carries the offending expression and its free variables.
try:
    (freq + amp).evaluate_or_raise()
except qp.UnassignedVariableError as e:
    print(e.expression)        # the expression
    print(e.free_variables)    # {amp} (freq was bound)
```

Waveforms call `evaluate_or_raise` internally on each parameter, so
`Gaussian(amplitude=amp, ...).envelope()` raises until you bind `amp`.

## Free variables

`expression.variables()` returns the set of free variables in an expression.
The platform's compiler uses this to figure out which variables an operation
actually depends on:

```python
(freq + 100).variables()              # {freq}
(freq + dur * 2 - 50).variables()     # {freq, dur}
Constant(5).variables()               # set()
```

## Equality and identity

The expression nodes use **structural** equality, with one twist. Variables
compare equal by `id`:

```python
v1 = Variable("freq")
v2 = Variable("freq")
v1 == v2                  # True; same id
v1 is v2                  # False
hash(v1) == hash(v2)      # True
```

That structural-by-id rule is what makes a full program survive `deepcopy`,
`qp.loads(qp.dumps(...))`, and `rebind` while still comparing equal
to the original. Within a single program, `QProgram.variable` enforces id
uniqueness, so identity and id-equality coincide in practice.

Constants, `BinaryOp`, `UnaryOp`, `Comparison`, `LogicalBinaryOp`,
`LogicalNot`, `MathFunc`, and `Where` all use plain structural equality:

```python
Constant(5) == Constant(5)
freq * 2 + 100 == freq * 2 + 100   # True
```

## When to use which method

| Goal                                       | Method                          |
|--------------------------------------------|---------------------------------|
| Walk the AST without binding anything       | `expression.variables()`        |
| Evaluate, tolerating unbound variables      | `expression.evaluate()`         |
| Evaluate, fail if anything is unbound       | `expression.evaluate_or_raise()` |
| Get a number from `int \| float \| Expression` argument | `evaluate_or_raise()` (works on plain numbers too via the helpers in `qprogram.variable`) |

Most user code never calls these directly. They are the tools the platform,
the serializer, and the test suite reach for.
