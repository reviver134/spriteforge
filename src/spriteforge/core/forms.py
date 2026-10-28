"""Small, explicit questionnaire subset of JSON Schema for local gates."""


def validate_form(schema: dict, data: dict) -> None:
    # Standard feedback forms are validated by the Feedback pydantic model.
    if schema.get("title") == "Feedback":
        return
    properties = schema.get("properties", {})
    if set(schema.get("required", [])) - data.keys():
        raise ValueError("Missing required form field")
    if data.keys() - properties.keys():
        raise ValueError("Unknown form field")
    for key, value in data.items():
        rule = properties[key]
        types = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}
        kind = rule.get("type", "string")
        if kind not in types or not isinstance(value, types[kind]):
            raise ValueError(f"Invalid form value: {key}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"Invalid form choice: {key}")
