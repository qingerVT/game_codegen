---
name: colyseus
description: Colyseus 0.15.x multiplayer framework reference — server lifecycle, Schema state, Client SDK, room patterns, and 0.17 delta notes
---

# Colyseus 0.15.x Reference

> Version scope: 0.15.x. Notes on 0.15 breaking changes from 0.14 are marked **[0.15]**. The current release as of 2026 is 0.17 — see the bottom of this file for 0.17 delta notes.

---

## Installation

```bash
npm create colyseus-app@latest ./my-server
cd my-server
npm start
```

Core packages:
- `colyseus` — server
- `colyseus.js` — JavaScript/TypeScript client
- `@colyseus/schema` — state serialization

TypeScript `tsconfig.json` requirements:
```json
{
  "compilerOptions": {
    "experimentalDecorators": true,
    "useDefineForClassFields": false
  }
}
```

---

## Server Bootstrap

```typescript
import { Server } from "colyseus";
import { WebSocketTransport } from "@colyseus/ws-transport";
import { MyRoom } from "./rooms/MyRoom";

const server = new Server({
  transport: new WebSocketTransport({ pingInterval: 10000 }),
  // presence: new RedisPresence("redis://localhost:6379"),  // for clustering
});

server.define("my_room", MyRoom);
server.listen(2567);
```

### `Server` constructor options

| Option | Type | Description |
|--------|------|-------------|
| `transport` | Transport | WebSocketTransport (default) or uWebSocketsTransport |
| `presence` | Presence | LocalPresence (default) or RedisPresence for multi-node |
| `driver` | Driver | Room registry driver; defaults to in-memory |
| `gracefullyShutdown` | boolean | Auto-register SIGTERM handler (default: `true`) |

### `server.define(name, RoomClass, defaultOptions?)`

Registers a room type with the matchmaker.

```typescript
server.define("battle", BattleRoom, { map: "woods" });
```

**Matchmaking filters and sorting:**
```typescript
server.define("battle", BattleRoom)
  .filterBy(["mode", "maxClients"])   // rooms match only when these options match
  .sortBy({ clients: -1 });           // prefer fullest room (-1 desc, 1 asc)
```

### Server utility methods

```typescript
server.simulateLatency(200);   // dev only — adds artificial lag (ms)
server.gracefullyShutdown();   // trigger graceful shutdown manually
```

---

## Room — Server Side

```typescript
import { Room, Client } from "colyseus";
import { MyState } from "./MyState";

export class MyRoom extends Room<MyState> {
  maxClients = 4;
  patchRate = 50;    // ms between state patches (default 50 = 20fps)
  autoDispose = true; // dispose when last client leaves (default true)

  state = new MyState();
}
```

### Room configuration properties

| Property | Default | Notes |
|----------|---------|-------|
| `maxClients` | `Infinity` | Hard cap; room locks automatically when reached |
| `patchRate` | `50` | Milliseconds. Set `null` to disable auto-patch |
| `autoDispose` | `true` | Dispose when empty |
| `roomId` | auto | Can be overridden in `onCreate()` |
| `metadata` | `undefined` | Visible to matchmaker; set only in `onCreate()` |

---

### Lifecycle methods

#### `onCreate(options)`
Called once when matchmaker creates the room. Initialize state, timers, message handlers here.

```typescript
onCreate(options: any) {
  this.state = new MyState();
  this.metadata = { mode: options.mode };
  this.setSimulationInterval((dt) => this.update(dt));
}
```

#### `onAuth(client, options, context)` *(async)*
Return auth data to allow the join; throw/return `false` to reject. Data is passed as `auth` to `onJoin`.

```typescript
async onAuth(client: Client, options: any, context: any) {
  // context.token  — JWT from client
  // context.headers, context.ip
  const user = await verifyToken(context.token);
  if (!user) throw new Error("Unauthorized");
  return user; // becomes client.auth in onJoin
}
```

#### `onJoin(client, options, auth?)` *(async)*
Called after auth passes. Add the player to state here.

```typescript
async onJoin(client: Client, options: any, auth?: any) {
  const player = new Player();
  player.name = auth?.name ?? "Guest";
  this.state.players.set(client.sessionId, player);
}
```

#### `onLeave(client, consented)` *(async)*
Called on intentional disconnect. `consented = true` means the client called `leave()`.

**[0.15] `allowReconnection()` second argument is now mandatory.**

```typescript
async onLeave(client: Client, consented: boolean) {
  this.state.players.get(client.sessionId).connected = false;

  if (!consented) {
    try {
      await this.allowReconnection(client, 30); // seconds; or "manual"
      this.state.players.get(client.sessionId).connected = true;
    } catch {
      this.state.players.delete(client.sessionId);
    }
  } else {
    this.state.players.delete(client.sessionId);
  }
}
```

