# Serialization internals

The writer and parser live under `qprogram.serialization`. Both are pure
Python, and both are driven by a small set of registries. This page is a tour
through the moving parts.

## The registries

Module-level dicts in `src/qprogram/serialization/registry.py`:

- `_operation_specs_by_qualified: dict[tuple[str | None, str], OperationSpec]`
  keyed by `(vendor, name)`. Core operations use `vendor=None`.
- `_operation_specs_by_class: dict[type, OperationSpec]`: the reverse
  direction the writer uses to find the wire name from an instance.
- `_block_specs_by_name: dict[str, BlockSpec]` keyed by the **qualified**
  header keyword (`"average"`, `"fake_inst.forever"`), plus
  `_block_specs_by_class` for the reverse.
- `_sweep_source_registry: dict[str, type[SweepSource]]` keyed by class name.
- `_waveform_registry: dict[str, type[Waveform | IQWaveform]]` keyed by class
  name.
- `_vendor_versions: dict[str, str]` keyed by vendor name, holding the
  declared semver string.

They are populated at import time. `qprogram.serialization.__init__` calls
`_register_core_specs()` for the core operations, blocks, and sweep sources;
`registry.py` calls `_register_builtin_waveforms()` for the built-in
waveforms; vendor packages call `register_vendor_operation` /
`register_vendor_block` / `register_vendor_version` on their own import.

## Operation specs

Each registered operation carries an `OperationSpec`:

```python
@dataclass(frozen=True)
class OperationSpec:
    name: str
    vendor: str | None
    cls: type[Operation]
    serialize: OperationSerializeFn | None = None
    parse: OperationParseFn | None = None
```

`spec.qualified_name` is `name` for a core op and `"<vendor>.<name>"` for a
vendor one, the exact keyword that appears on the wire.

`serialize` and `parse` are optional callbacks. When `None`, the writer
falls back to `default_serialize_operation` and the parser to
`default_parse_operation` (both in `_specs.py`). Those defaults reflect on
`cls.__init__` and emit / read positional and keyword arguments
automatically: a parameter with no default emits positionally, a parameter
with a default emits as `key=value` only when the stored value differs from
that default.

Blocks have the parallel `BlockSpec`, whose `serialize_header` /
`parse_header` callbacks own everything between the keyword and the trailing
colon.

A few nodes have custom callbacks because their shape does not fit the
default:

| Node                       | Why the special case                                                    |
|----------------------------|-------------------------------------------------------------------------|
| `Sync`                     | `targets` is one list-valued attribute, emitted as a run of bus tokens.  |
| `GetParameter`             | `-> var` arrow syntax for the output variable.                          |
| `Measure` and vendor measurement ops | The handle travels as a `name="..."` kwarg, and the parser has to resolve it back to the program's canonical handle. |
| `Average` (block header)   | Emits a shot count rather than an argument list.                        |

Sweep sources are not operations and not blocks: `for <var> in <Source>(...)`
is driven by the sweep-source registry, signature-driven in both directions,
with `[...]` as sugar for `Values`. Registering the class is the whole
extension step.

## The writer

The writer's entry point is `dumps(program)`. It builds a `_Writer`, which
walks the AST and emits lines. The same instance doubles as the *write
context* handed to every spec callback. `serialize_value`, `serialize_bus`,
`serialize_waveform`, `serialize_sweep_source`, and `var_ident` are the whole
surface a callback may rely on.

Lifecycle (`_Writer.dump`):

1. **Collect variable identifiers.** `_allocate_var_idents` maps each
   variable to the identifier it will carry in the file, which is its `id`
   verbatim.
2. **Emit the header.** `#!QProgram <major>.<minor>`.
3. **Emit `require` lines.** Walk the body *and* every fragment body,
   collect the vendors referenced by operations and blocks, and emit one
   `require <vendor> <major>.<minor>` per vendor with the version pulled
   from the vendor version registry. A vendor with no registered version is
   a `SerializationError`, since the file could not be compatibility-checked
   on load.
4. **Emit metadata.** If `label` is non-empty or `description` is not
   `None`.
5. **Emit the schema declaration.** If the program has a schema, walk its
   elements and emit the inline form. Preset, dynamic, and subclassed
   schemas all serialize the same way.
6. **Emit fragments.** In dependency order, computed at write time by
   `_topo_fragments`, so every definition precedes its first use.
7. **Emit the body.** `_write_block_contents` on `program.body`, two spaces
   of indentation per nesting level.

`_serialize_operation` dispatches through the registry and raises
`SerializationError` for an unregistered class rather than emitting a
placeholder that would silently drop the operation on reload.

## The parser

The parser's entry point is `loads(text)`. It is a `_Parser` instance that
works line by line.

Lifecycle:

1. **Header.** Read `#!QProgram <version>`, validate the major.
2. **`require` lines.** They must come directly after the header. For each,
   look up the installed version of the vendor and validate compatibility;
   a vendor that is installed but not yet imported is activated through its
   `qprogram.vendors` entry point.
3. **Sections.** `metadata:`, `schema:`, `fragment <name>(...):`, and
   `body:`, each dispatched by its opening line. `schema` appears at most
   once, and a fragment must be defined before it is called.
4. **Body.** Repeatedly read one statement until the dedent. Statements are
   `var` declarations, operations, fragment calls, or control-flow blocks.

An unrecognized top-level line, an unknown operation or block keyword, or a
misplaced `require` each raise `ParseError` with the line number. Silently
skipping any of them would hide a typo behind an empty-but-valid program.

### Recursive descent, no third-party tools

