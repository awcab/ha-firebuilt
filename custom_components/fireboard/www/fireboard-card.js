/**
 * Fireboard Lovelace card.
 *
 * Usage (zero config — auto-discovers all Fireboard devices):
 *   type: custom:fireboard-card
 *
 * Single device by HA device_id:
 *   type: custom:fireboard-card
 *   device: 5f3c... (from Settings -> Devices)
 *
 * Hand-pick entities (only renders these as probes):
 *   type: custom:fireboard-card
 *   title: Smoker
 *   entities:
 *     - sensor.fireboard_pit
 *     - sensor.fireboard_food
 *
 * History chart:
 *   hours: 2     # default 2; set to 0 to hide the trend chart
 */

const PLATFORM = "fireboard";
const HISTORY_REFRESH_MS = 60_000;

class FireboardCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._renderedKey = null;
    this._tickHandle = null;
    this._history = new Map();      // entity_id -> [{t, v}]
    this._historyFetchedAt = 0;
    this._historyFetchInFlight = false;
    this._compact = null;           // resolved lazily once setConfig runs
    // WS-fetched entity registry, for HA builds where hass.entities is
    // missing or doesn't contain platform info. Map<entity_id, {platform, device_id, unique_id, ...}>
    this._registryCache = null;
    this._registryFetching = false;
    this._wsDevicesCache = null;    // Map<device_id, {name, name_by_user}>
  }

  connectedCallback() {
    // Repaint once a minute so cook timer + chart x-axis advance.
    this._tickHandle = setInterval(() => {
      this._renderedKey = null;
      this._render();
    }, 60_000);
  }

  disconnectedCallback() {
    if (this._tickHandle) clearInterval(this._tickHandle);
  }

  setConfig(config) {
    this._config = config || {};
    this._renderedKey = null;
    this._history = new Map();
    this._historyFetchedAt = 0;
    this._compact = this._loadCompact();
  }

  _compactKey() {
    // Stable key derived from config so multiple cards on a dashboard
    // remember their own compact state independently.
    const k = {
      device: this._config.device || null,
      entities: this._config.entities || null,
      title: this._config.title || null,
    };
    return "fireboard-card.compact:" + JSON.stringify(k);
  }

  _loadCompact() {
    try {
      const stored = window.localStorage.getItem(this._compactKey());
      if (stored === "1") return true;
      if (stored === "0") return false;
    } catch (e) {
      // localStorage may be unavailable in restricted contexts; fall through.
    }
    return Boolean(this._config.compact);
  }

  _saveCompact() {
    try {
      window.localStorage.setItem(
        this._compactKey(),
        this._compact ? "1" : "0"
      );
    } catch (e) {
      // Ignore — state simply won't persist across reloads.
    }
  }

  _toggleCompact() {
    this._compact = !this._compact;
    this._saveCompact();
    this._renderedKey = null;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return {};
  }

  get _historyHours() {
    const v = Number(this._config.hours);
    return Number.isFinite(v) ? Math.max(0, v) : 2;
  }

  set hass(hass) {
    const isFirstSet = !this._hass;
    this._hass = hass;
    if (this._historyHours > 0) {
      if (
        isFirstSet ||
        Date.now() - this._historyFetchedAt > HISTORY_REFRESH_MS
      ) {
        this._fetchHistory();
      } else {
        this._appendCurrentToHistory();
      }
    }
    this._render();
  }

  async _fetchHistory() {
    if (!this._hass || this._historyFetchInFlight) return;
    const ids = this._historyEntityIds();
    if (!ids.length) return;
    this._historyFetchInFlight = true;
    const start = new Date(Date.now() - this._historyHours * 3600 * 1000);
    const end = new Date();
    try {
      const result = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      const next = new Map();
      for (const [id, points] of Object.entries(result || {})) {
        const cleaned = [];
        for (const p of points) {
          const v = parseFloat(p.s ?? p.state);
          const ts = p.lu != null ? p.lu * 1000 : Date.parse(p.last_updated || p.last_changed);
          if (Number.isFinite(v) && Number.isFinite(ts)) {
            cleaned.push({ t: ts, v });
          }
        }
        next.set(id, cleaned);
      }
      this._history = next;
      this._historyFetchedAt = Date.now();
      this._appendCurrentToHistory();
      this._renderedKey = null;
      this._render();
    } catch (err) {
      // Recorder may be disabled, or the WS API may have changed.
      // Stay silent in production but warn once for debugging.
      if (!this._historyWarned) {
        console.warn("Fireboard card: history fetch failed", err);
        this._historyWarned = true;
      }
    } finally {
      this._historyFetchInFlight = false;
    }
  }

  _historyEntityIds() {
    // Every Fireboard temperature entity we know about.
    const out = [];
    for (const [entityId, stateObj] of Object.entries(this._hass.states || {})) {
      const reg = this._entityReg(entityId);
      if (!reg || reg.platform !== PLATFORM) continue;
      const klass = stateObj.attributes && stateObj.attributes.device_class;
      if (klass !== "temperature") continue;
      if (this._config.device && reg.device_id !== this._config.device) continue;
      out.push(entityId);
    }
    if (Array.isArray(this._config.entities)) {
      for (const id of this._config.entities) {
        if (!out.includes(id)) out.push(id);
      }
    }
    return out;
  }

  _appendCurrentToHistory() {
    if (!this._hass) return;
    const now = Date.now();
    const cutoff = now - this._historyHours * 3600 * 1000;
    for (const id of this._historyEntityIds()) {
      const state = this._hass.states[id];
      if (!state) continue;
      const v = parseFloat(state.state);
      if (!Number.isFinite(v)) continue;
      if (!this._history.has(id)) this._history.set(id, []);
      const points = this._history.get(id);
      const last = points[points.length - 1];
      // Only push when temp changed enough or 30+ s elapsed since last point.
      if (!last || Math.abs(last.v - v) > 0.05 || now - last.t > 30_000) {
        points.push({ t: now, v });
      }
      while (points.length && points[0].t < cutoff) points.shift();
    }
  }

  _entityReg(entityId) {
    if (this._hass.entities && this._hass.entities[entityId]) {
      return this._hass.entities[entityId];
    }
    if (this._registryCache && this._registryCache.has(entityId)) {
      return this._registryCache.get(entityId);
    }
    return null;
  }

  _entityKind(stateObj) {
    // Identify the kind of Fireboard entity. We can't rely on unique_id
    // because hass.entities exposes EntityRegistryDisplayEntry, which
    // doesn't include unique_id — only the WS-fetched full registry
    // does. The integration tags every entity with a `fireboard_kind`
    // attribute we trust above all else; the rest is fallback for older
    // integration versions or third-party entities.
    const attrs = stateObj.attributes || {};
    if (typeof attrs.fireboard_kind === "string") return attrs.fireboard_kind;

    const reg = this._entityReg(stateObj.entity_id);
    const uid = (reg && reg.unique_id) || "";
    const id = stateObj.entity_id;
    const klass = attrs.device_class;
    const isBinarySensor = id.startsWith("binary_sensor.");

    // Alerts: binary_sensor.<...>_channel_N_alert + has channel attribute.
    if (isBinarySensor && (
        /_channel_\d+_alert$/.test(uid) ||
        /_channel_\d+_alert$/.test(id) ||
        (klass === "problem" && typeof attrs.channel === "number")
    )) return "alert";

    // Probe channel: temperature sensor with a `channel` integer attribute.
    if (klass === "temperature" && typeof attrs.channel === "number") return "channel";

    // Setpoint vs probe — both are temperature, but only one has `channel`.
    if (klass === "temperature" && (
        uid.endsWith("_setpoint") || id.endsWith("_setpoint")
    )) return "setpoint";

    if (uid.endsWith("_drive_output") || id.endsWith("_drive_output")) return "drive_output";
    if (uid.endsWith("_drive_battery") || id.endsWith("_drive_battery")) return "drive_battery";
    if (uid.endsWith("_cook_started") || id.endsWith("_cook_started")) return "cook_started";
    if (uid.endsWith("_cook_session") || id.endsWith("_cook_session")) return "cook_session";
    if (klass === "battery" || uid.endsWith("_battery") || id.endsWith("_battery")) return "battery";
    if (klass === "timestamp" && (
        uid.endsWith("_last_seen") || id.endsWith("_last_seen")
    )) return "last_seen";

    // Last-resort fallback: a temperature sensor that matches the channel
    // entity_id pattern (e.g., user renamed but kept a recognizable suffix).
    if (klass === "temperature" && (
        /_channel_\d+$/.test(uid) || /_channel_\d+$/.test(id)
    )) return "channel";

    return null;
  }

  _channelOf(stateObj) {
    const attrs = stateObj.attributes || {};
    if (typeof attrs.channel === "number") return attrs.channel;
    const reg = this._entityReg(stateObj.entity_id);
    const uid = (reg && reg.unique_id) || "";
    let m = uid.match(/_channel_(\d+)(?:_alert)?$/);
    if (m) return Number(m[1]);
    m = stateObj.entity_id.match(/_channel_(\d+)(?:_alert)?$/);
    if (m) return Number(m[1]);
    return null;
  }

  async _ensureRegistryCache() {
    if (this._registryCache !== null || this._registryFetching) return;
    if (!this._hass || typeof this._hass.callWS !== "function") return;
    this._registryFetching = true;
    let map;
    try {
      const list = await this._hass.callWS({
        type: "config/entity_registry/list",
      });
      map = new Map();
      for (const e of list || []) {
        if (e && e.platform === PLATFORM) map.set(e.entity_id, e);
      }
    } catch (err) {
      this._registryCache = new Map(); // mark as attempted so we don't loop
      this._registryFetching = false;
      console.warn("Fireboard card: WS entity_registry/list failed", err);
      return;
    }
    this._registryCache = map;
    this._registryFetching = false;

    // Best-effort device fetch — failures here don't matter to the cache.
    try {
      const devList = await this._hass.callWS({
        type: "config/device_registry/list",
      });
      const dmap = new Map();
      for (const d of devList || []) dmap.set(d.id, d);
      this._wsDevicesCache = dmap;
    } catch (_) {
      this._wsDevicesCache = null;
    }

    // Re-render now that we have data. Wrapped so a render error can't
    // poison the cache state we just successfully populated.
    try {
      this._renderedKey = null;
      this._render();
    } catch (err) {
      console.warn("Fireboard card: render after registry fetch failed", err);
    }
  }

  _device(deviceId) {
    if (this._hass.devices && this._hass.devices[deviceId]) {
      return this._hass.devices[deviceId];
    }
    if (this._wsDevicesCache && this._wsDevicesCache.has(deviceId)) {
      return this._wsDevicesCache.get(deviceId);
    }
    return null;
  }

  _groups() {
    const hass = this._hass;
    if (!hass) return [];

    if (Array.isArray(this._config.entities) && this._config.entities.length) {
      const channels = this._config.entities
        .map((id) => hass.states[id])
        .filter(Boolean)
        .map((s) => this._channelFromState(s));
      return [
        {
          name: this._config.title || "Fireboard",
          channels,
          battery: null,
          lastSeen: null,
          setpoint: null,
          drivePct: null,
          cookStarted: null,
          cookSession: null,
          alertEntities: [],
        },
      ];
    }

    const byDevice = new Map();
    let sawAnyFireboard = false;
    for (const [entityId, stateObj] of Object.entries(hass.states)) {
      const reg = this._entityReg(entityId);
      const platformMatch = reg && reg.platform === PLATFORM;
      const taggedFireboard =
        stateObj.attributes &&
        typeof stateObj.attributes.fireboard_kind === "string";
      if (!platformMatch && !taggedFireboard) continue;
      sawAnyFireboard = true;
      const deviceId = reg && reg.device_id;
      if (this._config.device && deviceId !== this._config.device) continue;
      const deviceKey = deviceId || "_orphan_";
      if (!byDevice.has(deviceKey)) byDevice.set(deviceKey, []);
      byDevice.get(deviceKey).push(stateObj);
    }

    // If we didn't find anything via hass.entities, kick off a WS registry
    // fetch — some HA builds don't populate hass.entities for custom cards.
    if (!sawAnyFireboard && !this._registryCache) {
      this._ensureRegistryCache();
    }

    const groups = [];
    for (const [deviceId, items] of byDevice.entries()) {
      const device = this._device(deviceId) || {};
      const group = {
        name:
          this._config.title ||
          device.name_by_user ||
          device.name ||
          "Fireboard",
        channels: [],
        battery: null,
        lastSeen: null,
        setpoint: null,
        drivePct: null,
        cookStarted: null,
        cookSession: null,
        alertEntities: [],
      };
      const alertsByChannel = new Map();
      for (const stateObj of items) {
        const kind = this._entityKind(stateObj);
        switch (kind) {
          case "channel":
            group.channels.push(this._channelFromState(stateObj));
            break;
          case "alert": {
            const ch = this._channelOf(stateObj);
            if (ch !== null) alertsByChannel.set(ch, stateObj);
            group.alertEntities.push(stateObj);
            break;
          }
          case "setpoint":
            group.setpoint = stateObj;
            break;
          case "drive_output":
            group.drivePct = stateObj;
            break;
          case "battery":
            group.battery = stateObj;
            break;
          case "last_seen":
            group.lastSeen = stateObj;
            break;
          case "cook_started":
            group.cookStarted = stateObj;
            break;
          case "cook_session":
            group.cookSession = stateObj;
            break;
          default:
            break;
        }
      }
      // Attach the matching alert state to each channel for tile coloring.
      for (const c of group.channels) {
        const m = c.entity_id && c.unique_id_match;
        if (m) {
          const alert = alertsByChannel.get(m);
          if (alert) c.alertOn = alert.state === "on";
        }
      }
      group.channels.sort((a, b) =>
        (a.label || "").localeCompare(b.label || "")
      );

      // The FireBoard Drive's "tied channel" is the probe it controls
      // the fan against — that's the ambient/pit reading. Pull it out of
      // the channel list so it doesn't get rendered twice. We still want
      // it in the trend chart, so keep a reference.
      group.controlChannel = null;
      const tied =
        group.drivePct &&
        group.drivePct.attributes &&
        group.drivePct.attributes.tied_channel;
      if (typeof tied === "number") {
        const idx = group.channels.findIndex((c) => c.unique_id_match === tied);
        if (idx >= 0) {
          group.controlChannel = group.channels[idx];
          group.channels.splice(idx, 1);
        }
      }

      groups.push(group);
    }
    // Devices that are actively being driven/cooked sort first; idle
    // probes-only devices fall to the bottom. Tie-break alphabetically.
    const priority = (g) => {
      let p = 0;
      if (g.cookSession && g.cookSession.state !== "unavailable") p += 4;
      if (g.setpoint && g.setpoint.state !== "unavailable") p += 2;
      if (g.controlChannel) p += 1;
      return p;
    };
    groups.sort((a, b) => {
      const diff = priority(b) - priority(a);
      if (diff !== 0) return diff;
      return a.name.localeCompare(b.name);
    });

    // Hide devices that aren't currently reporting any probe temperatures.
    // Set show_offline: true in the card config to keep them visible.
    if (!this._config.show_offline) {
      const live = groups.filter((g) => this._isDeviceConnected(g));
      if (live.length === 0 && groups.length > 0) {
        // Mark for the empty-state renderer so it can distinguish "no
        // data at all" from "everything filtered out".
        this._allOffline = true;
        return [];
      }
      this._allOffline = false;
      return live;
    }
    this._allOffline = false;
    return groups;
  }

  _isDeviceConnected(group) {
    // A device counts as "connected" if any of:
    //   - the control/pit channel is reporting a value
    //   - any regular probe channel is reporting
    //   - the device sent a recent heartbeat (last_seen < 5 min ago)
    // The last case keeps online-but-idle devices visible after a cook
    // ends instead of having them silently disappear from the dashboard.
    if (group.controlChannel && Number.isFinite(group.controlChannel.value)) {
      return true;
    }
    if (group.channels.some((c) => Number.isFinite(c.value))) {
      return true;
    }
    if (group.lastSeen && group.lastSeen.state && group.lastSeen.state !== "unavailable") {
      const t = Date.parse(group.lastSeen.state);
      if (Number.isFinite(t) && Date.now() - t < 5 * 60 * 1000) {
        return true;
      }
    }
    return false;
  }

  _channelFromState(stateObj) {
    const unit = stateObj.attributes.unit_of_measurement || "";
    const numeric = Number(stateObj.state);
    return {
      entity_id: stateObj.entity_id,
      unique_id_match: this._channelOf(stateObj),
      label:
        stateObj.attributes.friendly_name ||
        stateObj.entity_id.replace(/^sensor\./, ""),
      value: Number.isFinite(numeric) ? numeric : null,
      raw: stateObj.state,
      unit,
      isCelsius: unit.includes("C"),
      colorHex: stateObj.attributes.color_hex || null,
      alertOn: false,
    };
  }

  _tempColor(value, isCelsius) {
    if (value === null) return "var(--disabled-text-color, #888)";
    const max = isCelsius ? 260 : 500;
    const t = Math.max(0, Math.min(1, value / max));
    const hue = 220 - 220 * t;
    return `hsl(${hue}, 75%, 48%)`;
  }

  _formatRelative(ts, suffix = "ago") {
    if (!ts) return "—";
    const then = new Date(ts);
    if (isNaN(then.getTime())) return ts;
    const diffSec = Math.round((Date.now() - then.getTime()) / 1000);
    const abs = Math.abs(diffSec);
    let str;
    if (abs < 60) str = `${abs}s`;
    else if (abs < 3600) str = `${Math.floor(abs / 60)}m`;
    else if (abs < 86400) {
      const h = Math.floor(abs / 3600);
      const m = Math.floor((abs % 3600) / 60);
      str = m ? `${h}h ${m}m` : `${h}h`;
    } else {
      str = `${Math.floor(abs / 86400)}d`;
    }
    return `${str} ${suffix}`;
  }

  _fireMoreInfo(entityId) {
    const evt = new Event("hass-more-info", { bubbles: true, composed: true });
    evt.detail = { entityId };
    this.dispatchEvent(evt);
  }

  _render() {
    if (!this._hass) return;
    const groups = this._groups();
    const key = JSON.stringify([
      groups.map((g) => [
        g.name,
        g.channels.map((c) => [c.entity_id, c.raw, c.unit, c.alertOn]),
        g.controlChannel && [
          g.controlChannel.entity_id,
          g.controlChannel.raw,
          g.controlChannel.unit,
          g.controlChannel.alertOn,
        ],
        g.battery && g.battery.state,
        g.lastSeen && g.lastSeen.state,
        g.setpoint && [g.setpoint.state, g.setpoint.attributes.unit_of_measurement],
        g.drivePct && g.drivePct.state,
        g.cookStarted && g.cookStarted.state,
        g.cookSession && g.cookSession.state,
        Math.floor(Date.now() / 60000),
        this._historyFetchedAt,
        this._compact,
      ]),
      this._allOffline,
    ]);
    if (key === this._renderedKey) return;
    this._renderedKey = key;

    if (!groups.length) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>${this._renderDiagnosticEmpty()}</ha-card>`;
      return;
    }

    const showToggle = this._historyHours > 0;
    const toggleIcon = this._compact ? "mdi:chart-line" : "mdi:format-list-bulleted";
    const toggleLabel = this._compact ? "Show chart" : "Compact mode";
    const toggleHtml = showToggle
      ? `<button class="card-toggle" data-toggle aria-label="${toggleLabel}" title="${toggleLabel}">
           <ha-icon icon="${toggleIcon}"></ha-icon>
         </button>`
      : "";

    const banner = this._renderUpdateBanner(groups);
    const body = groups.map((g) => this._renderGroup(g)).join("");
    this.shadowRoot.innerHTML = `${this._styles()}<ha-card class="${
      this._compact ? "compact" : ""
    }">${toggleHtml}${banner}${body}</ha-card>`;

    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._fireMoreInfo(el.getAttribute("data-entity"));
      });
    });
    const toggleEl = this.shadowRoot.querySelector("[data-toggle]");
    if (toggleEl) {
      toggleEl.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._toggleCompact();
      });
    }
  }

  _renderAllOfflineEmpty() {
    return `
      <div class="empty-diag">
        <div class="empty-msg">
          No Fireboard devices are reporting probe data right now.
        </div>
        <div style="font-size: 0.85rem; color: var(--secondary-text-color);">
          Plug in a probe, or set <code>show_offline: true</code> in the card
          config to keep idle devices visible.
        </div>
      </div>`;
  }

  _renderUpdateBanner(groups) {
    // Rate-limit / update-error indicator. The coordinator-shared state
    // is duplicated across every device's last_seen sensor, so we just
    // grab the first one we find.
    let state = null;
    let detail = null;
    for (const g of groups) {
      if (g.lastSeen && g.lastSeen.attributes && g.lastSeen.attributes.update_state) {
        state = g.lastSeen.attributes.update_state;
        detail = g.lastSeen.attributes.update_error;
        break;
      }
    }
    if (!state) return "";
    const isRate = state === "rate_limited";
    const icon = isRate ? "mdi:speedometer-slow" : "mdi:alert-circle-outline";
    const label = isRate ? "Rate limited" : "Update error";
    const tooltip = detail ? this._escape(detail) : label;
    const message = isRate
      ? "Fireboard returned 429 — pausing updates briefly."
      : "Last update from Fireboard failed.";
    return `
      <div class="update-banner ${isRate ? "rate" : "error"}" title="${tooltip}">
        <ha-icon icon="${icon}"></ha-icon>
        <span class="update-banner-text"><strong>${label}.</strong> ${message}</span>
      </div>`;
  }

  _renderDiagnosticEmpty() {
    if (this._allOffline) return this._renderAllOfflineEmpty();
    const hass = this._hass;
    const totalStates = Object.keys((hass && hass.states) || {}).length;
    const hasEntitiesProp = !!(hass && hass.entities);
    const hasDevicesProp = !!(hass && hass.devices);
    const totalEntities = hasEntitiesProp ? Object.keys(hass.entities).length : 0;
    const totalDevices = hasDevicesProp ? Object.keys(hass.devices).length : 0;
    let firebPlatformCount = 0;
    let firebWithDevice = 0;
    if (hasEntitiesProp) {
      for (const e of Object.values(hass.entities)) {
        if (e.platform === PLATFORM) {
          firebPlatformCount++;
          if (e.device_id) firebWithDevice++;
        }
      }
    }
    const wsCount = this._registryCache ? this._registryCache.size : null;
    const wsState = this._registryFetching
      ? "fetching…"
      : wsCount === null
      ? "not fetched yet"
      : `${wsCount} entries`;

    let primary;
    if (firebPlatformCount === 0 && wsCount === 0) {
      primary = "No Fireboard entities exist in Home Assistant. " +
        "Open Settings → Devices &amp; Services and confirm the integration loaded successfully.";
    } else if (firebPlatformCount === 0 && wsCount === null) {
      primary = "Looking for Fireboard entities…";
    } else {
      primary = "Found Fireboard entities but none could be grouped into a device. " +
        "Check Settings → Devices &amp; Services → Fireboard and verify the device is shown.";
    }

    return `
      <div class="empty-diag">
        <div class="empty-msg">${primary}</div>
        <details>
          <summary>Diagnostics</summary>
          <ul>
            <li>hass.states: ${totalStates} entries</li>
            <li>hass.entities: ${hasEntitiesProp ? `present (${totalEntities})` : "missing"}</li>
            <li>hass.devices: ${hasDevicesProp ? `present (${totalDevices})` : "missing"}</li>
            <li>Fireboard-platform entities (via hass.entities): ${firebPlatformCount}</li>
            <li>Of those, with a device_id: ${firebWithDevice}</li>
            <li>WS registry fallback: ${wsState}</li>
          </ul>
        </details>
      </div>`;
  }

  _renderGroup(g) {
    // Drop probes whose current value is missing — keeps the grid focused
    // on the channels that actually have a reading right now. The chart
    // still uses g.channels directly, so historical lines aren't lost.
    const liveChannels = g.channels.filter((c) => Number.isFinite(c.value));
    const pitHasValue =
      g.controlChannel && Number.isFinite(g.controlChannel.value);

    // Compact mode keeps the pit channel inline with the other probes
    // (since the drive panel is hidden). Expanded mode promotes it to the
    // 3-column control panel below.
    let probeChannels;
    if (this._compact && pitHasValue) {
      const sp =
        g.setpoint && g.setpoint.state !== "unavailable"
          ? Number(g.setpoint.state)
          : null;
      const spUnit =
        sp !== null && g.setpoint && g.setpoint.attributes
          ? g.setpoint.attributes.unit_of_measurement || g.controlChannel.unit
          : null;
      const drivePctVal =
        g.drivePct && g.drivePct.state !== "unavailable"
          ? Number(g.drivePct.state)
          : null;
      // Pit gets a stamped copy with setpoint + drive bundled in so
      // _renderChannel can inline them on the tile.
      const pit = {
        ...g.controlChannel,
        _setpoint: sp,
        _setpointUnit: spUnit,
        _drivePct: Number.isFinite(drivePctVal) ? drivePctVal : null,
      };
      probeChannels = [pit, ...liveChannels];
    } else {
      probeChannels = liveChannels;
    }
    let channels;
    if (probeChannels.length > 0) {
      channels = `<div class="grid">${probeChannels
        .map((c) => this._renderChannel(c))
        .join("")}</div>`;
    } else if (pitHasValue) {
      // The pit/control channel is being shown in the drive panel above;
      // no need for a "No probes connected" placeholder.
      channels = "";
    } else {
      channels = `<div class="empty">No probes connected</div>`;
    }

    const meta = [];
    if (g.cookSession && g.cookSession.state !== "unavailable") {
      meta.push(`
        <span class="cook" data-entity="${g.cookSession.entity_id}" title="Active cook">
          <ha-icon icon="mdi:fire"></ha-icon>
          ${this._escape(g.cookSession.state)}
        </span>`);
    }
    if (g.cookStarted && g.cookStarted.state !== "unavailable") {
      meta.push(`
        <span class="cook" data-entity="${g.cookStarted.entity_id}" title="Cook elapsed">
          <ha-icon icon="mdi:timer-outline"></ha-icon>
          ${this._formatRelative(g.cookStarted.state, "elapsed")}
        </span>`);
    }
    if (g.battery && g.battery.state !== "unavailable") {
      const pct = Number(g.battery.state);
      const cls =
        Number.isFinite(pct) && pct < 20 ? "battery low" : "battery";
      meta.push(`
        <span class="${cls}" data-entity="${g.battery.entity_id}" title="Battery">
          <ha-icon icon="${this._batteryIcon(pct)}"></ha-icon>
          ${Number.isFinite(pct) ? pct.toFixed(0) + "%" : g.battery.state}
        </span>`);
    }
    if (g.lastSeen && g.lastSeen.state !== "unavailable") {
      meta.push(`
        <span class="seen" data-entity="${g.lastSeen.entity_id}" title="Last seen">
          <ha-icon icon="mdi:clock-outline"></ha-icon>
          ${this._formatRelative(g.lastSeen.state)}
        </span>`);
    }

    let drive = "";
    if (
      (g.setpoint && g.setpoint.state !== "unavailable") ||
      (g.drivePct && g.drivePct.state !== "unavailable") ||
      pitHasValue
    ) {
      const setVal =
        g.setpoint && g.setpoint.state !== "unavailable"
          ? `${Number(g.setpoint.state).toFixed(0)}<span class="unit">${this._escape(
              g.setpoint.attributes.unit_of_measurement || ""
            )}</span>`
          : "—";
      const drivePctVal = g.drivePct && g.drivePct.state !== "unavailable" ? Number(g.drivePct.state) : null;
      const driveDisplay = drivePctVal === null ? "—" : `${drivePctVal.toFixed(0)}%`;

      // Optional pit/ambient column when we have a tied control channel
      // AND it's currently reporting — otherwise we'd render a useless
      // "—" tile that the user just asked us to hide.
      let pitCol = "";
      if (pitHasValue) {
        const c = g.controlChannel;
        const pitColor = c.alertOn
          ? "var(--error-color, #db4437)"
          : this._tempColor(c.value, c.isCelsius);
        const pitDisplay =
          c.value === null
            ? "—"
            : `${c.value.toFixed(c.value >= 100 ? 0 : 1)}<span class="unit">${this._escape(
                c.unit
              )}</span>`;
        const delta =
          c.value !== null && g.setpoint && g.setpoint.state !== "unavailable"
            ? c.value - Number(g.setpoint.state)
            : null;
        const deltaStr =
          delta === null
            ? ""
            : `<span class="pit-delta ${delta > 0 ? "over" : delta < 0 ? "under" : "ok"}">${
                delta > 0 ? "+" : ""
              }${delta.toFixed(delta >= 10 || delta <= -10 ? 0 : 1)}°</span>`;
        pitCol = `
          <div class="drive-card pit" data-entity="${c.entity_id}" style="--pit-color:${pitColor}">
            <div class="drive-label">Pit</div>
            <div class="drive-value pit-value">${pitDisplay}</div>
            ${deltaStr}
          </div>`;
      }

      drive = `
        <div class="drive ${pitHasValue ? "with-pit" : ""}">
          ${pitCol}
          <div class="drive-card" data-entity="${
            g.setpoint ? g.setpoint.entity_id : ""
          }">
            <div class="drive-label">Setpoint</div>
            <div class="drive-value">${setVal}</div>
          </div>
          <div class="drive-card" data-entity="${
            g.drivePct ? g.drivePct.entity_id : ""
          }">
            <div class="drive-label">Drive</div>
            <div class="drive-value drive-fan">
              <ha-icon icon="mdi:fan" class="${
                drivePctVal && drivePctVal > 0 ? "spin" : ""
              }"></ha-icon>
              ${driveDisplay}
            </div>
            <div class="drive-bar"><div class="drive-bar-fill" style="width:${
              drivePctVal === null ? 0 : Math.max(0, Math.min(100, drivePctVal))
            }%"></div></div>
          </div>
        </div>`;
    }

    return `
      <div class="device">
        <div class="header">
          <div class="title">${this._escape(g.name)}</div>
          <div class="meta">${meta.join("")}</div>
        </div>
        ${drive}
        ${channels}
        ${this._renderChart(g)}
      </div>`;
  }

  _renderChart(g) {
    if (this._historyHours <= 0 || this._compact) return "";
    const series = [];
    // Always include the control channel first if present, so its line
    // sits at the bottom of the legend and is visually paired with the
    // setpoint dashed line.
    const allChannels = g.controlChannel
      ? [g.controlChannel, ...g.channels]
      : g.channels;
    for (const c of allChannels) {
      const points = this._history.get(c.entity_id);
      if (!points || points.length < 2) continue;
      const color = c.colorHex
        ? `#${c.colorHex.replace(/^#/, "")}`
        : this._tempColor(c.value, c.isCelsius);
      series.push({ label: c.label, color, points, dashed: false });
    }
    let setpointSeries = null;
    if (g.setpoint && g.setpoint.state !== "unavailable") {
      const points = this._history.get(g.setpoint.entity_id);
      if (points && points.length >= 2) {
        setpointSeries = {
          label: "Setpoint",
          color: "var(--accent-color, #ff9800)",
          points,
          dashed: true,
        };
        series.push(setpointSeries);
      }
    }
    if (!series.length) {
      // Show a placeholder so users know the chart slot exists but is waiting.
      return `<div class="chart-empty">Collecting trend data…</div>`;
    }

    const W = 100; // viewBox units; SVG scales to container width
    const H = 36;
    const padL = 8;
    const padR = 2;
    const padT = 2;
    const padB = 6;

    const now = Date.now();
    const tMin = now - this._historyHours * 3600 * 1000;
    const tMax = now;
    let vMin = Infinity;
    let vMax = -Infinity;
    for (const s of series) {
      for (const p of s.points) {
        if (p.t < tMin) continue;
        if (p.v < vMin) vMin = p.v;
        if (p.v > vMax) vMax = p.v;
      }
    }
    if (!Number.isFinite(vMin) || !Number.isFinite(vMax)) return "";
    if (vMin === vMax) {
      vMin -= 1;
      vMax += 1;
    } else {
      const span = vMax - vMin;
      vMin -= span * 0.1;
      vMax += span * 0.1;
    }

    const x = (t) =>
      padL + ((t - tMin) / (tMax - tMin)) * (W - padL - padR);
    const y = (v) =>
      padT + (1 - (v - vMin) / (vMax - vMin)) * (H - padT - padB);

    const paths = series
      .map((s) => {
        const pts = s.points.filter((p) => p.t >= tMin && p.t <= tMax);
        if (pts.length < 2) return "";
        const d = pts
          .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(2)},${y(p.v).toFixed(2)}`)
          .join(" ");
        const dash = s.dashed ? `stroke-dasharray="1.4,1.4"` : "";
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="0.9" stroke-linejoin="round" stroke-linecap="round" ${dash}></path>`;
      })
      .join("");

    // y-axis min/max labels and a faint mid-line
    const mid = (vMin + vMax) / 2;
    const yMid = y(mid);
    const unit = (g.channels[0] && g.channels[0].unit) || "";
    const fmt = (v) => `${v.toFixed(v >= 100 ? 0 : 1)}${this._escape(unit)}`;

    const legend = series
      .filter((s) => s !== setpointSeries)
      .map(
        (s) =>
          `<span class="leg"><i style="background:${s.color}"></i>${this._escape(
            s.label
          )}</span>`
      )
      .join("");

    return `
      <div class="chart">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart-svg">
          <line x1="${padL}" y1="${y(vMax)}" x2="${W - padR}" y2="${y(vMax)}" class="grid"/>
          <line x1="${padL}" y1="${yMid}"     x2="${W - padR}" y2="${yMid}"     class="grid"/>
          <line x1="${padL}" y1="${y(vMin)}" x2="${W - padR}" y2="${y(vMin)}" class="grid"/>
          ${paths}
        </svg>
        <div class="chart-axis">
          <span>${this._escape(`${this._historyHours}h ago`)}</span>
          <span class="ymax">${fmt(vMax)}</span>
          <span class="ymin">${fmt(vMin)}</span>
          <span>now</span>
        </div>
        ${legend ? `<div class="chart-legend">${legend}</div>` : ""}
      </div>`;
  }

  _batteryIcon(pct) {
    if (!Number.isFinite(pct)) return "mdi:battery-unknown";
    if (pct >= 90) return "mdi:battery";
    if (pct >= 70) return "mdi:battery-70";
    if (pct >= 50) return "mdi:battery-50";
    if (pct >= 30) return "mdi:battery-30";
    if (pct >= 10) return "mdi:battery-10";
    return "mdi:battery-alert";
  }

  _renderChannel(c) {
    const color = c.alertOn ? "var(--error-color, #db4437)" : this._tempColor(c.value, c.isCelsius);
    const display =
      c.value === null
        ? "—"
        : `${c.value.toFixed(c.value >= 100 ? 0 : 1)}<span class="unit">${this._escape(
            c.unit
          )}</span>`;
    const alertBadge = c.alertOn
      ? `<div class="alert-badge" title="Alert active"><ha-icon icon="mdi:alert"></ha-icon></div>`
      : "";

    // When the pit tile is rendered in compact mode it carries setpoint +
    // drive info to display alongside the current temp.
    let pitExtras = "";
    if (c._setpoint !== undefined && c._setpoint !== null) {
      const delta =
        c.value !== null ? c.value - c._setpoint : null;
      const deltaCls =
        delta === null
          ? ""
          : delta > 0
          ? "over"
          : delta < 0
          ? "under"
          : "ok";
      const deltaStr =
        delta === null
          ? ""
          : ` <span class="probe-delta ${deltaCls}">${
              delta > 0 ? "+" : ""
            }${delta.toFixed(delta >= 10 || delta <= -10 ? 0 : 1)}°</span>`;
      const drivePart =
        Number.isFinite(c._drivePct)
          ? `<span class="probe-drive"><ha-icon icon="mdi:fan" class="${
              c._drivePct > 0 ? "spin" : ""
            }"></ha-icon>${c._drivePct.toFixed(0)}%</span>`
          : "";
      pitExtras = `
        <div class="probe-meta">
          <span class="probe-setpoint">→ ${c._setpoint.toFixed(0)}<span class="unit">${this._escape(
            c._setpointUnit || c.unit || ""
          )}</span>${deltaStr}</span>
          ${drivePart}
        </div>`;
    }

    return `
      <div class="probe ${c.alertOn ? "alerting" : ""}${
      c._setpoint !== undefined ? " probe-pit" : ""
    }" data-entity="${c.entity_id}" style="--probe-color:${color}">
        ${alertBadge}
        <div class="probe-label">${this._escape(c.label)}</div>
        <div class="probe-temp">${display}</div>
        ${pitExtras}
        <div class="probe-bar"></div>
      </div>`;
  }

  _escape(s) {
    return String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[c])
    );
  }

  _styles() {
    return `<style>
      ha-card { display: block; padding: 16px; position: relative; }
      .card-toggle { position: absolute; top: 10px; right: 10px; width: 28px; height: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border: none; background: transparent; color: var(--secondary-text-color); border-radius: 50%; cursor: pointer; transition: background 0.12s ease, color 0.12s ease; z-index: 1; }
      .card-toggle:hover { background: var(--secondary-background-color, rgba(0,0,0,0.06)); color: var(--primary-text-color); }
      .card-toggle ha-icon { --mdc-icon-size: 18px; }
      ha-card.compact .device + .device { margin-top: 12px; padding-top: 10px; }
      ha-card.compact .header { margin-bottom: 8px; }
      ha-card.compact .grid { gap: 8px; }
      ha-card.compact .probe { padding: 8px 10px 10px; }
      ha-card.compact .probe-temp { font-size: 1.4rem; }
      ha-card.compact .drive { display: none; }
      .device + .device { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--divider-color); }
      .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; gap: 12px; flex-wrap: wrap; padding-right: 36px; }
      .title { font-size: 1.15rem; font-weight: 600; color: var(--primary-text-color); }
      .meta { display: flex; gap: 10px; align-items: center; font-size: 0.85rem; color: var(--secondary-text-color); flex-wrap: wrap; }
      .meta span { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
      .meta ha-icon { --mdc-icon-size: 16px; }
      .cook { color: var(--accent-color, #ff9800); font-weight: 500; }
      .battery.low { color: var(--error-color, #db4437); }

      .drive { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
      .drive.with-pit { grid-template-columns: 1.2fr 1fr 1fr; }
      .drive-card { position: relative; background: var(--secondary-background-color, rgba(0,0,0,0.04)); border-radius: 10px; padding: 10px 12px; cursor: pointer; }
      .drive-card.pit { background: color-mix(in srgb, var(--pit-color, var(--accent-color)) 12%, var(--secondary-background-color, rgba(0,0,0,0.04))); border: 1px solid color-mix(in srgb, var(--pit-color, var(--accent-color)) 30%, transparent); }
      .drive-card.pit .drive-value.pit-value { color: var(--pit-color, var(--primary-text-color)); }
      .pit-delta { position: absolute; top: 10px; right: 12px; font-size: 0.78rem; font-weight: 500; color: var(--secondary-text-color); }
      .pit-delta.over { color: var(--error-color, #db4437); }
      .pit-delta.under { color: var(--info-color, #4285f4); }
      .pit-delta.ok { color: var(--success-color, #43a047); }
      .drive-label { font-size: 0.72rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: 0.05em; }
      .drive-value { font-size: 1.4rem; font-weight: 600; color: var(--primary-text-color); margin-top: 2px; display: flex; align-items: center; gap: 6px; }
      .drive-value .unit { font-size: 0.85rem; font-weight: 400; color: var(--secondary-text-color); margin-left: 2px; }
      .drive-value ha-icon { --mdc-icon-size: 18px; color: var(--accent-color, #ff9800); }
      .drive-value ha-icon.spin { animation: fb-spin 2s linear infinite; }
      .drive-bar { height: 4px; background: var(--divider-color); border-radius: 2px; margin-top: 8px; overflow: hidden; }
      .drive-bar-fill { height: 100%; background: var(--accent-color, #ff9800); transition: width 0.3s ease; }
      @keyframes fb-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
      .probe { position: relative; padding: 12px 14px 14px; border-radius: 12px; background: var(--ha-card-background, var(--card-background-color)); border: 1px solid var(--divider-color); cursor: pointer; transition: transform 0.08s ease, box-shadow 0.08s ease; overflow: hidden; }
      .probe:hover { transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
      .probe.alerting { border-color: var(--error-color, #db4437); background: color-mix(in srgb, var(--error-color, #db4437) 8%, transparent); }
      .alert-badge { position: absolute; top: 6px; right: 6px; color: var(--error-color, #db4437); }
      .alert-badge ha-icon { --mdc-icon-size: 18px; }
      .probe-label { font-size: 0.78rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .probe-temp { font-size: 1.85rem; font-weight: 600; color: var(--probe-color); margin-top: 4px; line-height: 1.1; }
      .probe-temp .unit { font-size: 0.9rem; font-weight: 400; margin-left: 2px; color: var(--secondary-text-color); }
      .probe-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--probe-color); opacity: 0.85; }
      .empty { color: var(--secondary-text-color); font-style: italic; padding: 8px 0; text-align: center; }

      .update-banner { display: flex; align-items: center; gap: 8px; padding: 8px 10px; margin-bottom: 12px; border-radius: 8px; font-size: 0.85rem; line-height: 1.3; }
      .update-banner.rate { background: color-mix(in srgb, var(--warning-color, #ff9800) 15%, transparent); color: var(--primary-text-color); border: 1px solid color-mix(in srgb, var(--warning-color, #ff9800) 35%, transparent); }
      .update-banner.error { background: color-mix(in srgb, var(--error-color, #db4437) 15%, transparent); color: var(--primary-text-color); border: 1px solid color-mix(in srgb, var(--error-color, #db4437) 35%, transparent); }
      .update-banner ha-icon { --mdc-icon-size: 18px; flex-shrink: 0; }
      .update-banner.rate ha-icon { color: var(--warning-color, #ff9800); }
      .update-banner.error ha-icon { color: var(--error-color, #db4437); }
      .update-banner-text { flex: 1; }

      .probe-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px; font-size: 0.78rem; color: var(--secondary-text-color); flex-wrap: wrap; }
      .probe-setpoint { display: inline-flex; align-items: baseline; gap: 4px; }
      .probe-setpoint .unit { font-size: 0.7rem; }
      .probe-delta { font-weight: 500; }
      .probe-delta.over { color: var(--error-color, #db4437); }
      .probe-delta.under { color: var(--info-color, #4285f4); }
      .probe-delta.ok { color: var(--success-color, #43a047); }
      .probe-drive { display: inline-flex; align-items: center; gap: 3px; }
      .probe-drive ha-icon { --mdc-icon-size: 13px; color: var(--accent-color, #ff9800); }
      .probe-drive ha-icon.spin { animation: fb-spin 2s linear infinite; }
      ha-card.compact .probe.probe-pit { background: color-mix(in srgb, var(--probe-color) 10%, var(--ha-card-background, var(--card-background-color))); border-color: color-mix(in srgb, var(--probe-color) 30%, var(--divider-color)); }
      .empty-diag { color: var(--secondary-text-color); padding: 4px 2px; }
      .empty-diag .empty-msg { font-style: italic; margin-bottom: 8px; }
      .empty-diag details { font-size: 0.85rem; }
      .empty-diag summary { cursor: pointer; outline: none; user-select: none; }
      .empty-diag ul { margin: 6px 0 0; padding-left: 18px; }
      .empty-diag li { margin: 2px 0; }

      .chart { margin-top: 14px; position: relative; }
      .chart-empty { margin-top: 14px; padding: 10px; text-align: center; color: var(--secondary-text-color); font-style: italic; font-size: 0.8rem; border: 1px dashed var(--divider-color); border-radius: 8px; }
      .chart-svg { width: 100%; height: 110px; display: block; }
      .chart-svg .grid { stroke: var(--divider-color); stroke-width: 0.2; }
      .chart-axis { position: relative; height: 1.1rem; font-size: 0.7rem; color: var(--secondary-text-color); margin-top: 2px; }
      .chart-axis > span:nth-child(1) { position: absolute; left: 0; }
      .chart-axis > span:nth-child(4) { position: absolute; right: 0; }
      .chart-axis .ymax { position: absolute; left: 50%; transform: translateX(-50%); top: -110px; }
      .chart-axis .ymin { position: absolute; left: 50%; transform: translateX(-50%); top: -14px; }
      .chart-legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; font-size: 0.78rem; color: var(--secondary-text-color); }
      .chart-legend .leg { display: inline-flex; align-items: center; gap: 4px; }
      .chart-legend .leg i { display: inline-block; width: 10px; height: 3px; border-radius: 1px; }
    </style>`;
  }
}

if (!customElements.get("fireboard-card")) {
  customElements.define("fireboard-card", FireboardCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "fireboard-card")) {
  window.customCards.push({
    type: "fireboard-card",
    name: "Fireboard",
    description: "Live temperatures, setpoint, drive output, and cook timer from your Fireboard devices",
    preview: false,
  });
}
