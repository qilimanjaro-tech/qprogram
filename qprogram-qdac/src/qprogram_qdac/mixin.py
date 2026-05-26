"""Typed mixin that exposes the QDAC namespace as ``.qdac`` on a QProgram subclass.

The mixin is purely for IDE autocomplete: at runtime the base :class:`~qprogram.QProgram`'s
dynamic ``__getattr__`` already routes ``program.qdac.*`` to the registered
:class:`~qprogram_qdac.namespace.QdacNamespace`. Static type-checkers and editors don't see that
dynamic dispatch, so the mixin spells the namespace out as a typed ``@property``.

Usage — single vendor:

```python
from qprogram_qdac import QProgram

qp = QProgram()           # QProgram with .qdac typed
qp.qdac.set_offset(...)   # IDE autocomplete works
```

Usage — multiple vendors combined:

```python
from qprogram_qblox import QbloxMixin
from qprogram_qdac import QdacMixin
from qprogram import QProgram as BaseQProgram

class QProgram(QbloxMixin, QdacMixin, BaseQProgram):
    pass
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qprogram_qdac.namespace import QdacNamespace

if TYPE_CHECKING:
    from qprogram.qprogram import QProgram as _BaseQProgram


class QdacMixin:
    """Mixin that adds a typed ``.qdac`` property to QProgram.

    Compose with :class:`qprogram.QProgram` via multiple inheritance to get IDE autocomplete for
    QDAC operations. The mixin caches the namespace instance on the program so repeated
    ``program.qdac`` accesses return the same object — important for the shared namespace state
    that some vendor packages keep across calls.
    """

    @property
    def qdac(self: _BaseQProgram) -> QdacNamespace:  # type: ignore[misc]
        """Return this program's typed QDAC namespace.

        The first access constructs a :class:`QdacNamespace` bound to the program and caches it
        in a hidden attribute; subsequent accesses return the same instance.
        """
        try:
            return object.__getattribute__(self, "_qdac_ns")
        except AttributeError:
            pass
        ns = QdacNamespace(self)
        object.__setattr__(self, "_qdac_ns", ns)
        return ns
