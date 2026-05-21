# Buses and schemas

Every operation targets a bus by name. The simplest spelling works:

```python
program.play("drive_q0", "pi_pulse")
program.measure("readout_q0", "readout", "weights")
```

That is a valid program. Two things you give up by typing strings: there is
no tab-completion, and there is no validation. Type `"drvie_q0"` by accident
and you find out at execution time, on hardware.

`BusSchema` fixes both problems without changing what ends up in the AST.

## Schemas in 30 seconds

A `BusSchema` declares which kinds of buses each element of the chip has.
It does not declare how many qubits exist; you can index any qubit you like.

```python
from qprogram.buses import BusSchema

schema = BusSchema.transmon()    # qubits with drive (IQ) and readout (IQ, acquires)
q = schema.q

q[0].drive       # "q0/drive", a BusRef
q[3].readout     # "q3/readout", another BusRef
```

A `BusRef` is a real `str` subclass. Everywhere QProgram expects a bus name,
a `BusRef` works. It just carries extra fields:

```python
bus = q[0].readout

bus                # "q0/readout"
isinstance(bus, str)
bus.element        # "q"
bus.idx            # 0
bus.kind           # "readout"
bus.channel        # "IQ"
bus.acquires       # True
```


## Built-in presets

The presets cover the common chip topologies. They return typed subclasses,
so your IDE knows exactly which bus kinds are available.

| Preset                             | `.q` buses                                                | `.c` buses     |
|------------------------------------|-----------------------------------------------------------|----------------|
| `BusSchema.transmon()`             | `drive` (IQ), `readout` (IQ, acquires)                    | -              |
| `BusSchema.transmon_coupled()`     | `drive`, `readout`                                        | `flux`         |
| `BusSchema.flux_tunable_transmon()`           | `drive`, `readout`, `flux`                     | -              |
| `BusSchema.flux_tunable_transmon_coupled()`   | `drive`, `readout`, `flux`                     | `flux`         |
| `BusSchema.fluxonium()`            | `drive`, `readout`, `flux_x`, `flux_z`                    | -              |
| `BusSchema.fluxonium_coupled()`    | `drive`, `readout`, `flux_x`, `flux_z`                    | `flux`         |

Coupler indices are tuples:

```python
schema = BusSchema.flux_tunable_transmon_coupled()
schema.c[0, 1].flux        # "c0_1/flux"
```

## Custom naming

Some platforms have entrenched naming conventions. Plug yours in with
`BusNaming`:

```python
from qprogram.buses import BusNaming

schema = BusSchema.flux_tunable_transmon(
    naming=BusNaming("{kind}_{element}{index}_bus")
)
schema.q[0].drive       # "drive_q0_bus"
schema.q[0].readout     # "readout_q0_bus"
```

Supported placeholders: `{element}`, `{index}`, `{kind}`.

## Dynamic schemas

For one-off or exotic chip layouts, build the schema by hand. There is no
static typing on the result, but everything else works the same:

```python
schema = BusSchema()
schema.add_element("q", buses={
    "drive":   ("IQ", False),
    "readout": ("IQ", True),
    "charge":  ("single", False),
})
schema.add_element("resonator", buses={"probe": ("IQ", True)})

schema.q[0].charge          # "q0/charge"
schema.resonator[2].probe   # "resonator2/probe"
```

Each entry in `buses=` is `(channel, acquires)`. `channel` is either
`"single"` or `"IQ"`. `acquires` is `True` if the bus has an ADC.

## What validation buys you

Schema-backed buses get checked when you call an operation:

```python
schema = BusSchema.flux_tunable_transmon()
q = schema.q

program.play(q[0].drive, IQDrag(0.5, 40, 8, 0.1))     # OK
program.play(q[0].drive, Square(0.5, 100))               # ValidationError
program.play(q[0].flux,  FlatTop(0.5, 200, 20))          # OK
program.play(q[0].flux,  IQDrag(0.5, 40, 8, 0.1))     # ValidationError
program.measure(q[0].drive, "readout", "weights")       # ValidationError (no ADC)
program.measure(q[0].readout, "readout", "weights")     # OK
```

The schema also rejects bus types that do not exist:

```python
q[0].flux_x          # AttributeError on a plain transmon
schema.resonator     # AttributeError if not declared
```

Any index works:

```python
q[42].drive          # "q42/drive"
c[3, 7].flux         # "c3_7/flux"
```

## Plain strings always bypass validation

Raw strings still work and skip every check. This is on purpose: you can keep
a `.qp` file mostly schema-backed and slot in a one-off bus by name without
declaring it.

```python
program.play("raw_bus", Square(0.5, 100))               # OK, no validation
program.measure("raw_bus", "readout", "weights")        # OK, no validation
program.play(q[0].drive, "pi_pulse")                    # OK, alias bypass too
```

## One schema per program

A `QProgram` holds at most one `BusSchema`. Pass it at construction:

```python
program = qp.QProgram(label="rabi", schema=schema)
```

If you build a program with `schema=schema_a` and then call `program.play(
schema_b.q[0].drive, ...)`, QProgram refuses. That bus would serialise fine
but mean something different on load, so the validator fails loudly.

You can omit the schema entirely. In that case, the first BusRef you use is
adopted as the program's schema.

## Defining your own typed schema

For a chip type the presets do not cover, you can write a fully typed schema
class. The pattern mirrors what the presets do internally.

```python
from qprogram.buses import (
    BusSchema, BusRef, BusNaming,
    _TypedElementAccessor, _TypedElementFactory,
    CouplerFactory,
)


class MyQubitBuses(_TypedElementAccessor):
    @property
    def drive(self) -> BusRef:
        return self._ref("drive", "IQ")

    @property
    def readout(self) -> BusRef:
        return self._ref("readout", "IQ", acquires=True)

    @property
    def charge(self) -> BusRef:
        return self._ref("charge", "single")


class MyQubitFactory(_TypedElementFactory):
    _accessor_cls = MyQubitBuses

    def __getitem__(self, index: int) -> MyQubitBuses:
        return MyQubitBuses(self._element, index, self._naming)


class MyChipSchema(BusSchema):
    KIND = "my_chip"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("q", {
            "drive":   ("IQ", False),
            "readout": ("IQ", True),
            "charge":  ("single", False),
        })
        self.add_element("c", {"flux": ("single", False)})

    @property
    def q(self) -> MyQubitFactory:
        return MyQubitFactory("q", self._naming)

    @property
    def c(self) -> CouplerFactory:
        return CouplerFactory("c", self._naming)
```

User-defined classes serialise through the same inline form as the presets
(see [the .qp schema declaration](../reference/qp-format.md#schema-declaration)).
The Python class identity does not survive a round-trip, but the element
structure and the validation behaviour do.

## Three levels of bus referencing

| Approach                          | Setup            | Validation     | Tab-completion |
|-----------------------------------|------------------|----------------|----------------|
| `"drive_q0"` raw string           | none             | none           | no             |
| `BusSchema.transmon().q[0].drive` | one line         | channel, ADC   | yes            |
| `platform.get_bus_schema()`       | from platform    | channel, ADC   | yes            |

All three produce a `str` at the AST level. Mix them freely.
