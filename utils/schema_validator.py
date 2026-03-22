"""
Validates contract.json and module_graph against their JSON schemas.
"""
from __future__ import annotations

import jsonschema

CONTRACT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "contract",
    "type": "object",
    "required": ["game_id", "prompt", "specialists", "interfaces",
                 "network_protocol", "gameplay_spec", "multiplayer_spec",
                 "visual_spec", "contract_warnings"],
    "properties": {
        "game_id":  {"type": "string"},
        "prompt":   {"type": "string"},
        "specialists": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["type", "specialist_description", "assigned_modules"],
                "properties": {
                    "type":                   {"type": "string"},
                    "specialist_description": {"type": "string"},
                    "assigned_modules": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "interfaces": {
            "type": "object",
            "required": ["ctx_extensions", "events", "mesh_registry"],
            "properties": {
                "ctx_extensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "type", "description", "provided_by", "consumed_by"],
                        "properties": {
                            "name":        {"type": "string"},
                            "type":        {"type": "string"},
                            "description": {"type": "string"},
                            "provided_by": {"type": "string"},
                            "consumed_by": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "payload_shape", "emitted_by", "consumed_by"],
                        "properties": {
                            "name":          {"type": "string"},
                            "payload_shape": {"type": "object"},
                            "emitted_by":    {"type": "string"},
                            "consumed_by":   {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "mesh_registry": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "description", "provided_by", "consumed_by"],
                        "properties": {
                            "name":        {"type": "string"},
                            "description": {"type": "string"},
                            "provided_by": {"type": "string"},
                            "consumed_by": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                }
            }
        },
        "network_protocol": {
            "type": "object",
            "required": ["client_to_server", "server_to_client"],
            "properties": {
                "client_to_server": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "payload_shape", "description"],
                        "properties": {
                            "type":          {"type": "string"},
                            "payload_shape": {"type": "object"},
                            "description":   {"type": "string"}
                        }
                    }
                },
                "server_to_client": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "payload_shape", "description"],
                        "properties": {
                            "type":          {"type": "string"},
                            "payload_shape": {"type": "object"},
                            "description":   {"type": "string"},
                            "target": {"type": "string"}
                        }
                    }
                }
            }
        },
        "gameplay_spec": {
            "type": "object",
            "required": ["win_conditions", "fail_conditions", "collectibles", "player_config"],
            "properties": {
                "win_conditions":  {"type": "array", "items": {"type": "string"}},
                "fail_conditions": {"type": "array", "items": {"type": "string"}},
                "collectibles": {
                    "type": "array",
                    "items": {"type": "object"}
                },
                "player_config": {
                    "type": "object",
                    "properties": {
                        "max_players":     {"type": "integer"},
                        "respawn_enabled": {"type": "boolean"},
                        "move_speed":      {"type": "number"},
                        "jump_impulse":    {"type": "number"}
                    }
                }
            }
        },
        "multiplayer_spec": {
            "type": "object",
            "required": ["max_players", "sync_rate_hz", "singleplayer_fallback"],
            "properties": {
                "max_players":              {"type": "integer"},
                "sync_rate_hz":             {"type": "number"},
                "singleplayer_fallback":    {"type": "boolean"},
                "fallback_timeout_seconds": {"type": "number"}
            }
        },
        "visual_spec": {
            "type": "object",
            "properties": {
                "sky_color":         {"type": "string"},
                "fog_enabled":       {"type": "boolean"},
                "fog_near":          {"type": "number"},
                "fog_far":           {"type": "number"},
                "bloom_enabled":     {"type": "boolean"},
                "ambient_intensity": {"type": "number"}
            }
        },
        "contract_warnings": {
            "type": "array",
            "items": {"type": "string"}
        }
    }
}

MODULE_GRAPH_SCHEMA = {
    "type": "object",
    "required": ["waves"],
    "properties": {
        "waves": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["wave", "assignments"],
                "properties": {
                    "wave": {"type": "string"},
                    "assignments": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["name", "specialist", "depends_on"],
                            "properties": {
                                "name":       {"type": "string"},
                                "specialist": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}}
                            }
                        }
                    }
                }
            }
        }
    }
}


