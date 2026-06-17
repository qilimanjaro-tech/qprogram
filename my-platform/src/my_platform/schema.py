"""The RF-switch bus schema contributed by the MyPlatform package.

This module defines a **new** :class:`~qprogram.buses.BusSchema` for an RF-switch matrix. MyPlatform
drives a flux-tunable transmon *plus* this RF switch, and it builds that combined topology with the
core schema-composition operator — ``BusSchema.flux_tunable_transmon() + RFSwitchSchema()`` (see
:class:`~my_platform.MyPlatform`). No dedicated combined class is needed: ``+`` unions the element
families at runtime (``q`` from the transmon preset, ``switch`` from here).

It is the schema-side companion to the MyPlatform *vendor* extension (:mod:`my_platform.operations`,
:mod:`my_platform.namespace`): the vendor's :meth:`~my_platform.namespace.MyPlatformNamespace.set_rf_switch`
op targets the ``switch`` buses declared here, and its
:meth:`~my_platform.namespace.MyPlatformNamespace.set_crosstalk` op targets the ``flux`` buses the
flux-tunable-transmon preset contributes.

This demonstrates two things about the schema layer:

* A downstream package can ship a brand-new typed schema by following the documented
  "custom typed schema" recipe — subclass :class:`~qprogram.buses.BusSchema`, set
  :attr:`~qprogram.buses.BusSchema.KIND`, register elements with
  :meth:`~qprogram.buses.BusSchema.add_element`, and expose one ``@property`` accessor per element
  for IDE autocomplete. No core change is required.
* Schemas **compose** with ``+`` / :meth:`~qprogram.buses.BusSchema.combine`. Because a schema
  carries a single :class:`~qprogram.buses.BusNaming`, both element families share the default
  ``{element}{index}/{kind}`` pattern, so qubit buses read ``q0/drive`` and switch buses read
  ``switch0/rf``. (The combined result is a dynamic :class:`~qprogram.buses.BusSchema`; for a
  statically-typed combination you would instead subclass via multiple inheritance.)

The RF switch itself is an (imaginary) microwave routing matrix: each ``switch`` element is one
single-channel control line whose :meth:`~my_platform.namespace.MyPlatformNamespace.set_rf_switch`
op selects which output port the signal is routed to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.buses import (
    BusSchema,
    ChannelType,
    _TypedElementAccessor,
    _TypedElementFactory,
)

if TYPE_CHECKING:
    from qprogram.buses import BusNaming, BusRef


#: The bus kinds a single ``switch`` element exposes. An RF switch is a one-line control device, so
#: it has exactly one single-channel, non-acquiring bus.
_SWITCH_BUSES: dict[str, tuple[ChannelType, bool]] = {"rf": ("single", False)}


class RFSwitchBuses(_TypedElementAccessor):
    """Typed accessor for the buses of one RF switch (``rf``).

    ``_ref(kind, channel, *, acquires=False)`` (inherited from
    :class:`~qprogram.buses._TypedElementAccessor`) builds the :class:`~qprogram.buses.BusRef` and
    tags it with the producing schema instance, so a switch bus from one schema can't be smuggled
    into a program built with another.
    """

    @property
    def rf(self) -> BusRef:
        """Return the RF control line of this switch (single channel, no ADC)."""
        return self._ref("rf", "single")


class RFSwitchFactory(_TypedElementFactory):
    """Subscriptable factory returning :class:`RFSwitchBuses` — ``schema.switch[0]``."""

    _accessor_cls = RFSwitchBuses

    def __getitem__(self, index: int) -> RFSwitchBuses:
        return RFSwitchBuses(self._element, index, self._naming, self._parent)


class RFSwitchSchema(BusSchema):
    """A standalone schema for an RF-switch matrix — exposes a typed ``switch`` accessor.

    Each ``switch`` element is one single-channel ``rf`` control line. With the default naming
    convention the buses read ``switch0/rf``, ``switch1/rf``, ... (the ``switch[i]`` accessor is how
    you reach them):

        >>> schema = RFSwitchSchema()
        >>> str(schema.switch[0].rf)
        'switch0/rf'

    Use this directly for a switch-only program, or combine it with another schema for a device that
    has both — e.g. ``BusSchema.flux_tunable_transmon() + RFSwitchSchema()`` — since a
    :class:`~qprogram.QProgram` holds at most one schema. That is exactly what
    :class:`~my_platform.MyPlatform` does.
    """

    KIND = "rf_switch"

    def __init__(self, naming: BusNaming | None = None) -> None:
        super().__init__(naming=naming)
        self.add_element("switch", dict(_SWITCH_BUSES))

    @property
    def switch(self) -> RFSwitchFactory:
        """Return the subscriptable RF-switch accessor (``schema.switch[i].rf``)."""
        return RFSwitchFactory("switch", self._naming, self)


__all__ = [
    "RFSwitchBuses",
    "RFSwitchFactory",
    "RFSwitchSchema",
]
