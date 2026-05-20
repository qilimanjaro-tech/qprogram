# Serialization internals

The writer and parser live under `qprogram.serialization`. Both are pure
Python, both are about a thousand lines each, both are driven by a small
registry. This page is a tour through the moving parts.

## Three registries

Module-level dicts in `qprogram/src/qprogram/serialization/registry.py`:

- `_waveform_registry: dict[str, type[Waveform | IQWaveform]]` keyed by
  class name.
- `_operation_registry: dict[tuple[str | None, str], OperationSpec]` keyed
  by `(vendor, name)`. Core operations use `vendor=None`.
- `_vendor_version_registry: dict[str, tuple[int, int, int]]` keyed by
  vendor name.

Plus a reverse map `_operation_reverse: dict[type, tuple[str | None, str]]`
that the writer uses to look up the wire name from an operation instance.

All four are populated at import time. `qprogram` calls
`_register_builtins()` for core operations and built-in waveforms;
vendor packages call `register_vendor_operation` on their own import.

## Operation specs

Each registered operation carries an `OperationSpec`:

```python
@dataclass(frozen=True, slots=True)
class OperationSpec:
    cls: type[Operation]
    serialize: SerializeOp | None = None
    parse: ParseOp | None = None
```

`serialize` and `parse` are optional callbacks. When `None`, the writer
falls back to `default_serialize_operation` and the parser to
`default_parse_operation` (both in `_specs.py`). Those defaults reflect on
`cls.__init__` and emit / read positional and keyword arguments
automatically.

A few operations have custom callbacks because their shape does not fit the
default:

| Operation         | Why the special case                                                    |
|-------------------|-------------------------------------------------------------------------|
| `Sync`            | Variadic list of targets, no positional args.                            |
| `GetParameter`    | `-> var` arrow syntax for the output variable.                          |
| `SetCrosstalk`    | Carries a non-trivial `CrosstalkMatrix`, written as `crosstalk` ident.   |
| `Average` header  | Needs to emit a number rather than args.                                |
| `range(...)`      | Sweep generator with its own syntax.                                    |
| `[...]` / `file(...)` | Sweep generator alternatives.                                       |

Each callback signature is small. See `_specs.py` for the full list.

## The writer

The writer's entry point is `dumps(program)`. It is a class instance,
`_Writer`, that walks the AST and emits lines.

Lifecycle:

1. **Allocate variable identifiers.** `_allocate_var_idents` walks the
   variable list and assigns each one a final identifier. Identifiers come
   from the variable's `id`, sanitised against the reserved keyword set,
   with `_2`, `_3`, ... appended on collision. In-API programs (constructed
   through `QProgram.variable`) already have unique ids, so collisions only
   happen for AST that was assembled directly.
2. **Emit the header.** `#!QProgram <major>.<minor>`, blank line.
3. **Emit `require` lines.** Walk the AST, collect vendors used by
   operations, emit one `require <vendor> <major>.<minor>` per vendor with
   the version pulled from the vendor version registry.
4. **Emit metadata.** If `label` or `description` is set.
5. **Emit the schema declaration.** If the program has a schema, walk its
   `_elements` and emit the inline form. Preset, dynamic, and subclassed
   schemas all serialise the same way.
6. **Emit the body.** `_emit_block` on `program.body`, with two-space
   indentation per nesting level.

`_emit_operation` dispatches through the registry. The default serializer
reflects on `__init__`:

- Each positional-or-keyword parameter with `default is empty` becomes a
  positional arg.
- Each parameter with a default becomes `key=value` if the current attribute
  differs from the default.
- Internal attributes (anything starting with `_`) are skipped.
- Nested `Expression` / `Waveform` values are recursively rendered.

## The parser

The parser's entry point is `loads(text)`. It is a `_Parser` instance that
tokenises line by line.

Lifecycle:

1. **Header.** Read `#!QProgram <version>`, validate major.
2. **`require` lines.** For each, look up the installed version of the
   vendor and validate compatibility. Fail early with a clear message.
