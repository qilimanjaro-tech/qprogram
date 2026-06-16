"""``explain()`` — the execution plan rendered as a tree."""

from __future__ import annotations

from test_validation import _drag_sigma_excludes_hw, _empty_caps, _full_caps

import qprogram as qp
from qprogram import Fragment, QProgram, explain
from qprogram.operations.operation import Operation
from qprogram.waveforms import IQDrag

# ---------------------------------------------------------------------------
# Rendering basics
# ---------------------------------------------------------------------------


def test_domain_column_per_node():
    p = QProgram(label="basic")
    g = p.variable("g")
    with p.for_loop(g, 0, 1, 0.1):
        p.play("drive_q0", "pi")
    out = explain(p, _full_caps())
    assert out.splitlines()[0].startswith("plan for 'basic'")
    assert "for g in range(0, 1, 0.1):" in out
    assert 'play "drive_q0" "pi"' in out
    assert "[hw|sw]" in out
    assert "errors: 0" in out


def test_forced_software_reason_inline():
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(100), p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    out = explain(p, caps)
    # The warning lands on the highest forced block (Average). It is forced by *containing* the
    # software-only ForLoop, so its reason is structural — naming the sub-block — and carries the
    # sub-block's own constraint reason for context.
    average_line = next(line for line in out.splitlines() if line.lstrip("└─├│ ").startswith("average 100:"))
    assert "[sw]" in average_line
    assert "~ forced-sw: contains software-only sub-block 'ForLoop'" in average_line
    assert "IQDrag.sigma sweep is not real-time" in average_line
    assert "warnings: 1" in out.splitlines()[0]


def test_error_annotation_inline():
    p = QProgram()
    p.play("drive_q0", "pi")
    p.wait("drive_q0", 4)
    out = explain(p, _empty_caps(bus_tokens=frozenset({"op.play", "waveform.alias"})))
    wait_line = next(line for line in out.splitlines() if "wait" in line)
    assert "[--]" in wait_line
    assert "!! missing-capability:" in wait_line
    assert "errors: " in out.splitlines()[0]


def test_node_less_diagnostics_in_footer():
    caps = _full_caps(platform_limits={"max_loop_nesting": 1})
    p = QProgram()
    a = p.variable("a")
    b = p.variable("b")
    with p.for_loop(a, 0, 1, 0.5), p.for_loop(b, 0, 1, 0.5):
        p.play("drive_q0", "pi")
    out = explain(p, caps)
    footer = out.splitlines()[-1]
    assert footer.startswith("!! limit-exceeded:")
    assert "max_loop_nesting" in footer


def test_conditional_arms_rendered_with_conditions():
    p = QProgram()
    m = p.measure("readout_q0", "ro", "w", returns=("iq", "state"))
    with p.if_(m.state == 0):
        p.play("drive_q0", "reset")
    with p.else_():
        p.sync()
    out = explain(p, _full_caps())
    assert "if/elif/else chain" in out
    assert "if m0.state == 0:" in out
    assert "else:" in out
    assert 'play "drive_q0" "reset"' in out


def test_fragments_expanded_with_header_note():
    frag = Fragment("xp")
    bus = frag.parameter("bus")
    frag.play(bus, "pi")
    p = QProgram(label="frag_demo")
    p.call(frag, "drive_q0")
    out = explain(p, _full_caps())
    assert "(fragments expanded)" in out.splitlines()[0]
    assert 'play "drive_q0" "pi"' in out  # the substituted body, not the call


def test_unregistered_operation_falls_back_to_repr():
    class Mystery(Operation):
        def __init__(self) -> None:
            self.bus = "drive_q0"

    p = QProgram()
    p.body.append(Mystery())
    out = explain(p, _full_caps())
    assert "Mystery" in out  # repr fallback instead of a SerializationError


def test_empty_program():
    out = explain(QProgram(), _full_caps())
    assert "(empty)" in out


def test_parallel_row_renders_pipe_joined_headers():
    p = QProgram()
    a = p.variable("a")
    b = p.variable("b")
    with p.for_loop(a, 0, 1, 0.5) | p.for_loop(b, 5, 10, 2.5):
        p.set_gain("drive_q0", a)
    out = explain(p, _full_caps())
    assert "for a in range(0, 1, 0.5) | for b in range(5, 10, 2.5):" in out


# ---------------------------------------------------------------------------
# PlatformProtocol.explain delegation
# ---------------------------------------------------------------------------


def test_platform_explain_default_delegates():
    from qprogram import PlatformProtocol  # noqa: PLC0415
    from qprogram.buses import BusSchema  # noqa: PLC0415

    class DemoPlatform(PlatformProtocol):
        def get_bus_schema(self):
            return BusSchema.transmon()

        def get_buses(self):
            return []

        def get_parameters(self, bus):  # noqa: ARG002 — abstract signature
            return []

        def get_global_parameters(self):
            return []

        @property
        def capabilities(self):
            return _full_caps()

        def execute(self, qprogram, **kwargs):  # pragma: no cover — not exercised
            raise NotImplementedError

    p = QProgram(label="via_platform")
    p.play("drive_q0", "pi")
    out = DemoPlatform().explain(p)
    assert out.splitlines()[0].startswith("plan for 'via_platform'")
    assert 'play "drive_q0" "pi"' in out


# ---------------------------------------------------------------------------
# Diagnostic path stamping (validate-level, exercised via the public API)
# ---------------------------------------------------------------------------


def test_validate_stamps_resolvable_paths():
    caps = _full_caps(bus_predicates=(_drag_sigma_excludes_hw,))
    p = QProgram()
    sigma = p.variable("sigma")
    with p.average(100), p.for_loop(sigma, 1, 10, 1):
        p.play("drive_q0", IQDrag(amplitude=0.5, duration=40, sigma=sigma, beta=0.1))
    diagnostics, _ = qp.validate(p, caps)
    for diag in diagnostics:
        if diag.node is not None:
            assert diag.path is not None
            assert qp.resolve_path(p, diag.path) is diag.node
    warning = next(d for d in diagnostics if d.code == "forced-software")
    assert warning.path == (0,)  # the Average at body[0]
    assert "(at body[0])" in str(warning)


def test_node_less_diagnostic_has_no_path():
    caps = _full_caps(platform_limits={"max_loop_nesting": 0})
    p = QProgram()
    a = p.variable("a")
    with p.for_loop(a, 0, 1, 0.5):
        p.play("drive_q0", "pi")
    diagnostics, _ = qp.validate(p, caps)
    limit = next(d for d in diagnostics if d.code == "limit-exceeded")
    assert limit.path is None
    assert "(at " not in str(limit)
