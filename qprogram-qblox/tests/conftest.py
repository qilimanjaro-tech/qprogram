"""Shared fixtures for qprogram-qblox tests."""

from __future__ import annotations

import pytest

# Importing qprogram_qblox triggers vendor registration as a side-effect.
import qprogram_qblox  # noqa: F401
from qprogram import BusSchema, QProgram as BaseQProgram
from qprogram_qblox import QProgram as QbloxQProgram


@pytest.fixture
def transmon_schema() -> BusSchema:
    return BusSchema.transmon()


@pytest.fixture
def empty_qblox_program() -> QbloxQProgram:
    return QbloxQProgram()


@pytest.fixture
def qblox_program(transmon_schema: BusSchema) -> QbloxQProgram:
    return QbloxQProgram(schema=transmon_schema)


@pytest.fixture
def base_program(transmon_schema: BusSchema) -> BaseQProgram:
    """Base QProgram (no mixin) — proves runtime `.qblox` works via dynamic getattr."""
    return BaseQProgram(schema=transmon_schema)
