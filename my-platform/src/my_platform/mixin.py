"""Typed mixin that exposes the MyPlatform namespace as ``.myplatform`` on a QProgram subclass.

The mixin is purely for IDE autocomplete: at runtime the base :class:`~qprogram.QProgram`'s dynamic
``__getattr__`` already routes ``program.myplatform.*`` to the registered
:class:`~my_platform.namespace.MyPlatformNamespace` once :mod:`my_platform` is imported. Static
type-checkers and editors don't see that dynamic dispatch, so the mixin spells the namespace out as a
typed ``@property``.

Usage — pre-combined typed QProgram (qblox + qdac + myplatform):

```python
from my_platform import QProgram

qp = QProgram()                              # .qblox / .qdac / .myplatform all typed
qp.myplatform.set_rf_switch("switch0/rf", 2)
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from my_platform.namespace import MyPlatformNamespace

if TYPE_CHECKING:
    from qprogram.qprogram import QProgram as _BaseQProgram


class MyPlatformMixin:
    """Mixin that adds a typed ``.myplatform`` property to QProgram.

    Compose with :class:`qprogram.QProgram` (and any other vendor mixins) via multiple inheritance to
    get IDE autocomplete for MyPlatform operations. The first access constructs the namespace bound to
    the program and caches it on a hidden attribute; subsequent accesses return the same instance.
    """

    @property
    def myplatform(self: _BaseQProgram) -> MyPlatformNamespace:  # type: ignore[misc]
        """Return this program's typed MyPlatform namespace."""
        try:
            return object.__getattribute__(self, "_myplatform_ns")
        except AttributeError:
            pass
        ns = MyPlatformNamespace(self)
        object.__setattr__(self, "_myplatform_ns", ns)
        return ns
