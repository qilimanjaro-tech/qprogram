"""Ad-hoc capability profiles for the (imaginary) MyPlatform QPU.

A *profile* is a named, reusable bundle of capability tokens, numeric limits and
predicates (see ``qprogram.protocol.Profile``). A *platform* decides which profile
fills each ``(bus, domain)`` slot of its :class:`~qprogram.protocol.PlatformCapabilities`.

MyPlatform composes the two real vendor profiles rather than inventing tokens from
scratch, and adds its own vendor op tokens where it owns the hardware:

* **drive** reuses ``qblox-default-v1`` verbatim (no ad-hoc profile needed),
* **readout** extends ``qblox-default-v1`` to tighten the minimum ``Wait`` duration,
* **flux** extends ``qdac-default-v1``, adds a predicate that enforces a minimum dwell, and
  publishes MyPlatform's own ``vendor.myplatform.set_crosstalk`` op,
* **rf switch** is a fresh profile (``extends=None``) carrying only MyPlatform's own
  ``vendor.myplatform.set_rf_switch`` op — the bus type this package introduces.

These profiles together exercise all three capability axes: inherited **tokens** plus
MyPlatform's own vendor tokens, an overridden **limit** (readout), and a platform-authored
**predicate** (flux).

``extends=`` is resolved lazily (when ``CompilerCapabilities.from_profile`` walks the
chain), so the parent profiles only need to exist by the time the platform's
``capabilities`` property is read — not at *this* module's import time. We still import
the vendor packages in ``__init__`` before registering, to keep activation explicit.

These profiles are registered with the global ``PROFILE_REGISTRY`` as an import side
effect of ``import my_platform`` (``__init__`` calls :func:`register_myplatform_profiles`).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from qprogram.protocol import (
    Diagnostic,
    Profile,
    register_capability_tokens,
    register_profile,
)
from qprogram_qdac import Play as QdacPlay

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.blocks import Block
    from qprogram.operations import Operation
    from qprogram.protocol import DomainConstraint, ValidationContext


def _vendor_version_tuple() -> tuple[int, int, int]:
    """Read the installed package version and parse it into a ``(major, minor, patch)`` tuple.

    Single source of truth: ``pyproject.toml``'s ``version`` (the same value ``__init__`` registers
    via ``register_vendor_version``), so the profiles' informational ``vendor_versions`` can never
    drift from the registered protocol version.
    """
    try:
        parts = version("my-platform").split(".")
    except PackageNotFoundError:  # pragma: no cover — running from a source tree without metadata
        return (0, 1, 0)
    nums = [int(p) for p in parts[:3] if p.isdigit()]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


#: MyPlatform vendor protocol version, mirrored into the profiles' informational ``vendor_versions``
#: and the ``require myplatform <x.y>`` line of any ``.qp`` file that uses a MyPlatform op. Derived
#: from package metadata so it tracks ``pyproject.toml`` automatically.
MYPLATFORM_VENDOR_VERSION: tuple[int, int, int] = _vendor_version_tuple()

# Register MyPlatform's vendor capability tokens *before* constructing any profile that names them —
# Profile.__post_init__ validates that every listed token is in CAPABILITY_REGISTRY, so registration
# must come first (the same ordering qprogram_qdac.profiles relies on).
register_capability_tokens(
    "vendor.myplatform.set_crosstalk",
    "vendor.myplatform.set_rf_switch",
)

#: MyPlatform's flux DAC is slow; a single point must dwell at least this long. Shared
#: between the declarative ``limits`` entry (for tooling/introspection) and the predicate
#: that actually enforces it, so the two cannot drift apart.
MIN_FLUX_DWELL_NS = 200


def _flux_dwell_below_minimum(
    node: Operation | Block,
    ctx: ValidationContext,  # noqa: ARG001 — predicate signature is (node, ctx)
) -> Iterator[Diagnostic | DomainConstraint]:
    """Hard-error a ``qdac.play`` on the flux bus whose dwell is below the DAC minimum.

    This is the kind of per-platform rule the core validator deliberately leaves to
    profiles: the ``qdac-default-v1`` profile carries a ``min_dwell_ns`` value but ships no
    enforcement, noting it "is here for platforms that wire in their own predicate" — which
    is exactly what MyPlatform does here.
    """
    if isinstance(node, QdacPlay) and isinstance(node.dwell, int) and node.dwell < MIN_FLUX_DWELL_NS:
        yield Diagnostic(
            severity="error",
            code="myplatform.flux-dwell-too-short",
            message=(
                f"qdac.play dwell={node.dwell} ns is below MyPlatform's flux DAC minimum of {MIN_FLUX_DWELL_NS} ns"
            ),
            node=node,
        )


#: Readout chain. Same qblox real-time generator as drive, but MyPlatform's sequencer
#: enforces a longer minimum ``Wait`` duration on readout buses than the bare qblox
#: default (4 ns -> 16 ns). ``capabilities=frozenset()`` adds nothing — every qblox bus
#: token is inherited via ``extends``; only the limit is specialised (child limits override
#: the parent's). The core validator enforces ``min_wait_duration_ns`` directly.
MYPLATFORM_READOUT_V1 = Profile(
    name="myplatform-readout-v1",
    version=(1, 0, 0),
    extends="qblox-default-v1",
    capabilities=frozenset(),
    limits={"min_wait_duration_ns": 16},
)

#: Flux bias line. A slow, high-precision DAC (qdac) with no FPGA. Inherits the qdac
#: vendor ops and single-channel waveforms; MyPlatform widens the advisory ``min_dwell_ns``
#: to 200 ns AND ships :func:`_flux_dwell_below_minimum` to actually enforce it (the core
#: validator has no built-in dwell check).
MYPLATFORM_FLUX_V1 = Profile(
    name="myplatform-flux-v1",
    version=(1, 0, 0),
    extends="qdac-default-v1",
    # On top of the inherited qdac waveform-engine + offset/trigger ops, MyPlatform publishes its own
    # vendor op on flux lines: ``set_crosstalk``. Publishing the token here (and *only* on flux
    # profiles) is exactly what makes ``myplatform.set_crosstalk`` legal on a flux bus and a
    # ``missing-capability`` error anywhere else — schema-agnostically. A fluxonium platform reusing
    # this profile on its ``flux_x`` / ``flux_z`` slots gets the same op for free.
    capabilities=frozenset({"vendor.myplatform.set_crosstalk"}),
    limits={"min_dwell_ns": MIN_FLUX_DWELL_NS},
    predicates=(_flux_dwell_below_minimum,),
    vendor_versions={"myplatform": MYPLATFORM_VENDOR_VERSION},
)

#: RF-switch control line. A fast (real-time-capable) microwave routing matrix, so MyPlatform wires
#: its bus into BOTH execution domains (the platform fills ``hw`` *and* ``sw`` from this profile) — a
#: loop that sweeps the switch ``channel`` can stay real-time hardware. Alongside its own
#: ``vendor.myplatform.set_rf_switch`` op, the profile carries the core timing ops ``op.sync`` and
#: ``op.wait`` so the switch line can be aligned and held against the rest of the real-time pulse
#: program — without them a ``sync()`` spanning the switch bus (including a broadcast ``sync()``)
#: would fail to validate, which would make the "real-time capable" wiring above useless in practice.
#: This is a deliberate per-slot choice: contrast the flux bus, which carries *only* qdac ops because
#: the slow DAC isn't part of the real-time timing fabric. ``extends=None`` — nothing else inherited.
MYPLATFORM_RFSWITCH_V1 = Profile(
    name="myplatform-rfswitch-v1",
    version=(1, 0, 0),
    extends=None,
    capabilities=frozenset({"vendor.myplatform.set_rf_switch", "op.sync", "op.wait"}),
    vendor_versions={"myplatform": MYPLATFORM_VENDOR_VERSION},
)

_MYPLATFORM_PROFILES = (MYPLATFORM_READOUT_V1, MYPLATFORM_FLUX_V1, MYPLATFORM_RFSWITCH_V1)


def register_myplatform_profiles() -> None:
    """Register MyPlatform's ad-hoc profiles with the global registry.

    Idempotent: ``register_profile`` accepts the same ``Profile`` object repeatedly and
    only raises if a *different* profile reuses an already-taken name.
    """
    for profile in _MYPLATFORM_PROFILES:
        register_profile(profile)
