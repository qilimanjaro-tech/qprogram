# Reserved keywords

QProgram reserves a set of identifier-shaped names. Some are keywords the
`.qp` format spells out; the rest are held for syntax still to come. None of
them can be used as a `Variable` id, and none can be used as a vendor
namespace name. Holding the unused ones back keeps existing programs from
breaking the day a new keyword lands.

## The list

The complete set is exposed at runtime as `qprogram.RESERVED_KEYWORDS`. It
holds 29 names:

| Category                             | Keywords                                                              |
|--------------------------------------|-----------------------------------------------------------------------|
| Statement keywords in use            | `var`, `for`, `in`                                                     |
| Expression keywords in use           | `and`, `or`, `not`                                                     |
| Conditionals and iteration           | `if`, `else`, `elif`, `while`, `until`, `break`, `continue`, `return` |
| Definitions and reusable fragments   | `fragment`, `def`, `gate`                                              |
| Pattern matching                     | `case`, `match`                                                        |
| Timing and scheduling                | `repeat`                                                               |
| Conditional expression               | `where`                                                                |
| Bindings                             | `let`, `const`                                                         |
| Module system                        | `import`, `from`, `as`                                                 |
| Literals                             | `true`, `false`, `null`                                                |

Several of these are load-bearing: `var freq`, `for freq in Range(...)`,
`(a and b)`, `if` / `elif` / `else` block headers, and `fragment` sections are
all real `.qp` syntax. The rest are held against syntax the format does not
spell.

`where` is reserved even though `qprogram.where(cond, a, b)` already exists
as a helper. The bare keyword is held against the day it grows into syntax.

`barrier`, `align`, `align_left`, `align_right`, and `parallel` are **not**
reserved, even though neighboring pulse-level DSLs use them as keywords. In
QProgram's model, [`sync`](../guide/operations.md) plus
[`wait`](../guide/operations.md) cover every alignment case, and parallel
loops are spelled with the `|` operator rather than a `parallel` block. So
those five names are free to use as variable ids or vendor namespaces.

## Vendor namespaces

Vendor namespaces cannot use any of the keywords above, plus one more:
`"core"`. That set — `qprogram.RESERVED_KEYWORDS | {"core"}` — is exposed as
`qprogram._reserved.RESERVED_VENDOR_NAMES` and is what
`register_vendor_operation` and `register_vendor_version` check.

`"core"` is the sentinel for "no vendor". Core operations have
`vendor=None` on the wire; reserving `"core"` keeps a vendor from registering
operations that would look like `core.foo`.

`QProgram.register_vendor` applies a second rule on top of that set: it also
rejects a name that collides with a `QProgram` attribute. `program.<vendor>`
dispatch runs only after normal attribute lookup fails, so such a namespace
would be unreachable. That rules out `play`, `measure`, `sweep`, `body`,
`schema`, `variables`, and every other public attribute of the class:

```python
qp.QProgram.register_vendor("play", MyNamespace)
# ValueError: vendor name 'play' collides with a QProgram attribute; the
# namespace would be unreachable because normal attribute lookup wins over
# vendor dispatch
```

The forbidden set is therefore not enumerable from `RESERVED_VENDOR_NAMES`
alone, and `is_reserved_vendor` reports only the keyword half:
`is_reserved_vendor("play")` is `False` while
`QProgram.register_vendor("play", ...)` raises. The attribute rule lives only
in `register_vendor`, so `register_vendor_operation("play", "foo", FooOp)` is
accepted on its own. Pick a vendor name that is neither reserved nor a
`QProgram` attribute and the question does not come up.

## What is **not** reserved

The keyword reservations apply to identifiers (variable ids) and vendor
namespace names. They do not apply to:

- Operation names (`play`, `measure`, `wait`, ...).
- Block keyword names (`average`, `block`, ...).
- Sweep source names (`Range`, `Values`, `File`, ...).

So `program.variable("play")` and `var measure` are legal. As *vendor*
namespace names those two are still refused, by the attribute rule in the
section above rather than by the keyword list.

These are the registration sites future syntax will use. A `while` block
would land via
`qprogram.serialization.registry.register_block("while", WhileBlock)` without
breaking any existing `.qp` file: no variable can be named `while`, so the
parser will never see an ambiguity.

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
    e.id  # "if"
    e.reserved  # True

try:
    program.variable("1freq")
except qp.InvalidVariableIdError as e:
    e.id  # "1freq"
    e.reserved  # False
```

Vendor registration:

```python
from qprogram.serialization.registry import register_vendor_operation

register_vendor_operation("if", "foo", FooOp)
# ValueError: vendor name 'if' is reserved (see qprogram.RESERVED_KEYWORDS
# plus the 'core' sentinel); pick a different namespace for this vendor
# extension
```

## Runtime helpers

If you need to check programmatically:

```python
from qprogram import RESERVED_KEYWORDS
from qprogram._reserved import is_reserved_keyword, is_reserved_vendor

is_reserved_keyword("if")  # True
is_reserved_keyword("If")  # False
is_reserved_vendor("core")  # True
is_reserved_vendor("myvendor")  # False
is_reserved_vendor("play")  # False — but register_vendor("play", ...) raises
```

`RESERVED_KEYWORDS` is a `frozenset[str]`, so it is hashable and stable.

## Why pre-reserve

Two scenarios motivate the list.

1. **Block keywords.** A program holding a `Variable("while")` would go
   ambiguous the moment a `while` block enters the grammar: the parser could
   not tell a statement about the variable from the head of a loop. That is
   exactly the collision `var`, `for`, `in`, and `if` already have with the
   syntax in use. Reserving the rest up front keeps that future open.
2. **Vendor namespace shadowing.** A vendor named `if` would produce
   `.qp` lines like `if.play "drive_q0" pulse` that read as pseudo-keywords.
   Reserving the keyword set as forbidden vendor names keeps the namespace
   surface predictable.

If a keyword you care about is on this list, use a different `id` and carry
the original name in `label`:

```python
program.variable("if_var", label="if")  # works
```
