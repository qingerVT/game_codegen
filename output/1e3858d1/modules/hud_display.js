export default class HudDisplay {
  name = 'hud_display';

  _ctx = null;
  _leaderboardEl = null;
  _crosshairEl = null;
  _killFeedEl = null;
  _killFeedEntries = [];
  _killFeedHandler = null;
  _healthBarsContainer = null;
  _healthBars = new Map();
  _localPlayerId = null;

  async build(ctx) {
    this._ctx = ctx;

    // --- Leaderboard (top-right) ---
    const lb = document.createElement('div');
    lb.id = 'hud-leaderboard';
    lb.style.cssText = `
      position: absolute;
      top: 10px;
      right: 10px;
      min-width: 160px;
      padding: 8px 12px;
      background: rgba(0,0,0,0.7);
      border: 1px solid rgba(0,255,255,0.4);
      border-radius: 6px;
      font-family: 'Courier New', monospace;
      font-size: 13px;
      color: #fff;
      pointer-events: none;
      z-index: 100;
      user-select: none;
    `;
    ctx.uiOverlay.appendChild(lb);
    this._leaderboardEl = lb;

    // --- Crosshair (center) ---
    const ch = document.createElement('div');
    ch.id = 'hud-crosshair';
    ch.style.cssText = `
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 24px;
      height: 24px;
      pointer-events: none;
      z-index: 100;
    `;
    ch.innerHTML = `
      <svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="3" stroke="#fff" stroke-width="1" fill="none" opacity="0.8"/>
        <line x1="12" y1="0" x2="12" y2="8" stroke="#fff" stroke-width="1" opacity="0.6"/>
        <line x1="12" y1="16" x2="12" y2="24" stroke="#fff" stroke-width="1" opacity="0.6"/>
        <line x1="0" y1="12" x2="8" y2="12" stroke="#fff" stroke-width="1" opacity="0.6"/>
        <line x1="16" y1="12" x2="24" y2="12" stroke="#fff" stroke-width="1" opacity="0.6"/>
      </svg>
    `;
    ctx.uiOverlay.appendChild(ch);
    this._crosshairEl = ch;

    // --- Kill Feed (bottom-left) ---
    const kf = document.createElement('div');
    kf.id = 'hud-killfeed';
    kf.style.cssText = `
      position: absolute;
      bottom: 10px;
      left: 10px;
      max-width: 300px;
      font-family: 'Courier New', monospace;
      font-size: 12px;
      color: #fff;
      pointer-events: none;
      z-index: 100;
      user-select: none;
      display: flex;
      flex-direction: column-reverse;
      gap: 2px;
    `;
    ctx.uiOverlay.appendChild(kf);
    this._killFeedEl = kf;

    // --- Health bars container (rendered in 3D→2D projection) ---
    const hbc = document.createElement('div');
    hbc.id = 'hud-healthbars';
    hbc.style.cssText = `
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      pointer-events: none;
      z-index: 99;
      overflow: hidden;
    `;
    ctx.uiOverlay.appendChild(hbc);
    this._healthBarsContainer = hbc;

    // Listen for kill feed events
    this._killFeedHandler = (e) => {
      const { shooterId, victimId } = e.detail;
      this._addKillFeedEntry(shooterId, victimId);
    };
    ctx.eventBus.addEventListener('killFeedEntry', this._killFeedHandler);
  }

  start() {
    this._localPlayerId = this._ctx.localPlayerId;
  }

  update(dt) {
    this._updateLeaderboard();
    this._updateKillFeed(dt);
    this._updateHealthBars();
  }

  dispose() {
    const ctx = this._ctx;
    if (this._leaderboardEl && this._leaderboardEl.parentNode) {
      this._leaderboardEl.parentNode.removeChild(this._leaderboardEl);
    }
    if (this._crosshairEl && this._crosshairEl.parentNode) {
      this._crosshairEl.parentNode.removeChild(this._crosshairEl);
    }
    if (this._killFeedEl && this._killFeedEl.parentNode) {
      this._killFeedEl.parentNode.removeChild(this._killFeedEl);
    }
    if (this._healthBarsContainer && this._healthBarsContainer.parentNode) {
      this._healthBarsContainer.parentNode.removeChild(this._healthBarsContainer);
    }
    if (this._killFeedHandler) {
      ctx.eventBus.removeEventListener('killFeedEntry', this._killFeedHandler);
      this._killFeedHandler = null;
    }
    this._healthBars.clear();
    this._killFeedEntries = [];
  }

  // --- Leaderboard ---
  _updateLeaderboard() {
    const scoreState = this._ctx.scoreState;
    if (!scoreState) return;

    const entries = [];
    scoreState.forEach((score, pid) => {
      entries.push({ pid, score });
    });
    entries.sort((a, b) => b.score - a.score);

    let html = '<div style="margin-bottom:4px;color:cyan;font-weight:bold;font-size:14px;">⚔ LEADERBOARD</div>';
    for (let i = 0; i < entries.length; i++) {
      const { pid, score } = entries[i];
      const isLocal = pid === this._localPlayerId;
      const displayName = isLocal ? 'You' : pid.substring(0, 6);
      const color = isLocal ? '#0ff' : '#ccc';
      const bg = isLocal ? 'rgba(0,255,255,0.1)' : 'transparent';
      html += `<div style="padding:2px 4px;color:${color};background:${bg};border-radius:3px;">
        ${i + 1}. ${displayName}: <span style="font-weight:bold;">${score}</span>
      </div>`;
    }
    this._leaderboardEl.innerHTML = html;
  }

  // --- Kill Feed ---
  _addKillFeedEntry(shooterId, victimId) {
    const shooterName = shooterId === this._localPlayerId ? 'You' : shooterId.substring(0, 6);
    const victimName = victimId === this._localPlayerId ? 'You' : victimId.substring(0, 6);

    const entry = {
      text: `${shooterName} ☠ ${victimName}`,
      timeLeft: 4,
      el: null
    };

    const div = document.createElement('div');
    div.style.cssText = `
      padding: 3px 8px;
      background: rgba(0,0,0,0.6);
      border-left: 2px solid #f44;
      border-radius: 3px;
      transition: opacity 0.5s;
      opacity: 1;
      white-space: nowrap;
    `;
    div.textContent = entry.text;
    entry.el = div;

    this._killFeedEl.appendChild(div);
    this._killFeedEntries.push(entry);

    // cap at 6 visible
    while (this._killFeedEntries.length > 6) {
      const old = this._killFeedEntries.shift();
      if (old.el && old.el.parentNode) old.el.parentNode.removeChild(old.el);
    }
  }

  _updateKillFeed(dt) {
    for (let i = this._killFeedEntries.length - 1; i >= 0; i--) {
      const entry = this._killFeedEntries[i];
      entry.timeLeft -= dt;
      if (entry.timeLeft <= 0) {
        if (entry.el && entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
        this._killFeedEntries.splice(i, 1);
      } else if (entry.timeLeft < 1) {
        entry.el.style.opacity = Math.max(0, entry.timeLeft).toFixed(2);
      }
    }
  }

  // --- Health Bars ---
  _updateHealthBars() {
    const ctx = this._ctx;
    const players = ctx.players; // Map<string, { mesh, health, alive, ... }>
    const playerHealthMap = ctx.playerHealthMap; // Map<string, number>
    if (!players) return;

    const camera = ctx.camera;
    const canvas = ctx.composer
      ? ctx.composer.renderer.domElement
      : (camera && camera.parent && camera.parent.type === 'Scene'
        ? document.querySelector('canvas')
        : null);

    const width = this._healthBarsContainer.clientWidth || window.innerWidth;
    const height = this._healthBarsContainer.clientHeight || window.innerHeight;

    // Track which player IDs are active
    const activeIds = new Set();

    players.forEach((pData, pid) => {
      activeIds.add(pid);

      if (!pData.mesh) return;
      if (pData.alive === false) {
        // Hide bar for dead players
        const existing = this._healthBars.get(pid);
        if (existing) existing.style.display = 'none';
        return;
      }

      // Get health from playerHealthMap or pData.health
      let health = 100;
      if (playerHealthMap && playerHealthMap.has(pid)) {
        health = playerHealthMap.get(pid);
      } else if (pData.health !== undefined) {
        health = pData.health;
      }
      const healthPct = Math.max(0, Math.min(100, health)) / 100;

      // Project mesh position to screen
      const pos = new THREE.Vector3();
      pData.mesh.getWorldPosition(pos);
      pos.y += 1.0; // above player

      const projected = pos.clone().project(camera);

      // Check if behind camera
      if (projected.z > 1) {
        const existing = this._healthBars.get(pid);
        if (existing) existing.style.display = 'none';
        return;
      }

      const sx = (projected.x * 0.5 + 0.5) * width;
      const sy = (-projected.y * 0.5 + 0.5) * height;

      let bar = this._healthBars.get(pid);
      if (!bar) {
        bar = document.createElement('div');
        bar.style.cssText = `
          position: absolute;
          width: 40px;
          height: 5px;
          border: 1px solid rgba(255,255,255,0.5);
          border-radius: 2px;
          overflow: hidden;
          transform: translate(-50%, -50%);
          pointer-events: none;
        `;
        const fill = document.createElement('div');
        fill.className = 'hb-fill';
        fill.style.cssText = `
          width: 100%;
          height: 100%;
          transition: width 0.1s;
        `;
        bar.appendChild(fill);

        // Player name label
        const label = document.createElement('div');
        label.className = 'hb-label';
        label.style.cssText = `
          position: absolute;
          bottom: 7px;
          left: 50%;
          transform: translateX(-50%);
          font-family: 'Courier New', monospace;
          font-size: 9px;
          color: #fff;
          white-space: nowrap;
          text-shadow: 0 0 3px #000;
        `;
        const isLocal = pid === this._localPlayerId;
        label.textContent = isLocal ? 'You' : pid.substring(0, 6);
        bar.appendChild(label);

        this._healthBarsContainer.appendChild(bar);
        this._healthBars.set(pid, bar);
      }

      bar.style.display = 'block';
      bar.style.left = sx + 'px';
      bar.style.top = sy + 'px';

      const fill = bar.querySelector('.hb-fill');
      if (fill) {
        fill.style.width = (healthPct * 100) + '%';
        // Color: green > yellow > red
        if (healthPct > 0.6) {
          fill.style.background = '#0f0';
        } else if (healthPct > 0.3) {
          fill.style.background = '#ff0';
        } else {
          fill.style.background = '#f00';
        }
      }
    });

    // Remove health bars for players no longer in the map
    this._healthBars.forEach((bar, pid) => {
      if (!activeIds.has(pid)) {
        if (bar.parentNode) bar.parentNode.removeChild(bar);
        this._healthBars.delete(pid);
      }
    });
  }
}