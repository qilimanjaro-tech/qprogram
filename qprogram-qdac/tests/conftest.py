"""Shared fixtures for qprogram-qdac tests."""

from __future__ import annotations

import pytest

# Importing qprogram_qdac triggers vendor registration as a side-effect.
import qprogram_qdac  # noqa: F401
from qprogram import BusSchema, QProgram as BaseQProgram
from qprogram_qdac import QProgram as QdacQProgram


@pytest.fixture
def flux_tunable_schema() -> BusSchema:
    return BusSchema.flux_tunable_transmon()


@pytest.fixture
def empty_qdac_program() -> QdacQProgram:
    return QdacQProgram()


@pytest.fixture
def qdac_program(flux_tunable_schema: BusSchema) -> QdacQProgram:
    return QdacQProgram(schema=flux_tunable_schema)


@pytest.fixture
def base_program(flux_tunable_schema: BusSchema) -> BaseQProgram:
    """Base QProgram (no mixin) — proves runtime `.qdac` works via dynamic getattr."""
    return BaseQProgram(schema=flux_tunable_schema)
