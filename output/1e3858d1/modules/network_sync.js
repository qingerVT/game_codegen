export default class network_sync {
  name = 'network_sync';

  async build(ctx) {
    this._ctx = ctx;
    this._room = null;
    this._connected = false;
    this._disposed = false;

    // Handler stacking map: Map<string, Function[]>
    this._handlers = new Map();

    // Provide ctx extensions immediately
    ctx.scoreState = new Map();
    ctx.localPlayerId = null;

    // Throttle config
    this._syncInterval = 1 / (ctx.gameConfig?.syncRateHz || 20);
    this._moveSendTimer = 0;
    this._pendingMove = null;

    // Public API - attach to this instance so ctx.modules.network can use them
    this.send = (type, payload) => {
      if (this._room && this._connected) {
        try {
          this._room.send(type, payload);
        } catch (e) {
          // silently fail
        }
      }
      // offline: no-op
    };

    this.onMessage = (type, cb) => {
      if (!this._handlers.has(type)) this._handlers.set(type, []);
      this._handlers.get(type).push(cb);
    };

    this.isConnected = () => this._connected;

    // Event listeners for relaying to server
    this._onLocalPlayerMoved = (e) => {
      const { x, z, rotation } = e.detail;
      this._pendingMove = { x, z, rotation };
    };

    this._onShootRequested = (e) => {
      const { x, z, dirX, dirZ } = e.detail;
      this.send('player_shoot', { x, z, dirX, dirZ });
    };

    ctx.eventBus.addEventListener('localPlayerMoved', this._onLocalPlayerMoved);
    ctx.eventBus.addEventListener('shootRequested', this._onShootRequested);

    // Attempt Colyseus connection
    const fallbackTimeout = ctx.gameConfig?.fallbackTimeoutSeconds ?? 3;
    try {
      await this._connectWithTimeout(ctx, fallbackTimeout * 1000);
    } catch (e) {
      // Singleplayer fallback
      this._connected = false;
      ctx.localPlayerId = 'local_' + Math.random().toString(36).slice(2, 8);
      ctx.scoreState.set(ctx.localPlayerId, 0);
    }
  }

  async _connectWithTimeout(ctx, timeoutMs) {
    return new Promise(async (resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error('Connection timeout'));
      }, timeoutMs);

      try {
        if (!ctx.wsUrl || typeof ColyseusClient === 'undefined') {
          clearTimeout(timer);
          reject(new Error('No wsUrl or ColyseusClient'));
          return;
        }

        const client = new ColyseusClient(ctx.wsUrl);
        const room = await client.joinOrCreate('game_room');

        if (this._disposed) {
          clearTimeout(timer);
          room.leave();
          reject(new Error('Disposed during connect'));
          return;
        }

        clearTimeout(timer);
        this._room = room;
        this._connected = true;
        ctx.localPlayerId = room.sessionId;

        // Set up all server message routing
        this._setupRoomHandlers(ctx, room);

        resolve();
      } catch (e) {
        clearTimeout(timer);
        reject(e);
      }
    });
  }

  _setupRoomHandlers(ctx, room) {
    const dispatch = (type, data) => {
      const cbs = this._handlers.get(type);
      if (cbs) {
        for (const cb of cbs) {
          try { cb(data); } catch (e) { console.error(`[network] handler error for ${type}:`, e); }
        }
      }
    };

    // state_snapshot
    room.onMessage('state_snapshot', (data) => {
      if (data.players && Array.isArray(data.players)) {
        for (const p of data.players) {
          ctx.scoreState.set(p.playerId, p.score ?? 0);
        }
      }
      dispatch('state_snapshot', data);
    });

    // player_joined
    room.onMessage('player_joined', (data) => {
      ctx.scoreState.set(data.playerId, 0);
      dispatch('player_joined', data);
    });

    // player_left
    room.onMessage('player_left', (data) => {
      ctx.scoreState.delete(data.playerId);
      dispatch('player_left', data);
    });

    // player_moved
    room.onMessage('player_moved', (data) => {
      dispatch('player_moved', data);
    });

    // bullet_fired
    room.onMessage('bullet_fired', (data) => {
      dispatch('bullet_fired', data);
    });

    // player_hit — carries newScore for shooter
    room.onMessage('player_hit', (data) => {
      if (data.shooterId != null && data.newScore != null) {
        ctx.scoreState.set(data.shooterId, data.newScore);
      }
      dispatch('player_hit', data);
    });

    // player_killed — carries newScore for shooter
    room.onMessage('player_killed', (data) => {
      if (data.shooterId != null && data.newScore != null) {
        ctx.scoreState.set(data.shooterId, data.newScore);
      }
      dispatch('player_killed', data);
    });

    // Catch-all for any other message types that modules register for
    // We use a wildcard approach: listen for '*' if available, otherwise
    // we register dynamically. Colyseus JS SDK supports onMessage('*', cb).
    room.onMessage('*', (type, data) => {
      // Already handled specific types above, but external handlers may stack
      // The specific handlers above already dispatch. For types not in our list,
      // dispatch here.
      const knownTypes = ['state_snapshot', 'player_joined', 'player_left',
        'player_moved', 'bullet_fired', 'player_hit', 'player_killed'];
      if (!knownTypes.includes(type)) {
        // If this message carries a score update pattern, handle it
        if (data && data.playerId != null && data.newScore != null) {
          ctx.scoreState.set(data.playerId, data.newScore);
        }
        dispatch(type, data);
      }
    });

    // Handle room leave / error
    room.onLeave((code) => {
      this._connected = false;
    });

    room.onError((code, message) => {
      console.warn(`[network] room error: ${code} ${message}`);
    });
  }

  start() {
    // Nothing needed — connections established in build
  }

  update(dt) {
    // Throttled move sending
    if (this._pendingMove && this._connected) {
      this._moveSendTimer += dt;
      if (this._moveSendTimer >= this._syncInterval) {
        this.send('player_move', this._pendingMove);
        this._pendingMove = null;
        this._moveSendTimer = 0;
      }
    }
  }

  dispose() {
    this._disposed = true;
    const ctx = this._ctx;

    if (ctx && ctx.eventBus) {
      ctx.eventBus.removeEventListener('localPlayerMoved', this._onLocalPlayerMoved);
      ctx.eventBus.removeEventListener('shootRequested', this._onShootRequested);
    }

    if (this._room) {
      try { this._room.leave(); } catch (e) { /* ignore */ }
      this._room = null;
    }

    this._connected = false;
    this._handlers.clear();
  }
}