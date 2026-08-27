# Serialization internals

The writer and the parser live under `qprogram.serialization`. Both are pure
Python and both dispatch through the same registries rather than through
`isinstance` ladders over keywords, so adding an operation, a block, a
waveform, or a sweep source is a registration rather than an edit to either
side. The parser's only imports from outside the package are `re`, `pathlib`,
and `typing`; the writer adds `io` for its output buffer and numpy for array
values.

Snippets below that carry a `# src/qprogram/...` path comment are package
source and keep their intra-package imports, since `import qprogram` from
inside the package would close an import cycle. Anything written against the
installed package uses `import qprogram as qp`.

The format version is one constant, shared by both directions:

```python
# src/qprogram/serialization/_format.py
FORMAT_VERSION: Final[str] = "1.0"
```

It is emitted in the `#!QProgram` header and checked on load. Only the major
component is binding: a file whose major differs is rejected with
`Unsupported format version`, and any minor within the same major loads, so a
`1.4` file opens under a `1.0` runtime.

## The registries

Seven module-level dicts in `src/qprogram/serialization/registry.py` hold
everything the two directions dispatch on. Each pair of operation and block
tables exists because the two directions look up from opposite ends: the parser
has a keyword and wants a class, the writer has an instance and wants a
keyword.

| Table | Key | Read by |
|---|---|---|
| `_operation_specs_by_qualified` | `(vendor, name)`, with `vendor=None` for core operations | the parser, resolving an operation keyword |
| `_operation_specs_by_class` | the operation class | the writer, finding the wire keyword for an instance |
| `_block_specs_by_name` | the **qualified** header keyword (`"average"`, `"fake_inst.forever"`) | the parser, resolving a block header |
| `_block_specs_by_class` | the block class | the writer |
| `_sweep_source_registry` | class name (`"Range"`, `"Values"`) | both, for `for <var> in <Source>(...)` |
| `_waveform_registry` | class name (`"Gaussian"`) | both, for inline waveform constructors |
| `_vendor_versions` | vendor name, holding the declared semver string | the writer's `require` lines and the parser's compatibility check |

Class lookups are exact rather than by inheritance, so a subclass of a
registered operation needs a registration of its own before it can be written.

Population happens at import time. `qprogram.serialization.__init__` calls
`_register_core_specs()` for the core operations, blocks, and sweep sources;
`registry.py` calls `_register_builtin_waveforms()` at the bottom of the module
for the seventeen built-in waveform classes; a vendor package calls
`register_vendor_operation`, `register_vendor_block`, and
`register_vendor_version` as import side effects, either because the user
imported it or because a `require` line activated it through the
`qprogram.vendors` entry-point group.

Registration is conservative. Re-registering the *same* class under a key it
already holds is allowed, which lets an owner refresh its callbacks and lets a
side-effect module run twice. Claiming a key held by a *different* class raises
`ValueError` (`operation 'play' is already registered to ...; refusing to
replace it with ...`), because silently taking over another package's keyword
would change how every existing file using it parses. A vendor name in
`RESERVED_VENDOR_NAMES`, which is the [reserved
keywords](../reference/reserved.md) plus the `core` sentinel, is refused
outright.

## What a spec declares

Every registered operation carries an `OperationSpec`, and every registered
keyword-led block a `BlockSpec`:

```python
# src/qprogram/serialization/registry.py
@dataclass(frozen=True)
class OperationSpec:
    name: str
    vendor: str | None
    cls: type[Operation]
    serialize: OperationSerializeFn | None = None
    parse: OperationParseFn | None = None


@dataclass(frozen=True)
class BlockSpec:
    name: str
    cls: type[Block]
    vendor: str | None = None
    serialize_header: BlockSerializeHeaderFn | None = None
    parse_header: BlockParseHeaderFn | None = None
```

So a spec declares four things: the keyword, the namespace it belongs to, the
class to construct, and optionally the pair of callbacks that own the text
between the keyword and the end of the statement. `spec.qualified_name` is
`name` for a core entry and `"<vendor>.<name>"` for a vendor one, which is the
exact token that appears on the wire and, for blocks, also the registry key.
Recording `vendor` on `BlockSpec` is what lets the writer emit a `require` line
for a file whose only vendor content is a block.

