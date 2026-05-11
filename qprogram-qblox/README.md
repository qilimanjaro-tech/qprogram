# qprogram-qblox

Qblox vendor extensions for the [QProgram](../qprogram) pulse programming DSL.

Provides Qblox-specific operations registered under the `qblox` vendor namespace. The operations span the full
spectrum a vendor extension can offer — QProgram doesn't distinguish hardware vs software execution, so the
namespace mixes:

- **simple 1-1 sequencer ops**: `acquire`, `set_markers`, `set_trigger`, `wait_trigger`,
- **complex orchestration**: `active_reset` (measure + conditional reset pulse),
- **software-only**: `set_acquisition_threshold` (translates to a QCoDeS parameter set at execution time).

## Installation

```bash
pip install qprogram-qblox
```

## Usage

```python
import qprogram as qp
import qprogram_qblox  # registers the qblox vendor on import

program = qp.QProgram()
program.qblox.acquire("readout_q0", "weights")
program.qblox.set_markers("drive_q0", "0001")
program.qblox.set_acquisition_threshold("readout_q0", value=0.42)  # software-only
program.qblox.active_reset(                                          # complex orchestration
    bus="readout_q0", waveform="readout", weights="weights",
    control_bus="drive_q0", reset_pulse="pi",
)
```