The production parser is a state machine over a line-based tokenizer
(`_tokenize`, `_split_args`, `_parse_arg`, `_to_expression` are the main
helpers), with no external dependency: no `lark`, no `pyparsing`, no YAML
library. External parser tools add weight and tend to outgrow their welcome;
a custom parser for a small grammar stays small.

The format does have a normative machine-readable grammar in
`src/qprogram/grammar/qp.lark`, a Lark dialect using LALR and a two-space
`Indenter`. It is a specification artifact, not the production parser:
`tests/test_grammar.py` runs the writer's output corpus and the hypothesis
strategies through it, and checks that syntactic negatives fail under both,
so the two can never drift. `lark` is a dev-only dependency.

### Bus references

A bus reference is one of two things: a quoted string, or a path. Paths
look like `q[0].drive`, `c[0,1].flux`. Quoting is the type distinction, and
it is tracked through parsing by the `_QuotedStr` marker, so a raw-string
bus that happens to *look* like a path is never promoted. Promotion happens
post-parse, and only for the attributes an operation lists in `BUS_ATTRS`.

The parser resolves each path against the program's schema, producing a
fully-populated `BusRef` complete with `element`, `idx`, `kind`, `channel`,
`acquires`, and a back-pointer to the schema. The validators
(`_validate_waveform_channel`, `_validate_acquires`) therefore work
post-load on these refs. A program that was schema-backed when it was
written stays schema-backed after loading, even though the Python class
identity does not survive.

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
`Variable.id`, and the identifier in the file is that id verbatim. That
works because the constraint is enforced upstream: `Variable` validates its
id against `[A-Za-z_][A-Za-z0-9_]*` and rejects
[reserved keywords](../reference/reserved.md) at construction, and
`QProgram.variable` rejects a duplicate id within one program. So the writer
never sanitizes and never invents a disambiguation suffix, and `.qp` files
stay stable across re-serializations.

## Arrays in sweeps and waveforms

Array-valued arguments are emitted as bracket literals, in full, never
truncated: the literal has to reload to exactly the same values. That covers
`Values.points` (which gets the bare `[...]` sugar in a `for` header) and
`Arbitrary.samples` alike. Only 1-D arrays have a `.qp` form; anything else
raises `SerializationError`.

For a sweep whose values are large or live outside the program, use the file
source instead:

```
for amp in File(path="sweep_values.npy"):
  ...
```

The path, not the data, is what the `.qp` file carries. Both `File.length()`
and `File.values()` call `np.load`, and neither caches (a cached array would
join the source's structural equality, so a loaded instance would stop
comparing equal to a fresh one). So the `.npy` file has to be readable
wherever the program is used, not only where it runs: `length()` is what
`Parallel` calls to check lockstep when two sweeps compose with `|`, and what
the executor calls to size its result arrays. Composing a `File` sweep into a
parallel pair with the file missing raises `FileNotFoundError` at build
time.

## Round-trip guarantee

`tests/test_round_trip.py` asserts `dumps(loads(dumps(p))) == dumps(p)` one
feature surface at a time: metadata, variable declarations, each schema preset,
plain-string buses, the core operations, inline waveforms, expressions, sweeps
and parallel loops, conditionals, measurement handles and `fields=`, vendor
operations, and the `rebind` / `with_waveforms` transforms.

The big integration test
(`tests/test_round_trip.py::test_round_trip_full_features`) stacks many of
them into a single program: a transmon schema, an `average`, a parallel pair of
sweeps and a third sweep nested inside it, math-function and comparison
expressions, an `IQDrag` whose amplitude is a variable, `set_gain`,
`set_frequency`, `set_phase`, `set_offset`, `play`, `sync`, `wait`, and a
`measure` carrying `fields=("iq", "raw")`. It is not exhaustive: it holds no
vendor operation, no conditional, and no fragment; those have their own tests
(`test_round_trip_with_vendor`, `test_round_trip_vendor_op_inside_conditional`,
`test_round_trip_conditional_full_chain`, and
`tests/test_fragments_serialization.py`). On top of that,
`tests/test_round_trip_property.py` generates programs with hypothesis and
asserts both byte stability and structural equality after a round trip.

If you change anything in the writer or parser, run those first:

```bash
uv run pytest tests/test_round_trip.py tests/test_round_trip_property.py -v
```

If round-trip stability breaks, the cause is almost always one of:

- A new operation was added without a registration.
- A waveform or sweep source was added without a registration.
- A non-default keyword argument is being emitted positionally (or vice
  versa).
- A class attribute changed name and the registry still references the old
  one.

The error message will tell you which line of `.qp` text mismatched between
the first and second `dumps`. Diff them and the answer is usually obvious.

## Extension points worth knowing

- **`register_waveform(cls)`** at module-load time, anywhere.
- **`register_sweep_source(cls)`** for a new sweep source; it also registers
  the class's `TOKEN` in the capability registry.
- **`register_vendor_operation(vendor, name, cls)`** for vendor ops, and
  **`register_vendor_block(vendor, name, cls)`** for vendor control-flow
  blocks.
- **`register_vendor_version(vendor, version)`** once per vendor package.
- **`register_operation(name, cls, *, vendor=None, serialize=None,
  parse=None)`** and **`register_block(name, cls, *, vendor=None,
  serialize_header=None, parse_header=None)`** are the underlying calls, used by
  core registration and available when the default reflection cannot express the
  syntax.

Registration is conservative: claiming a key already held by a *different*
class raises, because silently taking over another package's keyword would
change how every existing file parses.