A `BlockSpec`'s callbacks own the header only, everything between the keyword
and the trailing colon; indentation and child statements are handled uniformly
by the body writer and the statement parser, so a block never has to think
about its own contents.

When both callbacks are `None` the writer falls back to
`default_serialize_operation` and the parser to `default_parse_operation`, both
in `_specs.py`. They reflect on `cls.__init__`, minus `self`. On the write
side, a parameter with no default emits positionally in declaration order; a
parameter with a default emits as `key=value` only when the stored value
differs from that default; a parameter with no matching attribute on the
instance is skipped, so `__init__` may accept a kwarg its body does not store.
On the parse side, a token counts as a keyword argument when it contains `=`,
does not open with a quote, and has no `(` before the first `=`; the remaining
tokens bind positionally by index, and the whole thing is then constructed with
keywords only, so positional ordering cannot drift between the two sides. More
positional tokens than the constructor has parameters is a `ParseError` that
names the excess and suggests the likely cause: ``If you meant an arithmetic
expression, parenthesize it: `(100 - t)`.``

Constructor failures are converted rather than allowed to escape, because the
line number is what `source_map` and the editor tooling depend on. A
`TypeError` becomes `cannot construct 'Play' from the given arguments: ...`,
and a `ValidationError` is passed through verbatim under the line tag, since
its message is already specific about the argument it rejected.

A handful of nodes need explicit callbacks because their wire form is not an
argument list:

| Node | Wire form | Callbacks |
|---|---|---|
| `Sync` | `sync`, or `sync <bus> <bus> ...` | `sync_serialize` / `sync_parse` |
| `GetParameter` | `get_parameter <bus> "param" -> <var>` | `get_parameter_serialize` / `get_parameter_parse` |
| `Measure`, and vendor measurement ops | `measure <bus> <wf> <weights> name="..."` | `measurement_op_serialize` / `make_measurement_op_parse(cls)` |
| `Average` | `average <shots>` | `average_serialize_header` / `average_parse_header` |

`Sync` needs one because `targets` is a single list-valued parameter emitted as
a run of bare bus tokens, and because the bare keyword carries meaning of its
own (synchronize every bus in the program, stored as `targets=None`). It renders
each target through `ctx.serialize_value` rather than `ctx.serialize_bus`, which
is what every other operation's bus argument goes through: `serialize_bus` knows
`BusRef` and quotes everything else, so a fragment `Parameter` would go out as a
quoted `"Parameter('drive')"` and stop substituting at expansion.
`serialize_value` delegates a `BusRef` straight back to `serialize_bus`, so the
two agree everywhere else.
`GetParameter` places its output variable after a `->` arrow, which it reaches
through `ctx.var_ident`. A measurement operation skips its `handle` parameter
and re-emits it as `name="..."`, and the parse side resolves that name through
`ctx.get_or_create_handle` so every measurement operation and every
`MeasurementRef` naming it share one Python instance after a load. It accepts
three spellings: the canonical `name=` kwarg, a quoted token in the `handle`
positional slot, or nothing at all, in which case the parser allocates a name
with the same convention the builder uses. The retired `returns=` kwarg gets
its own diagnostic rather than a generic unexpected-keyword error, because both
the keyword and the value shape changed: ``write `fields=["state", "iq"]`
instead of `returns="state,iq"`.``

`Parallel` and `Conditional` are not in the block registry at all. Neither is
keyword-led: `a | b | c:` composes other blocks' headers, and a conditional has
one header per arm rather than one for the node, so the writer special-cases
both. `Call` is an `Operation` subclass but has its own `name(args)` statement
form, so the writer tests for it before the generic operation branch.

## Sweep sources and waveforms

