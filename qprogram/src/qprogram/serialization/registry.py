from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qprogram.waveforms import IQWaveform, Waveform

# Maps waveform class name -> class
_waveform_registry: dict[str, type[Waveform | IQWaveform]] = {}

# Maps (vendor, operation_name) -> Operation class
_operation_registry: dict[tuple[str, str], type] = {}

# Maps Operation class -> (vendor, operation_name) for reverse lookup during serialization
_operation_reverse: dict[type, tuple[str, str]] = {}

# Maps vendor name -> declared protocol version (semver string, e.g. "0.1.0")
_vendor_versions: dict[str, str] = {}


def register_waveform(cls: type[Waveform | IQWaveform]) -> type:
    """Decorator to register a waveform type for serialization."""
    _waveform_registry[cls.__name__] = cls
    return cls


def register_vendor_operation(vendor: str, name: str, cls: type) -> None:
    """Register a vendor operation for serialization."""
    _operation_registry[(vendor, name)] = cls
    _operation_reverse[cls] = (vendor, name)


def register_vendor_version(vendor: str, version: str) -> None:
    """Register the protocol version of an installed vendor extension.

    The vendor extension package calls this on import (typically in its
    ``__init__.py``). The version is used by the ``.qp`` parser to check
    compatibility with ``require <vendor> <version>`` declarations.

    Args:
        vendor: Vendor name as used in the dot-notation operations (e.g. "qblox").
        version: Semver string (e.g. "0.1.0", "1.2.3"). Major.minor is what
            counts for compatibility; patch is informational.
    """
    _vendor_versions[vendor] = version


def get_waveform_class(name: str) -> type[Waveform | IQWaveform] | None:
    return _waveform_registry.get(name)


def get_operation_class(vendor: str, name: str) -> type | None:
    return _operation_registry.get((vendor, name))


def get_operation_vendor_name(cls: type) -> tuple[str, str] | None:
    return _operation_reverse.get(cls)


def get_vendor_version(vendor: str) -> str | None:
    """Return the registered protocol version of an installed vendor, or None."""
    return _vendor_versions.get(vendor)


def _register_builtins() -> None:
    """Register all built-in waveform types."""
    from qprogram.waveforms import (  # noqa: PLC0415
        Arbitrary,
        Chained,
        FlatTop,
        Gaussian,
        GaussianDragCorrection,
        IQDrag,
        IQPair,
        Ramp,
        Square,
        SuddenNetZero,
    )

    for cls in [
        Square,
        Gaussian,
        GaussianDragCorrection,
        Ramp,
        FlatTop,
        SuddenNetZero,
        Arbitrary,
        Chained,
        IQPair,
        IQDrag,
    ]:
        _waveform_registry[cls.__name__] = cls


_register_builtins()
