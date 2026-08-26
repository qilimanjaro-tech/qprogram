# Buses and schemas

Every operation targets a bus by name, and a plain string is a valid name:

```python
import qprogram as qp

program = qp.QProgram(label="rabi")
program.play("drive_q0", "pi_pulse")
program.measure("readout_q0", "readout", "weights")
```

That is a complete program. Two things you give up by typing strings: there is
no tab-completion, and nothing checks either the name or the kind of waveform
you put on it. Type `"drvie_q0"` by accident and you find out at execution
time, on hardware.

A `BusSchema` closes both gaps without changing what ends up in the AST.

## What a schema declares

A schema records element kinds and, for each element kind, the bus kinds that
element exposes, the channel each bus carries, and whether it has an ADC. It
says nothing about how many qubits the chip has, so any index resolves:

```python
schema = qp.BusSchema.transmon()
q = schema.q

q[0].drive  # "q0/drive", a BusRef
q[3].readout  # "q3/readout"
q[42].drive  # "q42/drive"
```

`schema.elements` maps element name to an `ElementSchema`, whose `buses` is
`{kind: (channel, acquires)}` in declaration order and whose `bus_names` is
just the kinds. `schema.naming` is the `BusNaming` the schema resolves strings
through, and `schema.KIND` is a class-level tag the presets set
(`"transmon"`, `"fluxonium_coupled"`, and so on) and nothing else reads.

A `BusRef` is a real `str` subclass, so it works everywhere QProgram expects a
bus name; in a `.qp` file it emits as an `element[idx].kind` path rather than a
quoted name. It carries six extra fields:

```python
bus = q[0].readout

bus  # "q0/readout"
isinstance(bus, str)  # True
bus.element  # "q"
bus.idx  # 0, or a tuple for a multi-index element
bus.kind  # "readout"
bus.channel  # "IQ"
bus.acquires  # True
bus.schema  # the BusSchema that produced it
```

The index field is `idx` rather than `index` because a `str` subclass must not
shadow the inherited `str.index` method. The fields live in `__slots__`, so an
instance carries them without gaining a per-instance `__dict__`, and `BusRef`
overrides `__reduce__` so `copy.deepcopy` and `pickle` rebuild the metadata:
the inherited `str.__reduce_ex__` would pass the string value alone back to a
constructor that wants six more arguments.

Building a ref by hand is how you describe a bus that lives outside any
schema. `qp.BusRef("aux0/rf", "aux", 0, "rf", "single", acquires=False)`
leaves `schema` at `None`, so a program bound to any schema accepts it, while
its `channel` and `acquires` still drive the checks below.

## Built-in presets

The presets return typed subclasses, so an IDE can complete the bus kinds. The
qubit element is always named `q` and the coupler element, where there is one,
is always named `c` with a single `flux` bus on a real-valued channel and no
ADC.

| Preset | Returns | `q` buses | `c` buses |
|---|---|---|---|
| `qp.BusSchema.transmon()` | `TransmonSchema` | `drive` (IQ), `readout` (IQ, acquires) | none |
| `qp.BusSchema.transmon_coupled()` | `TransmonCoupledSchema` | `drive` (IQ), `readout` (IQ, acquires) | `flux` (single) |
| `qp.BusSchema.flux_tunable_transmon()` | `FluxTunableTransmonSchema` | `drive` (IQ), `readout` (IQ, acquires), `flux` (single) | none |
| `qp.BusSchema.flux_tunable_transmon_coupled()` | `FluxTunableTransmonCoupledSchema` | `drive` (IQ), `readout` (IQ, acquires), `flux` (single) | `flux` (single) |
| `qp.BusSchema.fluxonium()` | `FluxoniumSchema` | `drive` (IQ), `readout` (IQ, acquires), `flux_x` (single), `flux_z` (single) | none |
| `qp.BusSchema.fluxonium_coupled()` | `FluxoniumCoupledSchema` | `drive` (IQ), `readout` (IQ, acquires), `flux_x` (single), `flux_z` (single) | `flux` (single) |

