from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from qprogram.operations.operation import Operation, _collect_variables

if TYPE_CHECKING:
    from qprogram.fragments import Fragment
    from qprogram.variable import Variable


class Call(Operation):
    """Instantiation of a :class:`~qprogram.Fragment` at a specific site with bound arguments.

    A first-class AST leaf: definitions and call sites survive serialization (``.qp`` emits a
    ``fragment <name>(...):`` section and a bare ``<name>(<args>)`` statement) and structural
    equality. :meth:`QProgram.expand` replaces every ``Call`` with the substituted fragment body —
    compilers and validators consume the expanded, fragment-free program.

    Built by :meth:`QProgram.call`; direct construction is rarely needed.

    Args:
        fragment: The called :class:`~qprogram.Fragment`.
        arguments: Fully-bound ``{param_id: value}`` mapping (one entry per fragment parameter),
            produced by :func:`qprogram.fragments.bind_arguments`.
    """

    # Buses reach a Call only through ``arguments`` values; the validator never routes a Call
    # (validation expands first), so no BUS_ATTRS are declared.
    BUS_ATTRS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, fragment: Fragment, arguments: dict[str, object]) -> None:
        self.fragment = fragment
        self.arguments = arguments

    def variables(self) -> set[Variable]:
        """Return host variables referenced by the bound arguments.

        The fragment body's own parameters/locals are *not* reported — they are placeholders that
        exist only until expansion.
        """
        out: set[Variable] = set()
        for value in self.arguments.values():
            out |= _collect_variables(value)
        return out

    def buses(self) -> set[str]:
        """Return buses bound directly as arguments (the fragment body's are visible post-expansion)."""
        return {value for value in self.arguments.values() if isinstance(value, str)}

    def required_capabilities(self) -> set[str]:
        """Return the empty set — ``validate()`` expands calls before checking capabilities."""
        return set()

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items())
        return f"Call({self.fragment.name}({args}))"
