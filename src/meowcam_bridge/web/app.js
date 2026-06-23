/* MeowCam Bridge — embedded web UI JavaScript (v0.2) */

(function () {
  const MAX_ROUTES = 8;
  const MAX_PRESETS = 16;

  const INPUT_PROFILES = [
    { value: 'ptzoptics_pt_joy_g4_visca_udp', label: 'PTZOptics PT-JOY-G4 — VISCA(UDP), custom ports' },
    { value: 'ptzoptics_pt_joy_g4_sony_visca_udp', label: 'PTZOptics PT-JOY-G4 — Sony VISCA(UDP), fixed 52381' },
  ];
  const OUTPUT_PROFILES = [
    { value: 'sony_brc_h900_brbk_ip10', label: 'Sony BRC-H900 + BRBK-IP10' },
  ];
  const VIDEO_SOURCE_TYPES = [
    { value: 'none', label: 'None (no preview)' },
    { value: 'ndi', label: 'NDI / NDI|HX source' },
    { value: 'usb', label: 'USB / HDMI capture card' },
    { value: 'testpattern', label: 'Test pattern (no hardware)' },
  ];
  const RESOLUTIONS = ['320x180', '480x270', '640x360', '960x540', '1280x720'];

  const DEFAULT_VIDEO = {
    enabled: false,
    source_type: 'none',
    ndi_source_name: '',
    usb_device_index: 0,
    resolution: '640x360',
    frame_rate: 8,
    jpeg_quality: 60,
    crop_x: 0,
    crop_y: 0,
    crop_w: 0,
    crop_h: 0,
  };
  const DEFAULT_ROUTE = {
    enabled: false,
    label: 'Camera',
    incoming_port: 52382,
    input_profile: 'ptzoptics_pt_joy_g4_visca_udp',
    output_profile: 'sony_brc_h900_brbk_ip10',
    camera_ip: '192.168.51.123',
    camera_port: 52381,
    status: 'unknown',
    movement_speed: 'medium',
    preset_labels: Array.from({ length: MAX_PRESETS }, (_, i) => `Preset ${i + 1}`),
    video: { ...DEFAULT_VIDEO },
  };

  const SPEED_PRESETS = {
    slow: { label: 'Slow', pan_speed: 3, tilt_speed: 3, zoom_speed: 2 },
    medium: { label: 'Medium', pan_speed: 9, tilt_speed: 8, zoom_speed: 4 },
    fast: { label: 'Fast', pan_speed: 18, tilt_speed: 17, zoom_speed: 7 },
  };

  const PAGE_DESCRIPTIONS = {
    preview: '2×2 live camera grid — click any feed to enlarge.',
    presets: 'Four cameras at a time, big touch-friendly preset controls with speed.',
    manual: 'Single live pane for the selected camera + PTZ / lens / OSD controls.',
    diagnostics: 'Live controller → bridge → camera packet trace for site checks.',
    settings: 'Per-camera Control + Video setup, plus ATEM configuration.',
  };

  const state = {
    config: null,
    routes: [],
    presetPage: 0,
    previewPage: 0,
    presetEditMode: false,
    manualSaveMode: false,
    osdActive: false,
    editing: false,
    booted: false,
    lastPresets: JSON.parse(localStorage.getItem('meowcam:lastPresets') || '{}'),
    // ATEM tally
    atem: null,            // {enabled, atem_ip, supersource_aux_output, input_mapping}
    atemConnected: false,
    tally: null,           // {pgm, pvw}
    presetSelectedCam: undefined,  // which camera is selected on the presets page
    presetRange: 0,  // 0 = presets 1-8, 1 = presets 9-16
  };

  // Per-route busy tracking (client-side mirror of server-side busy state)
  const routeBusy = {};  // {routeIndex: true/false}

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  /* ===================================================================
     Helpers
     =================================================================== */

  function cloneDefaultRoute(index) {
    return {
      ...DEFAULT_ROUTE,
      label: `Camera ${index + 1}`,
      incoming_port: 52382 + index,
      camera_ip: `192.168.51.${123 + index}`,
      preset_labels: [...DEFAULT_ROUTE.preset_labels],
      video: { ...DEFAULT_VIDEO },
    };
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    }[ch]));
  }

  function safeText(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function showStatus(message, ok = true) {
    const badge = $('#status-indicator');
    if (!badge) return;
    badge.textContent = message;
    badge.classList.toggle('ok', ok);
    badge.classList.toggle('err', !ok);
  }

  function setOsdActive(active) {
    state.osdActive = Boolean(active);
    const banner = $('#osd-warning');
    if (banner) banner.hidden = !state.osdActive;
    const pill = $('#osd-pill');
    if (pill) {
      pill.textContent = state.osdActive ? 'OSD active — PTZ may navigate menus' : 'OSD closed';
      pill.classList.toggle('active', state.osdActive);
    }
  }

  function speedModeFor(routeIndex) {
    return state.routes[routeIndex]?.movement_speed || 'medium';
  }

  function speedArgsFor(routeIndex) {
    return SPEED_PRESETS[speedModeFor(routeIndex)] || SPEED_PRESETS.medium;
  }

  function persistPresetState() {
    localStorage.setItem('meowcam:lastPresets', JSON.stringify(state.lastPresets));
  }

  function optionsHtml(options, selected) {
    return options.map((p) => `<option value="${escapeHtml(p.value)}" ${p.value === selected ? 'selected' : ''}>${escapeHtml(p.label)}</option>`).join('');
  }

  function resolutionList() {
    return RESOLUTIONS.map((r) => ({ value: r, label: r }));
  }

  /* View options derived from a route's video config */
  function viewOpts(route) {
    const v = route?.video || DEFAULT_VIDEO;
    const width = parseInt(String(v.resolution).split('x')[0], 10) || 480;
    return { fps: v.frame_rate || 8, quality: v.jpeg_quality || 60, width };
  }

  async function request(path, options = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const contentType = res.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : safeText(data);
      throw new Error(`${res.status} ${res.statusText}: ${detail}`);
    }
    return data;
  }

  function normaliseRoutes(routes) {
    const padded = [];
    for (let i = 0; i < MAX_ROUTES; i += 1) {
      const existing = routes.find((r) => r.index === i) || routes[i];
      const route = { ...cloneDefaultRoute(i), ...(existing || {}), index: i };
      route.preset_labels = (route.preset_labels || []).concat(DEFAULT_ROUTE.preset_labels).slice(0, MAX_PRESETS);
      route.video = { ...DEFAULT_VIDEO, ...(route.video || {}) };
      padded.push(route);
    }
    return padded;
  }

  function updateManualSpeedControls(mode) {
    const preset = SPEED_PRESETS[mode] || SPEED_PRESETS.medium;
    const pan = $('#pan-speed');
    const tilt = $('#tilt-speed');
    const zoom = $('#zoom-speed');
    if (pan) pan.value = preset.pan_speed;
    if (tilt) tilt.value = preset.tilt_speed;
    if (zoom) zoom.value = preset.zoom_speed;
    $$('.speed-mode-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.speed === mode));
  }

  async function setCameraSpeed(routeIndex, mode, persist = true) {
    const route = state.routes[routeIndex];
    if (!route || !SPEED_PRESETS[mode]) return;
    route.movement_speed = mode;
    updateManualSpeedControls(mode);
    renderPresets();
    renderPreview();
    if (persist) {
      const payload = { ...route, movement_speed: mode };
      delete payload.index;
      await request(`/api/routes/${routeIndex}`, { method: 'PUT', body: JSON.stringify(payload) });
      showStatus(`${route.label} speed saved as ${SPEED_PRESETS[mode].label}`, true);
    } else {
      showStatus(`${route.label} speed set to ${SPEED_PRESETS[mode].label}`, true);
    }
  }

  /* ===================================================================
     MJPEG <img> video view + watchdog
     (ported from docs/v0.2-preview-design/video-client.js, MJPEG only)
     =================================================================== */

  class MjpegImgView {
    constructor(pane, camIndex, opts = {}) {
      this.pane = pane;
      this.cam = camIndex;
      this.opts = { fps: 8, quality: 60, width: 480, ...opts };
      this.stalled = false;
      this._watchdog = null;
      this._pollTimer = null;
      this._reconnect = null;
      this._lastFrame = performance.now();
      this._frames = 0;
      this._fpsT0 = performance.now();
      this._img = null;
    }

    start() {
      this.pane.classList.remove('off');
      let img = this.pane.querySelector('img.mjpeg');
      if (!img) {
        const canvas = this.pane.querySelector('canvas');
        if (canvas) canvas.remove();
        img = document.createElement('img');
        img.className = 'mjpeg';
        img.alt = `Camera ${this.cam + 1} preview`;
        img.decoding = 'async';
        this.pane.prepend(img);
      }
      this._img = img;
      img.addEventListener('load', () => this._onFrame());
      img.addEventListener('error', () => this._scheduleReconnect());
      this._loadSrc();
      this._startWatchdog(3000);
      // Frame-presence poll: <img> never fires onload on multipart replace
      // parts in Chromium, so we confirm pixels are flowing via naturalWidth.
      this._pollTimer = setInterval(() => {
        if (this._img && this._img.naturalWidth > 1) this._onFrame();
      }, 700);
    }

    _loadSrc() {
      const { fps, quality, width } = this.opts;
      const bust = Date.now();
      this._img.src =
        `/api/video/feed/${this.cam}?fps=${fps}&quality=${quality}&width=${width}&t=${bust}`;
    }

    _onFrame() {
      this._lastFrame = performance.now();
      this._frames++;
      const now = performance.now();
      if (now - this._fpsT0 >= 1000) {
        const fpsEl = this.pane.querySelector('.vid-fps');
        if (fpsEl) fpsEl.textContent = `${this._frames} fps`;
        this._frames = 0;
        this._fpsT0 = now;
      }
      if (this.stalled) {
        this.stalled = false;
        this.pane.classList.remove('stalled');
      }
    }

    _startWatchdog(timeoutMs = 3000) {
      this._stopWatchdog();
      this._watchdog = setInterval(() => {
        const idle = performance.now() - this._lastFrame;
        if (idle > timeoutMs && !this.stalled) {
          this.stalled = true;
          this.pane.classList.add('stalled');
          this._scheduleReconnect();
        }
      }, 1000);
    }

    _stopWatchdog() {
      if (this._watchdog) { clearInterval(this._watchdog); this._watchdog = null; }
    }

    _scheduleReconnect() {
      clearTimeout(this._reconnect);
      this._reconnect = setTimeout(() => {
        if (!this._img) return;
        this._loadSrc();
      }, 1200);
    }

    stop() {
      this._stopWatchdog();
      clearTimeout(this._reconnect);
      clearInterval(this._pollTimer);
      if (this._img) {
        // Force the browser to abort the underlying multipart connection.
        this._img.src = '';
        this._img.removeAttribute('src');
      }
      this.pane.classList.remove('stalled');
    }
  }

  /* ===================================================================
     Config / routes loading
     =================================================================== */

  async function loadNetworkInterfaces() {
    try {
      const ifaces = await request('/api/network-interfaces');
      const sel = $('#bridge-ip-select');
      if (!sel) return;
      const prior = sel.value;
      sel.innerHTML = '<option value="0.0.0.0">0.0.0.0 (all interfaces)</option>';
      (ifaces || []).forEach((iface) => {
        const opt = document.createElement('option');
        opt.value = iface.ip;
        opt.textContent = `${iface.ip} (${iface.name})`;
        sel.appendChild(opt);
      });
      if (prior) sel.value = prior;
    } catch (_) { /* non-fatal */ }
  }

  async function loadConfig() {
    state.config = await request('/api/config');
    if (state.config.error) throw new Error(state.config.error);
    state.atem = state.config.atem || null;
    const currentIP = state.config.bridge_ip || '0.0.0.0';
    const bridgeSel = $('#bridge-ip-select');
    if (bridgeSel) {
      if (![...bridgeSel.options].some((o) => o.value === currentIP)) {
        const opt = document.createElement('option');
        opt.value = currentIP;
        opt.textContent = currentIP;
        bridgeSel.appendChild(opt);
      }
      bridgeSel.value = currentIP;
    }
    const manual = $('#bridge-ip-manual');
    if (manual) manual.value = '';
    updateAtemPill();
    await loadRoutes();
  }

  async function loadRoutes() {
    if (state.editing) return;
    const routes = await request('/api/routes');
    state.routes = normaliseRoutes(routes);
    renderSettingsRoutes();
    renderAtemConfig();
    renderCameraSelects();
    renderPreview();
    renderPresets();
    const selectedRoute = Number($('#manual-camera-select')?.value || 0);
    updateManualSpeedControls(speedModeFor(selectedRoute));
    if ($('#manual')?.classList.contains('active')) syncManualVideo();
    state.booted = true;
    showStatus('Ready', true);
  }

  /* ===================================================================
     Preset button builder (shared by Preview + Presets tabs)
     =================================================================== */

  function makePresetButton(route, i, speedMode) {
    const presetNumber = i + 1;
    const btn = document.createElement('button');
    const isLast = Number(state.lastPresets[String(route.index)]) === presetNumber;

    const presetSpeed = route.preset_speeds?.[i] || '';
    const speedIndicator = presetSpeed ? { slow: '›', medium: '››', fast: '›››' }[presetSpeed] || '' : '';
    const speedClass = presetSpeed ? ` speed-${presetSpeed}` : '';
    const thumbUrl = route.preset_thumbs?.[i] || '';
    btn.className = `${state.presetEditMode ? 'preset-btn editing' : 'preset-btn'}${speedClass} ${isLast ? 'last-used' : ''}`;
    
    if (thumbUrl) {
        btn.classList.add('has-thumb');
        btn.style.setProperty('--thumb-url', `url(${thumbUrl})`);
    }
    
    btn.innerHTML = `<strong>${presetNumber}</strong><span>${escapeHtml(route.preset_labels?.[i] || `Preset ${presetNumber}`)}</span>${speedIndicator ? `<em class="speed-mark">${speedIndicator}</em>` : ''}`;
    btn.title = state.presetEditMode
      ? 'Click to rename this preset'
      : `Recall preset at ${SPEED_PRESETS[speedMode]?.label || 'Medium'} speed`;
    btn.disabled = !route.enabled && !state.presetEditMode;
    btn.addEventListener('click', () => {
      if (state.presetEditMode) {
        renamePreset(route.index, i).catch((err) => alert(`Rename failed: ${err.message}`));
      } else {
        state.lastPresets[String(route.index)] = presetNumber;
        persistPresetState();
        // Update "last used" highlight without destroying/recreating video panes
        const allBtns = btn.closest('.preset-buttons, #preset-buttons-bar, .manual-preset-buttons');
        if (allBtns) allBtns.querySelectorAll('.preset-btn').forEach((b) => b.classList.remove('last-used'));
        btn.classList.add('last-used');
        sendCommand(route.index, 'preset_recall', { preset: presetNumber, ...speedArgsFor(route.index) });
      }
    });
    return btn;
  }

async function savePresetLabels(routeIndex, labels) {
    const route = state.routes[routeIndex];
    if (!route) return;
    const payload = { ...route, preset_labels: labels };
    delete payload.index;
    await request(`/api/routes/${routeIndex}`, { method: 'PUT', body: JSON.stringify(payload) });
    state.routes[routeIndex].preset_labels = labels;
    showStatus(`Saved preset names for ${route.label}`, true);
  }

  async function renamePreset(routeIndex, presetIndex) {
    const route = state.routes[routeIndex];
    if (!route) return;
    const labels = [...(route.preset_labels || DEFAULT_ROUTE.preset_labels)].slice(0, MAX_PRESETS);
    const current = labels[presetIndex] || `Preset ${presetIndex + 1}`;
    const result = await showModal({
      title: `Rename ${route.label} Preset ${presetIndex + 1}`,
      fields: [{ name: 'label', label: 'Preset name', value: current }],
      confirmText: 'Save',
    });
    if (!result) return;
    labels[presetIndex] = (result.label || '').trim() || `Preset ${presetIndex + 1}`;
    await savePresetLabels(routeIndex, labels);
    renderPresets();
    renderPreview();
  }

  /* ===================================================================
     PREVIEW TAB — 2x2 grid, click-to-enlarge, per-column presets
     =================================================================== */

  function buildPreviewCell(camIndex) {
    const route = state.routes[camIndex];
    const cell = document.createElement('article');
    cell.className = `preview-cell preview-video-only ${route.enabled ? '' : 'disabled'}`;
    cell.dataset.cam = String(camIndex);
    cell.innerHTML = `
      <div class="video-pane off" data-cam="${camIndex}">
        <img class="mjpeg" alt="${escapeHtml(route.label)} preview">
        <div class="video-overlay">
          <div class="video-tag"><span class="video-live-dot"></span><span class="vid-label">${escapeHtml(route.label)}</span><span class="vid-badge"></span></div>
          <div class="video-meta"><span class="vid-fps">— fps</span></div>
        </div>
        <div class="video-stall">Reconnecting…<small>feed paused</small></div>
        <div class="video-off"><span class="cam-icon">📷</span><span class="vid-off-text">No video source</span></div>
      </div>
      <div class="preview-cell-footer">
        <span class="preview-cam-label">${escapeHtml(route.label)}</span>
        <span class="route-status ${escapeHtml(route.status || 'unknown')}">${route.enabled ? escapeHtml(route.status || 'unknown') : 'off'}</span>
      </div>
    `;
    cell.querySelector('.video-pane').addEventListener('click', () => toggleEnlarge(camIndex));
    return cell;
  }

  function toggleEnlarge(camIndex) {
    const grid = $('#preview-grid');
    if (!grid) return;
    const cell = grid.querySelector(`.preview-cell[data-cam="${camIndex}"]`);
    if (!cell) return;
    const nowEnlarged = !cell.classList.contains('enlarged');
    grid.querySelectorAll('.preview-cell.enlarged').forEach((c) => c.classList.remove('enlarged'));
    cell.classList.toggle('enlarged', nowEnlarged);
    grid.classList.toggle('has-enlarged', nowEnlarged);
  }

  function updatePreviewCells() {
    const grid = $('#preview-grid');
    if (!grid) return;
    grid.querySelectorAll('.preview-cell').forEach((cell) => {
      const ci = Number(cell.dataset.cam);
      const route = state.routes[ci];
      if (!route) return;
      cell.classList.toggle('disabled', !route.enabled);
      const lab = cell.querySelector('.preview-cam-label');
      if (lab) lab.textContent = route.label;
      const st = cell.querySelector('.route-status');
      if (st) {
        st.className = `route-status ${escapeHtml(route.status || 'unknown')}`;
        st.textContent = route.enabled ? (route.status || 'unknown') : 'off';
      }
      const vidLab = cell.querySelector('.vid-label');
      if (vidLab) vidLab.textContent = route.label;
    });
  }

  function syncPreviewFeeds() {
    const grid = $('#preview-grid');
    if (!grid) return;
    grid.querySelectorAll('.video-pane[data-cam]').forEach((pane) => {
      const ci = Number(pane.dataset.cam);
      const route = state.routes[ci];
      const wantVideo = !!(route && route.enabled && route.video && route.video.enabled && route.video.source_type !== 'none');
      if (wantVideo) {
        pane.classList.remove('off');
        if (!pane._mcView) {
          pane._mcView = new MjpegImgView(pane, ci, viewOpts(route));
          pane._mcView.start();
        }
      } else {
        if (pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
        pane.classList.add('off');
      }
    });
    applyTally();
  }

  function stopPreviewFeeds() {
    const grid = $('#preview-grid');
    if (!grid) return;
    grid.querySelectorAll('.video-pane[data-cam]').forEach((pane) => {
      if (pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
    });
  }

  function renderPreview() {
    const grid = $('#preview-grid');
    if (!grid) return;
    const start = state.previewPage * 4;
    const cams = [0, 1, 2, 3].map((k) => start + k);
    const existing = Array.from(grid.querySelectorAll('.preview-cell')).map((c) => Number(c.dataset.cam));
    const same = existing.length === cams.length && cams.every((c, i) => existing[i] === c);
    if (same) {
      updatePreviewCells();
      syncPreviewFeeds();
      return;
    }
    // full rebuild — tear down any feeds first
    stopPreviewFeeds();
    grid.innerHTML = '';
    grid.classList.remove('has-enlarged');
    cams.forEach((camIndex) => grid.appendChild(buildPreviewCell(camIndex)));
    syncPreviewFeeds();
    $$('.preview-page-btn').forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.page) === state.previewPage));
    const editBtn = $('#btn-edit-presets');
    if (editBtn) editBtn.textContent = state.presetEditMode ? 'Done editing preset names' : 'Edit preset names';
  }

  /* ===================================================================
     PRESETS TAB (no video — unchanged behaviour)
     =================================================================== */

  function renderPresets() {
    const grid = $('#preset-grid');
    if (!grid) return;

    const start = state.presetPage * 4;
    const presetStart = state.presetRange * 8;
    const presetEnd = presetStart + 8;

    // Full rebuild — tear down feeds first
    stopPresetFeeds();
    grid.innerHTML = '';
    grid.className = 'preset-grid';

    const routes = state.routes.slice(start, start + 4);
    routes.forEach((route) => {
      const card = document.createElement('article');
      card.className = `camera-preset-card ${route.enabled ? '' : 'disabled'}`;
      card.innerHTML = `
        <div class="camera-card-head">
          <h3>${escapeHtml(route.label)}</h3>
          <span class="route-status ${escapeHtml(route.status || 'unknown')}">${route.enabled ? escapeHtml(route.status || 'unknown') : 'off'}</span>
        </div>
        <div class="preset-video-pane video-pane off" data-cam="${route.index}">
          <img class="mjpeg" alt="${escapeHtml(route.label)} preview">
          <div class="video-overlay">
            <div class="video-tag"><span class="video-live-dot"></span><span class="vid-label">${escapeHtml(route.label)}</span></div>
          </div>
          <div class="video-off"><span class="cam-icon">📷</span></div>
        </div>
        <div class="preset-buttons"></div>
      `;
      // Add preset buttons (8 per range)
      const btnContainer = card.querySelector('.preset-buttons');
      for (let i = presetStart; i < presetEnd; i++) {
        btnContainer.appendChild(makePresetButton(route, i, speedModeFor(route.index)));
      }
      grid.appendChild(card);
    });

    // Start video feeds
    syncPresetFeeds();
    $$('.preset-page-btn').forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.page) === state.presetPage));
    const rangeBtn = $('#btn-preset-range');
    if (rangeBtn) {
      rangeBtn.textContent = state.presetRange === 0 ? 'Presets 1–8' : 'Presets 9–16';
    }
    const editBtn = $('#btn-edit-presets');
    if (editBtn) editBtn.textContent = state.presetEditMode ? 'Done editing preset names' : 'Edit preset names';
  }