Sweep sources are neither operations nor blocks. A `for <var> in <Source>(...)`
header is driven by the sweep-source registry, keyed by class name and
signature-driven in both directions exactly as a waveform constructor is, so
registering the class is the whole extension step. `register_sweep_source` also
adds the class's `TOKEN` to the capability registry, which is what lets a
`Profile` list `sweep.file` without a separate `register_capability_tokens`
call.

`Values` is the one source with sugar: it writes as a bare bracket literal
(`for t in [10, 20, 40]:`) rather than as a constructor call, and the parser
treats a leading `[` in the source position as a `Values` constructor.
Combinators nest to any depth, because a nested constructor comes back through
the same argument parser: `Concat(sources=[Rotate(source=[...], by=1)])` parses
as readily as a bare `Range`.

## The writer

`dumps(program)` builds a `_Writer`, which walks the AST and emits lines. The
same instance doubles as the *write context* handed to every spec callback:
`serialize_value`, `serialize_bus`, `serialize_waveform`,
`serialize_sweep_source`, and `var_ident` are the whole surface a callback may
rely on. `save(program, path)` is `dumps` plus a UTF-8 write, independent of
the platform's locale.

Handing `dumps` a `Fragment` is an error rather than a partial file: fragments
are emitted as `fragment ...:` sections of the host program that calls them, so
the message tells you to serialize that program instead.

`_Writer.dump` runs the sections in file order:

1. **Collect variable identifiers.** `_allocate_var_idents` maps each variable
   to the identifier it will carry in the file, which is its `id` verbatim.
2. **Emit the header,** `#!QProgram <major>.<minor>`.
3. **Emit `require` lines.** Walk the body *and* every fragment body, collect
   the vendors referenced by operations and by blocks, and emit one
   `require <vendor> <major>.<minor>` per vendor, sorted by vendor name, with
   the patch component truncated because compatibility is defined at
   major.minor. A vendor with no registered version raises
   `SerializationError`, since the resulting file could not be
   compatibility-checked on load.
4. **Emit metadata,** if `label` is non-empty or `description` is not `None`.
5. **Emit the schema declaration,** if the program has one.
6. **Emit fragments,** in dependency order.
7. **Emit the body:** variable declarations, then the block tree.

The vendor walk uses `Block.walk` rather than recursing over `.elements`,
because `Conditional` keeps its arm bodies on `.arms` and `.else_body`; an
elements-only recursion would miss a vendor operation inside an `if_` arm and
emit a file with no `require` line for it.

Ordering inside those sections is computed at write time, not taken from
whatever order the program was built in. Fragments come from `_topo_fragments`,
a depth-first walk over nested `Call` nodes, so every definition precedes its
first use, which is the define-before-use rule the parser enforces. A call
cycle and two different fragments reachable under one name are both
`SerializationError`. Variables are emitted in `program.variables` order,
followed by one blank line. Metadata omits `label` when it is empty, because
the parser's default matches, but emits `description` whenever it is not
`None`, because an explicit empty string is a distinct value that has to
survive.

The schema is always written in the expanded inline form, one `element` header
per element with its bus kinds beneath, even for a preset such as
`qp.BusSchema.transmon()`. The preset classes are construction-time
conveniences on the Python side; the file records the structural contents
directly, so adding a bus to a preset can never silently change the meaning of
an existing `.qp` file. Custom subclasses and dynamic schemas take the same
path.

Indentation is a function of depth alone. `_write_body` starts the block walk
at column 2 and each nested block adds two more columns. A `Parallel` costs one
level for the whole composition rather than one per composed loop, since the
composed `for` headers are joined with ` | ` on a single line. Conditional arm
headers sit at the parent's indent and each arm body two columns further.

Put together, a program with metadata, a schema, a fragment, an `average`, and
a sweep writes as:

```
#!QProgram 1.0

metadata:
  label: "ordering demo"

schema:
  element q:
    drive info=IQ
    readout info=IQ+acquires

fragment pi_pulse(bus):
  play bus Gaussian(amplitude=0.5, duration=40, sigma=8)

body:
  var gain units="V"

  average 1000:
    for gain in Range(start=0.0, stop=0.5, step=0.1):
      set_gain q[0].drive gain
      pi_pulse(q[0].drive)
      measure q[0].readout "ro" "w" name="q0/readout/m0"
```

