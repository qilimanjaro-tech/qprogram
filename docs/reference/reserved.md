# Reserved keywords

QProgram refuses a set of identifier-shaped names as variable ids, fragment
names, and vendor namespaces. Fourteen of them are keywords the `.qp` format
already spells; the other fifteen are held back, so that giving one of them a
meaning later cannot change what a program that parses today means. The whole
set is `qp.RESERVED_KEYWORDS`, a `frozenset[str]` of 29 names, and it lives in
`src/qprogram/_reserved.py`.

## Where the reservation applies

Three construction sites check a name against the set, and each reports the
rejection in its own way.

A `qp.Variable` id, whether written as `program.variable("...")`, as a
`fragment.variable(...)` local, as a `fragment.parameter(...)`, or as a `var`
declaration in a `.qp` file, raises `qp.InvalidVariableIdError` with
`reserved=True`. A `qp.Fragment` name raises `qp.ValidationError`, since the
name is not a variable id and carries no `reserved` flag to set. A vendor
namespace name raises `ValueError`, from whichever registration function saw
it.

Operation, block, and sweep-source keywords are not checked. Those names are
the syntax the reservations are held for, so a variable and an operation may
share a name without ambiguity: the parser knows an operation keyword by its
position at the head of a statement.

## The keywords in use

Each of these already means something in a `.qp` file, so a variable named
after one would collide with the grammar rather than with a hypothetical
future version of it.

| Keyword | Where it appears |
|---|---|
| `var` | Variable declaration: `var freq units="Hz"` |
| `for` | Loop header: `for freq in Range(start=4e9, stop=6e9, step=1e6):` |
| `in` | The same header |
| `and` | Logical expression: `(hot and ready)` |
| `or` | Logical expression: `(hot or ready)` |
| `not` | Logical expression: `(not hot)` |
| `if` | Conditional header: `if m0.state == 0:` |
| `elif` | Continuation arm of a conditional chain |
| `else` | Terminal arm: `else:` |
| `fragment` | Fragment definition: `fragment x_pulse(drive, amp):` |
| `true` | Boolean literal |
| `false` | Boolean literal |
| `null` | The literal for Python `None` |
| `where` | Conditional expression: `where((amp > 0.5), amp, 0.0)` |

The first thirteen are hard keyword terminals in the canonical grammar
(`src/qprogram/grammar/qp.lark`), and `tests/test_grammar.py` asserts that
each one is in `RESERVED_KEYWORDS`, so the two cannot drift apart. `where` is
the exception: the grammar lexes `where(` as an ordinary call, and it is the
production parser that gives the name its meaning when it resolves the call.
It is reserved anyway, because `qp.where(cond, a, b)` already exists on the
Python side and the bare keyword is held against the day it grows into syntax
of its own.

## The keywords held back

The remaining fifteen have no meaning in the format today. The categories are
the ones the source file groups them under.

| Category | Keywords |
|---|---|
| Conditional and iteration control flow | `while`, `until`, `break`, `continue`, `return` |
| Definitions and reusable fragments | `def`, `gate` |
| Pattern matching | `case`, `match` |
| Timing and scheduling | `repeat` |
| Bindings | `let`, `const` |
| Imports and aliases | `import`, `from`, `as` |

Two scenarios motivate holding them. A program carrying a `Variable("while")`
would go ambiguous the moment a `while` block entered the grammar: the parser
could not tell a statement about the variable from the head of a loop. That is
the collision `var`, `for`, `in`, and `if` already have with the syntax in
use. And a vendor named `if` would produce `.qp` lines like
`if.play "drive_q0" "pi"`, which read as pseudo-keywords rather than as vendor
operations.

Future syntax would arrive through the same registries a vendor uses today. A
`while` block would land as a single
`qprogram.serialization.registry.register_block` call, and no existing file
could break, because no variable can be named `while` and the parser therefore
never faces the ambiguity.

## What is not reserved

The structural words that head a section are not keywords in the identifier
sense. `metadata`, `schema`, `body`, `require`, `element`, `naming`, and
`info` are soft keywords: the grammar admits each of them in an identifier
position, so `var body` declares a variable named `body`, and both the
production parser and the canonical grammar accept it.

`barrier`, `align`, `align_left`, `align_right`, and `parallel` are not
reserved either, even though neighboring pulse-level DSLs use them as
keywords. `sync` and `wait` cover that ground in QProgram's model, and
parallel loops are spelled with the `|` operator rather than a `parallel`
block, so those five names stay available as variable ids and vendor
namespaces.

