const { Server, Room } = require("colyseus");

class GameRoom extends Room {
    onCreate(options) {
        this.players = new Map();
        this.bullets = new Map();
        this.bulletCounter = 0;

        this.onMessage("player_move", (client, data) => {
            const player = this.players.get(client.sessionId);
            if (!player) return;

            player.x = data.x;
            player.z = data.z;
            player.rotation = data.rotation;

            this.broadcast("player_moved", {
                playerId: client.sessionId,
                x: data.x,
                z: data.z,
                rotation: data.rotation
            }, { except: client });
        });

        this.onMessage("player_shoot", (client, data) => {
            const player = this.players.get(client.sessionId);
            if (!player) return;
            if (player.health <= 0) return;

            this.bulletCounter++;
            const bulletId = `bullet_${client.sessionId}_${this.bulletCounter}`;

            const dirLen = Math.sqrt(data.dirX * data.dirX + data.dirZ * data.dirZ);
            if (dirLen === 0) return;
            const dirX = data.dirX / dirLen;
            const dirZ = data.dirZ / dirLen;

            const bullet = {
                bulletId,
                playerId: client.sessionId,
                x: data.x,
                z: data.z,
                dirX,
                dirZ,
                createdAt: Date.now()
            };

            this.bullets.set(bulletId, bullet);

            this.broadcast("bullet_fired", {
                bulletId,
                playerId: client.sessionId,
                x: data.x,
                z: data.z,
                dirX,
                dirZ
            });
        });

        this.setSimulationInterval((deltaTime) => {
            this.updateBullets(deltaTime);
        }, BULLET_CHECK_INTERVAL);
    }

    updateBullets(deltaTime) {
        const now = Date.now();
        const dt = deltaTime / 1000;
        const bulletsToRemove = [];

        for (const [bulletId, bullet] of this.bullets) {
            if (now - bullet.createdAt > BULLET_MAX_LIFETIME) {
                bulletsToRemove.push(bulletId);
                continue;
            }

            bullet.x += bullet.dirX * BULLET_SPEED * dt;
            bullet.z += bullet.dirZ * BULLET_SPEED * dt;

            if (Math.abs(bullet.x) > ARENA_SIZE || Math.abs(bullet.z) > ARENA_SIZE) {
                bulletsToRemove.push(bulletId);
                continue;
            }

            let hitPlayer = null;
            for (const [playerId, player] of this.players) {
                if (playerId === bullet.playerId) continue;
                if (player.health <= 0) continue;

                const dx = bullet.x - player.x;
                const dz = bullet.z - player.z;
                const dist = Math.sqrt(dx * dx + dz * dz);

                if (dist < HIT_RADIUS) {
                    hitPlayer = player;
                    break;
                }
            }

            if (hitPlayer) {
                bulletsToRemove.push(bulletId);

                hitPlayer.health -= BULLET_DAMAGE;
                const shooter = this.players.get(bullet.playerId);

                if (hitPlayer.health <= 0) {
                    hitPlayer.health = 0;
                    if (shooter) {
                        shooter.score += 1;
                    }
                    const newScore = shooter ? shooter.score : 0;

                    const spawn = this.getSpawnPosition();

                    this.broadcast("player_killed", {
                        shooterId: bullet.playerId,
                        victimId: hitPlayer.playerId,
                        newScore,
                        spawnX: spawn.x,
                        spawnZ: spawn.z
                    });

                    hitPlayer.health = MAX_HEALTH;
                    hitPlayer.x = spawn.x;
                    hitPlayer.z = spawn.z;
                } else {
                    if (shooter) {
                        shooter.score += 0;
                    }
                    const newScore = shooter ? shooter.score : 0;

                    this.broadcast("player_hit", {
                        shooterId: bullet.playerId,
                        victimId: hitPlayer.playerId,
                        newScore,
                        victimHealth: hitPlayer.health
                    });
                }
            }
        }

        for (const id of bulletsToRemove) {
            this.bullets.delete(id);
        }
    }

    getSpawnPosition() {
        const angle = Math.random() * Math.PI * 2;
        const radius = Math.random() * (ARENA_SIZE * 0.6);
        return {
            x: Math.cos(angle) * radius,
            z: Math.sin(angle) * radius
        };
    }

    onJoin(client, options) {
        const spawn = this.getSpawnPosition();

        const player = {
            playerId: client.sessionId,
            x: spawn.x,
            z: spawn.z,
            rotation: 0,
            score: 0,
            health: MAX_HEALTH
        };

        this.players.set(client.sessionId, player);

        const playersArray = [];
        for (const [pid, p] of this.players) {
            playersArray.push({
                playerId: p.playerId,
                x: p.x,
                z: p.z,
                rotation: p.rotation,
                score: p.score,
                health: p.health
            });
        }

        client.send("state_snapshot", {
            players: playersArray
        });

        this.broadcast("player_joined", {
            playerId: client.sessionId,
            x: spawn.x,
            z: spawn.z,
            score: 0
        }, { except: client });
    }

    onLeave(client, consented) {
        this.players.delete(client.sessionId);

        const bulletsToRemove = [];
        for (const [bulletId, bullet] of this.bullets) {
            if (bullet.playerId === client.sessionId) {
                bulletsToRemove.push(bulletId);
            }
        }
        for (const id of bulletsToRemove) {
            this.bullets.delete(id);
        }

        this.broadcast("player_left", {
            playerId: client.sessionId
        });
    }

    onDispose() {
    }
}


const app = require("http").createServer();
const gameServer = new Server({ server: app });
gameServer.define("game_room", GameRoom);
gameServer.listen(2572).then(() => {
  console.log("Colyseus listening on ws://localhost:2572");
});