Every section is preceded by a blank line, the fragment call is emitted with
its arguments positional in the fragment's parameter order (the keyword
spelling a caller used at build time is not part of the wire form), and the
measurement's auto-allocated handle name is emitted so the reload resolves the
same handle.

`_serialize_operation` and `_serialize_block_header` both raise
`SerializationError` for an unregistered class rather than emitting a
placeholder, because a placeholder would silently drop the node, or the whole
subtree beneath it, on reload. `serialize_value` raises for any value type the
format has no representation for, for an array of rank other than 1, for a
dict with non-string keys, and for a `MeasurementRef` whose handle name carries
a character that the unquoted `<name>.<field>` wire form cannot hold
(whitespace, a quote, `#`, a comma, a dot, or a bracket, brace, or
parenthesis).

## The parser

`loads(text, *, auto_activate=True)` builds a `_Parser`, which is single-use
and works line by line. That instance is the *parse context* the spec callbacks
reach through: `parse_value`, `parse_error`, `get_or_declare_variable`,
`declared_variable`, `get_or_create_handle`, `allocate_measurement_handle`, and
the `line_num` property a callback reads when it builds an error of its own.
`load(path)` reads UTF-8 and calls `loads`.

The header and its `require` lines come first, then a dispatch loop over
top-level lines routes `metadata:`, `schema:`, `fragment <name>(...):`, and
`body:` to their own parsers. The loop does not fix an order, so sections may
appear in any sequence, with three constraints it does enforce: a `require`
line reached by the loop rather than by the header pass is an error
(``` `require` declarations must appear directly after the header, before any
section ```), a second `schema:` is a duplicate-declaration error, and a
`fragment` section after `body:` is rejected. Emitting the schema before the
fragments, as the writer does, is what lets bus paths inside a fragment body
resolve; a hand-written file that declares the schema later keeps those paths
as raw strings. An unrecognized top-level line raises rather than being
skipped, since skipping it would hide a typo such as `bodyy:` behind an
empty-but-valid program.

`require` handling is where a file's vendor dependencies are checked. Majors
must match exactly and the file's minor must be no newer than the installed
extension's; the patch component is informational. When the named vendor has no
registered version and `auto_activate` is on, the parser looks up the
`qprogram.vendors` entry point whose *name* is the vendor and imports its
target module, whose registration side effects supply the namespace, the
version, and the operations. That is what makes a `.qp` file self-contained:
any environment with the extension installed can load it, imported or not. An
entry point that imports but registers no version is a packaging bug in the
extension and raises `VendorActivationError`, which the parser wraps into a
`ParseError` carrying the line number. Passing `auto_activate=False` forbids
the implicit import, and the resulting message says so
(`auto-activation is disabled; import the extension before loading`).

### Resolving a keyword to a registry entry

`_parse_statements` reads one statement at a time until a line outdents past
the block's `min_indent`, dispatching on the first significant token in a fixed
order.

A line beginning `var` is a declaration, parsed for its id and its optional
`label`, `units`, and `description`, and a duplicate id is an error. Anything
ending in `:` is a block header, handed to `_try_parse_block_header`: `for`, or
a `|`-composed run of `for` headers, builds a `Sweep` or a `Parallel` of sweeps
through the sweep-source registry; `if` opens a conditional chain, and an
`elif` or `else` reached here without a preceding `if` at the same indent is an
error; anything else takes its first word to `get_block_spec`, and an
unregistered keyword raises rather than skipping the indented body it heads.

A whole statement shaped `name(args)` is a fragment call. Operations never take
that shape, since their keyword is followed by whitespace-separated tokens, and
block headers end with a colon, so the form is unambiguous at statement
position. A name that is a registered waveform rather than a defined fragment
gets its own message, because writing `Gaussian(...)` on a line of its own is a
common mistake with an obvious fix.

