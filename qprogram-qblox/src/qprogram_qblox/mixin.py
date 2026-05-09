"""Typed mixin for Qblox vendor namespace on QProgram.

Provides a ``@property`` that gives IDE autocomplete for ``program.qblox.*``.

Usage — single vendor::

    from qprogram_qblox import QbloxMixin, QProgram

    qp = QProgram()          # QProgram with .qblox typed
    qp.qblox.acquire(...)    # IDE autocomplete works

Usage — multiple vendors combined by a platform::

    from qprogram_qblox import QbloxMixin
    from qprogram_qdac import QdacMixin
    from qprogram import QProgram as BaseQProgram

    class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
        pass
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram_qblox.namespace import QbloxNamespace

if TYPE_CHECKING:
    from qprogram.qprogram import QProgram as _BaseQProgram


class QbloxMixin:
    """Mixin that adds a typed ``.qblox`` property to QProgram.

    Combine with ``qprogram.QProgram`` via multiple inheritance to get
    full IDE autocomplete for Qblox operations.
    """

    @property
    def qblox(self: _BaseQProgram) -> QbloxNamespace:  # type: ignore[misc]
        """Qblox vendor namespace with typed operations."""
        # Check if already cached on the instance
        try:
            return object.__getattribute__(self, "_qblox_ns")
        except AttributeError:
            pass
        # Create and cache
        ns = QbloxNamespace(self)
        object.__setattr__(self, "_qblox_ns", ns)
        return ns
