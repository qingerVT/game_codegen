# Harness Environment Specification

## IGameModule Interface

Every module you write must export a default class conforming to this interface:

```js
export default class ModuleName {
  name = 'module_name';        // must match the assigned module name exactly

  async build(ctx) { }         // called once at startup — set up scene objects,
                               // physics bodies, event listeners here

  start() { }                  // called after ALL modules have finished build()
                               // safe to reference other modules here via ctx.modules

  update(dt) { }               // called every frame, dt = delta time in seconds

  dispose() { }                // called on teardown — clean up listeners, bodies, meshes
}
```

Rules:
- Your file is loaded as an ES module in the browser (`<script type="module">`) — use `export default class`, never `require()` or `module.exports`
- `build()` is async — you may await inside it
- Do NOT reference `ctx.modules.X` inside `build()` — other modules may not exist yet
- DO reference `ctx.modules.X` inside `start()` or `update()` — all modules are built by then
- `dispose()` must remove all Rapier rigid bodies and THREE objects you created
- Do NOT set up your own `requestAnimationFrame` loop — the harness calls `update(dt)` for you

## Game Loop (driven by harness)

The harness drives the lifecycle in this order:

1. All modules' `build(ctx)` called concurrently (per wave order in manifest)
2. All modules' `start()` called after every `build()` completes
3. Every frame: all modules' `update(dt)` called in manifest order
4. On teardown: all modules' `dispose()` called

---

## GameContext (ctx) Shape

`ctx` is passed into `build()` and is the same object reference throughout the game lifetime. Its base shape:

```js
{
  // THREE.js
  scene,             // THREE.Scene — add your meshes here
  camera,            // THREE.PerspectiveCamera — reposition but don't replace
  composer,          // EffectComposer — for post-processing passes
  sunLight,          // THREE.DirectionalLight — already in scene
  hemiLight,         // THREE.HemisphereLight — already in scene

  // Physics
  rapierWorld,       // RAPIER.World — create rigid bodies and colliders here
  RAPIER,            // RAPIER namespace — use for enums, constructors, etc.

  // Game config
  gameConfig,        // { worldWidth, worldDepth, gravity } — values set per game

  // Shared resources
  meshRegistry,      // Map<string, THREE.Mesh> — store/retrieve shared meshes by name
  eventBus,          // EventTarget — use for cross-module events
  uiOverlay,         // HTMLDivElement — append your UI elements here

  // Network
  wsUrl,             // string — Colyseus server URL
  localPlayerId,     // string — set by the network module in build(). Read in start(), never in build().
                     // Equals the Colyseus sessionId when connected, or a local fallback string offline.

  // Shared score state — set by the network or score module in build()
  scoreState,        // Map<playerId, score> — authoritative scores for all players.
                     // Updated from server messages. HUD modules read this every update().

  // Module references (available after all build() calls complete)
  modules,           // object keyed by module name — access any module via ctx.modules.<name>
}
```

**ctx_extensions:** Some fields beyond the base shape above are attached to ctx by other specialists during their `build()`. These are defined in `contract.interfaces.ctx_extensions`. If your specialist consumes a ctx_extension, it will be available on ctx by `start()` — do not access it in `build()`.

If your specialist **provides** a ctx_extension, attach it directly to ctx in `build()`:

```js
async build(ctx) {
  ctx.getTerrainHeight = (x, z) => { /* your implementation */ };
}
```

---

## Global Variables (no imports needed)

These are available on the global scope — do NOT import them:

```
THREE              // Three.js full namespace
RAPIER             // Rapier physics namespace
GLTFLoader         // Three.js GLTF loader
EffectComposer     // Three.js post-processing composer
UnrealBloomPass    // Three.js bloom pass
ColyseusClient     // Colyseus client
```

---

## Network Convention

All server communication must go through the network module — never use `ColyseusClient` directly:

```js
ctx.modules.network.send(type, payload)       // send message to server
ctx.modules.network.onMessage(type, callback) // register message handler — STACKS multiple handlers for the same type
```

