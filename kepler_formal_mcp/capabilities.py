"""Describe the installed Kepler Formal API from an isolated worker."""

from __future__ import annotations

from dataclasses import asdict, fields
from enum import Enum
from importlib import metadata
import platform
from typing import Any


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def get_capabilities() -> dict[str, Any]:
    """Return installed API metadata without constructing or loading designs.

    Import this function freely, but call it only in the worker: importing the
    native package there keeps any native output away from MCP stdout.
    """
    import kepler_formal

    enum_names = (
        "VerificationMode", "Solver", "SecEngine", "SecEncoding", "VerificationStatus",
    )
    result_fields = [field.name for field in fields(kepler_formal.VerificationResult)]
    result_fields.extend(
        name for name, attribute in vars(kepler_formal.VerificationResult).items()
        if isinstance(attribute, property) and not name.startswith("_")
    )
    return {
        "status": "success",
        "kepler_formal_version": kepler_formal.version(),
        "kepler_formal_git_hash": kepler_formal.git_hash(),
        "najaeda_version": metadata.version("najaeda"),
        "python_version": platform.python_version(),
        "enums": {
            name: [item.value for item in getattr(kepler_formal, name)]
            for name in enum_names
        },
        "option_defaults": _json_value(asdict(kepler_formal.VerificationOptions())),
        "result_fields": result_fields,
        "public_exports": list(kepler_formal.__all__),
        "in_process_only": (
            "NativeDesign and from_najaeda use local native pointer handles. "
            "They stay inside the Python worker and are not transported by MCP."
        ),
    }
