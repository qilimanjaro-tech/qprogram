# Copyright 2026 Qilimanjaro Quantum Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Human-readable rendering of a program's [`ExecutionPlan`][qprogram.ExecutionPlan].

`explain` runs [`validate`][qprogram.validate] and returns a string holding one row
per body node: the node as its ``.qp`` text, then the node's execution-domain set in an aligned
column — ``[rt|host]``, ``[rt]``, ``[host]``, or ``[--]`` (no executable domain) — plus inline
annotations: ``!!`` for errors, ``~`` for warnings (notably ``forced-host`` with its reasons),
``i`` for info. Node-less diagnostics (whole-program limits) land in a footer.

Sample::

    plan for 'rabi' — errors: 0 · warnings: 1 · info: 0
    body
    └─ average 1000:                                       [host]     ~ forced-host: ...
       └─ for g in Range(start=0.0, stop=1.0, step=0.01):  [host]
          ├─ set_frequency "drive_q0" g                    [rt|host]
          ├─ set_parameter "drive_q0" "lo" g               [host]
          └─ measure "readout_q0" "ro" "w" name="m0"       [rt|host]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram.blocks.block import Block
from qprogram.blocks.conditional import Conditional
from qprogram.blocks.parallel import Parallel
from qprogram.errors import SerializationError
from qprogram.operations.operation import Operation
from qprogram.serialization.writer import _Writer
from qprogram.validation import validate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from qprogram.protocol import Diagnostic, PlatformCapabilities
    from qprogram.qprogram import QProgram

_DOMAIN_TEXT = {
    frozenset({"rt", "host"}): "[rt|host]",
    frozenset({"rt"}): "[rt]",
    frozenset({"host"}): "[host]",
    frozenset(): "[--]",
}


def explain(program: QProgram, caps: PlatformCapabilities) -> str:
    """Render the execution plan of ``program`` under ``caps`` as a tree.

    Programs containing fragment calls are expanded first (the header says so) — the plan always
    describes what would actually execute.

    Args:
        program (QProgram): The program to classify and render.
        caps (PlatformCapabilities): The platform capability descriptor to validate against.

    Returns:
        A multi-line string: header with severity counts, the body tree with one row per node
        (``.qp`` text, domain column, inline diagnostics), and a footer for whole-program
        diagnostics that have no node.
    """
    # Imported here rather than at module load, which would be a cycle.
    from qprogram.operations.call import Call  # ruff: ignore[import-outside-top-level]

    expanded = False
    if any(isinstance(node, Call) for node in program.body.walk()):
        program = program.expand()
        expanded = True
    diagnostics, plan = validate(program, caps)

    by_node: dict[int, list[Diagnostic]] = {}
    floating: list[Diagnostic] = []
    for diag in diagnostics:
        if diag.node is None:
            floating.append(diag)
        else:
            by_node.setdefault(id(diag.node), []).append(diag)

    rows = _render_rows(program, plan, by_node)

    counts = {"error": 0, "warning": 0, "info": 0}
    for diag in diagnostics:
        counts[diag.severity] = counts.get(diag.severity, 0) + 1
    title = f"plan for {program.label!r}" if program.label else "plan"
    if expanded:
        title += " (fragments expanded)"
    header = f"{title} — errors: {counts['error']} · warnings: {counts['warning']} · info: {counts['info']}"

    lines = [header, "body"]
    if rows:
        width = max(len(text) for text, _, _ in rows)
        for text, domain, anns in rows:
            line = f"{text:<{width}}  {domain:<9}"
            if anns:
                line = f"{line}  {'  '.join(anns)}"
            lines.append(line.rstrip())
    else:
        lines.append("(empty)")
    if floating:
        lines.append("")
        lines.extend(_annotation(diag) for diag in floating)
    return "\n".join(lines)


