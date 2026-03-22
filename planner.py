"""
D1: Planner Agent
Prompt in → { module_graph, contract } out.

Usage:
    python planner.py "a coin platformer on floating islands"
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import anthropic

from utils.schema_validator import validate_contract, validate_module_graph
from utils.design_constraints import load_design_constraints

HARNESS_SPEC = Path(__file__).parent / "harness_spec.md"
DESIGN_CONSTRAINTS = load_design_constraints()

SYSTEM_PROMPT = """You are a game architecture planner for a parallel AI agent pipeline.

Given a game prompt, you will produce two JSON artifacts:

1. **module_graph** — a wave-structured dependency plan telling the orchestrator how to schedule specialist agents.
2. **contract** — a shared interface agreement that every specialist agent must honor.

## Rules

### Specialists
- Invent specialist types per prompt — do NOT use a fixed menu. Names should reflect gameplay domains.
- ALWAYS create a dedicated "network" specialist (type must contain "network"). It handles all Colyseus comms.
- Keep specialists focused: 1–2 modules each, max 3. Too many modules = slow LLM response = pipeline timeout.
- Every specialist must have a clear, non-overlapping domain.

### ModuleGraph
- Group assignments into waves. Wave A = no dependencies. Wave B = depends on Wave A modules, etc.
- `depends_on` must only reference module names from PRIOR waves (no forward references).
- Network is always in Wave A (no dependencies).
- Module names must be lowercase letters/numbers/underscores only: `^[a-z][a-z0-9_]*$`

### Contract
- Before assigning specialists, enumerate EVERY value that crosses a module boundary.
- For each ctx_extension, assign exactly ONE `provided_by` specialist. Never two.
- For each event, assign exactly ONE `emitted_by` specialist.
- `provided_by` and `consumed_by` values MUST match specialist `type` values exactly.
- Describe network_protocol messages in both directions.
- Add `contract_warnings` for any ambiguities you cannot resolve.

### Output format
Return ONLY valid JSON with this exact structure (no prose, no markdown fences):
{
  "module_graph": {
    "waves": [
      {
        "wave": "A",
        "assignments": [
          { "name": "module_name", "specialist": "specialist_type", "depends_on": [] }
        ]
      }
    ]
  },
  "contract": {
    "game_id": "<game_id>",
    "prompt": "<prompt>",
    "contract_warnings": [],
    "specialists": [
      {
        "type": "specialist_type",
        "specialist_description": "...",
        "assigned_modules": ["module_name"]
      }
    ],
    "interfaces": {
      "ctx_extensions": [
        {
          "name": "extensionName",
          "type": "TypeDescription",
          "description": "...",
          "provided_by": "specialist_type",
          "consumed_by": ["other_specialist_type"]
        }
      ],
      "events": [
        {
          "name": "eventName",
          "payload_shape": { "field": "type" },
          "emitted_by": "specialist_type",
          "consumed_by": ["other_specialist_type"]
        }
      ],
      "mesh_registry": [
        {
          "name": "meshKey",
          "description": "...",
          "provided_by": "specialist_type",
          "consumed_by": ["other_specialist_type"]
        }
      ]
    },
    "network_protocol": {
      "client_to_server": [
        { "type": "msgType", "description": "...", "payload_shape": { "field": "type" } }
      ],
      "server_to_client": [
        { "type": "msgType", "description": "...", "payload_shape": { "field": "type" }, "target": "broadcast" }
      ]
    },
    "gameplay_spec": {
      "win_conditions": ["..."],
      "fail_conditions": ["..."],
      "collectibles": [],
      "player_config": {}
    },
    "multiplayer_spec": {
      "max_players": 2,
      "sync_rate_hz": 20,
      "singleplayer_fallback": true,
      "fallback_timeout_seconds": 3
    },
    "visual_spec": {}
  }
}
"""

USER_TEMPLATE = """Game prompt: {prompt}

Produce the module_graph and contract JSON for this game.
The contract game_id should be: {game_id}

Remember:
- Invent specialist types freely based on the prompt domains
- Network is always its own specialist in Wave A
- Every cross-boundary dependency must be declared in interfaces
- Module names must match ^[a-z][a-z0-9_]*$
- Max 3 modules per specialist
- At least 1 event and 1 network message in each direction

Return ONLY the JSON object, no explanation."""

# Design constraints from DESIGN.md — appended at runtime
_DESIGN_CONSTRAINTS_HEADER = "\n\n## Mandatory Design Constraints\n\nThe following constraints MUST be reflected in the contract you produce:\n\n"


async def run_planner(prompt: str, game_id: str | None = None) -> dict:
    """
    Calls the LLM planner and returns { module_graph, contract }.
    Validates output and re-prompts up to 2 times on failure.
    """
    if game_id is None:
        game_id = str(uuid.uuid4())[:8]

    client = anthropic.AsyncAnthropic()

    last_errors = []
    for attempt in range(3):
        system_with_constraints = SYSTEM_PROMPT
        if DESIGN_CONSTRAINTS:
            system_with_constraints += _DESIGN_CONSTRAINTS_HEADER + DESIGN_CONSTRAINTS

        user_content = USER_TEMPLATE.format(prompt=prompt, game_id=game_id)

        if attempt > 0 and last_errors:
            user_content += (
                f"\n\nPrevious attempt failed validation. Fix these errors:\n"
                + "\n".join(f"- {e}" for e in last_errors)
            )

        # Use streaming for robustness with large responses
        full_text = ""
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=64000,
            thinking={"type": "adaptive"},
            system=system_with_constraints,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            async for text in stream.text_stream:
                full_text += text

        # Parse JSON
        try:
            # Strip any accidental markdown fences
            text = full_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())
        except json.JSONDecodeError as e:
            last_errors = [f"JSON parse error: {e}"]
            print(f"[planner] attempt {attempt+1} JSON parse failed: {e}", file=sys.stderr)
            continue

        module_graph = result.get("module_graph", {})
        contract = result.get("contract", {})

        # Inject game_id and prompt if missing
        contract.setdefault("game_id", game_id)
        contract.setdefault("prompt", prompt)
        contract.setdefault("contract_warnings", [])

        # Validate
        graph_errors = validate_module_graph(module_graph, contract)
        contract_errors = validate_contract(contract)
        all_errors = graph_errors + contract_errors

        if not all_errors:
            print(f"[planner] success on attempt {attempt+1}", file=sys.stderr)
            return {"module_graph": module_graph, "contract": contract}

        last_errors = all_errors
        print(
            f"[planner] attempt {attempt+1} validation failed ({len(all_errors)} errors):",
            file=sys.stderr
        )
        for e in all_errors[:5]:
            print(f"  {e}", file=sys.stderr)

    raise ValueError(
        f"Planner failed after 3 attempts. Last errors:\n"
        + "\n".join(last_errors)
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python planner.py \"<game prompt>\"")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    result = asyncio.run(run_planner(prompt))
    print(json.dumps(result, indent=2))
