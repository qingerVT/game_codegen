# game_codegen

A parallel AI-agent pipeline that converts a natural-language game prompt into a fully playable, browser-based multiplayer game using Three.js, Rapier, and Colyseus.

```
INPUT:  "a third-person platformer where you collect coins"
OUTPUT: output/{game_id}/
        ├── index.html
        ├── modules/
        ├── manifest.json
        ├── server/
        ├── start.sh
        ├── contract.json
        └── trace.json

ACCEPTANCE: bash start.sh -> open two browser tabs -> play
```

## Usage

```bash
python orchestrator.py "a coin platformer on floating islands"
```

## How It Works

A Planner agent decomposes the prompt into a wave-structured dependency graph and a `contract.json` interface agreement. A swarm of specialist agents then build their modules in parallel, each seeing only the contract — never each other's code. An integration agent assembles the output, generates the Colyseus server, and runs a headless Playwright check.

---

## Prompt Best Practices

The quality of the generated game is directly bounded by the quality of the prompt. The Planner must decompose your prompt into specialist domains and invent a contract — the more concrete and domain-specific your prompt, the cleaner the contract.

### Describe mechanics, not vibes

The Planner needs to identify distinct gameplay systems. Name them explicitly.

| Weak | Strong |
|---|---|
| "a fun multiplayer game" | "a top-down arena shooter where players shoot projectiles at each other and score points on hits" |
| "a game where you explore" | "a third-person platformer where you jump between floating islands and collect coins" |
| "a competitive game" | "a racing game where players drive through ordered checkpoints and the first to complete three laps wins" |

Each concrete mechanic becomes a specialist domain. Vague prompts produce overlapping domains and a contract full of warnings.

### Name the player interaction explicitly

Include: what the player **controls**, what the player **does**, and what **happens as a result**.

```
"players control a character that runs and jumps (control)
 to collect coins scattered across floating islands (action)
 and the first player to collect 10 coins wins (consequence)"
```

This directly maps to: physics/player specialist, gameplay/collectibles specialist, UI/score specialist.

### Name the multiplayer mechanic explicitly

State what must sync across clients. The network specialist is scoped entirely by this.

- "coin collection syncs scores to all players" → `coin_collected` event + score broadcast
- "projectile hits register across tabs" → `projectile_hit` message + damage broadcast
- "checkpoint progress syncs" → `checkpoint_reached` message + leaderboard broadcast

If you don't name the sync mechanic, the Planner invents one — which may not match your intent.

### State win and fail conditions

The `gameplay_spec` in the contract is built from these. Without them the Planner guesses.

```
"win: first player to collect all 20 coins
 fail: falling off the world (y < -20)"
```

### Scope the environment

Name the setting and any environmental features the player interacts with. This scopes the environment specialist.

- "floating islands at varied heights" → island geometry, varied elevations, `getTerrainHeight`
- "flat arena with walls" → simple floor, boundary colliders, no terrain height needed
- "procedural terrain with hills" → heightmap generation, `getTerrainHeight` with real values

Unscoped environments produce either an empty flat plane or an over-engineered landscape.

### Avoid these patterns

**Compound mechanics with no clear ownership boundary:**
```
# Bad — "fight" and "collect" and "environment reacts" all bleed into each other
"a game where players fight and collect things and the environment reacts"

# Better — each mechanic has a clear owner
"a game where players shoot each other for points (gameplay)
 and pick up health packs that spawn on the ground (collectibles)
 and the floor tiles change color when walked on (environment effect)"
```

**Referencing external IP or complex game systems:**
```
# Bad — requires deep domain knowledge the Planner doesn't have
"a game like Dark Souls with a stamina system and parry windows"

# Better — describe the mechanics directly
"a melee combat game where players have a stamina bar that depletes on attack
 and recovers when idle, and hits stagger the opponent briefly"
```

**Ambiguous player count:**
```
# Bad
"a multiplayer game"

# Better
"a 2–4 player game" or "exactly 2 players compete"
```

### Prompt template

```
[PERSPECTIVE] [PLAYER COUNT] [GENRE] where [PLAYER ACTION]
to [OBJECTIVE] and [WIN CONDITION].
[FAIL CONDITION].
Setting: [ENVIRONMENT DESCRIPTION].
Multiplayer: [WHAT SYNCS ACROSS CLIENTS].
```

Example:
```
Third-person, 2–4 players, platformer where players jump between floating islands
to collect coins and the first to 10 coins wins.
Falling below y=-20 is instant death with respawn.
Setting: floating rock islands at varied heights with gaps between them.
Multiplayer: coin collection and scores sync to all players in real time.
```

---

## Design Notes

### What class of prompt produces a bad contract

