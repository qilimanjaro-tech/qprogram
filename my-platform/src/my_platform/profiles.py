"""Ad-hoc capability profiles for the (imaginary) MyPlatform QPU.

A *profile* is a named, reusable bundle of capability tokens, numeric limits and
predicates (see ``qprogram.protocol.Profile``). A *platform* decides which profile
fills each ``(bus, domain)`` slot of its :class:`~qprogram.protocol.PlatformCapabilities`.

MyPlatform composes the two real vendor profiles rather than inventing tokens from
scratch:

* **drive** reuses ``qblox-default-v1`` verbatim (no ad-hoc profile needed),
* **readout** extends ``qblox-default-v1`` to tighten the minimum ``Wait`` duration,
* **flux** extends ``qdac-default-v1`` and adds a predicate that enforces a minimum dwell.

These profiles together exercise all three capability axes: inherited **tokens**, an
overridden **limit** (readout), and a platform-authored **predicate** (flux).

``extends=`` is resolved lazily (when ``CompilerCapabilities.from_profile`` walks the
chain), so the parent profiles only need to exist by the time the platform's
``capabilities`` property is read — not at *this* module's import time. We still import
the vendor packages in ``__init__`` before registering, to keep activation explicit.

These profiles are registered with the global ``PROFILE_REGISTRY`` as an import side
effect of ``import my_platform`` (``__init__`` calls :func:`register_myplatform_profiles`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.protocol import Diagnostic, Profile, register_profile
from qprogram_qdac import Play as QdacPlay

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.blocks import Block
    from qprogram.operations import Operation
    from qprogram.protocol import DomainConstraint, ValidationContext

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
                f"qdac.play dwell={node.dwell} ns is below MyPlatform's flux DAC minimum "
                f"of {MIN_FLUX_DWELL_NS} ns"
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
    capabilities=frozenset(),
    limits={"min_dwell_ns": MIN_FLUX_DWELL_NS},
    predicates=(_flux_dwell_below_minimum,),
)

_MYPLATFORM_PROFILES = (MYPLATFORM_READOUT_V1, MYPLATFORM_FLUX_V1)


def register_myplatform_profiles() -> None:
    """Register MyPlatform's ad-hoc profiles with the global registry.

    Idempotent: ``register_profile`` accepts the same ``Profile`` object repeatedly and
    only raises if a *different* profile reuses an already-taken name.
    """
    for profile in _MYPLATFORM_PROFILES:
        register_profile(profile)