Everything left is an operation. `_tokenize` splits the line, the first token
is split at its first `.` into `(vendor, name)` (no dot means `vendor=None`),
and `get_operation_spec(vendor, name)` returns the spec. A spec with a `parse`
callback receives the remaining tokens; otherwise `default_parse_operation`
reconstructs the operation from the signature. The three unknown-keyword
messages are different: a dotted name points at the extension package and the
file's `require` line, a bare name that happens to be a registered block
keyword says the header needs a trailing colon, and anything else reports that
no core operation is registered under that name.

`_tokenize` splits on whitespace at nesting depth zero only, tracking quote
state at every depth and honoring `\"` inside strings, so `fields=["state",
"iq"]` and `matrix={"a": 1.0}` survive as single tokens and a parenthesis
inside a quoted string never perturbs the nesting count. `parse_value` then
decodes one token into a value: quoted strings, `true`, `false`, `null`,
bracket and brace literals, parenthesized expressions, function-call shapes
(math functions and `where` first, then waveform and sweep-source
constructors), `<handle>.<field>` measurement references, identifiers already
declared as variables, and numbers. A token matching none of those comes back
as a plain string, which is how a bus path flows through untyped until the
promotion pass.

### Bus references

A bus reference is one of two things on the wire: a quoted string, or a path
like `q[0].drive` or `c[0,1].flux`, where a comma-joined index denotes a tuple.
Quoting *is* the type distinction, and it is tracked through parsing by the
`_QuotedStr` marker subclass, so a raw-string bus that happens to *look* like a
path is never promoted. Promotion runs post-parse and only over the attributes
the operation lists in `Operation.BUS_ATTRS`, which defaults to `("bus",)`;
`Sync` declares `("targets",)` and is handled element-wise, and `Call` declares
`()`. Promoting every string attribute would mangle a legitimate quoted string
that resembles a path, such as a vendor `set_parameter` alias of
`"cluster[0].module"`.

Within a bus attribute, a string that does not match the path syntax is left
alone, because that is a raw-string bus opting out of schema validation. A
program with no schema keeps every bus exactly as written. A path-shaped token
that does not resolve against the schema raises
`bus path 'q[7].drive' does not resolve against the program schema: ...`.

What comes back from a successful resolution is a fully populated `BusRef`,
with `element`, `idx`, `kind`, `channel`, `acquires`, and a back-pointer to the
schema, so the post-load validators (`_validate_waveform_channel`,
`_validate_acquires`) have everything they need. The schema itself is rebuilt
as a dynamic `BusSchema` through `add_element` calls, never as the typed preset
class, with each bus declared as `<kind> info=<channel>[+acquires]` where the
channel is exactly one of `single` or `IQ`. A program that was schema-backed
when it was written stays schema-backed after loading; only the Python class
identity is lost.

### Lazy imports

`loads`, `load`, and `ParseError` are resolved by a module-level `__getattr__`
on both `qprogram/__init__.py` and `qprogram/serialization/__init__.py`. The
parser module constructs `QProgram` instances, so importing it eagerly from
either place would close an import cycle; deferring the import until the
attribute is first read keeps the names on the package surface without one.
`dumps` and `save` are imported eagerly, because nothing the writer imports at
module level reaches `QProgram`.

If you refactor this, the rule is that anything importing `qprogram.QProgram`
has to be reached through that `__getattr__` rather than imported at the top of
`qprogram/__init__.py`.

## The canonical grammar

The format has a normative machine-readable grammar in
`src/qprogram/grammar/qp.lark`: a Lark dialect using LALR with a two-space
`Indenter` and no bracket types that suppress newlines, since `.qp` is strictly
line-based. `qprogram.grammar.grammar_text()` returns its source, `parser()`
builds the reference parser, and `parse_text()` parses a document with it after
normalizing a missing trailing newline. `lark` is a dev-only dependency, so
`parser()` raises `ModuleNotFoundError` on an ordinary install.

It is a specification artifact, not the production parser. Generating the
production parser from it would cost the three things the hand-written one
gives: a line number on every error, registry lookups that decide whether a
keyword exists at all, and schema resolution for bus paths. None of those are
expressible in a context-free grammar. What the separation costs in return is
that the two have to be kept in step, which `tests/test_grammar.py` does in
both directions.