**Vague domain boundaries** are the primary cause. A prompt like *"a game where players fight and collect things and the environment reacts"* cannot be cleanly decomposed — the Planner cannot determine whether physics, gameplay, or environment owns the reaction logic. The result is a contract where two specialists both claim `provided_by` for the same `ctx_extension`, or where the same event has two plausible emitters.

**Signs of a bad contract:**
- `contract_warnings[]` is non-empty after planning
- Two specialists share overlapping `specialist_description` scope
- `interfaces.events[]` is empty (no cross-module communication defined)

**How to fix it:**
- Review `contract_warnings[]` before specialists run — the orchestrator pauses for 60 seconds to allow edits
- Sharpen the prompt: name concrete mechanics rather than vibes ("players shoot projectiles that register hits on other players" rather than "players fight")
- Manually edit `contract.json` to resolve ownership: assign each `ctx_extension` a single `provided_by` and move the other to `consumed_by`

**Extremely simple prompts** (e.g., "a box that moves") produce contracts with too few specialists — technically valid but producing an unplayable game. The minimum is one network specialist and at least one domain specialist covering gameplay logic.

---

### What breaks when a module deviates from the network protocol vs. a bad contract

These are two distinct failure modes with very different symptoms:

**Network protocol deviation** (a module calls `ctx.modules.network.send('unknown_type', ...)` with a type not in `contract.network_protocol.client_to_server`):
- The Colyseus server has no handler for that type — the message is silently dropped
- The game continues running but multiplayer state does not sync correctly
- No crash, no error in the console — the failure is invisible without a two-client test
- Attribution: the module that sent the undeclared type is responsible, not the network specialist

**Bad contract implementation** (a specialist declared as `provided_by` for a `ctx_extension` does not attach it to `ctx` in `build()`):
- Every consuming specialist crashes immediately with `TypeError: ctx.X is not a function`
- The consumer wrote correct code — the fault is entirely in the provider
- Loud, immediate, easy to attribute: the error names the missing field, the contract names its `provided_by`

**The key distinction:** network deviations degrade silently and affect only multiplayer sync; bad contract implementations crash loudly and affect every consumer. Loud crashes are easier to detect and fix automatically. Silent deviations require the two-client Playwright test to surface.

---

### What degrades the 10-minute target most in practice

The dominant factor is **LLM call latency for large specialists**. A specialist assigned 3 modules with a complex contract (many events, many `ctx_extensions`) requires a large prompt and produces a large response. With up to 2 retries for syntax errors, a single specialist can consume 90+ seconds — and because the wave ceiling is the *slowest* specialist, not the sum, one slow specialist stalls all downstream waves.

**Ranked degraders:**

1. **Over-scoped specialists** — more than 2 modules per specialist significantly increases response size and retry risk. The Planner enforces a 3-module cap; keeping it to 1–2 is better.
2. **Retry cascades** — syntax errors in generated code trigger retries. Three specialists each needing 2 retries can double a wave's wall-clock time.
3. **npm install cold start** — the first run of `start.sh` installs Colyseus server dependencies (~20–40 seconds). Mitigated by pre-installing during Phase 2 while specialists run.
4. **Playwright cold start** — browser launch adds ~15–25 seconds. Mitigated by pre-warming the browser at orchestrator startup.
5. **Integration fix rounds** — each fix round re-runs the full Playwright test (~30 seconds per round, max 2 rounds).

**The 10-minute budget breaks down as:** Planner (~30s) + Wave A specialists in parallel (~2 min) + Wave B specialists in parallel (~2 min) + Integration + Playwright (~1 min) = ~5–6 minutes typical. Budget is consumed by retries and cold starts.

---

### How to support human editing of contract.json before subagents run

The orchestrator inserts a natural pause between planning and specialist execution:

1. The Planner writes `contract.json` to `output/{game_id}/contract.json`
2. If `contract_warnings[]` is non-empty, the orchestrator prints the warnings and the file path, then waits up to 60 seconds for a keypress before auto-proceeding
3. If no warnings, it proceeds immediately
4. After the pause, the orchestrator re-reads `contract.json` from disk (not from the in-memory planner output) and re-validates it before launching any specialist

**What is safe to edit:**
- `gameplay_spec` — win/fail conditions, collectible values, player speed, jump force
- `visual_spec` — sky color, fog, bloom settings
- `multiplayer_spec` — max players, sync rate, fallback timeout
- `specialist_description` — clarify or sharpen a specialist's scope
- `network_protocol` payload shapes — rename or add fields
- `contract_warnings` — clear entries once resolved

**What requires re-running the planner:**
- Adding or removing specialists
- Changing `specialists[].assigned_modules`
- Adding a new `ctx_extension` (requires a corresponding specialist as `provided_by`)
- Changing the wave structure or dependency order

If the edited file fails validation, the orchestrator reports the errors and waits for another edit rather than proceeding with a broken contract.
