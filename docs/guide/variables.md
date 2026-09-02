# Variables and expressions

Anywhere QProgram accepts a number, it also accepts a `Variable` or an
`Expression`. This is how a sweep works: a `Sweep` binds one variable to a
`SweepSource`, the runtime writes a value into that variable on each iteration,
and every expression built on top of it re-evaluates against the new binding.

Building an expression is pure data construction. `100 + t` allocates an AST
node and does nothing else; no arithmetic runs until something calls
`evaluate()` on it.

## Declaring variables

`QProgram.variable(id, *, label=None, units=None, description=None)` returns
the `Variable` and records it on the program:

```python
import qprogram as qp

program = qp.QProgram(label="ramsey")
freq = program.variable("freq", label="Drive frequency", units="Hz")
dur = program.variable("dur", units="ns")
amp = program.variable("amp")
```

`id` is positional and required. The other three are keyword-only and default
to `None`. Each call appends to `program.variables`, which reports them in
declaration order, and a repeated id raises `ValidationError` with
`Variable 'freq' is already declared on this QProgram`.

Two other calls declare variables on your behalf. `QProgram.get_parameter`
returns a fresh variable for the value the runtime reads, with an id derived
from `f"{bus}_{parameter}"` (non-word characters replaced by underscores, a
numeric suffix on collision) and the original `bus.parameter` string kept as
the label. `Fragment.variable` declares a fragment-local variable, renamed onto
the host program as `{fragment}_{id}` when the call is expanded; see
[Fragments](fragments.md).

`qp.Variable("freq")` builds the same object directly and works fine as an
expression leaf, but it belongs to no program. The `.qp` writer emits one `var`
line per entry in `program.variables` and resolves every referenced variable
through that table, so serializing a program that reaches an undeclared
variable fails with a `KeyError` on the id rather than a `SerializationError`.
Declare through `program.variable`.

### Identifier rules

The id is written verbatim as the identifier in `.qp` files (`for freq in
Range(start=...)`, `get_parameter "drive_q0" "lo_frequency" -> lo_freq`), so it
has to be safe to embed unquoted. Three rules apply: the id matches
`[A-Za-z_][A-Za-z0-9_]*`, it is unique within one `QProgram`, and it is not one
of the [reserved keywords](../reference/reserved.md).