Positively, the writer's output for a full-feature program, a schema program, a
fragment program, and a vendor program must parse under the grammar, as must
every program the round-trip hypothesis strategies generate (`programs()` and
`fragment_programs()`, imported from `tests/test_round_trip_property.py`).
Negatively, a corpus of thirteen syntactic malformations, from a missing header
through an unparenthesized expression to a dangling dict literal, must be
rejected by `qp.loads` and by the reference parser both. A last test asserts
that every hard keyword the grammar declares (`var`, `for`, `in`, `if`, `elif`,
`else`, `and`, `or`, `not`, `true`, `false`, `null`, `fragment`) is in
`qp.RESERVED_KEYWORDS`, so `var for` cannot be accepted by one side and
rejected by the other.

The grammar over-approximates whatever is semantic rather than syntactic: any
identifier is a valid operation or block keyword, section order is free, and
version shapes, duplicate declarations, and bus-path resolution are all
post-parse checks. It is exact about token shapes: quoting,
parenthesized expressions, call adjacency (`name(` with no space between),
list and dict literals, and two-space indentation.

## Variable identifiers

The serializer key for a variable is `Variable.id`, and the identifier in the
file is that id verbatim. That works because the constraint is enforced
upstream: `Variable` validates its id against `[A-Za-z_][A-Za-z0-9_]*` and
rejects [reserved keywords](../reference/reserved.md) at construction, and
`QProgram.variable` rejects a duplicate id within one program. So the writer
never sanitizes and never invents a disambiguation suffix, and `.qp` files stay
stable across re-serializations. Routing every emission through the
`_var_idents` table anyway keeps emit-time renaming a single-point change if
that ever becomes necessary.

A fragment section gets its own identifier scope. `_write_fragments` saves the
table, adds the fragment's parameters and locals to a copy, and restores it
afterwards, so a fragment may shadow a host id: a fragment body can only
reference its own parameters and locals, so there is nothing for the shadow to
hide. On the parse side a loop variable is declared on demand, which means a
hand-written file can drive `for t in Range(0, 100, 10):` with no `var t` line
of its own.

## Arrays and file-backed sweeps

Array-valued arguments are emitted as bracket literals, in full, never
truncated, because the literal has to reload to exactly the same values. That
covers `Values.points`, which gets the bare `[...]` sugar in a `for` header,
and `Arbitrary.samples` alike. Only 1-D arrays have a `.qp` form; anything else
raises `Cannot serialize a 2-D array; only 1-D arrays have a .qp form`. On the
way back in, a bracket literal decodes to a plain Python list and the
constructor converts it, so a consumer that wants an array is the one that
makes it.

`_parse_number` keeps `int` and `float` distinct: a literal written without a
decimal point or an exponent, whose value is integral, comes back as an `int`.
Without that, integer sweep bounds would silently become floats and the second
write would differ from the first. Non-finite literals (`inf`, `nan`) stay
floats, since `int()` on them raises.

For a sweep whose values are large or live outside the program, use the file
source instead. The path, not the data, is what the `.qp` file carries:

```
#!QProgram 1.0

body:
  var amp

  for amp in File(path="sweep_values.npy"):
    play "drive" "pi"
```

Both `File.length()` and `File.values()` call `np.load`, and neither caches; a
cached array would join the source's structural equality, so a loaded instance
would stop comparing equal to a fresh one. The `.npy` file therefore has to be
readable wherever the program is used, not only where it runs: `length()` is
what `Parallel` calls to check lockstep when two sweeps compose with `|`, and
what the executor calls to size its result arrays. Composing a `File` sweep
into a parallel pair with the file missing raises `FileNotFoundError` at build
time, and a file holding an empty array or an array of rank other than 1 raises
`ValidationError` naming the path.

## Round-trip stability

