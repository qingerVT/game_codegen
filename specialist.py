"""
D2: Specialist Runner
Generic runner for any specialist type.

Input:  SpecialistInput (specialist_type, specialist_description, assigned_modules, contract, harness_spec)
Output: SpecialistOutput (specialist_type, modules, iterations, duration_s, trace)
"""

import asyncio
import json
import time
from pathlib import Path
from typing import TypedDict

import anthropic

from utils.js_syntax_check import extract_js_from_response, validate_module_source
from utils.contract_filter import filter_contract_for_specialist, summarize_payload_shapes
from utils.design_constraints import load_design_constraints

DESIGN_CONSTRAINTS = load_design_constraints()

HARNESS_SPEC_PATH = Path(__file__).parent / "harness_spec.md"
HARNESS_SPEC = HARNESS_SPEC_PATH.read_text()

SYSTEM_TEMPLATE = """You are a specialist game module developer. Your role: {specialist_type}.

{specialist_description}

## Your Runtime Environment

{harness_spec}

## Your Contract Clauses

You only need to implement the clauses where you are listed as provider or consumer.
Here are your relevant contract sections:

{filtered_contract_json}

## Rules
- Write ES modules: `export default class`. Never `require()` or `import`.
- Your class `name` field MUST exactly match the module name assigned to you.
- Never access `ctx.modules.X` inside `build()` — only in `start()` or `update()`.
- If you provide a ctx_extension, attach it to `ctx` in `build()`: `ctx.extensionName = ...`
- NEVER call `requestAnimationFrame` — the harness calls `update(dt)` for you. For UI/HUD modules, update DOM elements inside `update(dt)`, not in a rAF loop.
- Remove ALL event listeners and Rapier bodies in `dispose()`.
- Do NOT use import statements — THREE, RAPIER, GLTFLoader, EffectComposer, UnrealBloomPass, ColyseusClient are globals.
- Use `ctx.localPlayerId` for the local player's ID — read it in `start()`, never in `build()` (network sets it during build).
- Use `ctx.scoreState` (a Map<playerId, score>) as the shared score state. Update it from server messages only — do NOT increment scores locally before server confirmation.
- HUD/score modules MUST display ALL players' scores as a leaderboard by reading `ctx.scoreState` every `update(dt)`.
"""

NETWORK_EXTRA = """
## Network Specialist Requirements
- Expose on ctx.modules.network (attach in build()):
  - `send(type, payload)` — sends a message to the server
  - `onMessage(type, callback)` — STACKS handlers; multiple modules may call onMessage for the same type and ALL callbacks fire. Implement this with a Map<type, callback[]> internally.
  - `isConnected()` — returns boolean connection state
- Connect to Colyseus at `ctx.wsUrl` using the global `ColyseusClient`
- ALWAYS use the room name `"game_room"` — this is what the server registers. Never invent a different room name.
- Implement singleplayer fallback: if connection fails within `fallback_timeout_seconds`
  (from contract multiplayer_spec, default 3), silently continue offline.
  In offline mode, `send()` is a no-op (never throws). `isConnected()` returns false.
- Do NOT buffer messages for replay — just drop them in offline mode.
- In `build()`, set `ctx.localPlayerId = null` and `ctx.scoreState = new Map()` as placeholders.
- After connecting, set `ctx.localPlayerId = room.sessionId` (or a fallback string in offline mode).
- Handle the server's initial state message on join: populate `ctx.scoreState` with all players' scores, and spawn meshes for already-present remote players.
- Handle `playerJoined` (and any equivalent server broadcast): call `ctx.scoreState.set(data.playerId, 0)` immediately so the new player appears in the leaderboard at score 0. Also spawn their avatar mesh.
- Handle ALL server messages that carry a score (e.g. `coinCollected`, `itemPickedUp`, any event with `{playerId, newScore}`): call `ctx.scoreState.set(playerId, newScore)`. Do NOT wait for a separate `scoreUpdate` — if the scoring confirmation itself includes `newScore`, update scoreState there and then.
- Handle `playerLeft` (and equivalents): call `ctx.scoreState.delete(data.playerId)` and remove their mesh.
- This is the ONLY place scores are written — other modules only READ ctx.scoreState.
"""

REQUEST_TEMPLATE = """Write the JavaScript module(s) for your specialist role.

Assigned module name(s): {module_names}

For EACH module, produce a complete ES module file.
If multiple modules, separate them with:
// === MODULE: <module_name> ===

Each module must:
- export default class with name = '<module_name>'
- implement build(ctx), start(), update(dt), dispose()
- follow all rules from the system prompt

Write only code — no explanation, no markdown fences."""

RETRY_TEMPLATE = """Your previous code had issues:

{issues}

Original code:
```js
{original_code}
```

Fix all issues and return the corrected module. Write only the fixed code — no markdown fences, no explanation, no comments outside the code."""


