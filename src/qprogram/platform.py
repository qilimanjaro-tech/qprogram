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
"""The interface every execution back-end implements.

[`PlatformProtocol`][qprogram.PlatformProtocol] is the seam between a program and the hardware (or simulator) that runs
it: resource discovery on one side, a capability descriptor plus validation, planning, explanation, and execution on the
other. [`PlatformProtocol.validate`][qprogram.PlatformProtocol.validate],
[`PlatformProtocol.plan`][qprogram.PlatformProtocol.plan], and
[`PlatformProtocol.explain`][qprogram.PlatformProtocol.explain] have working defaults that delegate into
[`qprogram.validation.validate`][qprogram.validate] and `qprogram.explain`, so a concrete platform supplies its
resources, its [`PlatformCapabilities`][qprogram.PlatformCapabilities], and
[`PlatformProtocol.execute`][qprogram.PlatformProtocol.execute].
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qprogram.buses import BusSchema
    from qprogram.protocol import Diagnostic, ExecutionPlan, PlatformCapabilities
    from qprogram.qprogram import QProgram
    from qprogram.result import QProgramResult


class PlatformProtocol(ABC):
    """Abstract interface that execution platforms must implement.

    Splits into resource discovery (``get_*``) and capability + execution (`capabilities`,
    [`validate`][qprogram.validate], `plan`, `explain`, `execute`). The convention is that
    `execute` calls [`validate`][qprogram.validate] first, raises
    [`UnsupportedOperationError`][qprogram.UnsupportedOperationError] on any ``severity="error"`` diagnostic, and
    surfaces ``"warning"`` / ``"info"`` diagnostics without raising — concrete platforms aren't
    forced to follow it, but skipping the check means cryptic compiler errors in place of
    structured diagnostics.
    """

    @abstractmethod
    def get_bus_schema(self) -> BusSchema:
        """Return the [`BusSchema`][qprogram.BusSchema] for this platform's chip.

        Returns:
            The schema naming the chip's elements and the bus kinds each one exposes.
        """
        ...

    @abstractmethod
    def get_buses(self) -> list[str]:
        """Return the names of every bus this platform exposes.

        Returns:
            Every bus name, spelled the way a program would reference it.
        """
        ...

    @abstractmethod
    def get_parameters(self, bus: str) -> list[str]:
        """Return the parameter names supported on ``bus``.

        Args:
            bus (str): Bus whose parameters to list.

        Returns:
            The parameter names ``set_parameter`` / ``get_parameter`` accept for that bus.
        """
        ...

    @abstractmethod
    def get_global_parameters(self) -> list[str]:
        """Return the parameter names that are not bound to any specific bus.

        Returns:
            The platform-wide parameter names.
        """
        ...

    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities:
        """The capability descriptor for this platform.

        A [`PlatformCapabilities`][qprogram.PlatformCapabilities] carries per-``(element, bus_kind)`` bus profiles, a
        platform-level profile (block / expression / bus-less ops), and a default-bus-profile fallback
        for raw-string buses. Each slot is a [`BusCapabilities`][qprogram.BusCapabilities] with rt / host halves.
        Users introspect this to know what the platform supports; the validator consumes the same
        object.
        """
        ...

    def validate(self, qprogram: QProgram) -> list[Diagnostic]:
        """Validate a program against this platform's capabilities.

        Default delegates to [`qprogram.validation.validate`][qprogram.validate] and discards the execution-plan
        half of the return value. Platforms may override to prepend device-specific predicates or
        short-circuit on the first error.

        Args:
            qprogram (QProgram): Program to validate.

        Returns:
            List of diagnostics; empty when the program is fully supported with no forced-host
            fallback events.
        """
        from qprogram.validation import validate as _validate  # ruff: ignore[import-outside-top-level]

        diagnostics, _ = _validate(qprogram, self.capabilities)
        return diagnostics

    def plan(self, qprogram: QProgram) -> ExecutionPlan:
        """Return the execution-domain plan for ``qprogram``.

        Default delegates to [`qprogram.validation.validate`][qprogram.validate] and discards the diagnostic half.
        Callers who want both — e.g. ``execute()`` implementations that gate on diagnostics and then
        compile against the plan — should call [`qprogram.validation.validate`][qprogram.validate] directly to
        avoid the duplicated walk.

        Args:
            qprogram (QProgram): Program to classify.

        Returns:
            Mapping from each AST node to its final domain set.
        """
        from qprogram.validation import validate as _validate  # ruff: ignore[import-outside-top-level]

        _, plan = _validate(qprogram, self.capabilities)
        return plan

    def explain(self, qprogram: QProgram) -> str:
        """Render the execution plan for ``qprogram`` as a human-readable tree.

        Default delegates to `qprogram.explain`: every body node is shown as its ``.qp``
        text with the domain set it will execute in (``[rt|host]`` / ``[rt]`` / ``[host]`` /
        ``[--]``), with errors, warnings (notably ``forced-host`` and its reasons), and info
        annotated inline. Programs with fragment calls are expanded first.

        Args:
            qprogram (QProgram): Program to classify and render.

        Returns:
            The rendered tree.
        """
        from qprogram.explain import explain as _explain  # ruff: ignore[import-outside-top-level]

        return _explain(qprogram, self.capabilities)

    @abstractmethod
    def execute(self, qprogram: QProgram) -> QProgramResult:
        """Execute a program and return its results.

        By convention the implementation calls [`validate`][qprogram.validate] first and raises
        [`UnsupportedOperationError`][qprogram.UnsupportedOperationError] on any ``severity="error"`` diagnostic.

        Args:
            qprogram (QProgram): Program to run.

        Returns:
            One record per measurement in the program.
        """
        ...

    def stream(self, qprogram: QProgram, **kwargs) -> Iterator[QProgramResult]:
        """Execute and yield partial results as they become available.

        Optional — the default raises `NotImplementedError`. Platforms that don't support
        streaming can leave this alone.

        Args:
            qprogram (QProgram): Program to run.
            **kwargs (Any): Platform-specific streaming options.

        Raises:
            NotImplementedError: Always, in the default implementation.
        """
        msg = "Streaming not supported by this platform"
        raise NotImplementedError(msg)
