# Reserved keywords

QProgram reserves a set of identifier-shaped names for future syntax. These
names cannot be used as `Variable` ids today, and they cannot be used as
vendor namespace names. Reserving them now keeps existing programs from
breaking the day a new keyword lands.

## The list

The complete set is exposed at runtime as `qprogram.RESERVED_KEYWORDS`. As
of this writing it contains:

| Category                             | Keywords                                                              |
|--------------------------------------|-----------------------------------------------------------------------|
| Conditionals and iteration           | `if`, `else`, `elif`, `while`, `until`, `break`, `continue`, `return` |
| Definitions and reusable fragments   | `fragment`, `def`, `gate`                                              |
| Pattern matching                     | `case`, `match`                                                        |
| Timing and scheduling                | `repeat`, `barrier`, `align`, `align_left`, `align_right`, `parallel` |
| Conditional expression               | `where`                                                                |
| Bindings                             | `let`, `const`                                                         |
| Module system (future)               | `import`, `from`, `as`                                                 |
| Literals                             | `true`, `false`, `null`                                                |

`where` is reserved even though `qprogram.where(cond, a, b)` already exists
as a helper. The bare keyword is reserved for when it grows into syntax.

## Vendor namespaces

Vendor namespaces cannot use any of the keywords above, plus one more:
`"core"`. The complete set of forbidden vendor names is
`qprogram.RESERVED_KEYWORDS | {"core"}`, also exposed as
`qprogram._reserved.RESERVED_VENDOR_NAMES`.

`"core"` is the sentinel for "no vendor". Core operations have
`vendor=None` on the wire; reserving `"core"` keeps a vendor from registering
operations that would look like `core.foo`.

## What is **not** reserved

Reservations apply to identifiers (variable ids) and vendor namespace names.
They do not apply to:

- Operation names (`play`, `measure`, `wait`, ...).
- Block keyword names (`for`, `average`, `block`, ...).
- Sweep generator names (`range`, `file`, ...).

These are the registration sites future syntax will use. If a future minor
version adds an `if` block, it will land via `register_block("if", IfBlock)`
without breaking any existing `.qp` file: no variable can be named `if`, so
the parser will never see ambiguity.

## Case sensitivity

The reserved list is case-sensitive. `if` is reserved; `If` and `IF` are
not. Variable ids and vendor names follow the same convention.

## What happens if you try

`Variable` ids:

```python
program.variable("if")
# qprogram.InvalidVariableIdError: Variable id 'if' is reserved for future
# QProgram syntax (see qprogram.RESERVED_KEYWORDS). Pick a non-reserved id
# such as 'if_var', or carry the original name in the optional `label`
# argument.
```

`InvalidVariableIdError.reserved` distinguishes the two failure modes:

```python
try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    e.id          # "if"
    e.reserved    # True

try:
    program.variable("1freq")
except qp.InvalidVariableIdError as e:
    e.id          # "1freq"
    e.reserved    # False
```

Vendor registration:

```python
register_vendor_operation("if", "foo", FooOp)
# raises a ValueError naming the conflict
```

## Runtime helpers

If you need to check programmatically:

```python
from qprogram import RESERVED_KEYWORDS
from qprogram._reserved import is_reserved_keyword, is_reserved_vendor

is_reserved_keyword("if")        # True
is_reserved_keyword("If")        # False
is_reserved_vendor("core")       # True
is_reserved_vendor("qblox")      # False
```

`RESERVED_KEYWORDS` is a `frozenset[str]`, so it is hashable and stable.

## Why pre-reserve

Two scenarios motivate the list.

1. **Block keywords.** If a user has a `Variable("if")` and a future minor
   version introduces an `if` block, the parser cannot tell `if = 5` from
   the start of an `if` block. Pre-reserving keeps that future open.
2. **Vendor namespace shadowing.** A vendor named `if` would produce
   `.qp` lines like `if.play "drive_q0" pulse` that read as pseudo-keywords.
   Reserving the keyword set as forbidden vendor names keeps the namespace
   surface predictable.

If a keyword you care about is on this list, use a different `id` and carry
the original name in `label`:

```python
program.variable("if_var", label="if")     # works
```
