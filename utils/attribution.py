"""
Attributes runtime errors to the owning specialist using contract definitions.
"""
from __future__ import annotations

import re


def attribute_error(
    error_text: str,
    stack_trace: str,
    contract: dict,
    module_map: dict | None = None
) -> str | None:
    """
    Attempts to match error text/stack against contract definitions.
    Returns the responsible specialist_type, or None if unattributable.

    Matching rules (in priority order):
    1. Error mentions a ctx_extension.name → return provided_by
    2. Error mentions an event.name in emit context → return emitted_by
    3. Error mentions a mesh_registry.name → return provided_by
    4. Error mentions a network_protocol message type → return network specialist type
    5. Stack trace mentions a module filename → return owning specialist
    6. Return None
    """
    combined = f"{error_text}\n{stack_trace}".lower()

    interfaces = contract.get("interfaces", {})

    # Rule 1: ctx_extension name
    for ext in interfaces.get("ctx_extensions", []):
        name = ext["name"].lower()
        if name in combined:
            return ext["provided_by"]

    # Rule 2: event name
    for event in interfaces.get("events", []):
        name = event["name"].lower()
        if name in combined:
            return event["emitted_by"]

    # Rule 3: mesh_registry name
    for mesh in interfaces.get("mesh_registry", []):
        name = mesh["name"].lower()
        if name in combined:
            return mesh["provided_by"]

    # Rule 4: network_protocol message type
    network_types = set()
    for msg in contract.get("network_protocol", {}).get("client_to_server", []):
        network_types.add(msg["type"].lower())
    for msg in contract.get("network_protocol", {}).get("server_to_client", []):
        network_types.add(msg["type"].lower())

    for msg_type in network_types:
        if msg_type in combined:
            # Find the network specialist type
            for s in contract.get("specialists", []):
                if "network" in s["type"].lower():
                    return s["type"]

    # Rule 5: module filename in stack trace
    if module_map:
        for module_name in module_map:
            if module_name.lower() in combined or f"{module_name}.js" in combined:
                # Find which specialist owns this module
                for s in contract.get("specialists", []):
                    if module_name in s.get("assigned_modules", []):
                        return s["type"]

    return None


def build_fix_prompt(
    error_text: str,
    attributed_specialist: str,
    contract: dict,
    original_source: str,
    module_name: str
) -> str:
    """
    Builds a targeted fix prompt for the responsible specialist.
    """
    return (
        f"Your module '{module_name}' caused a runtime error:\n\n"
        f"```\n{error_text}\n```\n\n"
        f"You are the '{attributed_specialist}' specialist. "
        f"Review your implementation and fix the issue.\n\n"
        f"Your current source:\n```js\n{original_source}\n```\n\n"
        f"Return only the corrected module source code."
    )
