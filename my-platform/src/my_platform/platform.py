"""The (imaginary) MyPlatform QPU — a worked example of per-bus capabilities.

MyPlatform is a small flux-tunable-transmon device — plus an RF-switch matrix — assembled from
two *real* vendor back-ends this monorepo already ships, and MyPlatform's own vendor ops:

============  ==========================  ===================  =======================
bus kind      back-end                    domains (hw / sw)    capability profile
============  ==========================  ===================  =======================
``drive``     qblox real-time generator   hw + sw              ``qblox-default-v1``
``readout``   qblox real-time generator   hw + sw              ``myplatform-readout-v1``
``flux``      qdac slow DAC (no FPGA)      sw only              ``myplatform-flux-v1``
``rf``        MyPlatform RF switch        hw + sw              ``myplatform-rfswitch-v1``
============  ==========================  ===================  =======================

The interesting member is :pyattr:`MyPlatform.capabilities`. It maps each
``(element, bus_kind)`` selector to a *different* ``BusCapabilities``, so the validator
and planner treat the same control-flow construct differently depending on which bus it
touches: a drive/readout/switch sweep stays real-time hardware, while a flux sweep is forced to
software dispatch (the qdac back-end has no FPGA).

MyPlatform also publishes two vendor ops of its own: ``set_crosstalk`` on the flux buses and
``set_rf_switch`` on the switch buses (see :mod:`my_platform.operations`).

Implementing :class:`~qprogram.platform.PlatformProtocol` requires six members
(``get_bus_schema``, ``get_buses``, ``get_parameters``, ``get_global_parameters``, the
``capabilities`` property and ``execute``). ``validate`` / ``plan`` / ``explain`` /
``stream`` come with working default bodies on the base class and are inherited unchanged.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from qprogram.buses import BusSchema
from qprogram.errors import UnsupportedOperationError
from qprogram.executor import ExecutionWarning, ReferencePlatform
from qprogram.platform import PlatformProtocol
from qprogram.protocol import BusCapabilities, CompilerCapabilities, PlatformCapabilities
from qprogram.validation import validate

from my_platform.schema import RFSwitchSchema

if TYPE_CHECKING:
    from qprogram.executor import MeasurementModel
    from qprogram.qprogram import QProgram
    from qprogram.result import QProgramResult


class MyPlatform(PlatformProtocol):
    """An imaginary flux-tunable-transmon QPU with heterogeneous per-bus capabilities.

    Args:
        n_qubits: Number of qubits the device exposes (drive/readout/flux per qubit).
        n_switches: Number of RF switches the device exposes (one ``rf`` control line each).
        model: Optional measurement model handed to the reference simulator that backs
            :meth:`execute`. Defaults to the seed-deterministic mock model.
        parameters: Optional platform parameter values (e.g. local-oscillator frequencies)
            seeded into the simulator's environment.
        capabilities: Optional capability grant to enforce. When given, it *overrides* the
            default per-bus descriptor computed by :attr:`capabilities` — same hardware, a
            different set of permissions. This is how one platform serves several clients: hand
            each a different :class:`~qprogram.PlatformCapabilities` (e.g. a full grant vs. a
            core-only grant that withholds every ``vendor.*`` token). When ``None`` (the default),
            the platform reports its own full, per-bus grant.
    """

    def __init__(
        self,
        n_qubits: int = 2,
        n_switches: int = 2,
        model: MeasurementModel | None = None,
        parameters: dict[str, float] | None = None,
        capabilities: PlatformCapabilities | None = None,
    ) -> None:
        # Build the device topology by *composing* schemas: the core flux-tunable-transmon preset
        # (q.drive / q.readout / q.flux) unioned with this package's RF-switch schema (switch.rf),
        # via the BusSchema ``+`` operator. No dedicated combined class needed.
        self._schema = BusSchema.flux_tunable_transmon() + RFSwitchSchema()
        self._n_qubits = n_qubits
        self._n_switches = n_switches
        self._model = model
        self.parameters = dict(parameters or {})
        # An injected grant (per-client permissions), or None to report the default full grant.
        self._capabilities = capabilities

    # ------------------------------------------------------------------ topology

    def get_bus_schema(self) -> BusSchema:
        """The chip topology: a flux-tunable transmon (drive + readout + flux per qubit) combined
        with an RF-switch matrix (one ``rf`` control line per switch)."""
        return self._schema

    def get_buses(self) -> list[str]:
        """Every addressable bus name, e.g. ``['q0/drive', 'q0/readout', 'q0/flux', ..., 'switch0/rf']``."""
        buses: list[str] = []
        for i in range(self._n_qubits):
            qubit = self._schema.q[i]
            buses += [str(qubit.drive), str(qubit.readout), str(qubit.flux)]
        buses += [str(self._schema.switch[i].rf) for i in range(self._n_switches)]
        return buses

    def get_parameters(self, bus: str) -> list[str]:
        """Tunable parameters exposed on a given bus (illustrative, MyPlatform-specific)."""
        if bus.endswith("drive"):
            return ["lo_frequency", "gain"]
        if bus.endswith("readout"):
            return ["lo_frequency", "integration_length", "threshold"]
        if bus.endswith("flux"):
            return ["offset", "dwell"]
        if bus.endswith("rf"):
            return ["active_channel", "insertion_loss_db"]
        return []

    def get_global_parameters(self) -> list[str]:
        """Device-wide parameters not attached to any single bus."""
        return ["repetition_duration_ns", "active_reset"]

    # ---------------------------------------------------------- capabilities (★)

    @property
    def capabilities(self) -> PlatformCapabilities:
        """The per-bus capability descriptor — the whole point of this example.

        If a grant was injected at construction (``MyPlatform(capabilities=...)``), it is
        returned verbatim — that is the seam that lets one platform enforce different
        per-client permissions. Otherwise the default full grant below is recomputed on each
        access (like ``ReferencePlatform``) so vendor tokens registered after construction are
        still picked up.
        """
        if self._capabilities is not None:
            return self._capabilities

        # Drive: a qblox real-time waveform generator. Use the vendor profile verbatim;
        # it carries the core bus ops (play/measure/wait/sync/set_*), the qblox waveforms
        # and the vendor.qblox.* ops. Wire it into BOTH domains — qblox can run an op in
        # real time (hw) or step it from software (sw).
        drive = CompilerCapabilities.from_profile("qblox-default-v1")

        # Readout: same qblox generator, but tightened via an ad-hoc profile that raises
        # the minimum `Wait` duration on readout buses (4 ns -> 16 ns). Identical token
        # set, one specialised limit (enforced by the core validator).
        readout = CompilerCapabilities.from_profile("myplatform-readout-v1")

        # Flux: a slow qdac DAC. It inherits the qdac vendor ops + single-channel
        # waveforms via an ad-hoc profile, plus a platform-authored predicate enforcing a
        # minimum dwell, and MyPlatform's own ``set_crosstalk`` op. Critically it has NO
        # hardware engine (``hw=None``) — there is no FPGA — so every flux op, and any loop
        # that sweeps a flux value, is dispatched from software.
        flux = CompilerCapabilities.from_profile("myplatform-flux-v1")

        # RF switch: a fast microwave routing matrix MyPlatform owns. It carries MyPlatform's
        # ``set_rf_switch`` op plus the core timing ops (sync/wait), and IS real-time capable, so it
        # fills BOTH domains — a swept-channel loop on a switch bus can stay real-time hardware and
        # be aligned with the pulse program.
        rf_switch = CompilerCapabilities.from_profile("myplatform-rfswitch-v1")

        # Platform slot: core blocks / sweeps / expressions / bus-less ops. This is where
        # `for_loop`, `average`, `if_`, expression nodes and `set_parameter` are checked,
        # regardless of which bus an op touches.
        base = CompilerCapabilities.from_profile("qprogram-base-v1")

        return PlatformCapabilities(
            bus={
                ("q", "drive"): BusCapabilities(hw=drive, sw=drive),
                ("q", "readout"): BusCapabilities(hw=readout, sw=readout),
                ("q", "flux"): BusCapabilities(hw=None, sw=flux),
                ("switch", "rf"): BusCapabilities(hw=rf_switch, sw=rf_switch),
            },
            platform=BusCapabilities(hw=base, sw=base),
            # Schema-less / raw-string buses fall here. Treat unknown buses as generic
            # real-time qblox lines so a quick raw-string program still validates.
            default_bus_profile=BusCapabilities(hw=drive, sw=drive),
        )

    # ------------------------------------------------------------------- execute

    def execute(self, qprogram: QProgram) -> QProgramResult:  # noqa: ARG002
        """Validate against *MyPlatform's* capabilities, then interpret the program.

        Follows the standard execution convention: expand fragments, raise
        :class:`UnsupportedOperationError` on any ``error`` diagnostic, warn (without
        raising) on ``warning`` diagnostics, and pass ``info`` through silently.

        MyPlatform is imaginary hardware, so the *numeric* interpretation is delegated to
        the reference software simulator — but the legality decision is made against this
        platform's own (much narrower) capabilities, not the reference's permissive set.
        """
        if qprogram.fragments:
            qprogram = qprogram.expand()

        diagnostics, _plan = validate(qprogram, self.capabilities)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            joined = "\n".join(f"  - {d}" for d in errors)
            msg = f"MyPlatform cannot execute this program:\n{joined}"
            raise UnsupportedOperationError(msg)
        for diag in diagnostics:
            if diag.severity == "warning":
                warnings.warn(str(diag), ExecutionWarning, stacklevel=2)

        # Hand the validated program to the reference interpreter for the actual numbers.
        # We already surfaced MyPlatform's own diagnostics, so silence the reference
        # platform's (redundant) re-validation warnings.
        backend = ReferencePlatform(schema=self._schema, model=self._model, parameters=self.parameters)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ExecutionWarning)
            return backend.execute(qprogram)