Reservation is case-sensitive. `if` is reserved; `If` and `IF` are not, and
neither is a name that merely contains a keyword, such as `if_active` or
`returns_value`.

## Vendor namespaces

A vendor namespace may not be any of the 29 keywords, and may not be `"core"`
either. That set, `RESERVED_KEYWORDS | {"core"}`, is
`qprogram._reserved.RESERVED_VENDOR_NAMES`, and it is what
`qp.register_vendor_operation`, `qp.register_vendor_block`, and
`qp.register_vendor_version` check:

```python
import qprogram as qp

qp.register_vendor_version("if", "1.0")
# ValueError: vendor name 'if' is reserved (see qprogram.RESERVED_KEYWORDS
# plus the 'core' sentinel); pick a different namespace for this vendor
# extension
```

`"core"` is the sentinel for "no vendor". Core operations carry `vendor=None`
on the wire, and reserving the name keeps a vendor from registering operations
that would be written `core.foo`.

`qp.QProgram.register_vendor` applies a second rule on top of that set: it
also rejects a name that collides with a `QProgram` attribute. Vendor dispatch
happens in `__getattr__`, which runs only after normal attribute lookup fails,
so such a namespace would be unreachable on every instance:

```python
import qprogram as qp

qp.QProgram.register_vendor("play", MyNamespace)
# ValueError: vendor name 'play' collides with a QProgram attribute; the
# namespace would be unreachable because normal attribute lookup wins over
# vendor dispatch
```

The test is `hasattr(QProgram, name)` plus the two public instance attributes
assigned in `__init__`, `label` and `description`, which are invisible on the
class but shadow dispatch on every instance. That rules out `play`, `measure`,
`sweep`, `average`, `body`, `schema`, `variables`, `buses`, `source_map`, and
the rest of the public surface, along with every inherited dunder. The
forbidden set is therefore computed rather than enumerable, and it is wider
than the keyword half: `register_vendor_version("play", "1.0")` is accepted on
its own, while `register_vendor("play", ...)` raises. Pick a vendor name that
is neither reserved nor a `QProgram` attribute and the difference never comes
up.

## Checking at runtime

Membership in the exported set is the public test:

```python
import qprogram as qp

"if" in qp.RESERVED_KEYWORDS  # True
"If" in qp.RESERVED_KEYWORDS  # False
sorted(qp.RESERVED_KEYWORDS)[:3]  # ['and', 'as', 'break']
```

The private module `qprogram._reserved` also carries `is_reserved_keyword`
and `is_reserved_vendor`, which wrap the two sets. Neither is re-exported at
the top level, and `is_reserved_vendor` reports only the keyword half of the
vendor rule, so `is_reserved_vendor("play")` is `False` even though
`register_vendor("play", ...)` raises.

## What the errors say

A reserved variable id and a malformed one raise the same class, and
`InvalidVariableIdError.reserved` separates them:

```python
import qprogram as qp

program = qp.QProgram()

try:
    program.variable("if")
except qp.InvalidVariableIdError as e:
    e.id  # 'if'
    e.reserved  # True

try:
    program.variable("1freq")
except qp.InvalidVariableIdError as e:
    e.id  # '1freq'
    e.reserved  # False
```

The reserved message names a way out:

```
Variable id 'if' is reserved for future QProgram syntax (see
qprogram.RESERVED_KEYWORDS). Pick a non-reserved id such as 'if_var', or carry
the original name in the optional `label` argument.
```

Taking that advice keeps the name a reader sees while giving the format an
identifier it accepts, since `label` is free text and is written to the `.qp`
file as a quoted string:

```python
import qprogram as qp

program = qp.QProgram()
program.variable("if_var", label="if")
```

A fragment takes a `label` too, but a `.qp` fragment section is headed by the
name and the parameter list alone, so the label does not survive
serialization and cannot carry the original name into the file. The rejection
is reported directly:

```python
import qprogram as qp

qp.Fragment("while")
# qprogram.ValidationError: fragment name 'while' is a reserved keyword
# (see qprogram.RESERVED_KEYWORDS)
```

## Related pages

[Variables and expressions](../guide/variables.md) covers the rest of the
identifier rules, [Errors](errors.md#invalidvariableiderror) places
`InvalidVariableIdError` in the hierarchy, and
[Vendor extensions](../developer/vendor-extensions.md) walks
through picking a namespace name.
