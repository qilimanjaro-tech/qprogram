"""Tests for MyPlatformMixin and the pre-combined QProgram (qblox + qdac + myplatform)."""

from __future__ import annotations

from qprogram import QProgram as BaseQProgram
from qprogram_qblox import QbloxMixin
from qprogram_qdac import QdacMixin

from my_platform import QProgram as ComboQProgram
from my_platform.mixin import MyPlatformMixin
from my_platform.namespace import MyPlatformNamespace


def test_combined_qprogram_mro():
    assert issubclass(ComboQProgram, QbloxMixin)
    assert issubclass(ComboQProgram, QdacMixin)
    assert issubclass(ComboQProgram, MyPlatformMixin)
    assert issubclass(ComboQProgram, BaseQProgram)


def test_mixin_property_returns_namespace():
    qp = ComboQProgram()
    assert isinstance(qp.myplatform, MyPlatformNamespace)


def test_mixin_property_caches_per_instance():
    qp = ComboQProgram()
    assert qp.myplatform is qp.myplatform


def test_all_three_namespaces_available():
    qp = ComboQProgram()
    # All three vendor namespaces resolve on the combined program.
    assert qp.qblox is not None
    assert qp.qdac is not None
    assert qp.myplatform is not None