async def run_specialist(
    specialist_type: str,
    specialist_description: str,
    assigned_modules: list,
    contract: dict,
    harness_spec: str = HARNESS_SPEC,
    _semaphore: asyncio.Semaphore = None,
) -> dict:
    """
    Runs one specialist and returns SpecialistOutput dict.
    """
    started_at = time.monotonic()
    client = anthropic.AsyncAnthropic()

    # Filter contract to only this specialist's clauses
    filtered = filter_contract_for_specialist(contract, specialist_type)
    filtered = summarize_payload_shapes(filtered)
    filtered_json = json.dumps(filtered, indent=2)

    # Build system prompt
    is_network = "network" in specialist_type.lower()
    system = SYSTEM_TEMPLATE.format(
        specialist_type=specialist_type,
        specialist_description=specialist_description,
        harness_spec=harness_spec,
        filtered_contract_json=filtered_json,
    )
    if is_network:
        system += NETWORK_EXTRA

    if DESIGN_CONSTRAINTS:
        system += "\n\n## Mandatory Design Constraints\n\n" + DESIGN_CONSTRAINTS

    module_names_str = ", ".join(f"'{m}'" for m in assigned_modules)
    user_message = REQUEST_TEMPLATE.format(module_names=module_names_str)

    iterations = 0
    trace_entries = []
    best_modules: dict[str, str] = {}   # cleanest version seen per module
    still_failing = list(assigned_modules)
    parsed: dict[str, str] = {}

    for attempt in range(3):
        iterations += 1
        t0 = time.monotonic()

        if _semaphore:
            async with _semaphore:
                full_text = await _call_llm(client, system, user_message)
        else:
            full_text = await _call_llm(client, system, user_message)

        t1 = time.monotonic()
        trace_entries.append({
            "attempt": attempt + 1,
            "duration_s": round(t1 - t0, 2),
        })

        # Parse only the modules we're currently working on
        parsed = _parse_modules(full_text, still_failing)

        # Validate each parsed module; lock in passing ones immediately
        all_issues = {}
        for name, source in parsed.items():
            issues = validate_module_source(source, name)
            if issues:
                all_issues[name] = issues
            else:
                best_modules[name] = source  # lock in clean version

        still_failing = list(all_issues.keys())

        if not still_failing:
            break

        # Build retry targeting only failing modules
        issues_text = ""
        for name, issues in all_issues.items():
            issues_text += f"\nModule '{name}':\n"
            for issue in issues:
                issues_text += f"  - {issue}\n"

        current_code = "\n\n".join(
            f"// === MODULE: {n} ===\n{parsed.get(n, '')}" for n in still_failing
        )
        user_message = RETRY_TEMPLATE.format(
            issues=issues_text.strip(),
            original_code=current_code,
        )

        print(
            f"[specialist:{specialist_type}] attempt {attempt+1} issues: {issues_text.strip()[:200]}",
        )

    # Best-effort: use last parsed for any still-failing modules
    if still_failing:
        for name in still_failing:
            if name in parsed and name not in best_modules:
                best_modules[name] = parsed[name]

    all_modules = best_modules

    duration_s = round(time.monotonic() - started_at, 2)
    return {
        "specialist_type": specialist_type,
        "modules": all_modules,
        "iterations": iterations,
        "duration_s": duration_s,
        "trace": trace_entries,
        "error": None if all_modules else "Failed to produce modules after 3 attempts",
    }


async def _call_llm(
    client: anthropic.AsyncAnthropic,
    system: str,
    user_message: str,
    max_tokens: int = 64000,
) -> str:
    """Streams a response and returns the full text."""
    full_text = ""
    async with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        async for text in stream.text_stream:
            full_text += text
    return full_text


def _parse_modules(response_text: str, assigned_modules: list) -> dict:
    """
    Parses one or more module sources from an LLM response.
    Handles both single-module and multi-module (separated by // === MODULE: name ===) responses.
    """
    import re

    # Check for multi-module separator
    separator_pattern = r'//\s*===\s*MODULE:\s*(\w+)\s*==='
    parts = re.split(separator_pattern, response_text)

    if len(parts) > 1:
        # Multi-module format: [before_first, name1, code1, name2, code2, ...]
        modules = {}
        i = 1
        while i + 1 < len(parts):
            name = parts[i].strip()
            code = extract_js_from_response(parts[i + 1].strip())
            if name in assigned_modules:
                modules[name] = code
            i += 2
        return modules

    # Single module
    code = extract_js_from_response(response_text)
    if len(assigned_modules) == 1:
        return {assigned_modules[0]: code}

    # Multiple modules expected but no separator — try to split on export default
    exports = re.split(r'(?=export\s+default\s+class)', code)
    exports = [e.strip() for e in exports if e.strip()]

    if len(exports) == len(assigned_modules):
        return {name: src for name, src in zip(assigned_modules, exports)}

    # Fallback: assign same code to first module and flag as error
    return {assigned_modules[0]: code} if assigned_modules else {}