Every preset takes an optional `naming` argument and nothing else; the schema
classes live in `qp.buses` under those names if you want to subclass one or
name one in a type annotation.

A coupler sits between qubits, so its index is usually a tuple, and a tuple
index joins with an underscore:

```python
coupled = qp.BusSchema.flux_tunable_transmon_coupled()
coupled.c[0, 1].flux  # "c0_1/flux"
coupled.c[3].flux  # "c3/flux", a single integer works too
```

## Bus naming

`BusNaming` holds one format string, and its `resolve(element, index, kind)`
substitutes the three pieces by keyword. The default pattern is
`BusNaming.DEFAULT_PATTERN`, `"{element}{index}/{kind}"`, which is why
`q[0].readout` reads `"q0/readout"`. A tuple index is joined with underscores
before substitution. Pass a `naming` to any preset, to `BusSchema()`, or to
`BusSchema.combine`, and every ref that schema produces adopts it:

```python
named = qp.BusSchema.flux_tunable_transmon(naming=qp.BusNaming("{kind}_{element}{index}_bus"))
named.q[0].drive  # "drive_q0_bus"
named.q[0].readout  # "readout_q0_bus"
qp.BusNaming().resolve("c", (0, 1), "flux")  # "c0_1/flux"
```

The three supported placeholders are `{element}`, `{index}` and `{kind}`, and
the pattern is not validated when the `BusNaming` is constructed. A bad
pattern raises the first time a ref is resolved: `KeyError` for a placeholder
outside the three (`"{element}-{port}"` raises `KeyError: 'port'`),
`ValueError` for a malformed format string or a format specification the
substituted text cannot satisfy (every piece arrives as text, so `{index:d}`
fails), and `IndexError` for a positional placeholder such as `{0}`.

Nothing requires the pattern to use all three placeholders, and a pattern that
omits `{kind}` collapses every bus on an element onto one name:
`BusNaming("{element}{index}")` resolves both `q[0].drive` and `q[0].readout`
to `"q0"`. A non-default pattern is written into the `naming:` line of the
`.qp` schema block, so it survives a round-trip; the default pattern is left
out of the file.

## Dynamic schemas

For a one-off or exotic layout, build the schema by hand. There is no static
typing on the result, because bus access goes through `__getattr__` rather
than declared properties, but everything else works the same:

```python
dynamic = qp.BusSchema()
dynamic.add_element(
    "q",
    buses={
        "drive": ("IQ", False),
        "readout": ("IQ", True),
        "charge": ("single", False),
    },
)
dynamic.add_element("resonator", buses={"probe": ("IQ", True)})

dynamic.q[0].charge  # "q0/charge"
dynamic.resonator[2].probe  # "resonator2/probe"
```

Each entry in `buses` is a `(channel, acquires)` pair: `channel` is `"single"`
or `"IQ"`, and `acquires` is `True` when the bus has an ADC. Registering an
element name twice replaces the earlier declaration rather than merging into
it, so the last call wins.

## Combining schemas

Two schemas add together, which is how a chip schema and a separate control
family (an RF switch, a set of couplers) end up in one place:

```python
switch = qp.BusSchema()
switch.add_element("switch", buses={"rf": ("single", False)})

combined = qp.BusSchema.flux_tunable_transmon() + switch
combined.q[0].flux  # "q0/flux"
combined.switch[0].rf  # "switch0/rf"
```

Either operand may be a schema instance or a schema class, so
`qp.buses.FluxTunableTransmonSchema + MyChipSchema` works as well; the class
form is provided by a metaclass, because `__add__` in a class body governs
instances only. `BusSchema.combine(*schemas, naming=None)` is the same
operation spelled out, and it is what you want for three or more schemas in
one call or for choosing the naming explicitly.

