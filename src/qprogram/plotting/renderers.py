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
"""The registry that decides who draws a [`Figure`][qprogram.plotting.Figure].

A renderer is any callable taking a figure, a [`Style`][qprogram.plotting.Style], and an optional surface to draw on.
What it returns is its own business — the matplotlib one hands back the `Axes` it drew,
which is what makes a figure composable with the rest of a notebook.

Registration mirrors [`register_sweep_source`][qprogram.register_sweep_source]: one name, one
implementation, and re-registering the same object is a no-op while a different one under a taken
name raises. ``"matplotlib"`` is the default and is registered on first use, so importing
``qprogram`` never imports a plotting library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from qprogram.plotting.model import Figure
    from qprogram.plotting.theme import Style

DEFAULT_RENDERER = "matplotlib"
"""The renderer used when a call names none."""


class Renderer(Protocol):
    """What [`register_renderer`][qprogram.plotting.register_renderer] accepts: a callable that draws a figure.

    A plain function satisfies it, and so does an instance of a class with ``__call__``, which is
    the way to carry per-renderer configuration.
    """

    def __call__(self, figure: Figure, style: Style, target: Any = None) -> Any:  # ruff: ignore[any-type]
        """Draw ``figure``.

        Args:
            figure (Figure): What to draw. Marks are given in drawing order.
            style (Style): The palette and the weights to draw it with.
            target (Any): An existing surface to draw on — a matplotlib `Axes` for the
                built-in renderer — or ``None`` to make a new one.

        Returns:
            Whatever handle the backend gives back for further work.
        """
        ...


_renderers: dict[str, Renderer] = {}


def register_renderer(name: str, renderer: Renderer) -> Renderer:
    """Register ``renderer`` under ``name``.

    Args:
        name (str): The name callers pass as ``renderer=``.
        renderer (Renderer): The callable that draws a figure.

    Returns:
        ``renderer``, so a class or function can be registered where it is defined.

    Raises:
        ValueError: If ``name`` is already registered to a different object. Replacing a renderer
            silently would change every plot in the process.
    """
    existing = _renderers.get(name)
    if existing is not None and existing is not renderer:
        msg = f"renderer {name!r} is already registered to {existing!r}; pick another name"
        raise ValueError(msg)
    _renderers[name] = renderer
    return renderer


def resolve_renderer(name: str | None = None) -> Renderer:
    """Return the renderer registered under ``name``.

    Only ``None`` asks for the default. Every other value has to name something, ``""`` included:
    an empty ``renderer=`` is a name that got lost on the way rather than a request for whatever is
    installed, and silently drawing with matplotlib would hide that.

    Args:
        name (str | None): A registered name, or ``None`` for `DEFAULT_RENDERER`.

    Returns:
        The renderer to draw with.

    Raises:
        KeyError: If ``name`` names no registered renderer.
        ModuleNotFoundError: If the default renderer is asked for without ``matplotlib``
            installed — install ``qprogram[viz]``.
    """
    if name is None:
        name = DEFAULT_RENDERER
    if name == DEFAULT_RENDERER and name not in _renderers:
        # Imported on first use, so that `import qprogram` never pulls in matplotlib.
        from qprogram.plotting import matplotlib_renderer  # ruff: ignore[import-outside-top-level]

        register_renderer(DEFAULT_RENDERER, matplotlib_renderer.render)
    if name not in _renderers:
        # The default is listed whether or not it has registered itself yet, since it registers on
        # the first call that asks for it and a reader of the message cannot see that it has not.
        available = ", ".join(sorted(set(_renderers) | {DEFAULT_RENDERER}))
        msg = f"No renderer named {name!r}; registered: {available}"
        raise KeyError(msg)
    return _renderers[name]


def available_renderers() -> tuple[str, ...]:
    """List the renderers registered so far, in name order.

    The default is absent until something has drawn with it, because it registers itself on first
    use rather than at import.

    Returns:
        The registered names.
    """
    return tuple(sorted(_renderers))