3. **Sections.** In any order: `metadata`, `schema`, `body`. `schema`
   appears at most once.
4. **Body.** Repeatedly read one statement until the dedent. Statements are
   `var` declarations, operations, or control-flow blocks.

### Recursive descent, no third-party tools

There is no `lark`, `pyparsing`, `pyyaml`, or `ruamel.yaml`. The parser is a
state machine over a line-based tokenizer (`_tokenize`, `_split_args`,
`_parse_arg`, `_to_expression` are the main helpers). The decision was
deliberate: external parser tools add weight and tend to outgrow their
welcome; a custom parser for a small grammar stays small.

### Bus references

A bus reference is one of two things: a quoted string, or a path. Paths
look like `q[0].drive`, `c[0,1].flux`. The parser resolves each path against
the program's schema, producing a fully-populated `BusRef` complete with
`element`, `index`, `kind`, `channel`, and `acquires`.

Validation hooks (`_validate_waveform_channel`, `_validate_acquires`)
therefore work post-load on these refs. A program that was schema-backed
when it was written stays schema-backed after loading, even though the
Python class identity does not survive.

### Lazy import

Both `dumps`/`save` and `loads`/`load` are exposed via module-level
`__getattr__` on `qprogram/__init__.py` and `qprogram/serialization/__init__.py`.
The parser imports `QProgram`, which would create a circular import if
loaded eagerly. The lazy entry points break the cycle; users see no
difference.

If you ever need to refactor this, the rule is: anything that imports
`qprogram.QProgram` must be lazy-imported from `qprogram/__init__.py`.

## Variable identifier allocation

A subtle but important detail. The serializer key for variables is
`Variable.id`. Two variables with the same label still get distinct
identifiers because `QProgram.variable` enforces id uniqueness on the
Python side. The writer's `_allocate_var_idents` table is keyed by
`Variable.id` (a string) and emits each one verbatim.

Older versions of the writer used `Variable._id` (the auto-incremented
integer) as the key, but `id` was already unique by construction, so the
hop through `_id` was redundant. Keep the table keyed by `id` going forward
to keep `.qp` files stable across re-serializations.

## Numpy arrays in `Loop`

`Loop.values` is stored as a numpy array. The writer emits short arrays as
literal lists (`[0.0, 0.5, 1.0]`) and long ones with an ellipsis and a
length suffix (`[0.0, 0.5, ..., 1.0] (100 values)`). The parser only knows
about the short form by default. For long sweeps, use the file form:

```
for amp in file("sweep_values.npy"):
  ...
```

The parser calls `np.load` to materialise the array.

## Round-trip guarantee

The test suite covers `dumps(loads(text)) == text` on every public feature.
The big integration test
(`qprogram/tests/test_round_trip.py::test_round_trip_full_features`)
combines schemas, parallel loops, nested averages, inline waveforms with
variables, every operation, vendor operations, and the `returns` field into
a single program and asserts byte stability.

If you change anything in the writer or parser, run the test suite first:

```bash
cd qprogram
uv run pytest tests/test_round_trip.py -v
```

If round-trip stability breaks, the cause is almost always one of:

- A new operation was added without a registration.
- A waveform was added without a registration.
- A non-default keyword argument is being emitted positionally (or vice
  versa).
- A class attribute changed name and the registry still references the old
  one.

The error message will tell you which line of `.qp` text mismatched between
the first and second `dumps`. Diff them and the answer is usually obvious.

## Extension points worth knowing

- **`register_waveform(cls)`** at module-load time, anywhere.
- **`register_vendor_operation(vendor, name, cls)`** for vendor ops.
- **`register_vendor_version(vendor, version)`** once per vendor package.
- **`register_core_operation(name, cls, *, serialize=None, parse=None)`**
  for core operations needing custom callbacks. New core operations are
  rare; reach for this only when the default reflection cannot express the
  syntax.

The whole writer/parser story is small enough to fit in a single PR.
Anything more elaborate (formal grammar, code generation, schemaful
binary form) is intentionally out of scope.