#### `onDispose()` *(async)*
Final teardown. Persist data, close external connections.

```typescript
async onDispose() {
  await db.saveRoomResult(this.roomId, this.state);
}
```

---

### Message handling

**[0.15+]** The preferred pattern uses a `messages` object declared on the class. The legacy `this.onMessage()` method also works but the object form is cleaner.

```typescript
// Object form (preferred in 0.15+)
messages = {
  "move": (client: Client, payload: { x: number; y: number }) => {
    const player = this.state.players.get(client.sessionId);
    player.x = payload.x;
    player.y = payload.y;
  },

  // Wildcard — catches any type not explicitly registered
  "*": (client: Client, type: string | number, payload: any) => {
    console.warn("Unhandled message:", type, payload);
  },
};

// Method form (also valid)
onCreate(options: any) {
  this.onMessage("fire", (client, payload) => {
    this.broadcast("fired", { by: client.sessionId, ...payload }, { except: client });
  });
}
```

Message `type` can be a `string` or a `number` (number avoids string overhead on hot paths).

---

### Sending messages

#### `this.broadcast(type, message, options?)`
Send to all connected clients.

```typescript
this.broadcast("tick", { time: this.clock.currentTime });

// Exclude sender
this.broadcast("action", payload, { except: client });

// Exclude multiple
this.broadcast("action", payload, { except: [client1, client2] });

// Delay until after next state patch (client gets state + message atomically)
this.broadcast("round_end", results, { afterNextPatch: true });
```

#### `client.send(type, message)`
Send to a single client.

```typescript
client.send("welcome", { sessionId: client.sessionId });
client.send(1, { hp: 100 }); // numeric type
```

#### `client.sendBytes(type, bytes)`
Send raw bytes (skip msgpack encoding).

```typescript
client.sendBytes("raw", new Uint8Array([0x01, 0x02, 0x03]));
```

---

### Client management

```typescript
// Iterate all connected clients
this.clients.forEach((client) => {
  client.send("ping", {});
});

// Look up by sessionId
const target = this.clients.getById(someSessionId);
if (target) target.send("dm", message);

// Force-disconnect a client
client.leave(4001); // code 4000–4999 = custom app codes
```

**Client properties:**

| Property | Type | Description |
|----------|------|-------------|
| `sessionId` | string | Unique per connection; stable across reconnects |
| `auth` | any | Data returned from `onAuth()` |
| `userData` | any | Arbitrary server-side storage, not synced |
| `reconnectionToken` | string | Opaque token for `client.reconnect()` |

---

### State management

Assign a Schema instance to `this.state`. Mutate it directly; never reassign.

```typescript
// Good — mutate in place
this.state.score += 10;
this.state.players.get(id).x = 5;

// Bad — reassignment breaks patch tracking
this.state = new MyState(); // DON'T
```

**Manual patch control** (e.g., tick-driven games):
```typescript
patchRate = null; // disable auto-patch

onCreate() {
  this.clock.setInterval(() => {
    if (this.hasChanges()) this.broadcastPatch();
  }, 100);
}
```

---

### Room control

```typescript
this.lock();    // remove from matchmaker pool (no new joins)
this.unlock();  // re-add to pool
this.disconnect(); // kick all clients, trigger onDispose
this.hasReachedMaxClients(); // boolean
```

**Presence (pub/sub across rooms/processes):**
```typescript
this.presence.publish("game:start", { roomId: this.roomId });
this.presence.subscribe("game:start", (data) => { ... });
this.presence.set("key", "value");
this.presence.get("key");
```

**Clock (lifecycle-safe timers):**
```typescript
this.clock.setTimeout(() => this.broadcast("countdown", 0), 5000);
this.clock.setInterval(() => this.tick(), 16);
this.clock.currentTime; // ms since room creation
```

---

## Schema — State Definition

```typescript
import { Schema, MapSchema, ArraySchema, type } from "@colyseus/schema";

export class Player extends Schema {
  @type("string")  sessionId: string = "";
  @type("number")  x: number = 0;
  @type("number")  y: number = 0;
  @type("boolean") connected: boolean = true;
}

export class GameState extends Schema {
  @type({ map: Player })   players = new MapSchema<Player>();
  @type(["string"])        log     = new ArraySchema<string>();
  @type("number")          tick: number = 0;
}
```

**Primitive types:** `"string"`, `"number"`, `"boolean"`, `"int8"`, `"uint8"`, `"int16"`, `"uint16"`, `"int32"`, `"uint32"`, `"float32"`, `"float64"`

