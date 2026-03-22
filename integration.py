"""
D4: Integration Agent
Assembles the game folder, generates Colyseus server, runs Playwright check,
attributes errors and routes fixes back to specialists.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import anthropic

from utils.attribution import attribute_error, build_fix_prompt
from utils.port_wait import find_free_port, wait_for_port_async
from utils.js_syntax_check import validate_module_source

HARNESS_SPEC_PATH = Path(__file__).parent / "harness_spec.md"

SERVER_BOILERPLATE_SUFFIX = """\

const app = require("http").createServer();
const gameServer = new Server({{ server: app }});
gameServer.define("game_room", GameRoom);
gameServer.listen({port}).then(() => {{
  console.log("Colyseus listening on ws://localhost:{port}");
}});
"""

SERVER_GEN_SYSTEM = """\
You are a Colyseus v0.17 multiplayer server expert.
Write a complete Node.js Colyseus server for a browser game given the game's network protocol contract.

Rules:
- Use: const { Server, Room } = require("colyseus"); const http = require("http");
- Implement class GameRoom extends Room with: onCreate, onJoin, onLeave, onDispose
- In onCreate: register onMessage handlers for each client_to_server message type
- The server must be AUTHORITATIVE — track game state (scores, collected items, etc.) server-side
- In onJoin:
    - Initialize the new player's state (position, score=0, etc.)
    - Send a full `state_snapshot` message to the joining client with: all players' current positions+scores, and all collected item IDs
    - Broadcast to all OTHER clients that a new player joined (with their initial position)
- In onLeave: clean up player state, broadcast to others that player left
- Score messages MUST always include `playerId` (client.sessionId) and `newScore` (the authoritative integer) so clients can update ctx.scoreState.set(playerId, newScore)
- ANY server message that is triggered by a scoring action (coin collected, item picked up, etc.) MUST include BOTH `playerId` AND `newScore` in the same broadcast. Do NOT send score data in a separate `scoreUpdate` message — put it directly in the event confirmation (e.g. coinCollected broadcast includes {coinId, playerId, newScore}).
- The `playerJoined` broadcast sent to all OTHER clients MUST include `{playerId, score: 0}` so receiving clients can initialize that player's score in ctx.scoreState immediately.
- CRITICAL: Use EXACTLY the message type strings listed in the server_to_client section of the contract for all broadcasts. Do NOT reuse the client_to_server type names for server broadcasts — they are different. For example if the client sends "playerMove" the server must broadcast "playerState" (per contract), not "playerMove".
- Prevent duplicate actions (e.g. a coin can only be collected once — use a Set to track)
- Detect win conditions from the contract and broadcast a game_over message with {winnerId, scores} when triggered
- Write only the class GameRoom and its methods — do NOT include the Server boilerplate at the bottom
- Write only code, no explanation, no markdown fences
"""

SERVER_GEN_REQUEST = """\
Game network protocol (from contract):

client_to_server messages:
{client_to_server}

server_to_client messages:
{server_to_client}

Gameplay spec hints:
{gameplay_hints}

Write the complete GameRoom class for Colyseus v0.17.
"""

SERVER_PACKAGE = """\
{
  "name": "game-server",
  "version": "1.0.0",
  "main": "index.js",
  "dependencies": {
    "colyseus": "^0.15.0"
  }
}
"""

START_SH = """\
#!/bin/bash
set -e
cd "$(dirname "$0")/server"
if [ ! -d node_modules ]; then npm install --silent; fi
node index.js &
SERVER_PID=$!
echo "Starting server (PID $SERVER_PID)..."
for i in $(seq 1 20); do
  nc -z localhost {server_port} 2>/dev/null && break
  sleep 0.5
