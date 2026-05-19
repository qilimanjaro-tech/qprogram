"""Qblox vendor extensions for QProgram.

This package provides:

1. **Runtime registration** — importing this package registers the ``qblox`` vendor
   namespace on QProgram and all Qblox operations with the ``.qp`` serializer.

2. **Typed mixin** — ``QbloxMixin`` adds a typed ``.qblox`` property for IDE autocomplete.

3. **Pre-combined QProgram** — ``QProgram`` from this package has ``.qblox`` typed.

The operations cover the full spectrum that a vendor extension can offer —
QProgram makes no distinction between hardware and software execution, so a
vendor namespace can mix:

- simple 1-1 sequencer instructions (``acquire``, ``set_markers``,
  ``set_trigger``, ``wait_trigger``),
- complex multi-step orchestrations (``active_reset`` — measure + conditional
  reset pulse via the trigger network),
- and software-only operations (``set_acquisition_threshold`` — translates to
  a QCoDeS parameter set at execution time, not to any sequencer instruction).

All three serialize identically in ``.qp`` files (``qblox.<op_name> <args>``);
the platform decides at execution time how to realize each one.

Usage (simplest — typed QProgram with Qblox)::

    from qprogram_qblox import QProgram

    qp = QProgram(label="example")
    qp.qblox.acquire("readout_q0", "weights")       # IDE autocomplete works
    qp.qblox.set_markers("drive_q0", "0001")         # IDE autocomplete works
    qp.qblox.set_acquisition_threshold("readout_q0", value=0.42)  # software-only

Usage (mixin — combine multiple vendors)::

    from qprogram_qblox import QbloxMixin
    from qprogram_qdac import QdacMixin
    from qprogram import QProgram as BaseQProgram

    class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
        pass

    qp = QProgram()
    qp.qblox.acquire(...)    # typed
    qp.qdac.play(...)        # typed

``.qp`` files can use::

    require qblox 0.1

    body:
      qblox.acquire "readout_q0" "weights"
      qblox.set_markers "drive_q0" "0001"
"""

from importlib.metadata import PackageNotFoundError, version

from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization._specs import make_measurement_op_parse
from qprogram.serialization.registry import register_vendor_operation, register_vendor_version

from qprogram_qblox.mixin import QbloxMixin
from qprogram_qblox.namespace import QbloxNamespace
from qprogram_qblox.operations import (
    Acquire,
    ActiveReset,
    SetAcquisitionThreshold,
    SetMarkers,
    SetTrigger,
    WaitTrigger,
)
from qprogram_qblox.profiles import QBLOX_DEFAULT_V1, _register as _register_qblox_profile

# Resolve our own package version once. This is the single source of truth
# for the qblox vendor protocol version: parsers will check that the file's
# `require qblox <major.minor>` is compatible with this number.
try:
    __version__ = version("qprogram-qblox")
except PackageNotFoundError:  # pragma: no cover — only when running from a source tree without metadata
    __version__ = "0.0.0"

# --- Step 1: Register the vendor namespace on base QProgram ---
# This makes program.qblox.<method>() work at runtime even without the mixin.
_BaseQProgram.register_vendor("qblox", QbloxNamespace)

# --- Step 2: Register the protocol version of this vendor extension ---
# The .qp parser uses this to validate `require qblox <x.y>` declarations.
register_vendor_version("qblox", __version__)

# --- Step 3: Register operations with the .qp serializer ---
# Operations cover the full spectrum a vendor can offer:
#   - simple 1-1 hardware ops (acquire, set_markers, set_trigger, wait_trigger),
#   - complex orchestrations (active_reset — measure + conditional reset),
#   - pure software ops (set_acquisition_threshold — QCoDeS at execution time).
# The .qp serializer treats them all uniformly; the platform decides at runtime
# how to lower each one onto its hardware.
register_vendor_operation("qblox", "acquire", Acquire, parse=make_measurement_op_parse(Acquire))
register_vendor_operation("qblox", "set_markers", SetMarkers)
register_vendor_operation("qblox", "set_trigger", SetTrigger)
register_vendor_operation("qblox", "wait_trigger", WaitTrigger)
register_vendor_operation("qblox", "active_reset", ActiveReset)
register_vendor_operation("qblox", "set_acquisition_threshold", SetAcquisitionThreshold)

# --- Step 4: Register the qblox capability profile bundle ---
# Vendor capability tokens are registered as a side effect of importing
# qprogram_qblox.profiles (above), so the profile's tokens validate.
_register_qblox_profile()


# --- Step 3: Pre-combined typed QProgram ---
class QProgram(QbloxMixin, _BaseQProgram):
    """QProgram with typed ``.qblox`` namespace.

    Identical to ``qprogram.QProgram`` but with IDE autocomplete for
    ``qp.qblox.acquire()``, ``qp.qblox.set_markers()``, etc.
    """

    pass


__all__ = [
    "QBLOX_DEFAULT_V1",
    "Acquire",
    "ActiveReset",
    "QProgram",
    "QbloxMixin",
    "QbloxNamespace",
    "SetAcquisitionThreshold",
    "SetMarkers",
    "SetTrigger",
    "WaitTrigger",
]
