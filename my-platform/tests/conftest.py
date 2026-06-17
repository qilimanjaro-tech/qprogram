"""Shared fixtures for my-platform tests.

Importing :mod:`my_platform` registers the qblox + qdac vendors, the ``myplatform`` vendor
namespace/version/operations, and MyPlatform's capability profiles — all as import side effects.
"""

from __future__ import annotations

import pytest

import my_platform  # noqa: F401 — imported for its registration side effects
from my_platform import MyPlatform
from my_platform import QProgram as MyPlatformQProgram
from my_platform.schema import RFSwitchSchema
from qprogram.buses import BusSchema


@pytest.fixture
def platform() -> MyPlatform:
    """A MyPlatform instance with the default 2 qubits + 2 switches."""
    return MyPlatform()


@pytest.fixture
def schema() -> BusSchema:
    """The combined flux-tunable-transmon + RF-switch schema MyPlatform drives, built via ``+``."""
    return BusSchema.flux_tunable_transmon() + RFSwitchSchema()


@pytest.fixture
def program(schema: BusSchema) -> MyPlatformQProgram:
    """A pre-combined QProgram (qblox + qdac + myplatform mixins) bound to the schema."""
    return MyPlatformQProgram(schema=schema)
