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

- a single bus-less waveform-engine sequencer op (:meth:`QdacNamespace.play`), since QDAC
  resolves the target channel from the surrounding context;
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
    qp.qdac.play(Ramp(0.0, 1.0, 1000), dwell=10)
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
      qdac.play Ramp(start=0.0, stop=1.0, duration=1000) dwell=10
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization.registry import (
    get_operation_spec_by_class,
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
# Most ops use the default signature-driven serialise/parse pair, but SetTrigger needs a
# custom writer for its ``outputs: tuple[int, ...]`` attribute — Python's ``str(tuple)`` yields
# ``(1, 2, 3)`` which the parser can't decode. We emit it as a ``[1, 2, 3]`` bracket-literal
# instead, which the parser already understands (it returns a numpy array, which the SetTrigger
# constructor's ``_normalize_outputs`` happily accepts).


def _serialize_qdac_set_trigger(op: SetTrigger, ctx: Any) -> str:  # noqa: ANN401
    """Custom serializer for :class:`SetTrigger`.

    Emits ``outputs`` as a ``[1, 2, 3]`` literal rather than the tuple's default ``str()`` form.
    Omits ``position`` and ``outputs`` when they match their defaults, matching the
    default-serializer convention.

    Args:
        op: SetTrigger instance to serialise.
        ctx: Writer instance (exposes ``serialize_bus`` etc).

    Returns:
        The serialised body of the line (the leading ``qdac.set_trigger`` keyword is prepended
        below).
    """
    spec = get_operation_spec_by_class(type(op))
    name = spec.qualified_name if spec is not None else "qdac.set_trigger"
    parts: list[str] = [ctx.serialize_bus(op.bus), ctx.serialize_value(op.duration)]
    if op.position != "start":
        parts.append(f"position={ctx.serialize_value(op.position)}")
    if op.outputs:
        # Comma-joined without spaces — the .qp parser's tokenizer splits on whitespace, so any
        # spaces inside the bracket would break the list into separate tokens. Numpy-array
        # decoding via the parser's ``[...]`` branch happily accepts the compact form.
        outputs_str = ",".join(str(i) for i in op.outputs)
        parts.append(f"outputs=[{outputs_str}]")
    return " ".join([name, *parts])


register_vendor_operation("qdac", "wait_trigger", WaitTrigger)
register_vendor_operation("qdac", "set_trigger", SetTrigger, serialize=_serialize_qdac_set_trigger)
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