**Collection types:** `{ map: T }`, `["string"]` / `[T]`

**[0.15] `MapSchema` mutation — bracket notation removed:**
```typescript
// 0.14 and earlier
this.state.players[client.sessionId] = new Player(); // BROKEN in 0.15

// 0.15+
this.state.players.set(client.sessionId, new Player());
this.state.players.delete(client.sessionId);
this.state.players.get(client.sessionId);
this.state.players.has(client.sessionId);
```

**Limits:** max 64 `@type` fields per Schema class; nest schemas to work around this.

---

## Client SDK

```typescript
import Colyseus from "colyseus.js";

const client = new Colyseus.Client("ws://localhost:2567");
```

### Connecting to rooms

All methods return `Promise<Room>`.

```typescript
// Join existing or create new (most common)
const room = await client.joinOrCreate("my_room", { mode: "ranked" });

// Join existing only — throws if none available
const room = await client.join("my_room", { mode: "ranked" });

// Always create a new room
const room = await client.create("my_room", { map: "woods" });

// Join a specific room by ID (works for private rooms)
const room = await client.joinById(roomId, options);

// Reconnect after drop — requires server-side allowReconnection()
// [0.15] API changed: single reconnectionToken replaces (roomId, sessionId)
const room = await client.reconnect(reconnectionToken);
```

Cache `room.reconnectionToken` in `localStorage` for reconnect flows:
```typescript
localStorage.setItem("reconnectionToken", room.reconnectionToken);
// On page reload:
const room = await client.reconnect(localStorage.getItem("reconnectionToken"));
```

---

### Room object (client side)

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `state` | Schema | Live synchronized server state |
| `sessionId` | string | This client's unique ID (matches `client.sessionId` server-side) |
| `roomId` | string | Shareable room identifier |
| `name` | string | Room handler name registered on server |
| `reconnectionToken` | string | Opaque token; cache for reconnection |

#### Sending messages

```typescript
room.send("move", { x: 10, y: 5 });
room.send(1, { action: "fire" }); // numeric type
room.sendBytes("raw", new Uint8Array([0x01, 0x02]));
```

#### Receiving messages

```typescript
room.onMessage("powerup", (payload) => {
  console.log("Got powerup:", payload);
});

// Wildcard — all message types
room.onMessage("*", (type, payload) => {
  console.log("Message:", type, payload);
});
```

#### State change callbacks

```typescript
// Fires on every patch
room.onStateChange((state) => {
  render(state);
});

// Fine-grained: fires only once with the first full state
room.onStateChange.once((state) => {
  initializeScene(state);
});
```

**Collection callbacks on state (0.15 syntax):**

**[0.15] Callback registration changed from assignment to method calls.**

```typescript
// 0.14 — property assignment (REMOVED in 0.15)
room.state.players.onAdd = (player, key) => {};

// 0.15+ — method call, returns detach function
const detach = room.state.players.onAdd((player: Player, sessionId: string) => {
  scene.addSprite(sessionId, player);
});

room.state.players.onRemove((player: Player, sessionId: string) => {
  scene.removeSprite(sessionId);
});

// Also: onChange on a collection fires alongside onAdd/onRemove in 0.15
room.state.players.onChange((player: Player, sessionId: string) => {
  scene.updateSprite(sessionId, player);
});

// Property-level listener on a Schema instance
player.listen("x", (newX, prevX) => {
  sprite.x = newX;
});
```

**`onAdd` auto-triggers for existing items (0.15):**
```typescript
// In 0.15, onAdd fires immediately for all existing entries — no need for triggerAll()
room.state.players.onAdd((player, sessionId) => {
  // runs for pre-existing players AND new ones
});

// Opt out of auto-trigger:
room.state.players.onAdd((player, sessionId) => { ... }, false);
```

#### Lifecycle events

```typescript
room.onLeave((code) => {
  // code 1000 = normal; 4000-4999 = app-defined; others = abnormal
  if (code === 1000) showLobby();
});

room.onError((code, message) => {
  console.error("Room error:", code, message);
});

// Leave the room (consented = true tells server it's intentional)
await room.leave();         // consented
await room.leave(false);    // simulates unexpected drop (tests reconnect logic)
```

---

## Common Patterns

### Player tracking

```typescript
// Server — Room
onCreate() {
  this.state = new GameState();
}

onJoin(client: Client, options: any) {
  const player = new Player();
  player.sessionId = client.sessionId;
  this.state.players.set(client.sessionId, player);
  this.broadcast("player_joined", { sessionId: client.sessionId }, { except: client });
}

onLeave(client: Client) {
  this.state.players.delete(client.sessionId);
}
```

### State snapshot on join