`dumps(loads(dumps(p))) == dumps(p)` holds because every choice the writer
makes is a function of the AST rather than of history, and every choice the
parser makes preserves the distinctions the writer's choices depend on.
Concretely: variable ids are written verbatim, so no sanitization or suffix
allocation can differ between two writes; keyword arguments are compared
against their constructor defaults, so the second write emits the same set as
the first; `_parse_number` keeps integers integral; `_escape_str` and
`_unescape_str` are exact inverses, so a label holding quotes, backslashes, or
newlines survives; `_QuotedStr` keeps a raw-string bus raw, so the second write
quotes it again rather than promoting it to a path; a schema is always written
structurally, so a preset and the dynamic schema it reloads as emit the same
text; fragment order and `require` order are recomputed deterministically; and
fragment-call arguments are always positional in parameter order, so a call
built with keywords reloads and rewrites identically.

`tests/test_round_trip.py` asserts byte stability one feature surface at a
time: metadata, variable declarations, several schema presets, plain-string
buses, the core operations, inline waveforms, expressions, sweeps and parallel
loops, conditionals, measurement handles and `fields=`, vendor operations, and
the `rebind` and `with_waveforms` transforms.

`test_round_trip_full_features` stacks many of them into one program: a
transmon schema, an `average`, a parallel pair of sweeps with a third sweep
nested inside it, math-function and comparison expressions, an `IQDrag` whose
amplitude is a variable, `set_gain`, `set_frequency`, `set_phase`, `set_offset`,
`play`, `sync`, `wait`, and a `measure` carrying `fields=("iq", "raw")`. It is
not exhaustive: it holds no vendor operation, no conditional, and no fragment,
which have their own tests (`test_round_trip_with_vendor`,
`test_round_trip_vendor_op_inside_conditional`,
`test_round_trip_conditional_full_chain`, and
`tests/test_fragments_serialization.py`). `test_round_trip_loaded_equals_original`
adds the structural half: a reloaded body compares equal to the original and
hashes the same. On top of that, `tests/test_round_trip_property.py` generates
programs with hypothesis and asserts both byte stability and structural
equality after a round trip, including one property that a path-shaped raw bus
string survives intact in a program that has a schema.

If you change anything in the writer or the parser, run those first:

```bash
uv run pytest tests/test_round_trip.py tests/test_round_trip_property.py -v
```

When stability breaks, the cause is almost always one of four things: a new
operation, waveform, or sweep source added without a registration; a
non-default keyword argument being emitted positionally, or the reverse; a
class attribute renamed while the registry still references the old name; or a
constructor default changed so that a stored value now compares equal to it.
The assertion prints both texts, so diffing the first `dumps` against the
second points at the line that moved.

## Extension points

| Call | What it registers |
|---|---|
| `qp.register_waveform(cls)` | a waveform class, keyed by `cls.__name__` |
| `qp.register_sweep_source(cls)` | a sweep source, keyed by `cls.__name__`, plus its `TOKEN` in the capability registry |
| `qp.register_vendor_operation(vendor, name, cls)` | a vendor operation, emitted as `<vendor>.<name>` |
| `qp.register_vendor_block(vendor, name, cls)` | a vendor control-flow block, emitted as `<vendor>.<name>:` |
| `qp.register_vendor_version(vendor, version)` | the vendor's protocol version, once per package |

`qprogram.serialization.register_operation(name, cls, *, vendor=None,
serialize=None, parse=None)` and `register_block(name, cls, *, vendor=None,
serialize_header=None, parse_header=None)` are the underlying calls. They are
what core registration uses, and what to reach for when the default reflection
cannot express the syntax. Neither is re-exported at the top level; both return
`cls` unchanged so the call can stand in for the class at the point of
registration, though a bare `@register_operation` decoration does not work,
since `cls` is the second positional parameter rather than the first.

A vendor block that repeats its body should also set `REPEATS = True` on the
class, so it counts toward the `max_loop_nesting` limit a
[profile](../guide/capabilities.md#profile-bundles) declares. The whole
vendor-package story, entry point included, is in [building a vendor
extension](vendor-extensions.md); the wire format itself is specified in the
[`.qp` file format reference](../reference/qp-format.md).
