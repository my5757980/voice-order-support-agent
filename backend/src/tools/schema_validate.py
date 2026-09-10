"""JSON Schema validation for tool arguments.

Deliberately a focused subset rather than a dependency: our schemas are authored in
`contracts/tools.schema.json`, fully under our control, and use only the keywords below.
A general validator would be more code to audit for the same guarantee.

FR-047 / TC-002: invalid arguments never reach a handler. This runs *in addition to*
`strict: true` at the provider boundary — provider-side strictness is a good first line,
but the registry must not depend on a vendor honouring it.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "type", "properties", "required", "additionalProperties", "items",
        "enum", "minLength", "maxLength", "minItems", "maxItems",
        "minimum", "maximum", "description",
    }
)

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


class ValidationError(ValueError):
    """Carries the path to the offending field so the error is actionable."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path or '<root>'}: {message}")
        self.path = path
        self.reason = message


def validate(value: Any, schema: dict[str, Any], path: str = "") -> None:
    """Raise ValidationError on the first problem found."""
    expected = schema.get("type")
    if expected:
        py = _TYPES.get(expected)
        if py is None:
            raise ValidationError(path, f"unsupported schema type {expected!r}")
        # bool is a subclass of int in Python; an integer field must not accept True.
        if expected in ("integer", "number") and isinstance(value, bool):
            raise ValidationError(path, f"expected {expected}, got boolean")
        if not isinstance(value, py):
            raise ValidationError(path, f"expected {expected}, got {type(value).__name__}")

    if (choices := schema.get("enum")) is not None and value not in choices:
        raise ValidationError(path, f"must be one of {sorted(map(str, choices))}")

    if isinstance(value, str):
        _check_string(value, schema, path)
    elif isinstance(value, list):
        _check_array(value, schema, path)
    elif isinstance(value, dict):
        _check_object(value, schema, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if (lo := schema.get("minimum")) is not None and value < lo:
            raise ValidationError(path, f"must be >= {lo}")
        if (hi := schema.get("maximum")) is not None and value > hi:
            raise ValidationError(path, f"must be <= {hi}")


def _check_string(value: str, schema: dict[str, Any], path: str) -> None:
    if (lo := schema.get("minLength")) is not None and len(value) < lo:
        raise ValidationError(path, f"must be at least {lo} characters")
    if (hi := schema.get("maxLength")) is not None and len(value) > hi:
        raise ValidationError(path, f"must be at most {hi} characters")


def _check_array(value: list[Any], schema: dict[str, Any], path: str) -> None:
    if (lo := schema.get("minItems")) is not None and len(value) < lo:
        raise ValidationError(path, f"must have at least {lo} item(s)")
    if (hi := schema.get("maxItems")) is not None and len(value) > hi:
        raise ValidationError(path, f"must have at most {hi} item(s)")
    if (item_schema := schema.get("items")) is not None:
        for i, item in enumerate(value):
            validate(item, item_schema, f"{path}[{i}]")


def _check_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    properties: dict[str, Any] = schema.get("properties", {})

    for name in schema.get("required", []):
        if name not in value:
            raise ValidationError(f"{path}.{name}" if path else name, "is required")

    # additionalProperties:false is not decoration — it is what stops a model from
    # smuggling an unexpected field (a customer_id, say) into a handler.
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                raise ValidationError(f"{path}.{key}" if path else key, "is not an allowed field")

    for key, item in value.items():
        if (sub := properties.get(key)) is not None:
            validate(item, sub, f"{path}.{key}" if path else key)


def assert_strict_compatible(schema: dict[str, Any], where: str = "") -> None:
    """Every tool schema must be usable with the provider's `strict: true` mode.

    That requires `additionalProperties: false` and an explicit `required` list on every
    object. Checked at import so a malformed contract fails at boot rather than mid-call.
    """
    if schema.get("type") != "object":
        return
    if schema.get("additionalProperties") is not False:
        raise ValueError(f"{where}: object schemas must set additionalProperties: false")
    if "required" not in schema:
        raise ValueError(f"{where}: object schemas must declare `required`")
    for name, sub in schema.get("properties", {}).items():
        if isinstance(sub, dict):
            assert_strict_compatible(sub, f"{where}.{name}")
