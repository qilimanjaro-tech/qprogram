"""QDAC vendor extensions for QProgram.

This package provides:

1. **Runtime registration** — importing this package registers the ``qdac`` vendor namespace on
   :class:`~qprogram.QProgram`, registers every QDAC operation with the ``.qp`` serializer, and
   registers the ``qdac-default-v1`` capability profile.

2. **Typed mixin** — :class:`QdacMixin` adds a typed ``.qdac`` property for IDE autocomplete.

3. **Pre-combined QProgram** — :class:`QProgram` from this package has ``.qdac`` typed out of the
   box.

QDAC is a slow high-precision DAC commonly used for flux biasing on transmon platforms. Its
operations span:

- a per-bus waveform-engine sequencer op (:meth:`QdacNamespace.play`) that uploads an envelope
  to one QDAC channel's waveform engine;
- per-bus trigger-network operations (:meth:`QdacNamespace.set_trigger`,
  :meth:`QdacNamespace.wait_trigger`); and
- a per-bus DC-offset op (:meth:`QdacNamespace.set_offset`) whose variable form lifts the
  enclosing loop to software-dispatched execution.

Usage (simplest — typed QProgram with QDAC)::

    from qprogram_qdac import QProgram
    from qprogram.waveforms import Ramp

    qp = QProgram(label="flux-sweep")
    qp.qdac.set_offset("flux_q0", 0.42)               # IDE autocomplete
    qp.qdac.set_trigger("flux_q0", 50, position="start", outputs={1, 2})
    qp.qdac.play("flux_q0", Ramp(0.0, 1.0, 1000), dwell=10)
    qp.qdac.wait_trigger("flux_q0", port=3)

Usage (mixin — combine multiple vendors)::

    from qprogram_qblox import QbloxMixin
    from qprogram_qdac import QdacMixin
    from qprogram import QProgram as BaseQProgram

    class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
        pass

``.qp`` files using qdac ops carry::

    require qdac 0.1

    body:
      qdac.set_offset "flux_q0" 0.42
      qdac.play "flux_q0" Ramp(from_amplitude=0.0, to_amplitude=1.0, duration=1000) dwell=10
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization.registry import (
    register_vendor_operation,
    register_vendor_version,
)

from qprogram_qdac.mixin import QdacMixin
from qprogram_qdac.namespace import QdacNamespace
from qprogram_qdac.operations import (
    Play,
    SetOffset,
    SetTrigger,
    WaitTrigger,
)
from qprogram_qdac.profiles import QDAC_DEFAULT_V1, _register as _register_qdac_profile

if TYPE_CHECKING:
    pass

# Resolve our own package version once. This is the single source of truth for the qdac vendor
# protocol version: parsers will check that the file's ``require qdac <major.minor>`` is
# compatible with this number.
try:
    __version__ = version("qprogram-qdac")
except PackageNotFoundError:  # pragma: no cover — only when running from a source tree without metadata
    __version__ = "0.0.0"

# --- Step 1: Register the vendor namespace on base QProgram ---
# This makes program.qdac.<method>() work at runtime even without the mixin.
_BaseQProgram.register_vendor("qdac", QdacNamespace)

# --- Step 2: Register the protocol version of this vendor extension ---
# The .qp parser uses this to validate ``require qdac <x.y>`` declarations.
register_vendor_version("qdac", __version__)


# --- Step 3: Register operations with the .qp serializer ---
# Every op uses the default signature-driven serialise/parse pair. SetTrigger's
# ``outputs: tuple[int, ...]`` serialises through the writer's generic sequence branch
# (``outputs=[1, 2, 3]``); the parser's bracket-aware tokenizer keeps the literal whole and
# ``_normalize_outputs`` converts the reloaded list back to the canonical sorted tuple.

register_vendor_operation("qdac", "wait_trigger", WaitTrigger)
register_vendor_operation("qdac", "set_trigger", SetTrigger)
register_vendor_operation("qdac", "set_offset", SetOffset)
register_vendor_operation("qdac", "play", Play)


# --- Step 4: Register the qdac capability profile bundle ---
# Vendor capability tokens are registered as a side effect of importing
# qprogram_qdac.profiles (above), so the profile's tokens validate.
_register_qdac_profile()


# --- Step 5: Pre-combined typed QProgram ---
class QProgram(QdacMixin, _BaseQProgram):
    """:class:`~qprogram.QProgram` pre-combined with :class:`QdacMixin`.

    Identical to :class:`qprogram.QProgram` but with IDE autocomplete for ``qp.qdac.*``.
    """


__all__ = [
    "QDAC_DEFAULT_V1",
    "Play",
    "QProgram",
    "QdacMixin",
    "QdacNamespace",
    "SetOffset",
    "SetTrigger",
    "WaitTrigger",
]
