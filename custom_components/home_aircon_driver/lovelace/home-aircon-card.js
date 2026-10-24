/*
 * Home Aircon Driver card
 * -----------------------
 * Native Lovelace card for the home_aircon_driver integration. Discovers
 * zones and tank guards from HA's entity registry (platform ===
 * "home_aircon_driver"), so the common case needs zero configuration:
 *
 *   type: custom:home-aircon-card          # every zone, one compact row each
 *
 * Optional config:
 *   zones: [lounge, bedroom]   # limit to specific zone slugs
 *   title: House climate       # optional heading
 *
 * Each zone is ONE compact block: temperature, status chip, a mode switch
 * (Off/Heat/Cool/Auto), the target, and two one-shot buttons:
 *   "Cool to..." / "Heat to..."  -> run until the zone reaches X, then
 *   drop back to normal automatically (home_aircon_driver.set_override).
 * A tank guard shows as a banner: green when fine, amber on warn, pulsing
 * red on critical (zones are being force-cooled).
 */

const ZONE_SUFFIXES = {
  sensor: ["temperature", "status", "override"],
  select: ["mode"],
  number: ["target", "deadband"],
};
const GUARD_SUFFIXES = { sensor: ["level", "hottest"] };
const MODES = [
  ["off", "Off"],
  ["heat", "Heat"],
  ["cool", "Cool"],
  ["auto", "Auto"],
];
const DURATIONS = [
  [60, "1 h"],
  [120, "2 h"],
  [180, "3 h"],
];
const STATUS_TEXT = {
  off: "Off",
  idle: "Idle",
  heating: "Heating",
  cooling: "Cooling",
  guard_warn: "Tank warning - cooling",
  guard_critical: "TANK CRITICAL - cooling",
  sensor_lost: "Sensors lost",
  override_cool: "Cooling (boost)",
  override_heat: "Heating (boost)",
};
const STATUS_KIND = {
  heating: "heat",
  override_heat: "heat",
  cooling: "cool",
  override_cool: "cool",
  guard_warn: "warn",
  guard_critical: "crit",
  sensor_lost: "warn",
};

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const num = (st) => {
  if (!st) return null;
  const v = parseFloat(st.state);
  return Number.isNaN(v) ? null : v;
};
const fmt = (v) => (v === null ? "--" : v.toFixed(1));
const fmt0 = (v) => (v === null || Number.isNaN(v) ? "--" : String(Math.round(v)));  // settings: whole degrees

const fmtLeft = (ts) => {
  const ms = ts * 1000 - Date.now();
  if (!(ms > 0)) return "ending";
  const m = Math.round(ms / 60000);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
};

// Shared by the card and its editor.
function discover(hass, slugFilter) {
  if (!hass || !hass.entities) return { zones: [], guards: [] };
  const want = slugFilter && slugFilter.length ? new Set(slugFilter.map(String)) : null;
  const items = new Map(); // slug -> {slug, name, kind, e: {suffix: entity_id}}
  for (const [entityId, entry] of Object.entries(hass.entities)) {
    if (entry.platform !== "home_aircon_driver") continue;
    const dot = entityId.indexOf(".");
    const domain = entityId.slice(0, dot);
    const objectId = entityId.slice(dot + 1);
    const table = { ...ZONE_SUFFIXES };
    for (const [d, sufs] of Object.entries(GUARD_SUFFIXES)) table[d] = [...(table[d] || []), ...sufs];
    for (const suffix of table[domain] || []) {
      if (!objectId.endsWith("_" + suffix)) continue;
      const slug = objectId.slice(0, objectId.length - suffix.length - 1);
      if (!slug) break;
      if (!items.has(slug)) {
        const device = entry.device_id && hass.devices ? hass.devices[entry.device_id] : null;
        items.set(slug, { slug, name: device ? device.name_by_user || device.name : slug, e: {} });
      }
      items.get(slug).e[suffix] = entityId;
      break;
    }
  }
  const all = [...items.values()];
  const byName = (a, b) => a.name.localeCompare(b.name);
  const zones = all.filter((i) => i.e.status).sort(byName);
  const guards = all.filter((i) => i.e.level).sort(byName);
  return { zones: want ? zones.filter((z) => want.has(z.slug)) : zones, guards };
}

class HomeAirconCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._ui = {}; // slug -> {panel: 'cool'|'heat'|'settings'|'graph'|null, x, mins}
    this._hist = {}; // slug -> {at, temp: [[ms, v]], status: [[ms, state]], loading}
    this._sig = "";
    this._timer = null;
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  static getConfigElement() {
    return document.createElement("home-aircon-card-editor");
  }
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    this._sig = "";
    this._render();
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    const { zones, guards } = discover(hass, this._config && this._config.zones);
    this._found = { zones, guards };
    let sig = [...zones, ...guards]
      .flatMap((i) => Object.values(i.e))
      .map((id) => {
        const s = hass.states[id];
        return s ? s.state + JSON.stringify(s.attributes.until_ts || "") : "-";
      })
      .join("|");
    const dsig = zones
      .flatMap((z) => this._dampers(z))
      .map((id) => (hass.states[id] ? hass.states[id].state : "-"))
      .join("");
    sig += "#" + dsig + zones.map((z) => { const p = hass.states["switch." + z.slug + "_programs"]; const st = hass.states[z.e.status]; return (p ? p.state : "") + JSON.stringify((st && st.attributes.program) || ""); }).join("");
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  connectedCallback() {
    // Remaining-time labels tick locally; no polling of HA.
    this._timer = setInterval(() => {
      if (this._hasOverride) this._render();
    }, 30000);
  }
  disconnectedCallback() {
    clearInterval(this._timer);
  }

  _dampers(z) {
    const st = this._hass && z.e.status ? this._hass.states[z.e.status] : null;
    return (st && st.attributes.damper_switches) || [];
  }

  // ---- 24 h graph ----------------------------------------------------
  _parseHist(rows) {
    // history/history_during_period (minimal_response): [{s, lu|last_updated}, ...]
    return (rows || [])
      .map((r) => {
        const t = r.lu !== undefined ? r.lu * 1000 : new Date(r.last_updated).getTime();
        return [t, r.s !== undefined ? r.s : r.state];
      })
      .filter((x) => !Number.isNaN(x[0]));
  }

  async _fetchHist(z) {
    const h = (this._hist[z.slug] = this._hist[z.slug] || {});
    if (h.loading) return;
    h.loading = true;
    try {
      const end = new Date();
      const start = new Date(end.getTime() - 24 * 3600 * 1000);
      const res = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: [z.e.temperature, z.e.status],
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      h.temp = this._parseHist(res[z.e.temperature])
        .map(([t, v]) => [t, parseFloat(v)])
        .filter((x) => !Number.isNaN(x[1]));
      h.status = this._parseHist(res[z.e.status]);
      h.at = Date.now();
      h.error = null;
    } catch (err) {
      h.error = String((err && err.message) || err);
    }
    h.loading = false;
    this._render();
  }

  _graphHtml(z) {
    const h = this._hist[z.slug];
    if (!h || (h.loading && !h.temp)) return `<div class="panel"><span class="hint">Loading 24 h history…</span></div>`;
    if (h.error) return `<div class="panel"><span class="hint">History unavailable: ${esc(h.error)}</span></div>`;
    const now = Date.now();
    const t0 = now - 24 * 3600 * 1000;
    const pts = (h.temp || []).filter((p) => p[0] >= t0 - 3600 * 1000);
    if (pts.length < 2) return `<div class="panel"><span class="hint">Not enough history yet.</span></div>`;
    const target = num(this._state(z.e.target));
    const vals = pts.map((p) => p[1]).concat(target === null ? [] : [target]);
    let lo = Math.floor(Math.min(...vals) - 0.5);
    let hi = Math.ceil(Math.max(...vals) + 0.5);
    if (hi - lo < 3) hi = lo + 3;
    const W = 320, H = 110, L = 26, R = 6, T = 6, B = 16;
    const X = (t) => L + ((Math.max(t, t0) - t0) / (now - t0)) * (W - L - R);
    const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

    // shaded runs: when the zone was heating / cooling
    const st = h.status || [];
    let bands = "";
    for (let i = 0; i < st.length; i++) {
      const [ta, sa] = st[i];
      const tb = i + 1 < st.length ? st[i + 1][0] : now;
      const kind = STATUS_KIND[sa];
      if (!kind || tb <= t0) continue;
      const cls = kind === "heat" ? "bh" : "bc";
      bands += `<rect class="${cls}" x="${X(ta).toFixed(1)}" y="${T}" width="${Math.max(1, X(tb) - X(ta)).toFixed(1)}" height="${H - T - B}"/>`;
    }
    let grid = "";
    for (let v = Math.ceil(lo); v <= hi; v += Math.ceil((hi - lo) / 5)) {
      grid += `<line class="gl" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="gt2" x="${L - 3}" y="${(Y(v) + 2.5).toFixed(1)}" text-anchor="end">${v}°</text>`;
    }
    let xl = "";
    for (const hrs of [24, 18, 12, 6, 0]) {
      const t = now - hrs * 3600 * 1000;
      const lab = hrs === 0 ? "now" : new Date(t).toLocaleTimeString([], { hour: "numeric" });
      xl += `<text class="gt2" x="${X(t).toFixed(1)}" y="${H - 3}" text-anchor="${hrs === 0 ? "end" : hrs === 24 ? "start" : "middle"}">${lab}</text>`;
    }
    let d = "";
    pts.forEach(([t, v], i) => {
      d += (i ? "L" : "M") + X(t).toFixed(1) + " " + Y(v).toFixed(1);
    });
    d += `L${X(now).toFixed(1)} ${Y(pts[pts.length - 1][1]).toFixed(1)}`;
    const tl = target === null ? "" : `<line class="tl" x1="${L}" x2="${W - R}" y1="${Y(target).toFixed(1)}" y2="${Y(target).toFixed(1)}"/>`;
    const mn = Math.min(...pts.map((p) => p[1])), mx = Math.max(...pts.map((p) => p[1]));
    return `<div class="panel graph"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      ${bands}${grid}${tl}<path class="ln" d="${d}"/>${xl}</svg>
      <div class="legend"><span>24 h: low ${fmt(mn)}° · high ${fmt(mx)}°</span>
      <span><i class="k bc"></i>cooling <i class="k bh"></i>heating <i class="k tk"></i>target</span></div></div>`;
  }

  _state(id) {
    return id && this._hass ? this._hass.states[id] : undefined;
  }
  _u(slug) {
    return (this._ui[slug] = this._ui[slug] || { panel: null, x: null, mins: 120 });
  }

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data);
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-act]");
    if (!el || !this._hass) return;
    const { act, slug } = el.dataset;
    const zone = (this._found.zones || []).find((z) => z.slug === slug);
    if (!zone) return;
    const u = this._u(slug);
    const val = el.dataset.val;
    const target = num(this._state(zone.e.target)) ?? 22;
    switch (act) {
      case "mode":
        this._call("select", "select_option", { entity_id: zone.e.mode, option: val });
        break;
      case "target":
        this._call("number", "set_value", {
          entity_id: zone.e.target,
          value: Math.min(32, Math.max(10, target + parseFloat(val))),
        });
        break;
      case "deadband": {
        const db = num(this._state(zone.e.deadband)) ?? 1;
        this._call("number", "set_value", {
          entity_id: zone.e.deadband,
          value: Math.min(4, Math.max(1, Math.round(db + parseFloat(val)))),
        });
        break;
      }
      case "programs":
      case "damper":
        this._call("homeassistant", "toggle", { entity_id: el.dataset.ent });
        break;
      case "panel":
        u.panel = u.panel === val ? null : val;
        if (u.panel === "graph") {
          const hh = this._hist[slug];
          if (!hh || !hh.at || Date.now() - hh.at > 300000) this._fetchHist(zone);
        }
        if (val === "cool" || val === "heat") {
          const cur = num(this._state(zone.e.temperature));
          u.x = u.x !== null && u.panelKind === val ? u.x : Math.round(val === "cool" ? Math.min(target, cur ?? target, 22) : Math.max(target, cur ?? target, 24));
          u.panelKind = val;
        }
        this._render();
        break;
      case "x":
        u.x = Math.min(32, Math.max(10, (u.x ?? 22) + parseFloat(val)));
        this._render();
        break;
      case "mins":
        u.mins = parseInt(val, 10);
        this._render();
        break;
      case "go":
        this._call("home_aircon_driver", "set_override", {
          zone: slug,
          mode: u.panel,
          target: u.x,
          max_minutes: u.mins,
        });
        u.panel = null;
        this._render();
        break;
      case "cancel":
        this._call("home_aircon_driver", "clear_override", { zone: slug });
        break;
    }
  }

  _guardHtml(g) {
    const lvl = this._state(g.e.level);
    const hot = num(this._state(g.e.hottest));
    const level = lvl ? lvl.state : "unknown";
    const reason = lvl && lvl.attributes.reason;
    const text =
      level === "critical"
        ? reason === "no_data"
          ? "No tank sensor data"
          : `Tank critical - ${fmt(hot)}°`
        : level === "warn"
          ? `Tank warning - ${fmt(hot)}°`
          : `Tanks OK - ${fmt(hot)}°`;
    return `<div class="guard ${esc(level)}"><span class="gi">${level === "ok" ? "🐟" : "⚠️"}</span>
      <b>${esc(g.name)}</b><span class="gt">${esc(text)}</span></div>`;
  }

  _zoneHtml(z) {
    const u = this._u(z.slug);
    const temp = num(this._state(z.e.temperature));
    const target = num(this._state(z.e.target)) ?? 22;
    const db = num(this._state(z.e.deadband)) ?? 1;
    const status = (this._state(z.e.status) || {}).state || "unknown";
    const mode = (this._state(z.e.mode) || {}).state || "off";
    const ov = this._state(z.e.override);
    const ovOn = ov && (ov.state === "cool" || ov.state === "heat");
    const kind = STATUS_KIND[status] || "";
    if (ovOn) this._hasOverride = true;

    const modes = MODES.map(
      ([v, l]) =>
        `<button class="seg ${mode === v ? "on " + v : ""}" data-act="mode" data-slug="${z.slug}" data-val="${v}">${l}</button>`
    ).join("");

    const banner = ovOn
      ? `<div class="ov ${ov.state}"><span>${ov.state === "cool" ? "❄️ Cooling" : "🔥 Heating"} to ${fmt0(
          parseFloat(ov.attributes.target)
        )}° · ${fmtLeft(ov.attributes.until_ts)} left</span>
         <button class="chip" data-act="cancel" data-slug="${z.slug}">Cancel</button></div>`
      : "";

    let panel = "";
    if (u.panel === "cool" || u.panel === "heat") {
      panel = `<div class="panel">
        <span class="lbl">${u.panel === "cool" ? "Cool" : "Heat"} to</span>
        <button class="step" data-act="x" data-slug="${z.slug}" data-val="-1">−</button>
        <b class="val">${fmt0(u.x)}°</b>
        <button class="step" data-act="x" data-slug="${z.slug}" data-val="1">+</button>
        <span class="lbl gap">for up to</span>
        ${DURATIONS.map(
          ([m, l]) =>
            `<button class="chip ${u.mins === m ? "on" : ""}" data-act="mins" data-slug="${z.slug}" data-val="${m}">${l}</button>`
        ).join("")}
        <button class="go ${u.panel}" data-act="go" data-slug="${z.slug}">Start</button>
      </div>`;
    } else if (u.panel === "graph") {
      panel = this._graphHtml(z);
    } else if (u.panel === "settings") {
      panel = `<div class="panel"><span class="lbl">Deadband</span>
        <button class="step" data-act="deadband" data-slug="${z.slug}" data-val="-1">−</button>
        <b class="val">${db.toFixed(0)}°</b>
        <button class="step" data-act="deadband" data-slug="${z.slug}" data-val="1">+</button>
        <span class="hint">starts at target ± half this, runs until target</span></div>`;
    }

    const stSt = this._state(z.e.status);
    const prog = stSt && stSt.attributes.program;
    const pSw = this._state("switch." + z.slug + "_programs");
    const until = prog && prog.next_event ? new Date(prog.next_event).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
    const progLine = prog
      ? `<div class="prog">📅 Program: ${esc(prog.mode)} ${fmt0(parseFloat(prog.target))}°${until ? " until " + until : ""}</div>`
      : "";
    const progBtn = pSw
      ? `<button class="chip ${pSw.state === "on" ? "on" : ""}" data-act="programs" data-slug="${z.slug}" data-ent="switch.${z.slug}_programs">📅 Programs ${pSw.state === "on" ? "on" : "off"}</button>`
      : "";
    const dIds = this._dampers(z);
    const dampers = dIds.length
      ? `<div class="acts"><span class="lbl">Rooms</span>${dIds
          .map((id) => {
            const st = this._state(id);
            const nm = ((st && st.attributes.friendly_name) || id).replace(new RegExp("^" + z.name + "\s+", "i"), "").replace(/ damper$/i, "");
            return `<button class="chip ${st && st.state === "on" ? "on" : ""}" data-act="damper" data-slug="${z.slug}" data-ent="${esc(id)}">${esc(nm)}</button>`;
          })
          .join("")}</div>`
      : "";
    return `<div class="zone ${kind}">
      <div class="top">
        <span class="name">${esc(z.name)}</span>
        <span class="temp">${fmt(temp)}°</span>
        <span class="status ${kind}">${esc(STATUS_TEXT[status] || status)}</span>
      </div>
      ${banner}
      ${progLine}
      <div class="ctl">
        <div class="segs">${modes}</div>
        <div class="tgt">
          <button class="step" data-act="target" data-slug="${z.slug}" data-val="-1">−</button>
          <b class="val" title="Target">${fmt0(target)}°</b>
          <button class="step" data-act="target" data-slug="${z.slug}" data-val="1">+</button>
        </div>
      </div>
      <div class="acts">
        <button class="chip cool ${u.panel === "cool" ? "on" : ""}" data-act="panel" data-slug="${z.slug}" data-val="cool">❄️ Cool to…</button>
        <button class="chip heat ${u.panel === "heat" ? "on" : ""}" data-act="panel" data-slug="${z.slug}" data-val="heat">🔥 Heat to…</button>
        ${progBtn}
        <button class="chip ${u.panel === "graph" ? "on" : ""}" data-act="panel" data-slug="${z.slug}" data-val="graph">📈 24h</button>
        <button class="chip ${u.panel === "settings" ? "on" : ""}" data-act="panel" data-slug="${z.slug}" data-val="settings">⚙</button>
      </div>
      ${dampers}
      ${panel}
    </div>`;
  }

  _render() {
    if (!this._hass || !this._found) {
      this.shadowRoot.innerHTML = "";
      return;
    }
    this._hasOverride = false;
    const { zones, guards } = this._found;
    const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
    const body =
      zones.length || guards.length
        ? guards.map((g) => this._guardHtml(g)).join("") + zones.map((z) => this._zoneHtml(z)).join("")
        : `<div class="empty">No Home Aircon Driver zones found. Add one under Settings → Devices &amp; services.</div>`;
    for (const z of zones) {
      const hh = this._hist[z.slug];
      if (this._u(z.slug).panel === "graph" && hh && hh.at && !hh.loading && Date.now() - hh.at > 300000) this._fetchHist(z);
    }
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${title}<div class="wrap">${body}</div></ha-card>`;
  }
}

const CSS = `
  :host { display:block; }
  ha-card { height:100%; padding:12px; box-sizing:border-box; }
  .title { font-size:1.1em; font-weight:500; padding:0 4px 8px; }
  .wrap { display:flex; flex-direction:column; gap:10px; }
  .empty { color:var(--secondary-text-color); padding:8px; }
  button { font:inherit; cursor:pointer; color:var(--primary-text-color); border:none; }
  .guard { display:flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px;
    background:rgba(76,175,80,.15); }
  .guard.warn { background:rgba(255,152,0,.22); }
  .guard.critical { background:rgba(244,67,54,.28); animation:pulse 1.4s infinite; }
  .guard .gt { margin-left:auto; }
  @keyframes pulse { 50% { background:rgba(244,67,54,.5); } }
  .zone { border-radius:12px; padding:10px 12px; background:var(--secondary-background-color);
    border-left:4px solid transparent; }
  .zone.heat { border-left-color:#ff7043; } .zone.cool { border-left-color:#42a5f5; }
  .zone.warn { border-left-color:#ffa726; } .zone.crit { border-left-color:#ef5350; }
  .top { display:flex; align-items:baseline; gap:10px; }
  .name { font-weight:500; flex:1; }
  .temp { font-size:1.6em; font-weight:300; }
  .status { font-size:.8em; padding:2px 8px; border-radius:10px; background:var(--divider-color); }
  .status.heat { background:#ff7043; color:#fff; } .status.cool { background:#42a5f5; color:#fff; }
  .status.warn { background:#ffa726; color:#000; } .status.crit { background:#ef5350; color:#fff; }
  .ov { display:flex; align-items:center; justify-content:space-between; margin-top:8px;
    padding:6px 10px; border-radius:8px; background:rgba(66,165,245,.2); }
  .prog { margin-top:6px; color:var(--secondary-text-color); font-size:.9em; }
  .ov.heat { background:rgba(255,112,67,.22); }
  .ctl { display:flex; align-items:center; gap:10px; margin-top:8px; flex-wrap:wrap; }
  .segs { display:flex; border-radius:8px; overflow:hidden; background:var(--divider-color); }
  .seg { padding:6px 12px; background:transparent; }
  .seg.on { background:var(--primary-color); color:#fff; }
  .seg.on.heat { background:#ff7043; } .seg.on.cool { background:#42a5f5; }
  .tgt { display:flex; align-items:center; gap:6px; margin-left:auto; }
  .val { min-width:3.2em; text-align:center; }
  .step { width:30px; height:30px; border-radius:50%; background:var(--divider-color); font-size:1.1em; }
  .acts { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
  .chip { padding:5px 12px; border-radius:16px; background:var(--divider-color); }
  .chip.on { outline:2px solid var(--primary-color); }
  .chip.cool { background:rgba(66,165,245,.25); } .chip.heat { background:rgba(255,112,67,.25); }
  .panel { display:flex; align-items:center; gap:8px; margin-top:8px; padding:8px 10px; flex-wrap:wrap;
    border-radius:8px; background:var(--card-background-color); }
  .lbl { color:var(--secondary-text-color); } .gap { margin-left:6px; }
  .hint { color:var(--secondary-text-color); font-size:.8em; }
  .panel.graph { display:block; }
  .graph svg { width:100%; height:130px; display:block; }
  .graph .ln { fill:none; stroke:var(--primary-text-color); stroke-width:1.4; vector-effect:non-scaling-stroke; }
  .graph .gl { stroke:var(--divider-color); stroke-width:.5; vector-effect:non-scaling-stroke; }
  .graph .tl { stroke:var(--secondary-text-color); stroke-width:1; stroke-dasharray:4 3; vector-effect:non-scaling-stroke; }
  .graph .gt2 { fill:var(--secondary-text-color); font-size:8px; }
  .graph .bc { fill:rgba(66,165,245,.28); } .graph .bh { fill:rgba(255,112,67,.30); }
  .legend { display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px; margin-top:4px;
    color:var(--secondary-text-color); font-size:.8em; }
  .k { display:inline-block; width:10px; height:10px; margin:0 3px 0 8px; border-radius:2px; vertical-align:-1px; }
  .k.bc { background:rgba(66,165,245,.6); } .k.bh { background:rgba(255,112,67,.6); }
  .k.tk { background:none; border-top:2px dashed var(--secondary-text-color); height:0; border-radius:0; }
  .go { margin-left:auto; padding:7px 18px; border-radius:16px; color:#fff; font-weight:500; }
  .go.cool { background:#42a5f5; } .go.heat { background:#ff7043; }
`;

class HomeAirconCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._draw();
  }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._draw();
  }
  _fire(config) {
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
  }
  _draw() {
    if (!this._hass) return;
    // hass can arrive before setConfig; never read config while undefined.
    this._config = this._config || {};
    const { zones } = discover(this._hass);
    const chosen = new Set(this._config.zones || []);
    this.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px">
      <label>Title <input type="text" id="t" value="${esc(this._config.title || "")}" style="width:100%"></label>
      <div>Zones (none ticked = all)</div>
      ${zones
        .map(
          (z) =>
            `<label><input type="checkbox" data-slug="${esc(z.slug)}" ${chosen.has(z.slug) ? "checked" : ""}> ${esc(z.name)}</label>`
        )
        .join("")}
    </div>`;
    this.querySelector("#t").addEventListener("change", (e) => {
      const c = { ...this._config };
      if (e.target.value) c.title = e.target.value;
      else delete c.title;
      this._fire(c);
    });
    this.querySelectorAll("input[type=checkbox]").forEach((cb) =>
      cb.addEventListener("change", () => {
        const slugs = [...this.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.dataset.slug);
        const c = { ...this._config };
        if (slugs.length) c.zones = slugs;
        else delete c.zones;
        this._fire(c);
      })
    );
  }
}

customElements.define("home-aircon-card", HomeAirconCard);
customElements.define("home-aircon-card-editor", HomeAirconCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "home-aircon-card",
  name: "Home Aircon Driver",
  description: "Zones with mode, target, one-tap Cool to / Heat to boosts, and tank guard status.",
});
