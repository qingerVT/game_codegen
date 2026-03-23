export default class CombatSystem {
  name = 'combat_system';

  constructor() {
    this._ctx = null;
    this._bullets = [];
    this._sparks = [];
    this._deathEffects = [];
    this._bulletTemplate = null;
    this._raycaster = new THREE.Raycaster();
    this._mouse = new THREE.Vector2();
    this._floorMesh = null;
    this._wallBounds = [];
    this._arenaConfig = null;
    this._lastShootTime = 0;
    this._fireInterval = 1 / 3; // 3 shots per second
    this._bulletSpeed = 15;
    this._bulletMaxDist = 40;
    this._onClickBound = null;
    this._onMouseMoveBound = null;
    this._aimX = 0;
    this._aimZ = 0;
    this._localPlayerId = null;
    this._disposed = false;
  }

  async build(ctx) {
    this._ctx = ctx;

    // Create bullet template and register in meshRegistry
    const bulletGeo = new THREE.SphereGeometry(0.1, 8, 8);
    const bulletMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 });
    this._bulletTemplate = new THREE.Mesh(bulletGeo, bulletMat);
    this._bulletTemplate.visible = false;
    ctx.meshRegistry.set('bullet_template', this._bulletTemplate);
  }

  start() {
    const ctx = this._ctx;
    this._localPlayerId = ctx.localPlayerId;
    this._floorMesh = ctx.meshRegistry.get('arena_floor') || null;
    this._arenaConfig = ctx.arenaConfig || null;
    if (this._arenaConfig) {
      this._wallBounds = this._arenaConfig.wallBounds || [];
    }

    // Mouse move for aim tracking
    this._onMouseMoveBound = (e) => this._onMouseMove(e);
    window.addEventListener('mousemove', this._onMouseMoveBound);

    // Click to shoot
    this._onClickBound = (e) => this._onClick(e);
    window.addEventListener('mousedown', this._onClickBound);

    // Network message handlers
    const net = ctx.modules.network;
    if (net) {
      net.onMessage('bullet_fired', (data) => this._onBulletFired(data));
      net.onMessage('player_hit', (data) => this._onPlayerHit(data));
      net.onMessage('player_killed', (data) => this._onPlayerKilled(data));
    }
  }

  _onMouseMove(e) {
    const ctx = this._ctx;
    const canvas = ctx.renderer ? ctx.renderer.domElement : document.querySelector('canvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    this._mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this._mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  _getAimWorldPos() {
    const ctx = this._ctx;
    this._raycaster.setFromCamera(this._mouse, ctx.camera);

    // Try floor mesh first
    if (this._floorMesh) {
      const hits = this._raycaster.intersectObject(this._floorMesh, false);
      if (hits.length > 0) {
        return hits[0].point;
      }
    }

    // Fallback: intersect y=0 plane
    const ray = this._raycaster.ray;
    if (Math.abs(ray.direction.y) > 0.0001) {
      const t = -ray.origin.y / ray.direction.y;
      if (t > 0) {
        return new THREE.Vector3(
          ray.origin.x + ray.direction.x * t,
          0,
          ray.origin.z + ray.direction.z * t
        );
      }
    }
    return null;
  }

  _onClick(e) {
    if (e.button !== 0) return;
    const ctx = this._ctx;
    const now = performance.now() / 1000;
    if (now - this._lastShootTime < this._fireInterval) return;

    const players = ctx.players;
    if (!players) return;
    const localPlayer = players.get(this._localPlayerId);
    if (!localPlayer || !localPlayer.alive) return;

    const aimPos = this._getAimWorldPos();
    if (!aimPos) return;

    const px = localPlayer.x;
    const pz = localPlayer.z;
    const dx = aimPos.x - px;
    const dz = aimPos.z - pz;
    const len = Math.sqrt(dx * dx + dz * dz);
    if (len < 0.001) return;

    const dirX = dx / len;
    const dirZ = dz / len;

    this._lastShootTime = now;

    // Emit shootRequested event for network relay
    ctx.eventBus.dispatchEvent(new CustomEvent('shootRequested', {
      detail: { x: px, z: pz, dirX, dirZ }
    }));

    // Also send directly via network
    if (ctx.modules.network) {
      ctx.modules.network.send('player_shoot', { x: px, z: pz, dirX, dirZ });
    }
  }

  _onBulletFired(data) {
    const { bulletId, playerId, x, z, dirX, dirZ } = data;

    // Get player color
    let color = 0xffffff;
    const ctx = this._ctx;
    if (ctx.players) {
      const player = ctx.players.get(playerId);
      if (player && player.mesh) {
        // Try to extract color from mesh material
        const mat = player.mesh.material || (player.mesh.children && player.mesh.children[0] && player.mesh.children[0].material);
        if (mat && mat.color) {
          color = mat.color.getHex();
        }
      }
    }

    this._spawnBullet(bulletId || ('b_' + Math.random()), playerId, x, z, dirX, dirZ, color);
  }

  _spawnBullet(bulletId, playerId, x, z, dirX, dirZ, color) {
    const ctx = this._ctx;
    const mesh = this._bulletTemplate.clone();
    mesh.visible = true;
    mesh.material = new THREE.MeshBasicMaterial({
      color: color,
      emissive: new THREE.Color(color),
      emissiveIntensity: 2,
      transparent: true,
      opacity: 0.95
    });
    mesh.position.set(x, 0.3, z);
    ctx.scene.add(mesh);

    // Trail - simple line geometry
    const trailGeo = new THREE.BufferGeometry();
    const trailPositions = new Float32Array(6); // 2 points
    trailPositions[0] = x; trailPositions[1] = 0.3; trailPositions[2] = z;
    trailPositions[3] = x; trailPositions[4] = 0.3; trailPositions[5] = z;
    trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3));
    const trailMat = new THREE.LineBasicMaterial({ color: color, transparent: true, opacity: 0.6 });
    const trail = new THREE.Line(trailGeo, trailMat);
    ctx.scene.add(trail);

    this._bullets.push({
      id: bulletId,
      playerId,
      mesh,
      trail,
      x, z,
      startX: x, startZ: z,
      dirX, dirZ,
      distTraveled: 0,
      alive: true
    });
  }

  _onPlayerHit(data) {
    const { shooterId, victimId, victimHealth } = data;
    const ctx = this._ctx;

    // Update health map
    if (ctx.playerHealthMap) {
      ctx.playerHealthMap.set(victimId, victimHealth);
    }

    // Spawn hit sparks at victim position
    if (ctx.players) {
      const victim = ctx.players.get(victimId);
      if (victim) {
        this._spawnHitSparks(victim.x, victim.z);
        // Red flash on victim mesh
        if (victim.mesh) {
          this._flashMesh(victim.mesh, 0xff0000, 0.15);
        }
      }
    }
  }

  _onPlayerKilled(data) {
    const { shooterId, victimId, spawnX, spawnZ } = data;
    const ctx = this._ctx;

    // Update health map
    if (ctx.playerHealthMap) {
      ctx.playerHealthMap.set(victimId, 0);
    }

    // Emit kill feed entry
    ctx.eventBus.dispatchEvent(new CustomEvent('killFeedEntry', {
      detail: { shooterId, victimId }
    }));

    // Death explosion effect at victim position
    if (ctx.players) {
      const victim = ctx.players.get(victimId);
      if (victim) {
        this._spawnDeathExplosion(victim.x, victim.z, victim.mesh);
      }
    }
  }

  _spawnHitSparks(x, z) {
    const ctx = this._ctx;
    const sparkCount = 8;
    for (let i = 0; i < sparkCount; i++) {
      const geo = new THREE.SphereGeometry(0.04, 4, 4);
      const mat = new THREE.MeshBasicMaterial({ color: 0xff8800, transparent: true, opacity: 1 });
      const spark = new THREE.Mesh(geo, mat);
      const angle = Math.random() * Math.PI * 2;
      const speed = 1 + Math.random() * 3;
      spark.position.set(x, 0.3 + Math.random() * 0.3, z);
      ctx.scene.add(spark);
      this._sparks.push({
        mesh: spark,
        vx: Math.cos(angle) * speed,
        vy: 1 + Math.random() * 2,
        vz: Math.sin(angle) * speed,
        life: 0.3 + Math.random() * 0.2,
        maxLife: 0.3 + Math.random() * 0.2
      });
    }
  }

  _flashMesh(mesh, color, duration) {
    // Temporarily change color of the mesh or its children
    const targets = [];
    mesh.traverse((child) => {
      if (child.isMesh && child.material) {
        targets.push({ mesh: child, originalColor: child.material.color ? child.material.color.clone() : null });
        if (child.material.color) {
          child.material = child.material.clone();
          child.material.color.set(color);
        }
      }
    });

    setTimeout(() => {
      targets.forEach(t => {
        if (t.mesh && t.mesh.material && t.originalColor) {
          t.mesh.material.color.copy(t.originalColor);
        }
      });
    }, duration * 1000);
  }

  _spawnDeathExplosion(x, z, playerMesh) {
    const ctx = this._ctx;

    // Particle burst
    const count = 15;
    for (let i = 0; i < count; i++) {
      const geo = new THREE.SphereGeometry(0.06, 4, 4);
      const mat = new THREE.MeshBasicMaterial({ color: 0xff3300, transparent: true, opacity: 1 });
      const p = new THREE.Mesh(geo, mat);
      const angle = Math.random() * Math.PI * 2;
      const speed = 2 + Math.random() * 4;
      p.position.set(x, 0.3, z);
      ctx.scene.add(p);
      this._sparks.push({
        mesh: p,
        vx: Math.cos(angle) * speed,
        vy: 1 + Math.random() * 3,
        vz: Math.sin(angle) * speed,
        life: 0.5 + Math.random() * 0.3,
        maxLife: 0.5 + Math.random() * 0.3
      });
    }

    // Mesh scale pulse on death
    if (playerMesh) {
      const originalScale = playerMesh.scale.clone();
      this._deathEffects.push({
        mesh: playerMesh,
        originalScale,
        time: 0,
        duration: 0.4
      });
    }
  }

  _bulletHitsWall(x, z) {
    for (const wb of this._wallBounds) {
      if (x >= wb.minX && x <= wb.maxX && z >= wb.minZ && z <= wb.maxZ) {
        return true;
      }
    }
    // Check arena bounds
    if (this._arenaConfig) {
      const hw = this._arenaConfig.width / 2;
      const hd = this._arenaConfig.depth / 2;
      if (x < -hw || x > hw || z < -hd || z > hd) {
        return true;
      }
    }
    return false;
  }

  update(dt) {
    if (this._disposed) return;

    // Update bullets
    for (let i = this._bullets.length - 1; i >= 0; i--) {
      const b = this._bullets[i];
      if (!b.alive) {
        this._removeBullet(b);
        this._bullets.splice(i, 1);
        continue;
      }

      const move = this._bulletSpeed * dt;
      b.x += b.dirX * move;
      b.z += b.dirZ * move;
      b.distTraveled += move;
      b.mesh.position.set(b.x, 0.3, b.z);

      // Update trail
      const trailLen = 0.8;
      const trailX = b.x - b.dirX * trailLen;
      const trailZ = b.z - b.dirZ * trailLen;
      const posAttr = b.trail.geometry.getAttribute('position');
      posAttr.array[0] = b.x;
      posAttr.array[1] = 0.3;
      posAttr.array[2] = b.z;
      posAttr.array[3] = trailX;
      posAttr.array[4] = 0.3;
      posAttr.array[5] = trailZ;
      posAttr.needsUpdate = true;

      // Check if bullet should be removed (wall hit or max distance)
      if (b.distTraveled > this._bulletMaxDist || this._bulletHitsWall(b.x, b.z)) {
        b.alive = false;
      }
    }

    // Update sparks
    for (let i = this._sparks.length - 1; i >= 0; i--) {
      const s = this._sparks[i];
      s.life -= dt;
      if (s.life <= 0) {
        this._ctx.scene.remove(s.mesh);
        if (s.mesh.geometry) s.mesh.geometry.dispose();
        if (s.mesh.material) s.mesh.material.dispose();
        this._sparks.splice(i, 1);
        continue;
      }
      s.vy -= 9.8 * dt;
      s.mesh.position.x += s.vx * dt;
      s.mesh.position.y += s.vy * dt;
      s.mesh.position.z += s.vz * dt;
      if (s.mesh.position.y < 0) s.mesh.position.y = 0;
      s.mesh.material.opacity = s.life / s.maxLife;
    }

    // Update death effects
    for (let i = this._deathEffects.length - 1; i >= 0; i--) {
      const de = this._deathEffects[i];
      de.time += dt;
      const t = de.time / de.duration;
      if (t >= 1) {
        // Restore
        if (de.mesh) {
          de.mesh.scale.copy(de.originalScale);
        }
        this._deathEffects.splice(i, 1);
        continue;
      }
      // Pulse effect: scale up then down
      const pulse = 1 + 0.5 * Math.sin(t * Math.PI);
      if (de.mesh) {
        de.mesh.scale.set(
          de.originalScale.x * pulse,
          de.originalScale.y * pulse,
          de.originalScale.z * pulse
        );
      }
    }
  }

  _removeBullet(b) {
    const ctx = this._ctx;
    ctx.scene.remove(b.mesh);
    ctx.scene.remove(b.trail);
    if (b.mesh.geometry) b.mesh.geometry.dispose();
    if (b.mesh.material) b.mesh.material.dispose();
    if (b.trail.geometry) b.trail.geometry.dispose();
    if (b.trail.material) b.trail.material.dispose();
  }

  dispose() {
    this._disposed = true;
    if (this._onClickBound) {
      window.removeEventListener('mousedown', this._onClickBound);
    }
    if (this._onMouseMoveBound) {
      window.removeEventListener('mousemove', this._onMouseMoveBound);
    }

    // Clean up all bullets
    for (const b of this._bullets) {
      this._removeBullet(b);
    }
    this._bullets.length = 0;

    // Clean up sparks
    for (const s of this._sparks) {
      this._ctx.scene.remove(s.mesh);
      if (s.mesh.geometry) s.mesh.geometry.dispose();
      if (s.mesh.material) s.mesh.material.dispose();
    }
    this._sparks.length = 0;

    this._deathEffects.length = 0;
  }
}