# Top-Down Arena Shooter — Game Rules

## Objective
Be the first player to reach **10 kills** to win the round.

## Players
- Up to **8 players** per room.
- If no opponent joins within 3 seconds, the game starts in single-player practice mode.

## Controls
| Input | Action |
|-------|--------|
| `W A S D` | Move |
| Mouse | Aim (character rotates toward cursor) |
| Left Click | Shoot |

## Combat
- Each player starts with **100 HP**.
- Each bullet deals **34 damage** (3 hits to kill).
- Fire rate: up to **3 shots per second**.
- Bullets travel at speed 15 and are destroyed on hitting a player or wall.
- Hit resolution is **server-authoritative** — the server decides if a bullet connects.

## Scoring
- **+1 kill** is awarded to the shooter each time they eliminate a player.
- Scores are live and visible on the leaderboard (top-right of screen).

## Respawn
- On death, a brief explosion effect plays.
- The player **respawns automatically after 2 seconds** at a random spawn point.
- There is no permanent elimination — the round continues until the kill limit is reached.

## Arena
- Rectangular dark metallic floor (**20×20 units**) with neon-edged boundary walls.
- **4–6 obstacle blocks** are scattered around the arena providing cover.
- Players and bullets collide with walls and obstacles.

## HUD
- **Leaderboard** (top-right): all players sorted by kill count, updated in real time.
- **Crosshair** (screen center): always visible.
- **Kill feed** (bottom-left): recent kill notifications, fade out after 4 seconds.
- **Health bars**: float above each player's character.

## Win Condition
The first player to reach **10 kills** wins the round. The game continues with respawns until that threshold is hit — there is no time limit.
