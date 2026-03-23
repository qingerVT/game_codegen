export default class PlayerController {
  name = 'player_controller';

  async build(ctx) {
    this.ctx = ctx;
    this.players = new Map();
    this.playerHealthMap = new Map();
    this.keys = { w: false, a: false, s: false, d: false };
    this.mouseX = 0;
    this.mouseY = 0;
    this.localX = 0;
    this.localZ = 0;
    this.localRotation = 0;
    this.localAlive = true;
    this.respawnTimer = 0;
    this.moveSpeed = 5;
    this.maxHealth = 100;
    this.sendTimer = 0;
    this.sendInterval = 1 / 20;
    this._handlers = [];
    this._domHandlers = [];

    ctx.players = this.players;
    ctx.playerHealthMap = this.playerHealthMap;

    // Create template player mesh (cylinder + gun barrel)
    const templateGroup = new THREE.Group();

    const bodyGeom = new THREE.CylinderGeometry(0.4, 0.4, 0.3, 16);
    const bodyMat = new THREE.MeshStandardMaterial({ color: 0x00ff00 });
    const bodyMesh = new THREE.Mesh(bodyGeom, bodyMat);
    bodyMesh.position.y = 0.15;
    templateGroup.add(bodyMesh);

    const barrelGeom = new THREE.BoxGeometry(0.1, 0.1, 0.5);
    const barrelMat = new THREE.MeshStandardMaterial({ color: 0xcccccc });
    const barrelMesh = new THREE.Mesh(barrelGeom, barrelMat);
    barrelMesh.position.set(0, 0.15, 0.45);
    templateGroup.add(barrelMesh);

    templateGroup.visible = false;
    ctx.scene.add(templateGroup);
    this._templateGroup = templateGroup;

    ctx.meshRegistry.set('player_body', templateGroup);
  }

  start() {
    const ctx = this.ctx;
    const net = ctx.modules.network;

    // Unique colors for players
    this._colorPalette = [
      0x00ff00, 0xff3333, 0x3399ff, 0xffff00,
      0xff00ff, 0x00ffff, 0xff8800, 0x88ff00
    ];
    this._colorIndex = 0;

    // Key handlers
    this._onKeyDown = (e) => {
      const k = e.key.toLowerCase();
      if (k in this.keys) this.keys[k] = true;
    };
    this._onKeyUp = (e) => {
      const k = e.key.toLowerCase();
      if (k in this.keys) this.keys[k] = false;
    };
    this._onMouseMove = (e) => {
      this.mouseX = e.clientX;
      this.mouseY = e.clientY;
    };

    window.addEventListener('keydown', this._onKeyDown);
    window.addEventListener('keyup', this._onKeyUp);
    window.addEventListener('mousemove', this._onMouseMove);
    this._domHandlers.push(
      ['keydown', this._onKeyDown],
      ['keyup', this._onKeyUp],
      ['mousemove', this._onMouseMove]
    );

    // Arena bounds
    const arenaConfig = ctx.arenaConfig;
    if (arenaConfig) {
      this.halfW = arenaConfig.width / 2;
      this.halfD = arenaConfig.depth / 2;
      this.wallBounds = arenaConfig.wallBounds || [];
      this.spawnPoints = arenaConfig.spawnPoints || [{ x: 0, z: 0 }];
    } else {
      const gw = ctx.gameConfig ? ctx.gameConfig.worldWidth : 20;
      const gd = ctx.gameConfig ? ctx.gameConfig.worldDepth : 20;
      this.halfW = gw / 2;
      this.halfD = gd / 2;
      this.wallBounds = [];
      this.spawnPoints = [{ x: 0, z: 0 }];
    }

    // Spawn local player
    const spawn = this.spawnPoints[Math.floor(Math.random() * this.spawnPoints.length)];
    this.localX = spawn.x;
    this.localZ = spawn.z;
    this._spawnLocalPlayer(ctx.localPlayerId, spawn.x, spawn.z);

    // Register network message handlers
    if (net) {
      this._registerMsg(net, 'player_joined', (data) => {
        if (data.playerId === ctx.localPlayerId) return;
        this._spawnRemotePlayer(data.playerId, data.x || 0, data.z || 0);
      });

      this._registerMsg(net, 'player_left', (data) => {
        this._removePlayer(data.playerId);
      });

      this._registerMsg(net, 'player_moved', (data) => {
        if (data.playerId === ctx.localPlayerId) return;
        const p = this.players.get(data.playerId);
        if (p) {
          p.x = data.x;
          p.z = data.z;
          p.rotation = data.rotation;
          p.mesh.position.set(data.x, 0, data.z);
          p.mesh.rotation.y = data.rotation;
        }
      });

      this._registerMsg(net, 'player_hit', (data) => {
        const victim = this.players.get(data.victimId);
        if (victim) {
          victim.health = data.victimHealth;
          this.playerHealthMap.set(data.victimId, data.victimHealth);

          // Red flash effect
          this._flashPlayer(data.victimId, 0xff0000);
        }

        // Emit hit event for effects
        ctx.eventBus.dispatchEvent(new CustomEvent('player_hit', {
          detail: { shooterId: data.shooterId, victimId: data.victimId, victimHealth: data.victimHealth }
        }));
      });

      this._registerMsg(net, 'player_killed', (data) => {
        const victim = this.players.get(data.victimId);
        if (victim) {
          victim.health = 0;
          victim.alive = false;
          this.playerHealthMap.set(data.victimId, 0);

          // Death effect: scale pulse then hide
          this._deathEffect(data.victimId);

          // Respawn after 2 seconds
          const respawnX = data.spawnX;
          const respawnZ = data.spawnZ;
          const vid = data.victimId;

          setTimeout(() => {
            const v = this.players.get(vid);
            if (v) {
              v.x = respawnX;
              v.z = respawnZ;
              v.health = this.maxHealth;
              v.alive = true;
              this.playerHealthMap.set(vid, this.maxHealth);
              v.mesh.position.set(respawnX, 0, respawnZ);
              v.mesh.visible = true;
              v.mesh.scale.set(1, 1, 1);

              if (vid === ctx.localPlayerId) {
                this.localX = respawnX;
                this.localZ = respawnZ;
                this.localAlive = true;
              }
            }
          }, 2000);

          if (data.victimId === ctx.localPlayerId) {
            this.localAlive = false;
          }
        }

        ctx.eventBus.dispatchEvent(new CustomEvent('player_killed', {
          detail: { shooterId: data.shooterId, victimId: data.victimId }
        }));
      });

      this._registerMsg(net, 'state_snapshot', (data) => {
        if (!data.players) return;
        for (const p of data.players) {
          if (p.playerId === ctx.localPlayerId) {
            this.localX = p.x;
            this.localZ = p.z;
            this.localRotation = p.rotation || 0;
            const local = this.players.get(p.playerId);
            if (local) {
              local.x = p.x;
              local.z = p.z;
              local.rotation = p.rotation || 0;
              local.health = p.health != null ? p.health : this.maxHealth;
              local.alive = local.health > 0;
              this.playerHealthMap.set(p.playerId, local.health);
              local.mesh.position.set(p.x, 0, p.z);
            }
          } else {
            if (!this.players.has(p.playerId)) {
              this._spawnRemotePlayer(p.playerId, p.x, p.z);
            }
            const remote = this.players.get(p.playerId);
            if (remote) {
              remote.x = p.x;
              remote.z = p.z;
              remote.rotation = p.rotation || 0;
              remote.health = p.health != null ? p.health : this.maxHealth;
              remote.alive = remote.health > 0;
              this.playerHealthMap.set(p.playerId, remote.health);
              remote.mesh.position.set(p.x, 0, p.z);
              remote.mesh.rotation.y = remote.rotation;
              remote.mesh.visible = remote.alive;
            }
          }
        }
      });
    }
  }

  update(dt) {
    const ctx = this.ctx;

    if (!this.localAlive) return;

    // WASD movement
    let dx = 0, dz = 0;
    if (this.keys.w) dz -= 1;
    if (this.keys.s) dz += 1;
    if (this.keys.a) dx -= 1;
    if (this.keys.d) dx += 1;

    if (dx !== 0 || dz !== 0) {
      const len = Math.sqrt(dx * dx + dz * dz);
      dx /= len;
      dz /= len;
    }

    let newX = this.localX + dx * this.moveSpeed * dt;
    let newZ = this.localZ + dz * this.moveSpeed * dt;

    // Clamp to arena
    const margin = 0.4;
    newX = Math.max(-this.halfW + margin, Math.min(this.halfW - margin, newX));
    newZ = Math.max(-this.halfD + margin, Math.min(this.halfD - margin, newZ));

    // Wall collision (AABB)
    for (const wb of this.wallBounds) {
      if (newX + margin > wb.minX && newX - margin < wb.maxX &&
          newZ + margin > wb.minZ && newZ - margin < wb.maxZ) {
        // Push out on the axis with smallest overlap
        const overlapLeft = (newX + margin) - wb.minX;
        const overlapRight = wb.maxX - (newX - margin);
        const overlapTop = (newZ + margin) - wb.minZ;
        const overlapBottom = wb.maxZ - (newZ - margin);

        const minOverlap = Math.min(overlapLeft, overlapRight, overlapTop, overlapBottom);

        if (minOverlap === overlapLeft) newX = wb.minX - margin;
        else if (minOverlap === overlapRight) newX = wb.maxX + margin;
        else if (minOverlap === overlapTop) newZ = wb.minZ - margin;
        else newZ = wb.maxZ + margin;
      }
    }

    this.localX = newX;
    this.localZ = newZ;

    // Mouse aim rotation (top-down)
    const camera = ctx.camera;
    const localPlayer = this.players.get(ctx.localPlayerId);
    if (localPlayer && camera) {
      // Project player world pos to screen
      const playerWorldPos = new THREE.Vector3(this.localX, 0, this.localZ);
      const projected = playerWorldPos.clone().project(camera);
      const screenX = (projected.x * 0.5 + 0.5) * window.innerWidth;
      const screenY = (-projected.y * 0.5 + 0.5) * window.innerHeight;

      const mdx = this.mouseX - screenX;
      const mdy = this.mouseY - screenY;
      // In top-down, screen X maps to world X, screen Y maps to world Z
      this.localRotation = Math.atan2(mdx, -mdy);

      localPlayer.x = this.localX;
      localPlayer.z = this.localZ;
      localPlayer.rotation = this.localRotation;
      localPlayer.mesh.position.set(this.localX, 0, this.localZ);
      localPlayer.mesh.rotation.y = this.localRotation;
    }

    // Send movement throttled
    this.sendTimer += dt;
    if (this.sendTimer >= this.sendInterval) {
      this.sendTimer = 0;
      const net = ctx.modules.network;
      if (net) {
        net.send('player_move', {
          x: this.localX,
          z: this.localZ,
          rotation: this.localRotation
        });
      }
      ctx.eventBus.dispatchEvent(new CustomEvent('localPlayerMoved', {
        detail: { x: this.localX, z: this.localZ, rotation: this.localRotation }
      }));
    }

    // Smooth camera follow
    if (camera) {
      const targetX = this.localX;
      const targetZ = this.localZ;
      const camLerp = 1 - Math.pow(0.01, dt);
      camera.position.x += (targetX - camera.position.x) * camLerp;
      camera.position.z += (targetZ + 1 - camera.position.z) * camLerp;
      camera.position.y = 25;
      camera.lookAt(new THREE.Vector3(camera.position.x, 0, camera.position.z - 1));
    }
  }

  dispose() {
    // Remove DOM handlers
    for (const [evt, handler] of this._domHandlers) {
      window.removeEventListener(evt, handler);
    }
    this._domHandlers = [];

    // Remove player meshes
    for (const [id, p] of this.players) {
      if (p.mesh && p.mesh.parent) {
        p.mesh.parent.remove(p.mesh);
      }
    }
    this.players.clear();
    this.playerHealthMap.clear();

    // Remove template
    if (this._templateGroup && this._templateGroup.parent) {
      this._templateGroup.parent.remove(this._templateGroup);
    }
  }

  _getNextColor() {
    const c = this._colorPalette[this._colorIndex % this._colorPalette.length];
    this._colorIndex++;
    return c;
  }

  _createPlayerMesh(color) {
    const group = new THREE.Group();

    const bodyGeom = new THREE.CylinderGeometry(0.4, 0.4, 0.3, 16);
    const bodyMat = new THREE.MeshStandardMaterial({ color: color, emissive: color, emissiveIntensity: 0.3 });
    const bodyMesh = new THREE.Mesh(bodyGeom, bodyMat);
    bodyMesh.position.y = 0.15;
    group.add(bodyMesh);

    const barrelGeom = new THREE.BoxGeometry(0.1, 0.1, 0.5);
    const barrelMat = new THREE.MeshStandardMaterial({ color: 0xcccccc });
    const barrelMesh = new THREE.Mesh(barrelGeom, barrelMat);
    barrelMesh.position.set(0, 0.15, 0.45);
    group.add(barrelMesh);

    group.userData.playerColor = color;
    group.userData.bodyMesh = bodyMesh;

    return group;
  }

  _spawnLocalPlayer(playerId, x, z) {
    if (this.players.has(playerId)) return;

    const color = this._getNextColor();
    const mesh = this._createPlayerMesh(color);
    mesh.position.set(x, 0, z);
    this.ctx.scene.add(mesh);

    const entry = {
      mesh,
      x,
      z,
      rotation: 0,
      health: this.maxHealth,
      alive: true,
      color
    };

    this.players.set(playerId, entry);
    this.playerHealthMap.set(playerId, this.maxHealth);
  }

  _spawnRemotePlayer(playerId, x, z) {
    if (this.players.has(playerId)) return;

    const color = this._getNextColor();
    const mesh = this._createPlayerMesh(color);
    mesh.position.set(x, 0, z);
    this.ctx.scene.add(mesh);

    const entry = {
      mesh,
      x,
      z,
      rotation: 0,
      health: this.maxHealth,
      alive: true,
      color
    };

    this.players.set(playerId, entry);
    this.playerHealthMap.set(playerId, this.maxHealth);
  }

  _removePlayer(playerId) {
    const p = this.players.get(playerId);
    if (p) {
      if (p.mesh && p.mesh.parent) {
        p.mesh.parent.remove(p.mesh);
      }
      this.players.delete(playerId);
      this.playerHealthMap.delete(playerId);
    }
  }

  _flashPlayer(playerId, flashColor) {
    const p = this.players.get(playerId);
    if (!p || !p.mesh) return;
    const bodyMesh = p.mesh.userData.bodyMesh;
    if (!bodyMesh) return;

    const origColor = p.color;
    bodyMesh.material.emissive.setHex(flashColor);
    bodyMesh.material.emissiveIntensity = 1.0;

    setTimeout(() => {
      if (bodyMesh.material) {
        bodyMesh.material.emissive.setHex(origColor);
        bodyMesh.material.emissiveIntensity = 0.3;
      }
    }, 150);
  }

  _deathEffect(playerId) {
    const p = this.players.get(playerId);
    if (!p || !p.mesh) return;

    // Scale pulse
    const mesh = p.mesh;
    mesh.scale.set(1.5, 1.5, 1.5);

    setTimeout(() => {
      mesh.scale.set(1, 1, 1);
      mesh.visible = false;
    }, 200);
  }

  _registerMsg(net, type, cb) {
    net.onMessage(type, cb);
    this._handlers.push({ type, cb });
  }
}