"""MyPlatform — an imaginary QProgram platform with per-bus capabilities.

Importing this package activates the two vendor back-ends it builds on (qblox + qdac)
and registers MyPlatform's ad-hoc capability profiles, so ``MyPlatform`` is ready to use
immediately::

    from my_platform import MyPlatform

    platform = MyPlatform()
    diagnostics = platform.validate(program)   # check against MyPlatform's capabilities
    print(platform.explain(program))           # render the per-bus execution plan
    result = platform.execute(program)         # validate, then simulate

Activation order matters: the vendor packages must be imported (registering their
``qblox-default-v1`` / ``qdac-default-v1`` profiles) before MyPlatform's profiles —
which ``extend`` them — are used.
"""

from __future__ import annotations

# Step 1: activate the vendor back-ends. Importing each package registers its vendor
# namespace, version, operations and capability profile as import side effects.
import qprogram_qblox  # noqa: F401  (imported for its registration side effects)
import qprogram_qdac  # noqa: F401  (imported for its registration side effects)

# Step 2: register MyPlatform's ad-hoc profiles (they `extend` the vendor profiles above).
from my_platform.profiles import (
    MYPLATFORM_FLUX_V1,
    MYPLATFORM_READOUT_V1,
    register_myplatform_profiles,
)
from my_platform.platform import MyPlatform

register_myplatform_profiles()

__all__ = [
    "MYPLATFORM_FLUX_V1",
    "MYPLATFORM_READOUT_V1",
    "MyPlatform",
    "register_myplatform_profiles",
]
