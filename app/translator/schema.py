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

def normalize_schema_for_cca(schema: Any, is_claude: bool = False) -> Any:
    if not isinstance(schema, dict):
        return schema

    cleaned: Dict[str, Any] = {}
    is_nullable = False
    if schema.get("nullable") is True:
        is_nullable = True

    unions = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(unions, list):
        for u in unions:
            if isinstance(u, dict):
                if u.get("type") in ("null", None) or (isinstance(u.get("type"), list) and "null" in u["type"]):
                    is_nullable = True

    for k, v in schema.items():
        if k in UNSUPPORTED_SCHEMA_KEYS:
            continue

        if k == "type":
            if isinstance(v, list):
                non_null_types = [t for t in v if t not in ("null", None)]
                if len(non_null_types) < len(v):
                    is_nullable = True
                if non_null_types:
                    cleaned["type"] = str(non_null_types[0]).lower()
                else:
                    cleaned["type"] = "string"
                    is_nullable = True
            elif isinstance(v, str):
                if v.lower() == "null":
                    cleaned["type"] = "string"
                    is_nullable = True
                else:
                    cleaned["type"] = v.lower()
            else:
                cleaned["type"] = str(v).lower()
        elif k == "properties" and isinstance(v, dict):
            cleaned[k] = {prop_name: normalize_schema_for_cca(prop_schema, is_claude=is_claude) for prop_name, prop_schema in v.items()}
        elif k == "items":
            if isinstance(v, dict):
                union_items = v.get("anyOf") or v.get("oneOf")
                if isinstance(union_items, list):
                    merged_props = {}
                    for variant in union_items:
                        if isinstance(variant, dict):
                            merged_props.update(variant.get("properties", {}))
                    cleaned["items"] = normalize_schema_for_cca({
                        "type": "object",
                        "properties": merged_props
                    }, is_claude=is_claude)
                else:
                    cleaned[k] = normalize_schema_for_cca(v, is_claude=is_claude)
            elif isinstance(v, list):
                cleaned[k] = [normalize_schema_for_cca(i, is_claude=is_claude) for i in v]
            else:
                cleaned[k] = v
        elif k in ("anyOf", "oneOf"):
            if is_claude:
                continue
            elif isinstance(v, list):
                cleaned[k] = [normalize_schema_for_cca(i, is_claude=is_claude) for i in v]
            else:
                cleaned[k] = v
        elif k == "allOf" and isinstance(v, list):
            cleaned[k] = [normalize_schema_for_cca(i, is_claude=is_claude) for i in v]
        elif isinstance(v, dict):
            cleaned[k] = normalize_schema_for_cca(v, is_claude=is_claude)
        else:
            cleaned[k] = v

    if "type" not in cleaned:
        if "properties" in cleaned:
            cleaned["type"] = "object"
        elif isinstance(unions, list) and unions:
            for u in unions:
                if isinstance(u, dict):
                    t = u.get("type")
                    if t and t not in ("null", None):
                        cleaned["type"] = str(t if isinstance(t, str) else t[0]).lower()
                        if "items" in u and "items" not in cleaned:
                            cleaned["items"] = normalize_schema_for_cca(u["items"], is_claude=is_claude)
                        break
            if "type" not in cleaned:
                cleaned["type"] = "string"
        else:
            cleaned["type"] = "string"

    if is_nullable:
        cleaned["nullable"] = True

    if cleaned.get("type") == "object" and "properties" not in cleaned:
        cleaned["properties"] = {}

    return cleaned
