# qprogram-qblox

Qblox vendor extensions for the [QProgram](../qprogram) pulse programming DSL.

Provides Qblox-specific operations (`acquire`, `set_markers`, `measure_reset`, `set_trigger`, `wait_trigger`)
registered under the `qblox` vendor namespace.

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
```