`onMessage` **stacks** callbacks — multiple modules may register for the same message type and all handlers will fire. Do not assume you are the only listener.

The network module handles singleplayer fallback transparently — your module does not need to check if a server is connected.

**The Colyseus room is always named `"game_room"`.**

### Server-Authoritative Scoring

The server is the single source of truth for scores. Follow this pattern to avoid double-counting:

1. **On local action** (e.g. coin touch): remove the object from the scene immediately (client-side prediction for feel), dispatch the local event, and `send()` the action to the server. **Do NOT increment score locally.**
2. **On server confirmation** (e.g. `coin_collected` message with `newScore`): update `ctx.scoreState.set(playerId, newScore)` for the given player. This is the only place scores change.
3. **HUD modules** read `ctx.scoreState` every `update(dt)` and render scores for ALL players.

```js
// ✅ Correct — server-authoritative
ctx.modules.network.onMessage('coin_collected', ({ coinId, playerId, newScore }) => {
  ctx.scoreState.set(playerId, newScore);   // authoritative score
  removeCoinMesh(coinId);                   // idempotent — safe to call again
});

// ❌ Wrong — double-counts when server echoes back to sender
this._localScore++;                         // local increment
ctx.modules.network.send('collect_coin', { coinId });
// then also handling the server echo: this._localScore++ again
```

### Network Module Contract

The network module must set these on `ctx` in `build()`:
- `ctx.localPlayerId` — the local player's session ID
- `ctx.scoreState` — an empty `Map<string, number>()` (populated by score/network module from server messages)

**Player join/leave:** When the server notifies that a player joined (any `playerJoined`-style message), the network module must immediately call `ctx.scoreState.set(playerId, 0)` so the HUD shows the new player at score 0 from the start. When a player leaves, call `ctx.scoreState.delete(playerId)`.

**Score updates from ANY message:** Every server message that contains a `newScore` field (or equivalent) must be handled by calling `ctx.scoreState.set(playerId, newScore)`. This includes scoring-event confirmations like `coinCollected`, `itemPickedUp`, etc. — not only a dedicated `scoreUpdate` message. Do NOT wait for a separate `scoreUpdate` message if the scoring event itself already contains the updated score.

```js
// ✅ On player join — initialize score immediately
room.onMessage('playerJoined', (data) => {
  ctx.scoreState.set(data.playerId, 0);
  // also spawn remote avatar mesh ...
});

// ✅ On any scoring confirmation that carries newScore
room.onMessage('coinCollected', (data) => {
  ctx.scoreState.set(data.playerId, data.newScore);   // REQUIRED
  removeCoinVisual(data.coinId);
});

// ✅ On initial state_snapshot — populate ALL players' scores
room.onMessage('state_snapshot', (data) => {
  for (const [pid, score] of Object.entries(data.scores)) {
    ctx.scoreState.set(pid, score);
  }
  ctx.localPlayerId = room.sessionId;
});
```

---

## EventBus Convention

Use the native EventTarget API. Event names and payload shapes are defined in `contract.interfaces.events`.

```js
// emit
ctx.eventBus.dispatchEvent(new CustomEvent('event_name', {
  detail: { /* payload per contract */ }
}));

// listen
ctx.eventBus.addEventListener('event_name', (e) => {
  const payload = e.detail;
});
```

Always remove listeners in `dispose()`:

```js
ctx.eventBus.removeEventListener('event_name', this._handler);
```

---

## MeshRegistry Convention

Use `meshRegistry` to share Three.js meshes between modules. Ownership is defined in `contract.interfaces.mesh_registry`.

```js
// providing module — set in build()
ctx.meshRegistry.set('mesh_name', myMesh);

// consuming module — get in start() after all builds complete
const myMesh = ctx.meshRegistry.get('mesh_name');
```

Only the specialist listed in `provided_by` sets a mesh. All others only get.