function syncPresetFeeds() {
    const grid = $('#preset-grid');
    if (!grid) return;
    const isActive = $('#presets')?.classList.contains('active');
    grid.querySelectorAll('.preset-video-pane[data-cam]').forEach((pane) => {
      const ci = Number(pane.dataset.cam);
      const route = state.routes[ci];
      const wantVideo = isActive && !!(route && route.enabled && route.video && route.video.enabled && route.video.source_type !== 'none');
      if (wantVideo) {
        pane.classList.remove('off');
        if (!pane._mcView) {
          pane._mcView = new MjpegImgView(pane, ci, { ...viewOpts(route), width: 320 });
          pane._mcView.start();
        }
      } else {
        if (pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
        pane.classList.add('off');
      }
    });
  }

  function stopPresetFeeds() {
    const grid = $('#preset-grid');
    if (!grid) return;
    grid.querySelectorAll('.preset-video-pane[data-cam]').forEach((pane) => {
      if (pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
    });
  }

  /* ===================================================================
     MANUAL TAB — camera select + single video pane
     =================================================================== */

  function renderCameraSelects() {
    const enabled = state.routes.filter((r) => r.enabled);
    const list = enabled.length ? enabled : state.routes;
    const sel = $('#manual-camera-select');
    if (!sel) return;
    const prior = sel.value;
    sel.innerHTML = '';
    list.forEach((r) => {
      const opt = document.createElement('option');
      opt.value = r.index;
      opt.textContent = `${r.index + 1}: ${r.label} (${r.camera_ip})${r.enabled ? '' : ' (disabled)'}`;
      sel.appendChild(opt);
    });
    if (prior && list.some((r) => String(r.index) === String(prior))) sel.value = prior;
  }

  function syncManualVideo() {
    const pane = $('#manual-video');
    const sel = $('#manual-camera-select');
    if (!pane || !sel) return;
    const ci = Number(sel.value || 0);
    const route = state.routes[ci] || cloneDefaultRoute(ci);
    pane.dataset.cam = String(ci);
    const label = pane.querySelector('.vid-label');
    if (label) label.textContent = route.label;
    const wantVideo = !!(route.enabled && route.video && route.video.enabled && route.video.source_type !== 'none');
    if (wantVideo) {
      pane.classList.remove('off');
      if (!pane._mcView || pane._mcView.cam !== ci) {
        if (pane._mcView) pane._mcView.stop();
        pane._mcView = new MjpegImgView(pane, ci, viewOpts(route));
        pane._mcView.start();
      }
    } else {
      if (pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
      pane.classList.add('off');
    }
    applyTally();
  }

  function stopManualFeed() {
    const pane = $('#manual-video');
    if (pane && pane._mcView) { pane._mcView.stop(); pane._mcView = null; }
  }

  /* ===================================================================
     MANUAL PRESET GRID — preset buttons for the selected camera
     =================================================================== */
  function renderManualPresets() {
    const sel = $('#manual-camera-select');
    if (!sel) return;
    const routeIndex = Number(sel.value || 0);
    const route = state.routes[routeIndex] || cloneDefaultRoute(routeIndex);
    const container = $('#manual-preset-grid');
    if (!container) return;
    container.innerHTML = '';

    // Header row: Save Mode toggle + range toggle
    const headerRow = document.createElement('div');
    headerRow.className = 'manual-preset-header';
    
    const saveToggle = document.createElement('button');
    saveToggle.id = 'btn-manual-save-mode';
    saveToggle.className = state.manualSaveMode ? 'danger' : 'accent';
    saveToggle.textContent = state.manualSaveMode ? '✖ Cancel Save Mode' : '▼ Save Mode';
    saveToggle.onclick = () => {
      state.manualSaveMode = !state.manualSaveMode;
      renderManualPresets();
    };
    headerRow.appendChild(saveToggle);

    const rangeToggle = document.createElement('button');
    rangeToggle.className = 'preset-range-btn';
    rangeToggle.textContent = state.presetRange === 0 ? 'Presets 1–8' : 'Presets 9–16';
    rangeToggle.onclick = () => {
      state.presetRange = 1 - state.presetRange;
      renderManualPresets();
    };
    headerRow.appendChild(rangeToggle);

    const modeLabel = document.createElement('span');
    modeLabel.className = 'manual-preset-mode-label';
    modeLabel.textContent = state.manualSaveMode ? 'Click a preset to save (red = save mode)' : 'Click a preset to recall';
    if (state.manualSaveMode) modeLabel.classList.add('save-active');
    headerRow.appendChild(modeLabel);

    container.appendChild(headerRow);

    // Preset grid — 8 buttons per row
    const grid = document.createElement('div');
    grid.className = 'manual-preset-buttons';

    const presetStart = state.presetRange * 8;
    const presetEnd = presetStart + 8;
    
    for (let i = presetStart; i < presetEnd; i++) {
      const presetNumber = i + 1;
      const label = route.preset_labels?.[i] || `Preset ${presetNumber}`;
      const presetSpeed = route.preset_speeds?.[i] || '';
      const speedMark = presetSpeed ? { slow: '›', medium: '››', fast: '›››' }[presetSpeed] || '' : '';
      const speedClass = presetSpeed ? ` speed-${presetSpeed}` : '';
      const thumbUrl = route.preset_thumbs?.[i] || '';
      const btn = document.createElement('button');
      btn.className = `preset-btn${speedClass}${state.manualSaveMode ? ' save-mode' : ''}`;
      if (thumbUrl) {
        btn.classList.add('has-thumb');
        btn.style.setProperty('--thumb-url', `url(${thumbUrl})`);
      }
      btn.innerHTML = `<strong>${presetNumber}</strong><span>${escapeHtml(label)}</span>${speedMark ? `<em class="speed-mark">${speedMark}</em>` : ''}`;
      btn.disabled = !route.enabled;
      btn.addEventListener('click', () => {
        if (state.manualSaveMode) {
          manualSavePreset(routeIndex, i).catch((err) => alert(`Save failed: ${err.message}`));
        } else {
          sendCommand(routeIndex, 'preset_recall', { preset: presetNumber, ...speedArgsFor(routeIndex) });
        }
      });
      grid.appendChild(btn);
    }
    container.appendChild(grid);
  }

async function manualSavePreset(routeIndex, presetIndex) {
    const route = state.routes[routeIndex];
    if (!route) return;
    const presetNumber = presetIndex + 1;
    const currentLabel = route.preset_labels?.[presetIndex] || `Preset ${presetNumber}`;
    const currentSpeed = route.preset_speeds?.[presetIndex] || route.movement_speed || 'medium';
    const result = await showModal({
      title: `Save ${route.label} Preset ${presetNumber}`,
      fields: [
        { name: 'label', label: 'Preset name', value: currentLabel },
        { name: 'speed', label: 'Movement speed', value: currentSpeed, type: 'select',
          options: [
            { value: 'slow', label: 'Slow ›' },
            { value: 'medium', label: 'Medium ››' },
            { value: 'fast', label: 'Fast ›››' },
          ] },
        { name: 'snapshot', label: 'Capture snapshot thumbnail?', value: 'yes', type: 'select',
          options: [
            { value: 'yes', label: 'Yes — capture current camera view' },
            { value: 'no', label: 'No — keep existing/no thumbnail' },
          ] },
      ],
      confirmText: 'Save Preset',
    });
    if (!result) return;
    
    // Capture snapshot if requested
    let thumbData = route.preset_thumbs?.[presetIndex] || '';
    if (result.snapshot === 'yes') {
      try {
        showStatus('Capturing snapshot…', true);
        const snapRes = await fetch(`/api/video/snapshot/${routeIndex}?quality=50&width=160&t=${Date.now()}`);
        if (snapRes.ok) {
          const blob = await snapRes.blob();
          // Convert to data URL
          thumbData = await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.readAsDataURL(blob);
          });
        }
      } catch (e) {
        console.warn('Snapshot capture failed:', e);
      }
    }
    
    // Update preset label, speed, and thumbnail in config
    const labels = [...(route.preset_labels || DEFAULT_ROUTE.preset_labels)].slice(0, MAX_PRESETS);
    labels[presetIndex] = (result.label || '').trim() || `Preset ${presetNumber}`;
    const speeds = [...(route.preset_speeds || [])].slice(0, MAX_PRESETS);
    while (speeds.length < MAX_PRESETS) speeds.push('');
    speeds[presetIndex] = result.speed || 'medium';
    const thumbs = [...(route.preset_thumbs || [])].slice(0, MAX_PRESETS);
    while (thumbs.length < MAX_PRESETS) thumbs.push('');
    thumbs[presetIndex] = thumbData;
    
    // Save to backend
    const payload = { ...route, preset_labels: labels, preset_speeds: speeds, preset_thumbs: thumbs };
    delete payload.index;
    await request(`/api/routes/${routeIndex}`, { method: 'PUT', body: JSON.stringify(payload) });
    state.routes[routeIndex].preset_labels = labels;
    state.routes[routeIndex].preset_speeds = speeds;
    state.routes[routeIndex].preset_thumbs = thumbs;
    
    // Send preset save command to camera
    await sendCommand(routeIndex, 'preset_save', { preset: presetNumber });
    showStatus(`Saved preset ${presetNumber} (${result.speed}) for ${route.label}`, true);
    
    // Exit save mode
    state.manualSaveMode = false;
    renderManualPresets();
    renderPresets();
    renderPreview();
  }

/* ===================================================================
     SETTINGS TAB — Control + Video setup cards per route
     =================================================================== */

  function renderSettingsRoutes() {
    const wrap = $('#settings-routes');
    if (!wrap) return;
    wrap.innerHTML = '';
    state.routes.forEach((r) => {
      const v = r.video;
      const block = document.createElement('div');
      block.className = 'route-block';
      block.dataset.index = r.index;
      block.dataset.status = r.status || 'unknown';
      block.innerHTML = `
        <div class="route-block-head">
          <span class="route-number">#${r.index + 1}</span>
          <input data-field="label" type="text" value="${escapeHtml(r.label)}">
          <label class="switch" title="Enable this camera route">
            <input data-field="enabled" type="checkbox" ${r.enabled ? 'checked' : ''}><span></span>
          </label>
          <span class="route-status ${escapeHtml(r.status || 'unknown')}">${escapeHtml(r.status || 'unknown')}</span>
        </div>
        <div class="setup-grid">
          <div class="setup-card">
            <h3>🎛️ Camera Control Setup</h3>
            <div class="field"><label>Incoming port</label><input data-field="incoming_port" type="number" min="1" max="65535" value="${r.incoming_port}"></div>
            <div class="field"><label>Controller profile</label><select data-field="input_profile">${optionsHtml(INPUT_PROFILES, r.input_profile || DEFAULT_ROUTE.input_profile)}</select></div>
            <div class="field"><label>Camera profile</label><select data-field="output_profile">${optionsHtml(OUTPUT_PROFILES, r.output_profile || DEFAULT_ROUTE.output_profile)}</select></div>
            <div class="field"><label>Camera IP</label><input data-field="camera_ip" type="text" value="${escapeHtml(r.camera_ip)}"></div>
            <div class="field"><label>Camera port</label><input data-field="camera_port" type="number" min="1" max="65535" value="${r.camera_port}"></div>
          </div>
          <div class="setup-card">
            <h3>🎥 Camera Video Setup</h3>
            <div class="field"><label>Video preview</label><label class="switch"><input data-v="enabled" type="checkbox" ${v.enabled ? 'checked' : ''}><span></span></label></div>
            <div class="field"><label>Source type</label><select data-v="source_type">${optionsHtml(VIDEO_SOURCE_TYPES, v.source_type || 'none')}</select></div>
            <div class="field ndi-field"><label>NDI source</label>
              <div class="ndi-picker">
                <select data-v="ndi_source_name" class="ndi-select"><option value="">— Select NDI source —</option></select>
                <button type="button" class="ndi-discover-btn" data-action="ndi-discover">🔍 Discover</button>
              </div>
            </div>
            <div class="field usb-field" style="display:${v.source_type === 'usb' ? 'block' : 'none'}"><label>USB device</label><select data-v="usb_device_index" class="usb-select"><option value="0">Device 0</option></select></div>
            <div class="field"><label>Frame rate</label><input data-v="frame_rate" type="number" min="1" max="30" value="${v.frame_rate ?? 8}"></div>
            <div class="field"><label>JPEG quality</label><input data-v="jpeg_quality" type="number" min="10" max="95" value="${v.jpeg_quality ?? 60}"></div>
            <div class="field"><label>Output Resolution (preview size)</label><select data-v="resolution">${optionsHtml(resolutionList(), v.resolution || '640x360')}</select></div>
            <div class="field crop-field">
              <label>📹 Crop / Region <small>(share one NDI feed across cameras)</small></label>
              <div class="crop-presets">
                ${(() => {
                  const cx = Math.round((v.crop_x ?? 0) * 100);
                  const cy = Math.round((v.crop_y ?? 0) * 100);
                  const cw = Math.round((v.crop_w ?? 0) * 100);
                  const ch = Math.round((v.crop_h ?? 0) * 100);
                  const presets = [
                    { id: 'full', label: 'Full Frame',  icon: '⬜', x: 0,  y: 0,  w: 0,  h: 0  },
                    { id: 'tl',   label: 'Top Left',    icon: '◰', x: 0,  y: 0,  w: 50, h: 50 },
                    { id: 'tr',   label: 'Top Right',   icon: '◳', x: 50, y: 0,  w: 50, h: 50 },
                    { id: 'bl',   label: 'Bottom Left', icon: '◱', x: 0,  y: 50, w: 50, h: 50 },
                    { id: 'br',   label: 'Bottom Right',icon: '◲', x: 50, y: 50, w: 50, h: 50 },
                  ];
                  const active = presets.find(p =>
                    Math.abs(p.x - cx) < 1 && Math.abs(p.y - cy) < 1 &&
                    Math.abs(p.w - cw) < 1 && Math.abs(p.h - ch) < 1
                  ) || {id:''};
                  return presets.map(p => `
                    <button type="button" class="crop-preset-btn ${p.id === active.id ? 'active' : ''}"
                      data-crop="${p.id}"
                      data-cx="${p.x}" data-cy="${p.y}" data-cw="${p.w}" data-ch="${p.h}"
                      title="${p.label}">
                      <span class="crop-icon">${p.icon}</span>
                      <span class="crop-label-sm">${p.label}</span>
                    </button>
                  `).join('');
                })()}
              </div>
              <input data-v="crop_x" type="hidden" value="${Math.round((v.crop_x ?? 0) * 100)}">
              <input data-v="crop_y" type="hidden" value="${Math.round((v.crop_y ?? 0) * 100)}">
              <input data-v="crop_w" type="hidden" value="${Math.round((v.crop_w ?? 0) * 100)}">
              <input data-v="crop_h" type="hidden" value="${Math.round((v.crop_h ?? 0) * 100)}">
            </div>
          </div>
        </div>
        <div class="route-actions">
          <button data-action="save-route" class="accent">Save Camera ${r.index + 1}</button>
          <button data-action="test-route">Test</button>
        </div>
      `;
      block.querySelectorAll('input, select').forEach((el) => {
        el.addEventListener('focus', () => { state.editing = true; });
        el.addEventListener('change', () => { state.editing = true; });
      });
      // Pre-populate NDI select with current value
      const ndiSel = block.querySelector('.ndi-select');
      if (ndiSel && v.ndi_source_name) {
        const opt = document.createElement('option');
        opt.value = v.ndi_source_name;
        opt.textContent = v.ndi_source_name;
        ndiSel.appendChild(opt);
        ndiSel.value = v.ndi_source_name;
      }
      // NDI discover button
      block.querySelector('.ndi-discover-btn')?.addEventListener('click', () => {
        discoverNdi(block).catch((err) => alert(`NDI discovery failed: ${err.message}`));
      });
      // USB field visibility and discovery
      const usbField = block.querySelector('.usb-field');
      const sourceType = block.querySelector('[data-v="source_type"]');
      const savedUsbIndex = v.usb_device_index;
      if (usbField && sourceType) {
        usbField.style.display = sourceType.value === 'usb' ? 'block' : 'none';
        if (sourceType.value === 'usb') {
          discoverUsb(block, savedUsbIndex);
        }
        sourceType.addEventListener('change', () => {
          usbField.style.display = sourceType.value === 'usb' ? 'block' : 'none';
          if (sourceType.value === 'usb') {
            discoverUsb(block, savedUsbIndex);
          }
        });
      }
      // Crop preset buttons
      block.querySelectorAll('.crop-preset-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const cx = parseFloat(btn.dataset.cx);  // already 0-100
          const cy = parseFloat(btn.dataset.cy);
          const cw = parseFloat(btn.dataset.cw);
          const ch = parseFloat(btn.dataset.ch);
          const setHidden = (field, val) => {
            const el = block.querySelector(`[data-v="${field}"]`);
            if (el) el.value = val;
          };
          setHidden('crop_x', cx); setHidden('crop_y', cy);
          setHidden('crop_w', cw); setHidden('crop_h', ch);
          // Update active button styling
          block.querySelectorAll('.crop-preset-btn').forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          state.editing = true;
          showStatus(`Crop set to ${btn.title}`, true);
        });
      });
      wrap.appendChild(block);
    });
  }

  async function discoverNdi(block) {
    const sel = block.querySelector('.ndi-select');
    if (!sel) return;
    const btn = block.querySelector('.ndi-discover-btn');
    if (btn) { btn.disabled = true; btn.textContent = '🔍 Searching…'; }
    try {
      const res = await request('/api/ndi/sources');
      const current = sel.value;
      sel.innerHTML = '<option value="">— Select NDI source —</option>';
      if (!res.available) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = res.error || 'NDI not available';
        opt.disabled = true;
        sel.appendChild(opt);
        showStatus('NDI not available — ' + (res.error || 'unknown'), false);
      } else if (res.sources.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No NDI sources found on network';
        opt.disabled = true;
        sel.appendChild(opt);
        showStatus('No NDI sources found', true);
      } else {
        res.sources.forEach((src) => {
          const opt = document.createElement('option');
          opt.value = src.ndi_name;
          opt.textContent = src.ndi_name;
          sel.appendChild(opt);
        });
        if (current) sel.value = current;
        showStatus(`Found ${res.sources.length} NDI source${res.sources.length !== 1 ? 's' : ''}`, true);
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🔍 Discover'; }
    }
  }

  async function discoverUsb(block, savedIndex) {
    const sel = block.querySelector('.usb-select');
    if (!sel) return;
    try {
      const res = await request('/api/usb/devices');
      const current = savedIndex != null ? String(savedIndex) : sel.value;
      sel.innerHTML = '';
      if (!res.available || !res.devices.length) {
        const opt = document.createElement('option');
        opt.value = '0';
        opt.textContent = res.error || 'No USB devices found';
        opt.disabled = true;
        sel.appendChild(opt);
      } else {
        res.devices.forEach((dev) => {
          const opt = document.createElement('option');
          opt.value = dev.index;
          opt.textContent = dev.label;
          sel.appendChild(opt);
        });
        // Restore the saved device index, not whatever the placeholder was
        if (current != null) sel.value = current;
      }
    } catch (err) {
      console.error('USB discovery failed:', err);
    }
  }

  function readRouteBlock(block) {
    const idx = Number(block.dataset.index);
    const v = (field) => block.querySelector(`[data-v="${field}"]`);
    const video = {
      enabled: v('enabled') ? v('enabled').checked : false,
      source_type: v('source_type') ? v('source_type').value : 'none',
      ndi_source_name: (v('ndi_source_name')?.value || '').trim(),
      usb_device_index: Number(v('usb_device_index')?.value || 0),
      resolution: v('resolution')?.value || '640x360',
      frame_rate: Number(v('frame_rate')?.value || 8),
      jpeg_quality: Number(v('jpeg_quality')?.value || 60),
      crop_x: Number(v('crop_x')?.value || 0) / 100,
      crop_y: Number(v('crop_y')?.value || 0) / 100,
      crop_w: Number(v('crop_w')?.value || 0) / 100,
      crop_h: Number(v('crop_h')?.value || 0) / 100,
    };
    return {
      enabled: block.querySelector('[data-field="enabled"]')?.checked ?? false,
      label: (block.querySelector('[data-field="label"]')?.value || '').trim() || `Camera ${idx + 1}`,
      incoming_port: Number(block.querySelector('[data-field="incoming_port"]')?.value),
      input_profile: block.querySelector('[data-field="input_profile"]')?.value || DEFAULT_ROUTE.input_profile,
      output_profile: block.querySelector('[data-field="output_profile"]')?.value || DEFAULT_ROUTE.output_profile,
      camera_ip: (block.querySelector('[data-field="camera_ip"]')?.value || '').trim() || DEFAULT_ROUTE.camera_ip,
      camera_port: Number(block.querySelector('[data-field="camera_port"]')?.value),
      status: block.dataset.status || 'unknown',
      movement_speed: state.routes[idx]?.movement_speed || 'medium',
      preset_labels: [...(state.routes[idx]?.preset_labels || DEFAULT_ROUTE.preset_labels)],
      video,
    };
  }

  async function saveRouteBlock(index) {
    const block = $(`#settings-routes .route-block[data-index="${index}"]`);
    if (!block) return;
    state.editing = false;
    const payload = readRouteBlock(block);
    await request(`/api/routes/${index}`, { method: 'PUT', body: JSON.stringify(payload) });
    showStatus(`Saved ${payload.label} — restarting bridge…`, true);
    try { await request('/api/bridge/restart', { method: 'POST', body: '{}' }); } catch (_) { /* non-fatal */ }
    await loadRoutes();
    showStatus(`Saved ${payload.label}`, true);
  }

  async function testRoute(index) {
    try {
      showStatus(`Testing camera ${index + 1}…`, true);
      const result = await request(`/api/routes/${index}/test`, {
        method: 'POST', body: JSON.stringify({ type: 'version' }),
      });
      alert(`${result.route_label}: ${result.ok ? 'OK' : 'FAILED'}\n${result.detail || result.result}`);
      await loadRoutes();
      await loadDiagnostics();
    } catch (err) {
      showStatus('Test failed', false);
      alert(`Camera test failed: ${err.message}`);
    }
  }

  /* ---------- ATEM configuration card ---------- */

  function renderAtemConfig() {
    const card = $('#settings-atem');
    if (!card) return;
    const a = state.atem || { enabled: false, atem_ip: '192.168.1.240', supersource_aux_output: 1, input_mapping: [1, 2, 3, 4, 5, 6, 7, 8] };
    const mapping = (a.input_mapping && a.input_mapping.length === MAX_ROUTES) ? a.input_mapping : Array.from({ length: MAX_ROUTES }, (_, i) => i + 1);
    const cells = mapping.map((inp, i) => `
      <div class="mapping-cell">
        <small>Cam ${i + 1}</small>
        <input data-amap="${i}" type="number" min="1" max="20" value="${inp}">
      </div>`).join('');
    card.innerHTML = `
      <h3>📺 ATEM Configuration</h3>
      <p class="atem-sub">Connect a Blackmagic ATEM switcher for SuperSource 2×2 routing and PGM/PVW tally overlays.</p>
      <div class="atem-grid">
        <div class="field"><label>Enable ATEM</label><label class="switch"><input data-af="enabled" type="checkbox" ${a.enabled ? 'checked' : ''}><span></span></label></div>
        <div class="field"><label>ATEM IP</label><input data-af="atem_ip" type="text" value="${escapeHtml(a.atem_ip || '')}"></div>
        <div class="field"><label>SuperSource AUX out</label><input data-af="supersource_aux_output" type="number" min="1" max="6" value="${a.supersource_aux_output ?? 1}"></div>
        <div class="atem-mapping">
          <div class="field" style="grid-template-columns:130px 1fr"><label>Input mapping</label><span style="color:var(--muted);font-size:.78rem;align-self:center">route_index → ATEM SDI input (1–20). Used for PGM/PVW tally badges.</span></div>
          <div class="mapping-grid">${cells}</div>
        </div>
      </div>
      <div class="route-actions">
        <button id="btn-save-atem" class="accent">Save ATEM Config</button>
        <button id="btn-atem-connect">Connect</button>
        <button id="btn-atem-disconnect">Disconnect</button>
      </div>
    `;
    card.querySelectorAll('input').forEach((el) => {
      el.addEventListener('focus', () => { state.editing = true; });
      el.addEventListener('change', () => { state.editing = true; });
    });
    $('#btn-save-atem')?.addEventListener('click', () => saveAtemConfig().catch((err) => { showStatus('ATEM save failed', false); alert(`ATEM save failed: ${err.message}`); }));
    $('#btn-atem-connect')?.addEventListener('click', () => atemConnect().catch((err) => alert(`ATEM connect failed: ${err.message}`)));
    $('#btn-atem-disconnect')?.addEventListener('click', () => request('/api/atem/disconnect', { method: 'POST', body: '{}' }).then(() => { updateAtemPill(); }).catch(() => {}));
  }

  async function saveAtemConfig() {
    const card = $('#settings-atem');
    if (!card) return;
    state.editing = false;
    const af = (f) => card.querySelector(`[data-af="${f}"]`);
    const input_mapping = Array.from({ length: MAX_ROUTES }, (_, i) => Number(card.querySelector(`[data-amap="${i}"]`)?.value || (i + 1)));
    const payload = {
      enabled: af('enabled') ? af('enabled').checked : false,
      atem_ip: (af('atem_ip')?.value || '').trim(),
      supersource_aux_output: Number(af('supersource_aux_output')?.value || 1),
      input_mapping,
    };
    const updated = await request('/api/atem/config', { method: 'PUT', body: JSON.stringify(payload) });
    state.atem = updated;
    updateAtemPill();
    showStatus('ATEM config saved', true);
  }

  async function atemConnect() {
    try {
      const res = await request('/api/atem/connect', { method: 'POST', body: '{}' });
      state.atemConnected = true;
      updateAtemPill();
      showStatus(`ATEM connected to ${res.atem_ip}`, true);
    } catch (err) {
      state.atemConnected = false;
      updateAtemPill();
      alert(`ATEM connection failed: ${err.message}`);
    }
  }

  /* ===================================================================
     ATEM tally polling + LIVE/PVW overlay
     =================================================================== */

  function camToAtemInput(ci) {
    const mapping = state.atem?.input_mapping;
    return mapping && mapping.length === MAX_ROUTES ? mapping[ci] : null;
  }

  function applyTally() {
    const pgm = state.tally ? state.tally.pgm : null;
    const pvw = state.tally ? state.tally.pvw : null;
    document.querySelectorAll('.video-pane[data-cam]').forEach((pane) => {
      const ci = Number(pane.dataset.cam);
      const inp = camToAtemInput(ci);
      const onPgm = inp != null && inp === pgm;
      const onPvw = inp != null && inp === pvw && inp !== pgm;
      pane.classList.toggle('pgm', onPgm);
      pane.classList.toggle('pvw', onPvw);
    });
  }

  function clearTally() {
    document.querySelectorAll('.video-pane[data-cam]').forEach((p) => p.classList.remove('pgm', 'pvw'));
  }

  function updateAtemPill() {
    const pill = $('#atem-pill');
    if (!pill) return;
    const enabled = !!(state.atem && state.atem.enabled);
    pill.hidden = !enabled;
    if (!enabled) return;
    pill.classList.toggle('on', state.atemConnected);
    pill.textContent = state.atemConnected ? 'ATEM live' : 'ATEM connecting…';
  }

  async function pollTally() {
    if (!(state.atem && state.atem.enabled)) {
      state.atemConnected = false;
      state.tally = null;
      updateAtemPill();
      clearTally();
      return;
    }
    try {
      const t = await request('/api/atem/tally');
      state.atemConnected = true;
      state.tally = { pgm: t.pgm_source, pvw: t.pvw_source };
      updateAtemPill();
      applyTally();
    } catch (_) {
      const wasConnected = state.atemConnected;
      state.atemConnected = false;
      state.tally = null;
      updateAtemPill();
      clearTally();
      // If it was connected and dropped, try a soft reconnect.
      if (wasConnected) { atemConnect().catch(() => {}); }
    }
  }

    /* ===================================================================
     Modal dialog (replaces browser prompt())
     =================================================================== */
  function showModal({ title, fields, confirmText = 'OK', cancelText = 'Cancel' }) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      const dialog = document.createElement('div');
      dialog.className = 'modal-dialog';
      dialog.innerHTML = `<h3>${escapeHtml(title)}</h3>`;
      const inputs = {};
      (fields || []).forEach((f) => {
        const label = document.createElement('label');
        label.className = 'modal-field';
        label.textContent = f.label || f.name;
        const inp = document.createElement('input');
        inp.type = f.type || 'text';
        inp.value = f.value || '';
        if (f.options) {
          const sel = document.createElement('select');
          f.options.forEach((opt) => {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (opt.value === f.value) o.selected = true;
            sel.appendChild(o);
          });
          inputs[f.name] = sel;
          label.appendChild(sel);
        } else {
          inputs[f.name] = inp;
          label.appendChild(inp);
        }
        dialog.appendChild(label);
      });
      const btnRow = document.createElement('div');
      btnRow.className = 'modal-buttons';
      const cancelBtn = document.createElement('button');
      cancelBtn.textContent = cancelText;
      cancelBtn.onclick = () => { overlay.remove(); resolve(null); };
      const okBtn = document.createElement('button');
      okBtn.className = 'accent';
      okBtn.textContent = confirmText;
      okBtn.onclick = () => {
        const result = {};
        Object.entries(inputs).forEach(([k, v]) => { result[k] = v.value; });
        overlay.remove();
        resolve(result);
      };
      btnRow.appendChild(cancelBtn);
      btnRow.appendChild(okBtn);
      dialog.appendChild(btnRow);
      overlay.appendChild(dialog);
      overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };
      document.body.appendChild(overlay);
      const firstInput = Object.values(inputs)[0];
      if (firstInput) { firstInput.focus(); firstInput.select?.(); }
    });
  }

