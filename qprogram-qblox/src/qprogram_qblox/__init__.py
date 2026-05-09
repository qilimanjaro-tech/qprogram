"""Qblox vendor extensions for QProgram.

This package provides:

1. **Runtime registration** — importing this package registers the ``qblox`` vendor
   namespace on QProgram and all Qblox operations with the ``.qp`` serializer.

2. **Typed mixin** — ``QbloxMixin`` adds a typed ``.qblox`` property for IDE autocomplete.

3. **Pre-combined QProgram** — ``QProgram`` from this package has ``.qblox`` typed.

Usage (simplest — typed QProgram with Qblox)::

    from qprogram_qblox import QProgram

    qp = QProgram(label="example")
    qp.qblox.acquire("readout_q0", "weights")       # IDE autocomplete works
    qp.qblox.set_markers("drive_q0", "0001")         # IDE autocomplete works

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
from qprogram.serialization.registry import register_vendor_operation, register_vendor_version

from qprogram_qblox.mixin import QbloxMixin
from qprogram_qblox.namespace import QbloxNamespace
from qprogram_qblox.operations import Acquire, MeasureReset, SetMarkers, SetTrigger, WaitTrigger

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
register_vendor_operation("qblox", "acquire", Acquire)
register_vendor_operation("qblox", "set_markers", SetMarkers)
register_vendor_operation("qblox", "set_trigger", SetTrigger)
register_vendor_operation("qblox", "wait_trigger", WaitTrigger)
register_vendor_operation("qblox", "measure_reset", MeasureReset)


# --- Step 3: Pre-combined typed QProgram ---
class QProgram(QbloxMixin, _BaseQProgram):
    """QProgram with typed ``.qblox`` namespace.

    Identical to ``qprogram.QProgram`` but with IDE autocomplete for
    ``qp.qblox.acquire()``, ``qp.qblox.set_markers()``, etc.
    """

    pass


__all__ = [
    "Acquire",
    "MeasureReset",
    "QProgram",
    "QbloxMixin",
    "QbloxNamespace",
    "SetMarkers",
    "SetTrigger",
    "WaitTrigger",
]
