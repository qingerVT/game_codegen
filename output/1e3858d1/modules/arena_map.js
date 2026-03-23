export default class ArenaMap {
  name = 'arena_map';

  constructor() {
    this._meshes = [];
    this._bodies = [];
    this._ctx = null;
  }

  async build(ctx) {
    this._ctx = ctx;
    const { scene, rapierWorld, RAPIER } = ctx;
    const width = ctx.gameConfig?.worldWidth || 20;
    const depth = ctx.gameConfig?.worldDepth || 20;
    const halfW = width / 2;
    const halfD = depth / 2;
    const wallThickness = 0.5;
    const wallHeight = 1.5;

    // --- Floor ---
    const floorGeo = new THREE.PlaneGeometry(width, depth);
    const floorMat = new THREE.MeshStandardMaterial({
      color: 0x1a1a2e,
      metalness: 0.8,
      roughness: 0.3
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = 0;
    floor.receiveShadow = true;
    scene.add(floor);
    this._meshes.push(floor);

    // Floor grid lines for visual depth
    const gridHelper = new THREE.GridHelper(width, 20, 0x00ffff, 0x0a0a2a);
    gridHelper.position.y = 0.01;
    scene.add(gridHelper);
    this._meshes.push(gridHelper);

    // Floor physics (static)
    const floorBodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(0, -0.05, 0);
    const floorBody = rapierWorld.createRigidBody(floorBodyDesc);
    const floorColliderDesc = RAPIER.ColliderDesc.cuboid(halfW, 0.05, halfD);
    rapierWorld.createCollider(floorColliderDesc, floorBody);
    this._bodies.push(floorBody);

    // Register floor mesh
    ctx.meshRegistry.set('arena_floor', floor);

    // --- Neon edge material ---
    const neonMat = new THREE.MeshStandardMaterial({
      color: 0x00ffff,
      emissive: 0x00ffff,
      emissiveIntensity: 0.6,
      metalness: 0.9,
      roughness: 0.2
    });

    const wallMeshes = [];
    const wallBounds = [];

    const createWall = (cx, cz, hw, hd, label) => {
      const geo = new THREE.BoxGeometry(hw * 2, wallHeight, hd * 2);
      const mesh = new THREE.Mesh(geo, neonMat);
      mesh.position.set(cx, wallHeight / 2, cz);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
      this._meshes.push(mesh);
      wallMeshes.push(mesh);

      // Add neon edge lines
      const edges = new THREE.EdgesGeometry(geo);
      const lineMat = new THREE.LineBasicMaterial({ color: 0x00ffff });
      const lineSegments = new THREE.LineSegments(edges, lineMat);
      lineSegments.position.copy(mesh.position);
      scene.add(lineSegments);
      this._meshes.push(lineSegments);

      wallBounds.push({
        minX: cx - hw,
        maxX: cx + hw,
        minZ: cz - hd,
        maxZ: cz + hd
      });

      // Physics
      const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(cx, wallHeight / 2, cz);
      const body = rapierWorld.createRigidBody(bodyDesc);
      const colliderDesc = RAPIER.ColliderDesc.cuboid(hw, wallHeight / 2, hd);
      rapierWorld.createCollider(colliderDesc, body);
      this._bodies.push(body);
    };

    // Boundary walls: North, South, East, West
    const ht = wallThickness / 2;
    // North (positive Z)
    createWall(0, halfD + ht, halfW + wallThickness, ht, 'wall_north');
    // South (negative Z)
    createWall(0, -(halfD + ht), halfW + wallThickness, ht, 'wall_south');
    // East (positive X)
    createWall(halfW + ht, 0, ht, halfD + wallThickness, 'wall_east');
    // West (negative X)
    createWall(-(halfW + ht), 0, ht, halfD + wallThickness, 'wall_west');

    // --- Obstacle blocks ---
    const obstacleMat = new THREE.MeshStandardMaterial({
      color: 0x2a2a4a,
      emissive: 0xff00ff,
      emissiveIntensity: 0.15,
      metalness: 0.7,
      roughness: 0.3
    });

    const obstacles = [
      { x: -4, z: -4, hw: 1.0, hd: 0.5 },
      { x: 4, z: 4, hw: 0.5, hd: 1.0 },
      { x: -5, z: 3, hw: 0.7, hd: 0.7 },
      { x: 5, z: -3, hw: 1.2, hd: 0.4 },
      { x: 0, z: 0, hw: 0.8, hd: 0.8 },
      { x: -2, z: 6, hw: 0.6, hd: 1.0 }
    ];

    const obstHeight = 1.2;

    for (const obs of obstacles) {
      const geo = new THREE.BoxGeometry(obs.hw * 2, obstHeight, obs.hd * 2);
      const mesh = new THREE.Mesh(geo, obstacleMat);
      mesh.position.set(obs.x, obstHeight / 2, obs.z);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
      this._meshes.push(mesh);
      wallMeshes.push(mesh);

      // Neon edges on obstacles
      const edges = new THREE.EdgesGeometry(geo);
      const lineMat = new THREE.LineBasicMaterial({ color: 0xff00ff });
      const lineSegments = new THREE.LineSegments(edges, lineMat);
      lineSegments.position.copy(mesh.position);
      scene.add(lineSegments);
      this._meshes.push(lineSegments);

      wallBounds.push({
        minX: obs.x - obs.hw,
        maxX: obs.x + obs.hw,
        minZ: obs.z - obs.hd,
        maxZ: obs.z + obs.hd
      });

      // Physics
      const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(obs.x, obstHeight / 2, obs.z);
      const body = rapierWorld.createRigidBody(bodyDesc);
      const colliderDesc = RAPIER.ColliderDesc.cuboid(obs.hw, obstHeight / 2, obs.hd);
      rapierWorld.createCollider(colliderDesc, body);
      this._bodies.push(body);
    }

    // Register wall meshes array
    ctx.meshRegistry.set('arena_walls', wallMeshes);

    // --- Spawn points (away from obstacles and walls) ---
    const spawnPoints = [
      { x: -7, z: -7 },
      { x: 7, z: -7 },
      { x: -7, z: 7 },
      { x: 7, z: 7 },
      { x: 0, z: -7 },
      { x: 0, z: 7 },
      { x: -7, z: 0 },
      { x: 7, z: 0 }
    ];

    // --- Expose arenaConfig on ctx ---
    ctx.arenaConfig = {
      width,
      depth,
      wallMeshes,
      wallBounds,
      spawnPoints
    };

    // --- Ambient visual: subtle fog ---
    scene.fog = new THREE.FogExp2(0x0a0a1a, 0.015);
    scene.background = new THREE.Color(0x0a0a1a);
  }

  start() {
    // Nothing needed; other modules read ctx.arenaConfig in their start()
  }

  update(dt) {
    // Static arena, no per-frame updates needed
  }

  dispose() {
    const ctx = this._ctx;
    if (!ctx) return;

    // Remove all meshes from scene
    for (const mesh of this._meshes) {
      if (mesh.parent) mesh.parent.remove(mesh);
      if (mesh.geometry) mesh.geometry.dispose();
      if (mesh.material) {
        if (Array.isArray(mesh.material)) {
          mesh.material.forEach(m => m.dispose());
        } else if (mesh.material.dispose) {
          mesh.material.dispose();
        }
      }
    }
    this._meshes.length = 0;

    // Remove Rapier bodies
    for (const body of this._bodies) {
      try {
        ctx.rapierWorld.removeRigidBody(body);
      } catch (e) {
        // body may already be removed
      }
    }
    this._bodies.length = 0;

    // Clean up registry entries
    ctx.meshRegistry.delete('arena_floor');
    ctx.meshRegistry.delete('arena_walls');
    delete ctx.arenaConfig;
  }
}