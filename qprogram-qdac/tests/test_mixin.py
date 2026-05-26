"""Tests for QdacMixin — the typed-property pattern."""

from __future__ import annotations

from qprogram import QProgram as BaseQProgram
from qprogram_qdac import QProgram as QdacQProgram
from qprogram_qdac.mixin import QdacMixin
from qprogram_qdac.namespace import QdacNamespace


def test_qprogram_subclasses_mixin():
    assert issubclass(QdacQProgram, QdacMixin)
    assert issubclass(QdacQProgram, BaseQProgram)


def test_mixin_property_returns_namespace():
    qp = QdacQProgram()
    assert isinstance(qp.qdac, QdacNamespace)


def test_mixin_property_caches_per_instance():
    qp = QdacQProgram()
    first = qp.qdac
    second = qp.qdac
    assert first is second


def test_mixin_namespace_distinct_per_instance():
    qp1 = QdacQProgram()
    qp2 = QdacQProgram()
    assert qp1.qdac is not qp2.qdac


def test_mixin_dynamic_lookup_on_base_qprogram_still_works():
    """The dynamic __getattr__ on base QProgram also exposes .qdac at runtime."""
    qp = BaseQProgram()
    ns = qp.qdac  # type: ignore[attr-defined]
    assert isinstance(ns, QdacNamespace)


def test_mixin_multiple_vendor_composition():
    """Mixins compose via MRO — combine QdacMixin with another mixin."""
    from qprogram.vendor import VendorNamespace

    class FakeNS(VendorNamespace):
        pass

    BaseQProgram.register_vendor("fake_for_qdac_test", FakeNS)

    class FakeMixin:
        @property
        def fake_for_qdac_test(self) -> FakeNS:
            return FakeNS(self)  # type: ignore[arg-type]

    class Combined(QdacMixin, FakeMixin, BaseQProgram):
        pass

    qp = Combined()
    assert isinstance(qp.qdac, QdacNamespace)
    assert isinstance(qp.fake_for_qdac_test, FakeNS)