The result is a plain `BusSchema` holding the union of the inputs' elements, so
`combined.q[0].drive` resolves at runtime with no static typing, the same
trade-off as `add_element`. Build your refs from the combined schema rather
than from the originals: a ref's `schema` back-pointer has to match the schema
attached to the program.

`combine` raises `ValueError` when it is called with no schemas, when the
inputs disagree on their naming pattern and no `naming` is passed
(`cannot combine schemas with different naming patterns [...]`), and when two
inputs declare the same element name with different buses
(`cannot combine schemas: element 'q' is defined differently [...]`, which is
what `transmon() + flux_tunable_transmon()` produces, since both define `q`).
Re-declaring an identical element is allowed and merges once.

## Channel types and acquisition

`ChannelType` is `Literal["single", "IQ"]`: a single real-valued output, or a
pair of I and Q outputs. What the channel gates is the waveform an operation
carries, not the operation itself, and the two waveform hierarchies are
disjoint, so the test is exact. An `"IQ"` bus takes an `IQWaveform` (`IQPair`,
`IQDrag`, `IQRotation`, `IQZero`, `Modulated`) and rejects a `Waveform`; a
`"single"` bus takes a `Waveform` (`Square`, `FlatTop`, `Gaussian`, and the
rest) and rejects an `IQWaveform`.

`play` checks its waveform, and `measure` checks both its readout pulse and
its integration weights. Every other bus-carrying operation (`wait`, `sync`,
`set_frequency`, `set_phase`, `reset_phase`, `set_gain`, `set_offset`,
`set_parameter`, `get_parameter`) accepts either channel type. `set_offset` in
particular does not compare `offset_path1` against the bus channel, so a
second path on a single-channel bus passes both the builder and `validate`
without comment.

`acquires` is read by `measure`, which refuses a bus without an ADC, and again
when a fragment is expanded, because a bus that arrived as a fragment parameter
could not be checked while the fragment body was being written.

## Checks that run when you build a program

The schema-identity check compares the ref's `schema` back-pointer against the
program's, so it only fires for a ref a schema produced. The channel and
acquisition checks read `channel` and `acquires` off the ref itself, so they
fire for a hand-built `BusRef` too. Missing elements and missing bus kinds
fail earlier still, when the accessor is read:

| Check | Fires when | Result |
|---|---|---|
| element lookup | `schema.<name>` names no registered element | `AttributeError: No element 'resonator' in schema. Available: q` |
| bus kind, dynamic schema | `schema.q[0].<kind>` is not declared for that element | `AttributeError: 'q' has no bus 'flux'. Available: drive` |
| bus kind, typed schema | the accessor class defines no such property | `AttributeError: 'TransmonQubitBuses' object has no attribute 'flux'` |
| schema identity | the ref's `schema` is not the program's schema | `ValidationError: BusRef 'q0/drive' (element='q', kind='drive') comes from a different BusSchema ...` |
| acquisition | `measure` on a ref with `acquires=False` | `ValidationError: Bus 'q0/drive' does not support acquisition (acquires=False). ...` |
| channel, IQ bus | a single-channel `Waveform` on an `"IQ"` bus | `ValidationError: Bus 'q0/drive' is an IQ channel but received a single-channel Waveform (Square). ...` |
| channel, single bus | an `IQWaveform` on a `"single"` bus | `ValidationError: Bus 'q0/flux' is a single channel but received an IQWaveform (IQDrag). ...` |

Each mistake therefore surfaces on the line that made it:

```python
ftt = qp.BusSchema.flux_tunable_transmon()
q = ftt.q
prog = qp.QProgram(label="checks", schema=ftt)

prog.play(q[0].drive, qp.waveforms.IQDrag(0.5, 40, 8, 0.1))  # OK
prog.play(q[0].drive, qp.waveforms.Square(0.5, 100))  # ValidationError
prog.play(q[0].flux, qp.waveforms.FlatTop(0.5, 200, 20))  # OK
prog.play(q[0].flux, qp.waveforms.IQDrag(0.5, 40, 8, 0.1))  # ValidationError
prog.measure(q[0].drive, "readout", "weights")  # ValidationError, no ADC
prog.measure(q[0].readout, "readout", "weights")  # OK
q[0].flux_x  # AttributeError, a flux-tunable transmon has one flux bus
ftt.resonator  # AttributeError, this schema has no resonator element
```

