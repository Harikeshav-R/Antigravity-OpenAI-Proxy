from typing import Any, Dict

UNSUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "title",
    "additionalProperties",
    "patternProperties",
    "propertyNames",
    "$id",
    "default",
}

def normalize_schema_for_cca(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema

    cleaned: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in UNSUPPORTED_SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, dict):
            cleaned[k] = {prop_name: normalize_schema_for_cca(prop_schema) for prop_name, prop_schema in v.items()}
        elif k == "items" and isinstance(v, (dict, list)):
            cleaned[k] = normalize_schema_for_cca(v) if isinstance(v, dict) else [normalize_schema_for_cca(i) for i in v]
        elif isinstance(v, dict):
            cleaned[k] = normalize_schema_for_cca(v)
        else:
            cleaned[k] = v

    if cleaned.get("type") == "object" and "properties" not in cleaned:
        cleaned["properties"] = {}

    return cleaned