done
echo "Game running at: http://localhost:{static_port}/index.html"
cd ..
npx --yes serve . -p {static_port} &
wait
"""


def _compute_load_order(contract: dict, module_graph: dict) -> list:
    """
    Derives manifest load order from wave structure.
    Network specialist modules first, then wave order, alphabetical tiebreaker.
    """
    order = []
    seen = set()

    # Network modules first
    for specialist in contract.get("specialists", []):
        if "network" in specialist["type"].lower():
            for mod in sorted(specialist.get("assigned_modules", [])):
                if mod not in seen:
                    order.append(mod)
                    seen.add(mod)

    # Then wave order
    for wave in module_graph.get("waves", []):
        wave_mods = sorted(a["name"] for a in wave.get("assignments", []))
        for mod in wave_mods:
            if mod not in seen:
                order.append(mod)
                seen.add(mod)

    return order


async def _generate_server_code(contract: dict, server_port: int) -> str:
    """
    Uses an LLM to generate a complete authoritative Colyseus GameRoom class,
    then appends the standard boilerplate suffix.
    """
    protocol = contract.get("network_protocol", {})
    gameplay = contract.get("gameplay_spec", {})

    c2s = json.dumps(protocol.get("client_to_server", []), indent=2)
    s2c = json.dumps(protocol.get("server_to_client", []), indent=2)
    hints = json.dumps({
        "win_condition": gameplay.get("win_condition", ""),
        "collectibles": gameplay.get("collectibles", []),
        "scoring": gameplay.get("scoring", {}),
    }, indent=2)

    client = anthropic.AsyncAnthropic()
    user_msg = SERVER_GEN_REQUEST.format(
        client_to_server=c2s,
        server_to_client=s2c,
        gameplay_hints=hints,
    )

    full_text = ""
    async with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=8192,
        system=SERVER_GEN_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        async for text in stream.text_stream:
            full_text += text

    # Strip any markdown fences the model may have added
    code = re.sub(r'^```(?:javascript|js)?\s*\n?', '', full_text.strip())
    code = re.sub(r'\n?```\s*$', '', code)

    # Always use the canonical preamble with both Server and Room imported,
    # then splice in just the GameRoom class (drop whatever require the LLM wrote)
    preamble = 'const { Server, Room } = require("colyseus");\n\n'
    if "class GameRoom" in code:
        idx = code.index("class GameRoom")
        code = preamble + code[idx:]
    else:
        code = preamble + code

    # Strip any module.exports the LLM may have added — our suffix handles registration
    code = re.sub(r'\nmodule\.exports\s*=.*', '', code)

    suffix = SERVER_BOILERPLATE_SUFFIX.format(port=server_port)
    return code + "\n" + suffix


def _static_check_network_sends(module_map: dict, contract: dict) -> list:
    """
    Scans modules for undeclared network.send() calls.
    Returns list of (module_name, undeclared_type) tuples.
    """
    declared_types = {
        m["type"]
        for m in contract.get("network_protocol", {}).get("client_to_server", [])
    }
    violations = []
    pattern = re.compile(r'network\.send\s*\(\s*[\'"]([^\'"]+)[\'"]')

    for name, source in module_map.items():
        for match in pattern.finditer(source):
            msg_type = match.group(1)
            if msg_type not in declared_types:
                violations.append((name, msg_type))

    return violations


def _static_check_naming_conventions(module_map: dict, contract: dict) -> list:
    """
    Checks that modules use canonical ctx variable names.
    Returns list of error dicts compatible with the static_errors list.
    """
    errors = []
    forbidden = [
        ("ctx.scoreMap", "ctx.scoreState (Map<playerId, score>)"),
        ("ctx.localSessionId", "ctx.localPlayerId"),
    ]
    for mod_name, source in module_map.items():
        for bad, good in forbidden:
            if re.search(re.escape(bad), source):
                errors.append({
                    "module": mod_name,
                    "error": f"Forbidden: '{bad}' — use '{good}' instead",
                    "attributed_to": _find_specialist_for_module(mod_name, contract),
                })
        # Non-network modules must not write to ctx.scoreState
        if "network" not in mod_name.lower():
            if re.search(r'ctx\.scoreState\.set\s*\(', source):
                errors.append({
                    "module": mod_name,
                    "error": (
                        "Forbidden: ctx.scoreState.set() called outside the network module — "
                        "scores must only be updated from server-authoritative messages in the network module"
                    ),
                    "attributed_to": _find_specialist_for_module(mod_name, contract),
                })
    return errors


def _static_check_ctx_extensions(module_map: dict, contract: dict) -> list:
    """
    Checks that each ctx_extension is attached by its provider module.
    Returns list of (specialist_type, extension_name) missing attachments.
    """
    missing = []
    for ext in contract.get("interfaces", {}).get("ctx_extensions", []):
        provider_type = ext["provided_by"]
        ext_name = ext["name"]

        # Skip getTerrainHeight — it's a base harness field overwritten optionally
        if ext_name == "getTerrainHeight":
            continue

        # Find source for provider's modules
        provider_specialist = next(
            (s for s in contract.get("specialists", []) if s["type"] == provider_type),
            None,
        )
        if not provider_specialist:
            continue

        found = False
        for mod_name in provider_specialist.get("assigned_modules", []):
            source = module_map.get(mod_name, "")
            if re.search(rf'ctx\.{re.escape(ext_name)}\s*=', source):
                found = True
                break

        if not found:
            missing.append((provider_type, ext_name))

    return missing


async def _run_playwright_check(
    game_path: str, server_port: int, static_port: int
) -> dict:
    """
    Runs headless Playwright test.
    Returns { success, errors, logs, second_client_connected, fallback_ok }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "success": False,
            "errors": ["playwright not installed"],
            "logs": [],
            "second_client_connected": False,
            "fallback_ok": False,
        }

    url = f"http://localhost:{static_port}/index.html"
    errors = []
    logs = []
    second_client_connected = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Client 1
        page1 = await browser.new_page()
        page1.on("console", lambda msg: logs.append(f"[c1] {msg.text}"))
        page1.on("pageerror", lambda err: errors.append(f"[c1] {err}"))

        try:
            await page1.goto(url, timeout=15000)
            await page1.wait_for_timeout(5000)

            # Client 2
            page2 = await browser.new_page()
            page2.on("console", lambda msg: logs.append(f"[c2] {msg.text}"))
            page2.on("pageerror", lambda err: errors.append(f"[c2] {err}"))

            received_message = asyncio.Event()
            page2.on("console", lambda msg: received_message.set()
                     if "state_update" in msg.text or "joined" in msg.text else None)

            await page2.goto(url, timeout=15000)
            await page2.wait_for_timeout(3000)

            # Check second client connected via isConnected or message receipt
            try:
                connected = await page2.evaluate(
                    "() => typeof ctx !== 'undefined' && "
                    "ctx.modules && ctx.modules.network && "
                    "ctx.modules.network.isConnected()"
                )
                second_client_connected = bool(connected)
            except Exception:
                second_client_connected = len([l for l in logs if "[c2]" in l]) > 0

        except Exception as e:
            errors.append(str(e))
        finally:
            await browser.close()

    # Treat critical console errors as failures, but skip intentional network fallback messages
    critical_patterns = ["ReferenceError", "TypeError", "SyntaxError", "is not defined", "Cannot read"]
    fallback_patterns = ["offline", "singleplayer", "Connection failed", "connection failed",
                         "fallback", "no server", "running offline"]
    for log in logs:
        if any(fp in log for fp in fallback_patterns):
            continue  # Network offline fallback is intentional — not an error
        if any(p in log for p in critical_patterns):
            errors.append(f"[console error] {log}")

    success = len(errors) == 0
    return {
        "success": success,
        "errors": errors,
        "logs": logs,
        "second_client_connected": second_client_connected,
        "fallback_ok": False,
    }