def validate_contract(contract: dict) -> list[str]:
    """Returns list of validation error messages. Empty list = valid."""
    errors = []

    # JSON Schema validation
    validator = jsonschema.Draft7Validator(CONTRACT_SCHEMA)
    for error in validator.iter_errors(contract):
        errors.append(f"Schema: {error.json_path}: {error.message}")

    if errors:
        return errors

    specialist_types = {s["type"] for s in contract.get("specialists", [])}

    # Must have at least one network specialist
    has_network = any("network" in s["type"].lower() for s in contract["specialists"])
    if not has_network:
        errors.append("Contract must contain a specialist with 'network' in its type")

    # Must have at least one non-network specialist
    non_network = [s for s in contract["specialists"] if "network" not in s["type"].lower()]
    if not non_network:
        errors.append("Contract must contain at least one non-network domain specialist")

    # No specialist with empty assigned_modules
    for s in contract["specialists"]:
        if not s.get("assigned_modules"):
            errors.append(f"Specialist '{s['type']}' has no assigned_modules")
        if len(s.get("assigned_modules", [])) > 3:
            errors.append(f"Specialist '{s['type']}' has {len(s['assigned_modules'])} modules (max 3)")

    # Must have at least one event
    if not contract["interfaces"].get("events"):
        errors.append("Contract must contain at least one event in interfaces.events")

    # Must have at least one network message in each direction
    if not contract["network_protocol"].get("client_to_server"):
        errors.append("Contract must contain at least one client_to_server message")
    if not contract["network_protocol"].get("server_to_client"):
        errors.append("Contract must contain at least one server_to_client message")

    # Referential integrity: provided_by / consumed_by must match known specialist types
    for ext in contract["interfaces"].get("ctx_extensions", []):
        if ext["provided_by"] not in specialist_types:
            errors.append(f"ctx_extension '{ext['name']}' provided_by unknown specialist '{ext['provided_by']}'")
        for c in ext.get("consumed_by", []):
            if c not in specialist_types:
                errors.append(f"ctx_extension '{ext['name']}' consumed_by unknown specialist '{c}'")

    for event in contract["interfaces"].get("events", []):
        if event["emitted_by"] not in specialist_types:
            errors.append(f"event '{event['name']}' emitted_by unknown specialist '{event['emitted_by']}'")
        for c in event.get("consumed_by", []):
            if c not in specialist_types:
                errors.append(f"event '{event['name']}' consumed_by unknown specialist '{c}'")

    for mesh in contract["interfaces"].get("mesh_registry", []):
        if mesh["provided_by"] not in specialist_types:
            errors.append(f"mesh '{mesh['name']}' provided_by unknown specialist '{mesh['provided_by']}'")

    return errors


def validate_module_graph(graph: dict, contract: dict | None = None) -> list[str]:
    """Validates ModuleGraph structure. Returns list of error messages."""
    errors = []

    validator = jsonschema.Draft7Validator(MODULE_GRAPH_SCHEMA)
    for error in validator.iter_errors(graph):
        errors.append(f"Schema: {error.json_path}: {error.message}")

    if errors:
        return errors

    all_names = []
    seen_names = set()

    for wave in graph["waves"]:
        for assignment in wave["assignments"]:
            name = assignment["name"]
            # Validate module name format
            import re
            if not re.match(r'^[a-z][a-z0-9_]*$', name):
                errors.append(f"Module name '{name}' must match ^[a-z][a-z0-9_]*$")
            # Duplicate check
            if name in seen_names:
                errors.append(f"Duplicate module name '{name}' across waves")
            seen_names.add(name)
            all_names.append(name)

    # Validate depends_on references: must refer to modules in prior waves
    prior_names = set()
    for wave in graph["waves"]:
        for assignment in wave["assignments"]:
            for dep in assignment.get("depends_on", []):
                if dep not in prior_names:
                    errors.append(
                        f"Module '{assignment['name']}' depends_on '{dep}' "
                        f"which is not in a prior wave (forward reference or typo)"
                    )
        # After checking all assignments in this wave, add them to prior
        for assignment in wave["assignments"]:
            prior_names.add(assignment["name"])

    # Validate that each specialist in graph has a matching entry in contract
    if contract:
        contract_specialist_types = {s["type"] for s in contract.get("specialists", [])}
        for wave in graph["waves"]:
            for assignment in wave["assignments"]:
                if assignment["specialist"] not in contract_specialist_types:
                    errors.append(
                        f"Module '{assignment['name']}' references specialist "
                        f"'{assignment['specialist']}' not found in contract.specialists"
                    )

    return errors
