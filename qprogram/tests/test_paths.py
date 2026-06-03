"""Structural AST paths and the ``.qp`` source map."""

from __future__ import annotations

import pytest

import qprogram as qp
from qprogram import QProgram, format_path, node_path, resolve_path
from qprogram.paths import iter_child_edges

# ---------------------------------------------------------------------------
# Fixture program covering every edge kind
# ---------------------------------------------------------------------------


def _program() -> QProgram:
    p = QProgram()
    g = p.variable("g")
    h = p.variable("h")
    p.play("drive_q0", "pi")  # body[0]
    with p.average(100), p.for_loop(g, 0, 1, 0.1):  # body[1] / body[1][0]
        p.wait("drive_q0", 4)  # body[1][0][0]
        m = p.measure("readout_q0", "ro", "w", returns=("iq", "state"))  # body[1][0][1]
        with p.if_(m.state == 0):  # body[1][0][2]
            p.play("drive_q0", "reset")  # body[1][0][2].arm:0[0]
        with p.elif_(m.state == 1):
            p.wait("drive_q0", 8)  # body[1][0][2].arm:1[0]
        with p.else_():
            p.sync()  # body[1][0][2].else[0]
    with p.for_loop(g, 0, 1, 0.5) | p.for_loop(h, 5, 10, 2.5):  # body[2] (Parallel)
        p.set_gain("drive_q0", g)  # body[2][0]
    return p


def test_body_path_is_empty_tuple():
    p = _program()
    assert node_path(p, p.body) == ()
    assert resolve_path(p, ()) is p.body


def test_node_path_resolve_path_inverses_over_all_nodes():
    p = _program()
    for node in p.body.walk():
        path = node_path(p, node)
        assert path is not None, f"{type(node).__name__} not reachable"
        assert resolve_path(p, path) is node


def test_paths_cover_conditional_arms_and_else():
    p = _program()
    cond = next(n for n in p.body.walk() if type(n).__name__ == "Conditional")
    assert node_path(p, cond) == (1, 0, 2)
    assert node_path(p, cond.arms[0][1]) == (1, 0, 2, "arm:0")
    assert node_path(p, cond.arms[1][1]) == (1, 0, 2, "arm:1")
    assert node_path(p, cond.else_body) == (1, 0, 2, "else")
    # The op inside the elif arm.
    assert node_path(p, cond.arms[1][1].elements[0]) == (1, 0, 2, "arm:1", 0)


def test_paths_cover_parallel_loop_headers():
    p = _program()
    par = next(n for n in p.body.walk() if type(n).__name__ == "Parallel")
    assert node_path(p, par) == (2,)
    assert node_path(p, par.loops[0]) == (2, "loop:0")
    assert node_path(p, par.loops[1]) == (2, "loop:1")
    assert node_path(p, par.elements[0]) == (2, 0)


def test_identity_matching_distinguishes_structural_twins():
    p = QProgram()
    p.sync()
    p.sync()
    first, second = p.body.elements
    assert first == second  # structurally identical...
    assert node_path(p, first) == (0,)
    assert node_path(p, second) == (1,)  # ...but addressed separately


def test_node_path_returns_none_for_foreign_node():
    p = _program()
    other = QProgram()
    other.sync()
    assert node_path(p, other.body.elements[0]) is None


def test_resolve_path_dangling_raises_keyerror():
    p = _program()
    with pytest.raises(KeyError, match="does not exist"):
        resolve_path(p, (99,))
    with pytest.raises(KeyError, match="arm:5"):
        resolve_path(p, (1, 0, 2, "arm:5"))


def test_format_path():
    assert format_path(()) == "body"
    assert format_path((1, 0, 2, "arm:1", 0)) == "body[1][0][2].arm:1[0]"
    assert format_path((2, "loop:0")) == "body[2].loop:0"


def test_iter_child_edges_matches_walk_coverage():
    """Every node walk() yields (except the root) is reachable through iter_child_edges."""
    p = _program()
    reachable = {id(p.body)}

    def collect(node):
        for _, child in iter_child_edges(node):
            reachable.add(id(child))
            collect(child)

    collect(p.body)
    for node in p.body.walk():
        assert id(node) in reachable, f"{type(node).__name__} missed by iter_child_edges"


# ---------------------------------------------------------------------------
# .qp source map
# ---------------------------------------------------------------------------


def test_source_map_maps_paths_to_their_lines():
    p = _program()
    text = qp.dumps(p)
    reloaded = qp.loads(text)
    lines = text.splitlines()
    expectations = {
        (0,): 'play "drive_q0" "pi"',
        (1,): "average 100:",
        (1, 0): "for g in range(0, 1, 0.1):",
        (1, 0, 0): 'wait "drive_q0" 4',
        (1, 0, 2): "if m0.state == 0:",
        (1, 0, 2, "arm:0"): "if m0.state == 0:",
        (1, 0, 2, "arm:0", 0): 'play "drive_q0" "reset"',
        (1, 0, 2, "arm:1"): "elif m0.state == 1:",
        (1, 0, 2, "else"): "else:",
        (1, 0, 2, "else", 0): "sync",
        (2, 0): 'set_gain "drive_q0" g',
    }
    for path, expected_text in expectations.items():
        line_num = reloaded.source_map[path]
        assert lines[line_num - 1].strip() == expected_text, path


def test_source_map_parallel_headers_share_the_header_line():
    p = _program()
    reloaded = qp.loads(qp.dumps(p))
    sm = reloaded.source_map
    assert sm[(2,)] == sm[(2, "loop:0")] == sm[(2, "loop:1")]


def test_source_map_covers_every_body_node():
    p = _program()
    reloaded = qp.loads(qp.dumps(p))
    sm = reloaded.source_map
    for node in reloaded.body.walk():
        if node is reloaded.body:
            continue
        path = node_path(reloaded, node)
        assert path in sm, f"{type(node).__name__} at {format_path(path)} unmapped"


def test_source_map_empty_for_python_built_programs():
    assert _program().source_map == {}


def test_source_map_cleared_by_expand():
    text = '#!QProgram 1.0\n\nfragment f1(bus):\n  sync\n\nbody:\n  f1("drive")\n'
    p = qp.loads(text)
    assert p.source_map  # the call statement is mapped
    assert p.expand().source_map == {}


def test_source_map_round_trips_a_diagnostic_path_to_the_offending_line():
    """The headline flow: a Diagnostic.path from validating a built program addresses the exact
    line in the serialized .qp text."""
    from test_validation import _empty_caps  # noqa: PLC0415 — shared fixture helper

    p = QProgram()
    p.play("drive_q0", "pi")
    p.wait("drive_q0", 4)
    diagnostics, _ = qp.validate(p, _empty_caps(bus_tokens=frozenset({"op.play", "waveform.alias"})))
    diag = next(d for d in diagnostics if d.code == "missing-capability")
    assert diag.path == (1,)  # the Wait
    text = qp.dumps(p)
    line = qp.loads(text).source_map[diag.path]
    assert text.splitlines()[line - 1].strip() == 'wait "drive_q0" 4'