/* ===================================================================
     Command sending (manual PTZ / lens / OSD / presets)
     =================================================================== */

  function isHoldToMoveCommand(command) {
    return ['pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'zoom_in', 'zoom_out', 'focus_near', 'focus_far'].includes(command);
  }

  function commandArgs(command) {
    const routeIndex = Number($('#manual-camera-select')?.value || 0);
    const modePreset = speedArgsFor(routeIndex);
    const panSpeed = Number($('#pan-speed')?.value || modePreset.pan_speed);
    const tiltSpeed = Number($('#tilt-speed')?.value || modePreset.tilt_speed);
    const zoomSpeed = Number($('#zoom-speed')?.value || modePreset.zoom_speed);
    const args = {};
    if (command.startsWith('pan_') || command.startsWith('tilt_') || command === 'stop') {
      args.pan_speed = panSpeed;
      args.tilt_speed = tiltSpeed;
    }
    if (command.startsWith('zoom_')) args.zoom_speed = zoomSpeed;
    return args;
  }

  /* Update preset button disabled state based on routeBusy */
  function updatePresetButtonsBusy() {
    // Preset page buttons (in .preset-buttons within each camera card)
    document.querySelectorAll('#preset-grid .preset-buttons .preset-btn').forEach((btn) => {
      const card = btn.closest('.camera-preset-card');
      if (!card) return;
      const pane = card.querySelector('.preset-video-pane[data-cam]');
      const ci = pane ? Number(pane.dataset.cam) : 0;
      const isBusy = !!routeBusy[ci];
      btn.classList.toggle('busy', isBusy);
      if (isBusy) btn.disabled = true;
      else {
        const route = state.routes[ci];
        btn.disabled = state.presetEditMode ? false : (!route || !route.enabled);
      }
    });
    // Manual page buttons
    document.querySelectorAll('.manual-preset-buttons .preset-btn').forEach((btn) => {
      const sel = $('#manual-camera-select');
      const ci = Number(sel?.value || 0);
      const isBusy = !!routeBusy[ci];
      btn.classList.toggle('busy', isBusy);
      if (isBusy) btn.disabled = true;
      else {
        const route = state.routes[ci];
        btn.disabled = !route || !route.enabled;
      }
    });
  }

async function sendCommand(routeIndex, command, args = {}) {
    const isPresetRecall = command === 'preset_recall';
    try {
      if (routeIndex === null || Number.isNaN(routeIndex)) return alert('Select a camera first.');
      // Check if this route is busy (only for preset_recall)
      if (isPresetRecall && routeBusy[routeIndex]) {
        showStatus(`${state.routes[routeIndex]?.label || 'Camera'} is still moving — please wait for confirmation`, false);
        return;
      }
      showStatus(`Sending ${command}…`, true);
      if (isPresetRecall) {
        routeBusy[routeIndex] = true;
        updatePresetButtonsBusy();
      }
      const result = await request('/api/command', {
        method: 'POST', body: JSON.stringify({ route_index: routeIndex, command, args }),
      });
      if (command === 'menu_open') setOsdActive(true);
      if (command === 'menu_close') setOsdActive(false);
      showStatus(result.ok ? `${command} sent` : `${command} failed`, Boolean(result.ok));
      await loadDiagnostics();
      if (!result.ok && !isPresetRecall) alert(`${command} failed: ${result.detail || 'unknown error'}`);
    } catch (err) {
      const errMsg = String(err.message || '');
      if (errMsg.includes('409') || errMsg.includes('busy')) {
        showStatus(`${state.routes[routeIndex]?.label || 'Camera'} is still moving — please wait for confirmation`, false);
      } else {
        showStatus('Command failed', false);
        alert(`Command failed: ${err.message}`);
      }
    } finally {
      // Release busy state after a delay (camera completion reply will clear it server-side,
      // but we also clear it client-side after a timeout as safety)
      if (isPresetRecall) {
        setTimeout(() => {
          routeBusy[routeIndex] = false;
          updatePresetButtonsBusy();
        }, 8000);
      }
    }
  }

  /* ===================================================================
     Bridge IP / config export-import / diagnostics
     =================================================================== */

  async function saveBridgeIP() {
    const sel = $('#bridge-ip-select');
    const manual = $('#bridge-ip-manual');
    let ip = manual.value.trim() || sel.value;
    if (!ip) ip = '0.0.0.0';
    try {
      const cfg = await request('/api/config');
      cfg.bridge_ip = ip;
      await request('/api/config', { method: 'PUT', body: JSON.stringify(cfg) });
      showStatus(`Bridge IP set to ${ip} — restarting bridge…`, true);
      try { await request('/api/bridge/restart', { method: 'POST', body: '{}' }); } catch (_) { /* non-fatal */ }
      showStatus(`Bridge IP set to ${ip}`, true);
      manual.value = '';
    } catch (err) {
      showStatus('Failed to save Bridge IP', false);
      alert(`Failed: ${err.message}`);
    }
  }

  async function exportConfig() {
    const data = await request('/api/config/export', { method: 'POST', body: '{}' });
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'meowcam-bridge.json';
    a.click();
    URL.revokeObjectURL(url);
    showStatus('Config exported', true);
  }

  async function importConfig(file) {
    const text = await file.text();
    const data = JSON.parse(text);
    await request('/api/config/import', { method: 'POST', body: JSON.stringify(data) });
    showStatus('Config imported', true);
    await loadConfig();
  }

  async function loadDiagnostics() {
    try {
      const diag = await request('/api/diagnostics');
      if (diag.error) throw new Error(diag.error);
      $('#last-controller').textContent = safeText(diag.last_controller_addr);
      $('#last-command').textContent = safeText(diag.last_command);
      $('#last-reply').textContent = safeText(diag.last_camera_reply);
      const routeSummary = (diag.routes || []).map((r) => `${r.index + 1}:${r.label}=${r.status}${r.enabled ? '' : '(off)'}`).join('  ');
      $('#route-status').textContent = routeSummary || 'No routes';
      $('#event-log').textContent = (diag.event_log || []).length ? diag.event_log.join('\n') : 'No events yet.';
    } catch (err) {
      const el = $('#event-log');
      if (el) el.textContent = `Diagnostics unavailable: ${err.message}`;
    }
  }

  async function resetDiagnostics() {
    await request('/api/diagnostics/reset', { method: 'POST', body: '{}' });
    showStatus('Diagnostics reset', true);
    await loadDiagnostics();
  }

  /* ===================================================================
     Tab switching + feed lifecycle (connection-cap mitigation)
     =================================================================== */

  function activateTab(name) {
    $$('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    $$('.tab-panel').forEach((p) => p.classList.remove('active'));
    const panel = document.getElementById(name);
    if (panel) panel.classList.add('active');
    const desc = $('#page-description');
    if (desc) desc.textContent = PAGE_DESCRIPTIONS[name] || '';
    // Keep only the active video tab's feeds alive.
    if (name === 'preview') {
      renderPreview();
    } else {
      stopPreviewFeeds();
    }
    if (name === 'presets') {
      syncPresetFeeds();
    } else {
      stopPresetFeeds();
    }
    if (name === 'manual') {
      syncManualVideo();
      renderManualPresets();
    } else {
      stopManualFeed();
    }
  }

  /* ===================================================================
     Event binding
     =================================================================== */

  function bindEvents() {
    $$('.tab-btn').forEach((btn) => btn.addEventListener('click', () => activateTab(btn.dataset.tab)));

    $$('.preset-page-btn').forEach((btn) => btn.addEventListener('click', () => {
      state.presetPage = Number(btn.dataset.page);
      renderPresets();
    }));
    $$('.preview-page-btn').forEach((btn) => btn.addEventListener('click', () => {
      state.previewPage = Number(btn.dataset.page);
      renderPreview();
    }));

    // Preset range toggle (1-8 / 9-16)
    $('#btn-preset-range')?.addEventListener('click', () => {
      state.presetRange = 1 - state.presetRange;
      renderPresets();
    });

    document.addEventListener('click', (event) => {
      const speedBtn = event.target.closest('.speed-mode-btn');
      if (!speedBtn) return;
      const routeIndex = Number($('#manual-camera-select')?.value || 0);
      setCameraSpeed(routeIndex, speedBtn.dataset.speed, true).catch((err) => alert(`Speed change failed: ${err.message}`));
    });
    $('#btn-save-speed')?.addEventListener('click', () => {
      const routeIndex = Number($('#manual-camera-select')?.value || 0);
      const activeMode = $('.speed-mode-btn.active')?.dataset.speed || speedModeFor(routeIndex);
      setCameraSpeed(routeIndex, activeMode, true).catch((err) => alert(`Speed save failed: ${err.message}`));
    });

    $('#manual-camera-select')?.addEventListener('change', () => {
      const routeIndex = Number($('#manual-camera-select')?.value || 0);
      updateManualSpeedControls(speedModeFor(routeIndex));
      syncManualVideo();
      renderManualPresets();
    });

    $('#btn-edit-presets')?.addEventListener('click', () => {
      state.presetEditMode = !state.presetEditMode;
      renderPresets();
      renderPreview();
    });

    $('#btn-save-bridge-ip')?.addEventListener('click', () => saveBridgeIP());

    $('#settings-routes')?.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const block = button.closest('.route-block');
      if (!block) return;
      const index = Number(block.dataset.index);
      if (button.dataset.action === 'save-route') saveRouteBlock(index).catch((err) => { showStatus('Save failed', false); alert(`Save failed: ${err.message}`); });
      if (button.dataset.action === 'test-route') testRoute(index);
    });

    $$('.manual-controls-area button[data-cmd]').forEach((btn) => {
      const raw = btn.dataset.cmd;
      const command = raw === 'autofocus' ? 'autofocus_toggle' : raw;
      if (isHoldToMoveCommand(command)) {
        let activePointer = null;
        const startMove = (event) => {
          event.preventDefault();
          activePointer = event.pointerId;
          btn.setPointerCapture?.(event.pointerId);
          const routeIndex = Number($('#manual-camera-select')?.value);
          sendCommand(routeIndex, command, commandArgs(command));
        };
        const stopMove = (event) => {
          if (activePointer !== null && event.pointerId !== activePointer) return;
          activePointer = null;
          const routeIndex = Number($('#manual-camera-select')?.value);
          const stopCommand = command.startsWith('zoom_') ? 'zoom_stop' : command.startsWith('focus_') ? 'focus_stop' : 'stop';
          sendCommand(routeIndex, stopCommand, {});
        };
        btn.addEventListener('pointerdown', startMove);
        btn.addEventListener('pointerup', stopMove);
        btn.addEventListener('pointercancel', stopMove);
        btn.addEventListener('pointerleave', (event) => { if (activePointer !== null) stopMove(event); });
      } else {
        btn.addEventListener('click', () => {
          const routeIndex = Number($('#manual-camera-select')?.value);
          sendCommand(routeIndex, command, commandArgs(command));
        });
      }
    });

    $('#btn-export')?.addEventListener('click', () => exportConfig().catch((err) => { showStatus('Export failed', false); alert(`Export failed: ${err.message}`); }));
    $('#btn-import')?.addEventListener('click', () => $('#import-file')?.click());
    $('#import-file')?.addEventListener('change', (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      importConfig(file).catch((err) => { showStatus('Import failed', false); alert(`Import failed: ${err.message}`); }).finally(() => { event.target.value = ''; });
    });
    $('#btn-reset-routes')?.addEventListener('click', () => resetDiagnostics().catch((err) => { showStatus('Reset failed', false); alert(`Reset failed: ${err.message}`); }));
  }

/* ===================================================================
     Init
     =================================================================== */

  bindEvents();
  loadNetworkInterfaces();
  loadConfig().catch((err) => {
    showStatus('Backend unavailable', false);
    console.warn('Failed to load config', err);
  });
  loadDiagnostics();
  setInterval(loadDiagnostics, 5000);
  setInterval(() => {
    if (state.editing) return;
    const settingsActive = $('#settings')?.classList.contains('active');
    if (settingsActive) return;
    loadRoutes();
  }, 15000);
  setInterval(pollTally, 500);

  // Poll server busy state every 2s to clear client-side busy flags when camera finishes
  setInterval(async () => {
    try {
      const st = await request('/api/bridge/status');
      if (st && st.busy) {
        let changed = false;
        for (let i = 0; i < MAX_ROUTES; i++) {
          const serverBusy = !!st.busy[i];
          const clientBusy = !!routeBusy[i];
          if (clientBusy && !serverBusy) {
            routeBusy[i] = false;
            changed = true;
          }
        }
        if (changed) updatePresetButtonsBusy();
      }
    } catch (e) { /* ignore poll errors */ }
  }, 2000);
})();