The first `onStateChange` fires with the full state (all existing players etc.) before any deltas — no extra "sync" message needed.

```typescript
// Client
room.onStateChange.once((state) => {
  // Bootstrap UI from complete initial state
  state.players.forEach((player, sessionId) => addPlayerSprite(sessionId, player));
});

room.state.players.onAdd((player, sessionId) => {
  addPlayerSprite(sessionId, player);
});
```

### Broadcast with except (sender excluded)

```typescript
// Server
this.onMessage("chat", (client, message: { text: string }) => {
  this.broadcast("chat", {
    from: client.sessionId,
    text: message.text,
  }, { except: client }); // don't echo back to sender
  client.send("chat_ack", { delivered: true });
});
```

### afterNextPatch — atomic state + event delivery

Use when clients must see the updated state before processing the event:

```typescript
this.state.round += 1;
this.broadcast("round_start", { round: this.state.round }, { afterNextPatch: true });
// Clients receive the patch (with new round number) then the message together
```

### Message type conventions

Use string types for clarity in dev; switch to numeric types for high-frequency messages:

```typescript
// Low-frequency: string types are fine
this.onMessage("player_ready", handler);

// High-frequency (e.g., input): use numeric enum
const enum Msg { Input = 1, Fire = 2, Reload = 3 }
this.onMessage(Msg.Input, (client, payload) => { ... });
// client side:
room.send(1, inputPayload);
```

### Reconnection flow

```typescript
// Server
async onLeave(client: Client, consented: boolean) {
  this.state.players.get(client.sessionId).connected = false;
  if (!consented) {
    try {
      await this.allowReconnection(client, 30); // [0.15] argument required
      this.state.players.get(client.sessionId).connected = true;
    } catch {
      this.state.players.delete(client.sessionId);
    }
  } else {
    this.state.players.delete(client.sessionId);
  }
}

// Client — cache token, restore on reload
room.onLeave(() => {
  localStorage.setItem("rcToken", room.reconnectionToken);
});

// On reconnect attempt:
try {
  const token = localStorage.getItem("rcToken");
  const room = await client.reconnect(token); // [0.15] single token arg
  bindRoomHandlers(room);
} catch {
  // token expired or room disposed — go to lobby
}
```

### Manual patchRate (tick-driven server)

```typescript
export class GameRoom extends Room<GameState> {
  patchRate = null; // disable automatic patches

  onCreate() {
    this.setSimulationInterval((dt) => {
      this.physicsUpdate(dt);
      this.broadcastPatch(); // explicit patch each tick
    }, 16); // ~60fps
  }
}
```

### RedisPresence for multi-node (0.15 constructor)

**[0.15]** Connection string passed directly (not as object):

```typescript
import { RedisPresence } from "colyseus";
// 0.14: new RedisPresence({ url: "redis://..." })
// 0.15:
const server = new Server({
  presence: new RedisPresence("redis://localhost:6379"),
});
```

---

## 0.15.x Breaking Changes Summary

| Area | Change |
|------|--------|
| `MapSchema` | `map[key] = val` removed; use `map.set(key, val)` |
| Schema callbacks | `.onAdd = fn` removed; use `.onAdd(fn)` — returns detach fn |
| Schema `onChange` | `changes` array param removed on Schema instances; use `.listen()` |
| `onAdd` | Auto-triggers for existing items; pass `false` to suppress |
| `allowReconnection` | Second arg now mandatory (`number` seconds or `"manual"`) |
| `client.reconnect()` | Takes single `reconnectionToken` instead of `(roomId, sessionId)` |
| `RedisPresence` / `RedisDriver` | Constructor takes connection string directly |
| `@colyseus/arena` | Renamed to `@colyseus/tools`; config file renamed to `app.config.ts` |
| `@colyseus/social` | Fully removed; `client.auth` no longer exists |
| `@colyseus/command` | Generic changed to `Command<MyRoom>` instead of `Command<MyState>` |
| `triggerAll()` | Deprecated; `onAdd` handles existing items automatically |

---

## 0.17 Delta (if upgrading beyond 0.15)

- `onDrop(client, code?)` is a new lifecycle method for unexpected disconnects (replaces the `!consented` branch in `onLeave`).
- `onReconnect(client)` added as an explicit lifecycle callback.
- Server uses `defineServer()` / `defineRoom()` factory functions instead of `new Server()` / `server.define()`.
- `messages = { ... }` object pattern is now the primary API over `this.onMessage()` calls.
- `Callbacks.get(room)` is the recommended client-side state callback API over direct `.onAdd()` on collections.
- `onBeforePatch(state)` hook added for pre-patch state mutations.
- `client.view` (StateView) added for per-client state filtering.
