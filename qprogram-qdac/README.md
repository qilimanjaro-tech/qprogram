# qprogram-qdac

QDevil QDAC vendor extensions for the [QProgram](../qprogram) pulse programming DSL.

Provides QDAC-specific operations registered under the `qdac` vendor namespace. QDAC is a slow
high-precision DAC commonly used for flux biasing — operations here cover its waveform-engine
sequencing primitives:

- **`set_offset`** — set a static DC offset on a DAC channel; the offset value may be a swept
  `Variable`, in which case the qdac platform falls back to software-dispatched sweeps (one shot
  per iteration).
- **`set_trigger`** — configure trigger outputs on a channel (start, step, end, end_step
  positions; one or more output channels).
- **`wait_trigger`** — block a channel on an external trigger input.
- **`play`** — emit an arbitrary waveform from the QDAC waveform engine on a channel, with
  explicit dwell, delay, repetitions, and stepped/continuous mode.

## Installation

```bash
pip install qprogram-qdac
```

## Usage

```python
import qprogram as qp
import qprogram_qdac  # registers the qdac vendor on import

from qprogram.waveforms import Ramp

program = qp.QProgram()
program.qdac.set_offset("flux_q0", 0.42)
program.qdac.set_trigger("flux_q0", duration=50, position="start", outputs={1, 2})
program.qdac.play("flux_q0", Ramp(from_amplitude=0.0, to_amplitude=1.0, duration=1000),
                  dwell=10, delay=0, repetitions=1, stepped=False)
program.qdac.wait_trigger("flux_q0", port=3)
```

For typed IDE autocomplete, use the pre-combined `qdac.QProgram`:

```python
from qprogram_qdac import QProgram

program = QProgram()
program.qdac.set_offset(...)   # autocomplete + type-checked
```

Compose with other vendor mixins for multi-vendor platforms:

```python
from qprogram_qblox import QbloxMixin
from qprogram_qdac import QdacMixin
from qprogram import QProgram as BaseQProgram

class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
    pass
```
