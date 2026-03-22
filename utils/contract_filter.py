"""
Filters contract.json to only the clauses relevant to a given specialist.
Keeps system prompt concise and prevents specialists from reading others' concerns.
"""

import copy


def filter_contract_for_specialist(contract: dict, specialist_type: str) -> dict:
    """
    Returns a reduced contract containing only:
    - ctx_extensions where provided_by == specialist_type or specialist_type in consumed_by
    - events where emitted_by == specialist_type or specialist_type in consumed_by
    - mesh_registry where provided_by == specialist_type or specialist_type in consumed_by
    - network_protocol (always included in full for the network specialist;
      included as read-only reference for others)
    - gameplay_spec, multiplayer_spec, visual_spec (always included)
    - The specialist's own entry from specialists[]
    """
    filtered = {
        "game_id": contract.get("game_id", ""),
        "prompt": contract.get("prompt", ""),
        "gameplay_spec": copy.deepcopy(contract.get("gameplay_spec", {})),
        "multiplayer_spec": copy.deepcopy(contract.get("multiplayer_spec", {})),
        "visual_spec": copy.deepcopy(contract.get("visual_spec", {})),
        "contract_warnings": contract.get("contract_warnings", []),
    }

    # Own specialist entry
    own_specialist = next(
        (s for s in contract.get("specialists", []) if s["type"] == specialist_type),
        None
    )
    filtered["specialist"] = copy.deepcopy(own_specialist) if own_specialist else {}

    # Filter ctx_extensions
    filtered["interfaces"] = {
        "ctx_extensions": [
            copy.deepcopy(ext)
            for ext in contract.get("interfaces", {}).get("ctx_extensions", [])
            if ext["provided_by"] == specialist_type
            or specialist_type in ext.get("consumed_by", [])
        ],
        "events": [
            copy.deepcopy(event)
            for event in contract.get("interfaces", {}).get("events", [])
            if event["emitted_by"] == specialist_type
            or specialist_type in event.get("consumed_by", [])
        ],
        "mesh_registry": [
            copy.deepcopy(mesh)
            for mesh in contract.get("interfaces", {}).get("mesh_registry", [])
            if mesh["provided_by"] == specialist_type
            or specialist_type in mesh.get("consumed_by", [])
        ],
    }

    # Network protocol:
    # - network specialist gets the full protocol
    # - all others get only the message types they send (client_to_server)
    #   so they know what's legal to call via ctx.modules.network.send()
    is_network = "network" in specialist_type.lower()
    if is_network:
        filtered["network_protocol"] = copy.deepcopy(contract.get("network_protocol", {}))
    else:
        # Non-network specialists only need to know what to send
        filtered["network_protocol"] = {
            "client_to_server": copy.deepcopy(
                contract.get("network_protocol", {}).get("client_to_server", [])
            ),
            "server_to_client": copy.deepcopy(
                contract.get("network_protocol", {}).get("server_to_client", [])
            ),
            "_note": "Use ctx.modules.network.send(type, payload) to send. "
                     "Use ctx.modules.network.onMessage(type, cb) to receive."
        }

    return filtered


def estimate_token_count(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def summarize_payload_shapes(filtered_contract: dict) -> dict:
    """
    If filtered contract is too large (>800 token estimate for the interfaces section),
    truncate payload_shape descriptions to just field names.
    """
    import json
    interfaces_str = json.dumps(filtered_contract.get("interfaces", {}))
    if estimate_token_count(interfaces_str) <= 800:
        return filtered_contract

    # Summarize payload shapes
    result = copy.deepcopy(filtered_contract)
    for section in ["ctx_extensions", "events", "mesh_registry"]:
        for item in result.get("interfaces", {}).get(section, []):
            if "payload_shape" in item and isinstance(item["payload_shape"], dict):
                item["payload_shape"] = {k: "..." for k in item["payload_shape"]}

    for direction in ["client_to_server", "server_to_client"]:
        for msg in result.get("network_protocol", {}).get(direction, []):
            if "payload_shape" in msg and isinstance(msg["payload_shape"], dict):
                msg["payload_shape"] = {k: "..." for k in msg["payload_shape"]}

    return result