def _render_rows(
    program: QProgram,
    plan: Mapping[Block | Operation, frozenset[str]],
    by_node: Mapping[int, list[Diagnostic]],
) -> list[tuple[str, str, list[str]]]:
    """Walk the body and produce one ``(tree text, domain column, annotations)`` row per node.

    Nodes are rendered through the ``.qp`` writer's own serializers, so a row reads the way the
    program would be written to a file; a node the writer cannot serialize falls back to ``repr``.

    Args:
        program (QProgram): The program whose body is rendered, and the writer's context for
            turning its nodes back into ``.qp`` text.
        plan (Mapping[Block | Operation, frozenset[str]]): Identity-keyed execution plan, read for
            each node's domain column.
        by_node (Mapping[int, list[Diagnostic]]): Diagnostics grouped by the ``id()`` of the node
            they point at.

    Returns:
        One ``(tree text, domain column, annotations)`` triple per rendered node, in document
        order. A node missing from ``plan`` gets an empty domain column.
    """
    # Imported here rather than at module load, which would be a cycle.
    from qprogram.operations.call import Call  # ruff: ignore[import-outside-top-level]

    writer = _Writer(program)
    writer._allocate_var_idents()  # ruff: ignore[private-member-access] — explain deliberately reuses the writer's row renderers
    rows: list[tuple[str, str, list[str]]] = []

    def domain_text(node: Block | Operation) -> str:
        support = plan.get(node)
        if support is None:
            return ""
        return _DOMAIN_TEXT.get(frozenset(support), "[--]")

    def label(node: Block | Operation) -> str:
        try:
            if isinstance(node, Call):
                return writer._serialize_call(node)  # ruff: ignore[private-member-access]
            if isinstance(node, Operation):
                return writer._serialize_operation(node)  # ruff: ignore[private-member-access]
            if isinstance(node, Parallel):
                headers = " | ".join(writer._serialize_sweep_header(lp) for lp in node.loops)  # ruff: ignore[private-member-access]
                return f"{headers}:"
            if isinstance(node, Block):
                return f"{writer._serialize_block_header(node)}:"  # ruff: ignore[private-member-access]
        except SerializationError:
            pass
        return repr(node)

    def add_row(prefix: str, text: str, node: Block | Operation, extra: tuple[Block | Operation, ...] = ()) -> None:
        anns = [_annotation(diag) for n in (node, *extra) for diag in by_node.get(id(n), ())]
        rows.append((f"{prefix}{text}", domain_text(node), anns))

    def render_children(parent: Block, prefix: str) -> None:
        children = list(parent.elements)
        for i, child in enumerate(children):
            last = i == len(children) - 1
            render(child, prefix + ("└─ " if last else "├─ "), prefix + ("   " if last else "│  "))

    def render(node: Block | Operation, branch_prefix: str, cont_prefix: str) -> None:
        if isinstance(node, Conditional):
            add_row(branch_prefix, "if/elif/else chain", node)
            arms: list[tuple[str, Block]] = []
            for i, (condition, body) in enumerate(node.arms):
                keyword = "if" if i == 0 else "elif"
                arms.append((f"{keyword} {writer._serialize_condition(condition)}:", body))  # ruff: ignore[private-member-access]
            if node.else_body is not None:
                arms.append(("else:", node.else_body))
            for i, (arm_label, body) in enumerate(arms):
                last = i == len(arms) - 1
                add_row(cont_prefix + ("└─ " if last else "├─ "), arm_label, body)
                render_children(body, cont_prefix + ("   " if last else "│  "))
            return
        if isinstance(node, Parallel):
            # Loop-header diagnostics (the headers live in the row's label) attach to this row.
            add_row(branch_prefix, label(node), node, tuple(node.loops))
            render_children(node, cont_prefix)
            return
        if isinstance(node, Block):
            add_row(branch_prefix, label(node), node)
            render_children(node, cont_prefix)
            return
        add_row(branch_prefix, label(node), node)

    render_children(program.body, "")
    return rows


def _annotation(diag: Diagnostic) -> str:
    """Render one diagnostic as an inline marker: ``!!`` error, ``~`` warning, ``i`` info.

    Args:
        diag (Diagnostic): The diagnostic to render.

    Returns:
        A single-line marker. A ``forced-host`` warning is shortened to its reason clause, since
        the row it annotates already names the block.
    """
    if diag.severity == "error":
        return f"!! {diag.code}: {diag.message}"
    if diag.severity == "warning":
        if diag.code == "forced-host":
            # Message shape: "Block 'X' falls back to host-side execution: <reasons>."
            _, _, detail = diag.message.partition("falls back to host-side execution: ")
            return f"~ forced-host: {detail.rstrip('.') or diag.message}"
        return f"~ {diag.code}: {diag.message}"
    return f"i {diag.code}: {diag.message}"


__all__ = ["explain"]
