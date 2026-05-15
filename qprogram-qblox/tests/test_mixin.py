"""Tests for QbloxMixin — the typed-property pattern."""

from __future__ import annotations

from qprogram import QProgram as BaseQProgram
from qprogram_qblox import QProgram as QbloxQProgram
from qprogram_qblox.mixin import QbloxMixin
from qprogram_qblox.namespace import QbloxNamespace


def test_qprogram_subclasses_mixin():
    assert issubclass(QbloxQProgram, QbloxMixin)
    assert issubclass(QbloxQProgram, BaseQProgram)


def test_mixin_property_returns_namespace():
    qp = QbloxQProgram()
    assert isinstance(qp.qblox, QbloxNamespace)


def test_mixin_property_caches_per_instance():
    qp = QbloxQProgram()
    first = qp.qblox
    second = qp.qblox
    assert first is second


def test_mixin_namespace_distinct_per_instance():
    qp1 = QbloxQProgram()
    qp2 = QbloxQProgram()
    assert qp1.qblox is not qp2.qblox


def test_mixin_dynamic_lookup_on_base_qprogram_still_works():
    """The dynamic __getattr__ on base QProgram also exposes .qblox at runtime."""
    qp = BaseQProgram()
    ns = qp.qblox  # type: ignore[attr-defined]
    assert isinstance(ns, QbloxNamespace)


def test_mixin_multiple_vendor_composition():
    """Mixins compose via MRO — combine QbloxMixin with another mixin."""
    from qprogram.vendor import VendorNamespace

    class FakeNS(VendorNamespace):
        pass

    BaseQProgram.register_vendor("fake", FakeNS)

    class FakeMixin:
        @property
        def fake(self) -> FakeNS:
            return FakeNS(self)  # type: ignore[arg-type]

    class Combined(QbloxMixin, FakeMixin, BaseQProgram):
        pass

    qp = Combined()
    assert isinstance(qp.qblox, QbloxNamespace)
    assert isinstance(qp.fake, FakeNS)