`measure` runs its checks in the order schema identity, acquisition, readout
pulse channel, weights channel, then measurement-name allocation, so the
first thing wrong with the call is the thing reported. Vendor operations
appended through a `VendorNamespace` get the schema-identity check on every
`BusRef` attribute they carry, including refs inside lists, but not the
channel and acquisition checks, which the `play` and `measure` builders run on
their own arguments.

A string waveform alias carries no channel, so it is not checked when the
operation is appended. It is checked when `with_waveforms` resolves the alias
to a concrete waveform, against the bus the operation targets, which is the
same `ValidationError` arriving later. The bus it resolves against is the one
the operation carries at that moment, so a
[`rebind`](#rebinding-buses-in-a-program) run first changes both the check and,
for a `WaveformLibrary` with per-bus entries, which waveform the alias
resolves to.

## Plain strings always bypass validation

Raw strings still work and skip every check. This is on purpose: you can keep
a program mostly schema-backed and slot in a one-off bus by name without
declaring it.

```python
prog.play("raw_bus", qp.waveforms.Square(0.5, 100))  # OK, no validation
prog.measure("raw_bus", "readout", "weights")  # OK, no validation
prog.play(q[0].drive, "pi_pulse")  # OK, the alias is checked later
```

Two other behaviors follow the same split. Auto-allocated measurement names
are per bus for a `BusRef` (`q0/readout/m0`, `q0/readout/m1`) and share one
global counter for raw-string buses (`m0`, `m1`); see
[Measurements and results](measurements.md). And a platform's capabilities are
declared per `(element, bus kind)` slot, so a schema-backed `BusRef` routes to
the profile for its own kind of bus while a raw string always routes to
`default_bus_profile`; see [Capabilities and validation](capabilities.md).

## One schema per program

A `QProgram` holds at most one `BusSchema`, passed at construction:

```python
program = qp.QProgram(label="rabi", schema=schema)
```

If you build a program with `schema=schema_a` and then call
`program.play(schema_b.q[0].drive, ...)`, QProgram refuses with a
`ValidationError` at the call site. The comparison is by identity, not by
structure, so two separately constructed `transmon()` schemas count as
different. That bus would serialize fine but mean something different on load,
so the builder rejects it rather than letting it through.

You can omit the schema entirely. In that case the first schema-backed ref the
program sees is adopted as the program's schema, and every later ref is
compared against it. A fragment carries a schema the same way, and calling one
reconciles the two: a fragment built against a schema lends it to a program
that has none, and two different schemas raise. See [Fragments](fragments.md).

## Defining your own typed schema

For a chip type the presets do not cover, write a typed schema class. The
pattern is what the presets do internally, and the base classes are the
underscore-prefixed ones in `qp.buses`: an accessor exposes one property per
bus kind, a factory turns an index into an accessor, and the schema exposes one
property per element.

```python
class MyQubitBuses(qp.buses._TypedElementAccessor):
    @property
    def drive(self) -> qp.BusRef:
        return self._ref("drive", "IQ")

    @property
    def readout(self) -> qp.BusRef:
        return self._ref("readout", "IQ", acquires=True)

    @property
    def charge(self) -> qp.BusRef:
        return self._ref("charge", "single")


class MyQubitFactory(qp.buses._TypedElementFactory):
    _accessor_cls = MyQubitBuses

    def __getitem__(self, index: int) -> MyQubitBuses:
        return MyQubitBuses(self._element, index, self._naming, self._parent)


class MyChipSchema(qp.BusSchema):
    KIND = "my_chip"

    def __init__(self, naming: qp.BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element(
            "q",
            {
                "drive": ("IQ", False),
                "readout": ("IQ", True),
                "charge": ("single", False),
            },
        )
        self.add_element("c", {"flux": ("single", False)})

    @property
    def q(self) -> MyQubitFactory:
        return MyQubitFactory("q", self._naming, self)

    @property
    def c(self) -> qp.buses.CouplerFactory:
        return qp.buses.CouplerFactory("c", self._naming, self)
```

A factory takes `(element, naming, schema)` and an accessor takes
`(element, index, naming, schema)`; that last argument is the back-pointer
every `BusRef` carries in `bus.schema`, and it is what lets a program reject a
reference built from a different schema. `_ref(kind, channel, acquires=False)`
does the rest: it resolves the name through the schema's naming and fills in
the metadata, with `acquires` keyword-only.

Call `add_element` for every element, including the ones your typed properties
already cover. The properties are what a reader writes, but the `elements`
dictionary is what the `.qp` writer emits and what `combine` merges, and
`_ref` never consults it: a class with a `q` property and no
`add_element("q", ...)` resolves `q[0].drive` happily, writes an empty
`schema:` block, and then fails to reload with
`ParseError: Line 7: inline schema has no element declarations`. Accept
`naming` in the constructor too, so the class works with `combine` and with
`rebind(naming=...)`, both of which re-declare elements under another pattern.

User-defined classes serialize through the same inline form as the presets
(see [the .qp schema declaration](../reference/qp-format.md#schema-declaration)).
The Python class identity does not survive a round-trip: a loaded program gets
a plain `BusSchema` with `KIND` back at `""`, holding the same elements, bus
kinds and naming. Runtime access and the validation behavior are unchanged,
and the typed properties are gone.

## Re-resolving a bus coordinate

`qp.buses.resolve_ref(schema, element, index, kind)` turns a structural
coordinate back into a `BusRef`. It is two attribute reads with a subscript
between them, `getattr(getattr(schema, element)[index], kind)`, which is why it
works identically for a typed preset and for a dynamic schema: the typed path
lands on a declared property, the dynamic path on `__getattr__`. The ref comes
back resolved under `schema`'s naming and pointing at `schema`, and the
failures are the accessor failures from the table above, an `AttributeError`
for an unknown element or bus kind.

```python
qp.buses.resolve_ref(coupled, "q", 0, "readout")  # "q0/readout"
qp.buses.resolve_ref(coupled, "c", (0, 1), "flux")  # "c0_1/flux"
```

Two callers use it. The `.qp` parser calls it for every `element[i].kind` path
in a file, which is how a loaded program gets real `BusRef`s rather than
strings. `QProgram.rebind` calls it for every ref it rewrites, which is how
re-indexing a qubit or moving a program onto another chip's schema stays
checked against that schema. The naming-only case goes through
`qp.buses.naming_substituted_schema(schema, naming)`, which returns a dynamic
copy of `schema` with the same elements declared under a new `BusNaming`.

## Rebinding buses in a program

`QProgram.rebind` rewrites the bus on every operation in a program by
re-resolving each one structurally instead of substituting strings. Its
parameters are all keyword-only:

```python
program.rebind(
    schema=None,
    elements=None,
    naming=None,
    strings=None,
    allow_unported_strings=False,
)
```

`schema` is the schema the refs are resolved against, and it defaults to the
program's own, which is the re-index-within-one-chip case; pass another schema
to move the program onto another chip. `elements` maps `(element, idx)` to
`(element, idx)`, so `{("q", 0): ("q", 1)}` moves every operation on qubit 0
onto qubit 1 while every pair the map does not list passes through unchanged;
an index is an `int` or a tuple, the same shape as `BusRef.idx`. `naming`
re-resolves the refs under a different `BusNaming`, through
`qp.buses.naming_substituted_schema`, and it needs a schema to substitute into:
on a program that has none it raises `ValidationError: rebind(naming=...)
requires the program to have a schema to re-resolve against`. `strings` covers
raw-string buses, described below.

Every schema-backed ref goes through `qp.buses.resolve_ref` with its remapped
element and index and its own bus kind, so the rebound bus is a `BusRef` again,
carrying the `channel` and `acquires` the target schema declares and still
emitting as an `element[idx].kind` path in a `.qp` file. A coordinate the target
schema does not declare fails rather than resolving to a plausible name:
rebinding a `play` on `q[0].drive` onto a transmon coupler raises
`AttributeError: 'CouplerBuses' object has no attribute 'drive'`, because a
coupler declares only `flux`. Auto-allocated measurement names are re-derived
from the new bus while user-supplied names are left alone; see
[Measurements and results](measurements.md).

`rebind` returns a new `QProgram` and mutates nothing. The copy is deep, and
fragment calls are expanded into it first when the program has any, so the
result holds the inlined bodies rather than the calls; see
[Fragments](fragments.md). The schema is copied along with the program unless
you pass one, and the schema-identity check compares by identity, so a ref held
from the original schema cannot be appended to the rebound program; read the
schema back off the result if you want to keep building.

```python
schema = qp.BusSchema.transmon()
q = schema.q

program = qp.QProgram(label="rabi", schema=schema)
program.play(q[0].drive, "pi_pulse")
handle = program.measure(q[0].readout, "readout", "weights")
handle.name  # "q0/readout/m0"

ported = program.rebind(elements={("q", 0): ("q", 1)})
sorted(ported.buses)  # ["q1/drive", "q1/readout"]
sorted(program.buses)  # ["q0/drive", "q0/readout"], the original is untouched
ported.measurement_handles()[0].name  # "q1/readout/m0"

renamed = program.rebind(naming=qp.BusNaming("{kind}_{element}{index}_bus"))
sorted(renamed.buses)  # ["drive_q0_bus", "readout_q0_bus"]
```

A raw-string bus carries no element, index or kind, so there is nothing to
re-resolve it from, and `rebind` will not guess. A raw string that `strings`
does not cover fails the whole call:

```
ValidationError: rebind left raw-string bus(es) unported: 'aux_line'. Raw strings carry no schema metadata to re-resolve — map them via strings={...} (map a name to itself to keep it), or pass allow_unported_strings=True to leave them in place.
```

The two ways past that differ in what the call records. `strings` renames the
buses it lists, and mapping a name to itself keeps it while saying in the source
that keeping it was the intent. `allow_unported_strings=True` lifts the failure
for every uncovered string at once, and what it costs is that record: the
strings stay pointing at the old buses in a program whose schema-backed refs
have all moved, and neither the call nor the result says which ones were left
behind.

```python
mixed = qp.QProgram(label="mixed", schema=schema)
mixed.play(q[0].drive, "pi_pulse")
mixed.play("aux_line", qp.waveforms.Square(0.5, 100))

mixed.rebind(elements={("q", 0): ("q", 1)})  # ValidationError
mixed.rebind(elements={("q", 0): ("q", 1)}, strings={"aux_line": "aux_rf"})  # renamed
mixed.rebind(elements={("q", 0): ("q", 1)}, strings={"aux_line": "aux_line"})  # kept
mixed.rebind(elements={("q", 0): ("q", 1)}, allow_unported_strings=True)  # kept
```

## Three levels of bus referencing

| Approach | Setup | Validation | Tab-completion |
|---|---|---|---|
| `"drive_q0"` raw string | none | none | no |
| `qp.BusSchema.transmon().q[0].drive` | one line | channel, ADC | yes |
| `platform.get_bus_schema()` | from platform | channel, ADC | yes |

All three produce a `str` at the AST level. Mix them freely. The third is the
one to reach for when a platform is in the loop: `get_bus_schema` is on
`PlatformProtocol`, so the schema comes from the same object that will run the
program, and the bus names it produces are the ones that platform expects.