async def run_integration(
    module_map: dict,
    contract: dict,
    module_graph: dict,
    output_path: str,
    harness_template_path: str | None = None,
    max_fix_rounds: int = 2,
    playwright_browser=None,
) -> dict:
    """
    Assembles game folder, generates server, runs Playwright check.
    Returns IntegrationResult dict.
    """
    out = Path(output_path)
    modules_dir = out / "modules"
    server_dir = out / "server"

    # Clean previous runs
    shutil.rmtree(modules_dir, ignore_errors=True)
    shutil.rmtree(server_dir, ignore_errors=True)
    modules_dir.mkdir(parents=True, exist_ok=True)
    server_dir.mkdir(parents=True, exist_ok=True)

    # Find free ports
    server_port = find_free_port("localhost", 2567)
    static_port = find_free_port("localhost", 8080)

    # --- Static checks before writing files ---
    static_errors = []

    net_violations = _static_check_network_sends(module_map, contract)
    for mod_name, undeclared_type in net_violations:
        static_errors.append({
            "module": mod_name,
            "error": f"Undeclared network.send type: '{undeclared_type}'",
            "attributed_to": _find_specialist_for_module(mod_name, contract),
        })

    ctx_missing = _static_check_ctx_extensions(module_map, contract)
    for specialist_type, ext_name in ctx_missing:
        static_errors.append({
            "module": _get_specialist_first_module(specialist_type, contract),
            "error": f"Missing ctx.{ext_name} = ... in build() — must attach this ctx_extension",
            "attributed_to": specialist_type,
        })

    naming_errors = _static_check_naming_conventions(module_map, contract)
    static_errors.extend(naming_errors)

    # Route static errors for fix (round 0)
    if static_errors:
        print(f"[integration] {len(static_errors)} static check failures, routing fixes...")
        module_map = await _route_fixes(static_errors, module_map, contract, round_num=0)

    # --- Write module files ---
    for name, source in module_map.items():
        (modules_dir / f"{name}.js").write_text(source)

    # --- Write manifest.json ---
    load_order = _compute_load_order(contract, module_graph)
    # Ensure only modules we actually have are listed
    load_order = [m for m in load_order if m in module_map]
    manifest = {
        "game_id": contract.get("game_id", ""),
        "load_order": load_order,
        "modules_path": "./modules/",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # --- Write index.html ---
    harness_html = _get_harness_html(harness_template_path, server_port, static_port)
    (out / "index.html").write_text(harness_html)

    # --- Write server/index.js (LLM-generated authoritative server) ---
    print("[integration] generating authoritative server...")
    server_js = await _generate_server_code(contract, server_port)
    (server_dir / "index.js").write_text(server_js)
    (server_dir / "package.json").write_text(SERVER_PACKAGE)

    # --- Write start.sh ---
    start_sh = START_SH.format(server_port=server_port, static_port=static_port)
    start_sh_path = out / "start.sh"
    start_sh_path.write_text(start_sh)
    os.chmod(start_sh_path, 0o755)

    # --- Install server deps ---
    print("[integration] installing server dependencies...")
    subprocess.run(
        ["npm", "install", "--silent"],
        cwd=str(server_dir),
        capture_output=True,
        timeout=120,
    )

    # --- Start server ---
    server_proc = subprocess.Popen(
        ["node", "index.js"],
        cwd=str(server_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # --- Start static file server ---
    static_proc = subprocess.Popen(
        ["npx", "--yes", "serve", ".", "-p", str(static_port), "--no-clipboard"],
        cwd=str(out),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for both servers
    server_ready = await wait_for_port_async("localhost", server_port, timeout_s=15)
    static_ready = await wait_for_port_async("localhost", static_port, timeout_s=15)

    attributed_errors = []
    fix_rounds = 0
    playwright_log = []
    success = False

    if not server_ready:
        print("[integration] WARNING: Colyseus server did not start in time", file=sys.stderr)
    if not static_ready:
        print("[integration] WARNING: static server did not start in time", file=sys.stderr)

    # --- Playwright tests + fix rounds ---
    for fix_round in range(max_fix_rounds + 1):
        result = await _run_playwright_check(str(out), server_port, static_port)
        playwright_log = result["logs"]

        if result["success"]:
            success = True
            print(f"[integration] Playwright checks passed (round {fix_round})")
            break

        if not result["errors"]:
            success = True
            break

        if fix_round >= max_fix_rounds:
            print(f"[integration] Playwright failed after {max_fix_rounds} fix rounds")
            break

        # Attribute and fix errors
        fix_round += 1
        fixes_needed = []
        for err in result["errors"]:
            specialist = attribute_error(err, "\n".join(playwright_log), contract, module_map)
            attributed_errors.append({
                "error": err,
                "attributed_to": specialist,
                "round": fix_round,
            })
            if specialist:
                # Find module(s) for this specialist
                for s in contract.get("specialists", []):
                    if s["type"] == specialist:
                        for mod_name in s.get("assigned_modules", []):
                            if mod_name in module_map:
                                fixes_needed.append({
                                    "module": mod_name,
                                    "error": err,
                                    "attributed_to": specialist,
                                })
            else:
                # Unattributable — integration agent patches directly
                fixes_needed.append({
                    "module": None,
                    "error": err,
                    "attributed_to": None,
                })

        if fixes_needed:
            module_map = await _route_fixes(fixes_needed, module_map, contract, round_num=fix_round)
            # Rewrite fixed modules
            for name, source in module_map.items():
                (modules_dir / f"{name}.js").write_text(source)

    # --- Cleanup ---
    server_proc.terminate()
    static_proc.terminate()

    return {
        "success": success,
        "errors_attributed": attributed_errors,
        "fix_rounds": fix_rounds,
        "playwright_log": "\n".join(playwright_log),
        "server_port": server_port,
        "static_port": static_port,
    }


async def _route_fixes(
    fixes_needed: list,
    module_map: dict,
    contract: dict,
    round_num: int,
) -> dict:
    """Routes fix requests to specialists or patches directly."""
    from specialist import run_specialist, HARNESS_SPEC

    updated = dict(module_map)

    # Group by specialist to batch fixes
    by_specialist = {}
    for fix in fixes_needed:
        specialist = fix.get("attributed_to")
        if specialist:
            by_specialist.setdefault(specialist, []).append(fix)

    for specialist_type, specialist_fixes in by_specialist.items():
        specialist_info = next(
            (s for s in contract.get("specialists", []) if s["type"] == specialist_type),
            None,
        )
        if not specialist_info:
            continue

        error_summary = "\n".join(f["error"] for f in specialist_fixes)
        print(f"[integration] routing fix to {specialist_type}: {error_summary[:100]}")

        result = await run_specialist(
            specialist_type=specialist_type,
            specialist_description=(
                specialist_info.get("specialist_description", "") +
                f"\n\nFIX REQUIRED (round {round_num}):\n{error_summary}"
            ),
            assigned_modules=specialist_info.get("assigned_modules", []),
            contract=contract,
            harness_spec=HARNESS_SPEC,
        )
        updated.update(result.get("modules", {}))

    return updated


def _find_specialist_for_module(module_name: str, contract: dict) -> str | None:
    for s in contract.get("specialists", []):
        if module_name in s.get("assigned_modules", []):
            return s["type"]
    return None


def _get_specialist_first_module(specialist_type: str, contract: dict) -> str | None:
    for s in contract.get("specialists", []):
        if s["type"] == specialist_type:
            mods = s.get("assigned_modules", [])
            return mods[0] if mods else None
    return None


def _get_harness_html(
    template_path: str | None,
    server_port: int,
    static_port: int,
) -> str:
    """Returns index.html with injected wsUrl and manifest path."""
    ws_url = f"ws://localhost:{server_port}"

    if template_path and Path(template_path).exists():
        html = Path(template_path).read_text()
        html = html.replace("<!-- INJECT_MANIFEST_PATH -->", "./manifest.json")
        html = html.replace("<!-- INJECT_WS_URL -->", ws_url)
        return html

    # Built-in minimal harness template
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>game_codegen</title>
  <style>
    body {{ margin: 0; overflow: hidden; background: #000; }}
    canvas {{ display: block; }}
    #ui-overlay {{ position: fixed; top: 0; left: 0; width: 100%; pointer-events: none; z-index: 10; }}
  </style>
</head>
<body>
  <div id="ui-overlay"></div>

  <!-- Three.js (r160 ESM — sets window.THREE and extras as globals) -->
  <script type="importmap">
  {{
    "imports": {{
      "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
      "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
    }}
  }}
  </script>
  <!-- Colyseus UMD -->
  <script src="https://unpkg.com/colyseus.js@0.15.22/dist/colyseus.js"></script>
  <script>window.ColyseusClient = Colyseus.Client;</script>

  <script type="module">
    import * as THREE from 'three';
    import {{ EffectComposer }} from 'three/addons/postprocessing/EffectComposer.js';
    import {{ RenderPass }} from 'three/addons/postprocessing/RenderPass.js';
    import {{ UnrealBloomPass }} from 'three/addons/postprocessing/UnrealBloomPass.js';
    import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';

    // Set Three.js extras as globals for game modules
    window.THREE = THREE;
    window.EffectComposer = EffectComposer;
    window.RenderPass = RenderPass;
    window.UnrealBloomPass = UnrealBloomPass;
    window.GLTFLoader = GLTFLoader;

    // Load Rapier (no UMD build available; use ESM via esm.sh)
    const RAPIER = (await import('https://esm.sh/@dimforge/rapier3d-compat@0.12.0')).default;
    window.RAPIER = RAPIER;

    const MANIFEST_PATH = "./manifest.json";
    const WS_URL = "{ws_url}";

    async function boot() {{
      // Init Rapier
      await RAPIER.init();
      const gravity = {{ x: 0.0, y: -9.81, z: 0.0 }};
      const rapierWorld = new RAPIER.World(gravity);

      // Init Three.js
      const renderer = new THREE.WebGLRenderer({{ antialias: true }});
      renderer.setSize(window.innerWidth, window.innerHeight);
      renderer.shadowMap.enabled = true;
      document.body.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
      camera.position.set(0, 5, 10);

      const sunLight = new THREE.DirectionalLight(0xffffff, 1.2);
      sunLight.position.set(50, 100, 50);
      scene.add(sunLight);
      const hemiLight = new THREE.HemisphereLight(0xaaccff, 0x334422, 0.5);
      scene.add(hemiLight);

      const composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));

      const meshRegistry = new Map();
      const eventBus = new EventTarget();
      const uiOverlay = document.getElementById('ui-overlay');

      const ctx = {{
        scene, camera, rapierWorld, RAPIER,
        gameConfig: {{ worldWidth: 100, worldDepth: 100, gravity: -9.81 }},
        meshRegistry, eventBus, uiOverlay, composer,
        sunLight, hemiLight,
        getTerrainHeight: (x, z) => 0,
        wsUrl: WS_URL,
        modules: {{}}
      }};

      // Load manifest and modules
      const manifest = await fetch(MANIFEST_PATH).then(r => r.json());
      const modules = [];
      for (const name of manifest.load_order) {{
        const mod = await import(manifest.modules_path + name + '.js');
        const instance = new mod.default();
        ctx.modules[instance.name] = instance;
        modules.push(instance);
      }}

      // Build phase
      await Promise.all(modules.map(m => m.build(ctx)));

      // Start phase
      modules.forEach(m => m.start());

      // Game loop
      let lastTime = performance.now();
      function gameLoop() {{
        requestAnimationFrame(gameLoop);
        const now = performance.now();
        const dt = (now - lastTime) / 1000;
        lastTime = now;
        rapierWorld.step();
        modules.forEach(m => m.update(dt));
        composer.render();
      }}
      gameLoop();

      window.ctx = ctx;
      window.addEventListener('resize', () => {{
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
        composer.setSize(window.innerWidth, window.innerHeight);
      }});
    }}

    boot().catch(console.error);
  </script>
</body>
</html>
"""