The pattern and reserved rules both raise
[`InvalidVariableIdError`](../reference/errors.md#invalidvariableiderror),
which also subclasses `ValueError`. Its `reserved` attribute says which rule
tripped, and the message differs accordingly:

```python
try:
    program.variable("2q")
except qp.InvalidVariableIdError as e:
    print(e.id, e.reserved)
    # 2q False
    # "Variable id '2q' is invalid: must match [A-Za-z_][A-Za-z0-9_]* ..."

try:
    program.variable("where")
except qp.InvalidVariableIdError as e:
    print(e.id, e.reserved)
    # where True
    # "Variable id 'where' is reserved for future QProgram syntax ..."
```

The keyword list is reserved against syntax the `.qp` format may grow: a
`Variable("if")` becomes ambiguous the moment the format has an `if` block, so
the id is rejected now to keep files that parse today parsing later.
Reservations are case-sensitive, which makes `If`, `Where`, and `True` valid
ids. Duplicate ids are the third rule and raise `ValidationError`, not
`InvalidVariableIdError`.

Anything richer than an identifier belongs in `label` and `description`:

```python
phi = program.variable(
    "phi",
    label="Phase offset",
    units="rad",
    description="NCO phase for the echo arm",
)
```

### label, units, and description

All three are free-form strings and none of them affects execution. They are
written into the `var` line of a `.qp` file, parsed back from it, and carried
across fragment expansion when a fragment-local variable is renamed onto the
host program. `label` and `units` travel one step further: the executor writes
them onto the swept coordinate of every result array, as the `long_name` and
`units` attributes that xarray's own plotting reads. Validation and the
capability token vocabulary ignore all three, and nothing reads `description`.

That means `units="ns"` records what the numbers mean and converts nothing. A
variable swept over `Range(0, 200, 4)` and passed to `wait` carries
nanoseconds because `wait` takes nanoseconds, not because the variable says so.
A program declaring one annotated variable and one bare one serializes like
this, and the file round-trips back to a program equal to the original:

```
#!QProgram 1.0

metadata:
  label: "ramsey"

body:
  var t label="Delay" units="ns" description="Free-evolution delay"
  var amp

  for t in Range(start=0.0, stop=200.0, step=4.0):
    set_frequency "drive_q0" (5000000000.0 + (t * 1000.0))
    set_gain "drive_q0" minimum((amp / 2), 0.5)
    wait "drive_q0" (100 + t)
    play "drive_q0" Gaussian(amplitude=amp, duration=40, sigma=8)
    measure "readout_q0" "readout" "weights" name="m0"
```

Only the attributes a variable actually carries are emitted, so `amp` declares
as a bare `var amp`. See
[Variable declarations](../reference/qp-format.md#variable-declarations) for
the quoting and escaping rules.

## The current value

Each variable holds one value, and `evaluate()` reads it from the instance.
Before anything binds it, that value is the `UNASSIGNED` sentinel, a falsy
singleton whose `repr` is `UNASSIGNED`:

```python
freq.value  # UNASSIGNED
freq.set_value(5e9)
freq.value  # 5e9
freq.reset()
freq.value  # UNASSIGNED
```

`set_value` stores whatever number you give it with no checking. The executor
calls it twice over: once per loop iteration for the variable a `Sweep` binds,
and once per `get_parameter` for the value read back from the platform. The
sweep path coerces to `float`, so a variable swept over an integer `Range`
reads back as a float; the `get_parameter` path passes the platform's parameter
store value through unchanged, and reads `0.0` for a key the store does not
hold.

Nothing in the package calls `reset()`. A variable therefore keeps the last
value bound to it after a run finishes, which matters whenever you read a
variable back after execution:

```python
delay = program.variable("delay", units="ns")
with program.sweep(delay, qp.Range(0, 20, 10)):
    program.wait("drive_q0", 100 + delay)
    program.measure("readout_q0", "readout", "weights")

delay.value  # UNASSIGNED
result = qp.simulate(program)
delay.value  # 20.0, the last value the sweep bound
```

Calling `set_value` from your own code is worth doing when you want to evaluate
an expression or render a waveform in plain Python for plotting or debugging.

## Expression nodes

Ten concrete node types make up the AST. Every one of them is an `Expression`,
carries the same `evaluate()` and `variables()` methods, and has a `.qp` form
the writer and parser agree on.

| Node | Built by | `.qp` form |
|---|---|---|
| `Constant` | a numeric literal in expression position | `100`, `5e9` |
| `Variable` | `program.variable(...)` | the bare id |
| `MeasurementRef` | `handle.state` inside a comparison | `m0.state` |
| `BinaryOp` | `+`, `-`, `*`, `/` | `(a + b)` |
| `UnaryOp` | unary `-`, unary `+` | `(-a)` |
| `Comparison` | `<`, `<=`, `>`, `>=`, `qp.eq`, `qp.ne` | `(a < b)` |
| `LogicalBinaryOp` | `&`, `\|`, `qp.and_`, `qp.or_` | `(a and b)` |
| `LogicalNot` | `~`, `qp.not_` | `(not a)` |
| `MathFunc` | `qp.sin` and friends, and `abs()` | `sin(a)` |
| `Where` | `qp.where` | `where(c, a, b)` |

`Variable` and `MeasurementRef` are the two bindings the runtime writes to.
`MeasurementRef` points at a field of a measurement result and exists so that a
conditional can branch on a classified state; `"state"` is the only field it
accepts, because that is the only one a branch can test. You normally get one
from the `handle.state` proxy rather than constructing it, and
[Control flow](control-flow.md) covers that side of it.

### Arithmetic

The four binary operators and both unary signs work on any expression, in
either operand order. A numeric literal on the other side is wrapped as a
`Constant`:

```python
t = program.variable("t")

100 + t  # BinaryOp("+", Constant(100), t)
t - 50  # BinaryOp("-", t, Constant(50))
amp * 2  # BinaryOp("*", amp, Constant(2))
(t + 100) / 2  # BinaryOp("/", BinaryOp("+", t, Constant(100)), Constant(2))
-amp  # UnaryOp("-", amp)
```

`bool` is rejected everywhere a number is wrapped, because `True` would coerce
silently to `1` and hide the mistake: `qp.Constant(True)` raises `TypeError`
with `Constant value must be int or float, got bool`. Any other non-numeric
operand raises `Cannot use str in an Expression; expected Expression,
handle.<field>, int, or float`.

`**`, `%`, and `//` are not overloaded and raise the ordinary Python
`unsupported operand type(s)` error, since the `.qp` format has no form for
them. Write `t * t` for a square, and reach for `qp.exp` and `qp.log` for
anything else.

### Comparisons and logical combination

`<`, `<=`, `>`, and `>=` build `Comparison` nodes. `==` and `!=` do not:
`Variable.__eq__` compares ids and has to keep returning a plain `bool`, or
variables could not live in the sets `variables()` returns, or serve as dict
keys. Use `qp.eq` and `qp.ne` for the expression-building form.

`and`, `or`, and `not` are Python keywords and cannot be overloaded at all, so
the NumPy and SymPy convention applies: `&`, `|`, and `~`, or the named helpers
`qp.and_`, `qp.or_`, and `qp.not_`.

```python
amp < 0.5  # Comparison("<", amp, Constant(0.5))
qp.eq(t, 100)  # Comparison("==", t, Constant(100))
qp.ne(amp, 0)  # Comparison("!=", amp, Constant(0))
(amp < 0.5) & (t > 100)  # LogicalBinaryOp("and", ...)
qp.or_(amp < 0.0, amp > 1.0)  # LogicalBinaryOp("or", ...)
~(amp < 0.5)  # LogicalNot(...)
```

The parentheses in `(amp < 0.5) & (t > 100)` are not optional. `&` and `|` bind
tighter than the comparison operators in Python, so `amp < 0.5 & t > 100`
parses as `amp < (0.5 & t) > 100` and fails on the wrong thing.

Logical operands must already be expressions; numbers are not coerced, because
a logical operand is a condition rather than a value. Passing a bare `bool`
gets a message that names the usual cause:

```python
qp.and_(amp < 0.5, True)
# TypeError: LogicalBinaryOp right operand must be an Expression; got bool
# — if you wrote `var == literal` or `var != literal`, use
# qprogram.eq(var, literal) / qprogram.ne(...) instead, since Variable's
# `==` returns a plain bool, not a Comparison.
```

`LogicalBinaryOp` never short-circuits. Both operands are always evaluated, so
an unbound variable in either half propagates `UNASSIGNED` regardless of which
side it sits on, and an unbound-variable diagnostic does not depend on operand
order.

Comparisons and logical nodes are data, not booleans, and `Expression` blocks
the accident that would otherwise follow. `__bool__` raises rather than
reporting that a `Comparison` instance is truthy:

```python
if amp < 0.5:  # TypeError
    ...
# TypeError: Expression has no truth value — use
# .evaluate()/.evaluate_or_raise() to compute it, or
# qprogram.where(cond, then, else_) to build a conditional expression.
```

The same guard is why `min(a, b)` on two variables raises, and why `qp.minimum`
exists.

### Math functions and `where`

Nine math functions build `MathFunc` nodes. `qp.sin`, `qp.cos`, `qp.tan`,
`qp.exp`, `qp.log`, and `qp.sqrt` take one operand; `qp.minimum` and
`qp.maximum` take two or more and raise `TypeError` with
`minimum() requires at least two arguments` below that; and the built-in
`abs()` produces `MathFunc("abs", ...)` through `__abs__`. The `.qp` name of
each node is its `MathFunc.name`, which is the helper's own name.

```python
qp.sin(freq * 2 * 3.14159)
qp.sqrt(amp)
qp.log(amp)  # natural log
abs(t - 100)
qp.minimum(amp, 0.5)
qp.maximum(amp, 0.0, 1.0)
```

Evaluation runs through NumPy, imported lazily so that building an expression
does not pay for it. The transcendental functions return Python floats, while
`abs`, `minimum`, and `maximum` preserve an integer input. They also inherit
NumPy's out-of-domain behavior instead of raising: `qp.log(0).evaluate()`
returns `-inf` with a `RuntimeWarning`, and `qp.sqrt(-1).evaluate()` returns
`nan`.

`qp.where(condition, then, else_)` is the ternary. The condition has to be an
expression, while the two branches accept numbers and get wrapped. Only the
chosen branch is evaluated, so the branch not taken may reference a variable
that happens to be unbound:

```python
qp.where(t > 100, amp, 0.0)

cond = program.variable("cond")
used = program.variable("used")
unused = program.variable("unused")
cond.set_value(1)
used.set_value(42)
qp.where(qp.eq(cond, 1), used, unused).evaluate()  # 42
```

An unbound condition still makes the whole node `UNASSIGNED`, and
`variables()` reports all three subtrees including the branch evaluation skips.

## Where expressions are accepted

Every operation that takes a number takes an expression in the same position:

```python
program.wait("drive_q0", 100 + t)  # int | Expression
program.set_frequency("drive_q0", 5e9 + freq * 1e6)  # float | Expression
program.set_gain("drive_q0", amp / 2)
program.set_offset("drive_q0", -amp)
program.set_parameter("drive_q0", "lo_frequency", freq + 1e6)
program.play(
    "drive_q0",
    qp.waveforms.Gaussian(amplitude=amp, duration=40 + t, sigma=8),
)
```

Waveform constructors accept an expression on every numeric parameter, and
`Waveform.envelope()` resolves it when it renders the samples; see
[Waveforms](waveforms.md). The `.qp` format is narrower than the AST here. A
constructor argument may be a number, a quoted string, or a bare variable
reference, and the parser rejects anything else, so `Gaussian(amplitude=amp)`
round-trips but `Gaussian(amplitude=amp / 2)` writes without complaint and then
fails to parse back. [Errors](../reference/errors.md) has the detail.

## Evaluating an expression

`evaluate()` walks the tree, reads each variable's current value, and returns a
number:

```python
expr = freq * 2 + 100
expr.evaluate()  # UNASSIGNED (freq has no value)

freq.set_value(50)
expr.evaluate()  # 200

freq.reset()
expr.evaluate()  # UNASSIGNED
```

`UNASSIGNED` propagates: any unbound variable anywhere in the tree makes the
whole expression `UNASSIGNED`, and it takes precedence over an arithmetic
failure that a bound value would have hit. `(t / 0).evaluate()` raises
`ZeroDivisionError` once `t` is bound and returns `UNASSIGNED` while it is not.

Comparisons and logical nodes evaluate to a `bool`, which is an `int` subclass,
so a comparison used as a numeric operand behaves as `0` or `1`.

### evaluate_or_raise

`evaluate_or_raise()` returns the number or raises
[`UnassignedVariableError`](../reference/errors.md#unassignedvariableerror)
instead of handing back the sentinel. Use it where the caller has no way to
carry on without a value:

```python
expr.evaluate_or_raise()  # raises while freq is unbound

freq.set_value(50)
expr.evaluate_or_raise()  # 200

try:
    (freq + amp).evaluate_or_raise()
except qp.UnassignedVariableError as e:
    print(e.expression)  # (Variable('freq') + Variable('amp'))
    print(e.free_variables)  # {Variable('amp'), Variable('freq')}
```

`free_variables` is `expression.variables()`, collected when the error is
constructed. It is every variable the tree references, not only the unbound
ones, so a partly bound expression reports all of them.

Waveforms call `evaluate_or_raise` on each parameter internally, which is why
`qp.waveforms.Gaussian(amplitude=amp, duration=40, sigma=8).envelope()` raises
until `amp` is bound.

## Free variables

`expression.variables()` returns the set of variables the tree references.
Every node unions its children's sets, so one call covers the whole subtree:

```python
(freq + 100).variables()  # {freq}
(freq + dur * 2 - 50).variables()  # {freq, dur}
qp.Constant(5).variables()  # set()
```

`Operation.variables()` and `Block.variables()` are built on this: they walk
public attributes, descend into expressions, waveform parameters, and nested
lists, and union what they find. That is how the compiler works out which
variables an operation depends on. `Sweep.variables()` adds the variable it
binds to whatever its body reports, and `Parallel.variables()` unions in each
composed loop's own, because those loop headers sit outside the shared body the
inherited walk covers.

`MeasurementRef.variables()` returns an empty set. A measurement reference is a
different kind of binding, written by the runtime when the measurement produces
a result rather than by a loop, so it takes no part in the variable walk.

## Capability tokens

An operation reports the capabilities it needs from `required_capabilities()`,
and an operation that carries an expression adds one `expr.*` token per node
kind in it. `qp.protocol.expression_tokens` does the recursion:

```python
op = qp.operations.Wait(bus="drive_q0", duration=100 + t * 2)
sorted(op.required_capabilities())
# ['expr.binary_op', 'expr.constant', 'expr.variable', 'op.wait']
```

The node kinds map to `expr.constant`, `expr.variable`,
`expr.measurement_ref`, `expr.binary_op`, `expr.unary_op`, `expr.comparison`,
`expr.logical_and_or`, `expr.logical_not`, `expr.where`, and one
`expr.math.<name>` per math function. A plain numeric literal contributes
nothing, because it is not a node until it is wrapped.

The operations that contribute expression tokens are `wait`, `set_frequency`,
`set_phase`, `set_gain`, `set_offset`, and `set_parameter`, each on its numeric
arguments, plus `Conditional` on each arm's condition. `Play` does not. Its
tokens describe the waveform (`waveform.single`, `waveform.gaussian`, and so
on), so an expression inside a waveform parameter contributes no `expr.*` token
even though `Play.variables()` still finds the variable in it.

`expr.*` tokens are checked against the platform's own capability set rather
than the slot the operation routes to, since they describe which node kinds the
platform's compiler can lower, not what any one instrument can do. A missing
token becomes a `missing-capability` diagnostic naming the token and the
profile that lacks it. [Capabilities](capabilities.md) covers the routing.

## Equality and identity

Expression nodes compare structurally, with one twist: variables compare by
`id`.

```python
v1 = qp.Variable("freq")
v2 = qp.Variable("freq")
v1 == v2  # True; same id
v1 is v2  # False
hash(v1) == hash(v2)  # True
```

`hash` is over `("Variable", id)`, so two variables with the same id collapse
to one entry in the set `variables()` returns. That is what makes a whole
program survive `deepcopy` and a `qp.loads(qp.dumps(...))` round-trip and still
compare equal: after a round-trip the original Python objects are gone, and the
ids are all that is left to match on. `QProgram` itself defines no `__eq__`, so
the comparison to make is `reloaded.body == program.body`.

Everything else is plain structural equality over the fields the node holds.
`MeasurementRef` compares by `(handle.name, field)` for the same
survive-the-round-trip reason.

```python
qp.Constant(5) == qp.Constant(5)  # True
freq * 2 + 100 == freq * 2 + 100  # True
qp.sin(freq) == qp.cos(freq)  # False
```

## When to use which method

| Goal | Method |
|---|---|
| Collect the variables in a tree without binding anything | `expression.variables()` |
| Evaluate, tolerating unbound variables | `expression.evaluate()` |
| Evaluate, fail if anything is unbound | `expression.evaluate_or_raise()` |
| Get a number from an `int \| float \| Expression` argument | `x.evaluate_or_raise() if isinstance(x, qp.Expression) else x` |

Most user code calls none of these. They are what the platform, the serializer,
and the test suite reach for; the code that builds a program only builds nodes.
