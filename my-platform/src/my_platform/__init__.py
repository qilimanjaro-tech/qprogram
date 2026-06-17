"""MyPlatform — an imaginary QProgram platform *and* vendor extension.

This package wears two hats:

* a **platform** — :class:`MyPlatform` declares per-bus capabilities and validates / explains /
  executes programs against them (the original worked example of the capability layer); and
* a **vendor** — it ships the ``myplatform`` namespace (``program.myplatform.*``), a couple of
  vendor operations, a new RF-switch bus schema, and its own capability profiles/tokens.

Importing this package activates everything as an import side effect, so ``MyPlatform`` and the
``myplatform`` vendor are ready to use immediately::

    from my_platform import MyPlatform

    platform = MyPlatform()
    schema = platform.get_bus_schema()         # flux-tunable transmon + RF switch, combined

    prog = ...                                  # build a program against `schema`
    prog.myplatform.set_crosstalk(schema.q[0].flux, [[1.0, 0.1], [0.1, 1.0]])
    prog.myplatform.set_rf_switch(schema.switch[0].rf, 2)

    diagnostics = platform.validate(prog)       # check against MyPlatform's capabilities
    print(platform.explain(prog))               # render the per-bus execution plan
    result = platform.execute(prog)             # validate, then simulate

Activation order matters: the qblox + qdac vendor packages are imported first (registering their
``qblox-default-v1`` / ``qdac-default-v1`` profiles), then MyPlatform registers its own vendor
namespace/version/operations and its profiles — some of which ``extend`` the vendor ones.

Because MyPlatform is now also a vendor, it declares a ``[project.entry-points."qprogram.vendors"]``
table (``myplatform = "my_platform"``) so a ``.qp`` file carrying ``require myplatform <x.y>`` can
auto-activate this package on :func:`~qprogram.loads`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# Step 1: activate the vendor back-ends MyPlatform builds on. Importing each package registers its
# vendor namespace, version, operations and capability profile as import side effects.
import qprogram_qblox  # noqa: F401  (imported for its registration side effects)
import qprogram_qdac  # noqa: F401  (imported for its registration side effects)
from qprogram.qprogram import QProgram as _BaseQProgram
from qprogram.serialization.registry import (
    register_vendor_operation,
    register_vendor_version,
)
from qprogram_qblox import QbloxMixin
from qprogram_qdac import QdacMixin

from my_platform.mixin import MyPlatformMixin
from my_platform.namespace import MyPlatformNamespace
from my_platform.operations import SetCrosstalk, SetRFSwitch, set_crosstalk_serialize
from my_platform.platform import MyPlatform
from my_platform.profiles import (
    MYPLATFORM_FLUX_V1,
    MYPLATFORM_READOUT_V1,
    MYPLATFORM_RFSWITCH_V1,
    register_myplatform_profiles,
)
from my_platform.schema import RFSwitchSchema

# Resolve our own package version once — the single source of truth for the myplatform vendor
# protocol version. Parsers check that a file's ``require myplatform <major.minor>`` is compatible.
try:
    __version__ = version("my-platform")
except PackageNotFoundError:  # pragma: no cover — only when running from a source tree without metadata
    __version__ = "0.0.0"

# Step 2: register the myplatform vendor namespace on base QProgram so ``program.myplatform.*`` works
# at runtime on any QProgram instance, with or without the typed mixin.
_BaseQProgram.register_vendor("myplatform", MyPlatformNamespace)

# Step 3: register the vendor protocol version (validates ``require myplatform <x.y>`` on parse).
register_vendor_version("myplatform", __version__)

# Step 4: register the vendor operations with the .qp serializer. ``set_rf_switch`` uses the default
# signature-driven serialize/parse pair; ``set_crosstalk`` needs a custom serializer (its matrix is
# 2-D, which the generic value serializer doesn't emit) but the default parser reverses it — the
# matrix arrives as a nested list and the constructor coerces it back to an ndarray.
register_vendor_operation("myplatform", "set_rf_switch", SetRFSwitch)
register_vendor_operation("myplatform", "set_crosstalk", SetCrosstalk, serialize=set_crosstalk_serialize)

# Step 5: register MyPlatform's profiles (they reference the vendor tokens registered when
# my_platform.profiles was imported above, and ``extend`` the qblox/qdac profiles).
register_myplatform_profiles()


# Step 6: pre-combined typed QProgram — qblox + qdac + myplatform namespaces all typed for IDE use.
class QProgram(QbloxMixin, QdacMixin, MyPlatformMixin, _BaseQProgram):
    """:class:`~qprogram.QProgram` pre-combined with the qblox, qdac, and myplatform mixins.

    Identical to :class:`qprogram.QProgram` but with IDE autocomplete for ``qp.qblox.*``,
    ``qp.qdac.*``, and ``qp.myplatform.*``.
    """


__all__ = [
    "MYPLATFORM_FLUX_V1",
    "MYPLATFORM_READOUT_V1",
    "MYPLATFORM_RFSWITCH_V1",
    "MyPlatform",
    "MyPlatformMixin",
    "MyPlatformNamespace",
    "QProgram",
    "RFSwitchSchema",
    "SetCrosstalk",
    "SetRFSwitch",
    "register_myplatform_profiles",
]
