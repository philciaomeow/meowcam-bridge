/* MeowCam Bridge — embedded web UI JavaScript */

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
  const DEFAULT_ROUTE = {
    enabled: false,
    label: 'Camera',
    incoming_port: 52382,
    input_profile: 'ptzoptics_pt_joy_g4_visca_udp',
    output_profile: 'sony_brc_h900_brbk_ip10',
    camera_ip: '192.168.51.123',
    camera_port: 52381,
    status: 'unknown',
    preset_labels: Array.from({ length: MAX_PRESETS }, (_, i) => `Preset ${i + 1}`),
  };

  const state = {
    config: null,
    routes: [],
    presetPage: 0,
    presetEditMode: false,
    osdActive: false,
    editing: false,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function cloneDefaultRoute(index) {
    return {
      ...DEFAULT_ROUTE,
      label: `Camera ${index + 1}`,
      incoming_port: 52382 + index,
      camera_ip: `192.168.51.${123 + index}`,
      preset_labels: [...DEFAULT_ROUTE.preset_labels],
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
      padded.push(route);
    }
    return padded;
  }

  function optionsHtml(options, selected) {
    return options.map((p) => `<option value="${escapeHtml(p.value)}" ${p.value === selected ? 'selected' : ''}>${escapeHtml(p.label)}</option>`).join('');
  }

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
    } catch (_) {}
  }

  async function loadConfig() {
    state.config = await request('/api/config');
    if (state.config.error) throw new Error(state.config.error);
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
    await loadRoutes();
  }

  async function loadRoutes() {
    if (state.editing) return;
    const routes = await request('/api/routes');
    state.routes = normaliseRoutes(routes);
    renderRoutes();
    renderCameraSelects();
    renderPresets();
    showStatus('Ready', true);
  }

  function routeFromRow(row) {
    const idx = Number(row.dataset.index);
    const presetLabels = state.routes[idx]?.preset_labels || DEFAULT_ROUTE.preset_labels;
    return {
      enabled: row.querySelector('[data-field="enabled"]').checked,
      label: row.querySelector('[data-field="label"]').value.trim() || `Camera ${idx + 1}`,
      incoming_port: Number(row.querySelector('[data-field="incoming_port"]').value),
      input_profile: row.querySelector('[data-field="input_profile"]').value || DEFAULT_ROUTE.input_profile,
      output_profile: row.querySelector('[data-field="output_profile"]').value || DEFAULT_ROUTE.output_profile,
      camera_ip: row.querySelector('[data-field="camera_ip"]').value.trim() || DEFAULT_ROUTE.camera_ip,
      camera_port: Number(row.querySelector('[data-field="camera_port"]').value),
      status: row.dataset.status || 'unknown',
      preset_labels: [...presetLabels],
    };
  }

  async function saveRoute(index) {
    const row = $(`#routes-table tbody tr[data-index="${index}"]`);
    if (!row) return;
    state.editing = false;
    const payload = routeFromRow(row);
    await request(`/api/routes/${index}`, { method: 'PUT', body: JSON.stringify(payload) });
    showStatus(`Saved ${payload.label} — restarting bridge…`, true);
    try { await request('/api/bridge/restart', { method: 'POST', body: '{}' }); } catch (_) {}
    await loadRoutes();
    showStatus(`Saved ${payload.label}`, true);
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
    const next = prompt(`Rename ${route.label} preset ${presetIndex + 1}`, current);
    if (next === null) return;
    labels[presetIndex] = next.trim() || `Preset ${presetIndex + 1}`;
    await savePresetLabels(routeIndex, labels);
    renderPresets();
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

  function renderRoutes() {
    const tbody = $('#routes-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    state.routes.forEach((r) => {
      const tr = document.createElement('tr');
      tr.dataset.index = r.index;
      tr.dataset.status = r.status || 'unknown';
      tr.innerHTML = `
        <td class="route-number">${r.index + 1}</td>
        <td><label class="switch"><input data-field="enabled" type="checkbox" ${r.enabled ? 'checked' : ''}><span></span></label></td>
        <td><input data-field="label" type="text" value="${escapeHtml(r.label)}"></td>
        <td><input data-field="incoming_port" type="number" min="1" max="65535" value="${r.incoming_port}"></td>
        <td><select data-field="input_profile">${optionsHtml(INPUT_PROFILES, r.input_profile || DEFAULT_ROUTE.input_profile)}</select></td>
        <td><select data-field="output_profile">${optionsHtml(OUTPUT_PROFILES, r.output_profile || DEFAULT_ROUTE.output_profile)}</select></td>
        <td><input data-field="camera_ip" type="text" value="${escapeHtml(r.camera_ip)}"></td>
        <td><input data-field="camera_port" type="number" min="1" max="65535" value="${r.camera_port}"></td>
        <td><span class="route-status ${escapeHtml(r.status || 'unknown')}">${escapeHtml(r.status || 'unknown')}</span></td>
        <td class="row-actions"><button data-action="save-route">Save</button><button data-action="test-route">Test</button></td>
      `;
      tr.querySelectorAll('input, select').forEach((el) => {
        el.addEventListener('focus', () => { state.editing = true; });
        el.addEventListener('change', () => { state.editing = true; });
      });
      tbody.appendChild(tr);
    });
  }

  function renderCameraSelects() {
    const enabled = state.routes.filter((r) => r.enabled);
    const list = enabled.length ? enabled : state.routes;
    ['manual-camera-select'].forEach((id) => {
      const sel = document.getElementById(id);
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
    });
  }

  function renderPresets() {
    const grid = $('#preset-grid');
    if (!grid) return;
    grid.innerHTML = '';
    const start = state.presetPage * 4;
    const routes = state.routes.slice(start, start + 4);
    routes.forEach((route) => {
      const card = document.createElement('article');
      card.className = `camera-preset-card ${route.enabled ? '' : 'disabled'}`;
      card.innerHTML = `
        <div class="camera-card-head">
          <div><h3>${escapeHtml(route.label)}</h3><p>${escapeHtml(route.camera_ip)} · in ${route.incoming_port}</p></div>
          <span class="route-status ${escapeHtml(route.status || 'unknown')}">${route.enabled ? escapeHtml(route.status || 'unknown') : 'off'}</span>
        </div>
        <div class="preset-buttons"></div>
      `;
      const buttons = card.querySelector('.preset-buttons');
      (route.preset_labels || DEFAULT_ROUTE.preset_labels).slice(0, MAX_PRESETS).forEach((label, i) => {
        const btn = document.createElement('button');
        btn.className = state.presetEditMode ? 'preset-btn editing' : 'preset-btn';
        btn.innerHTML = `<strong>${i + 1}</strong><span>${escapeHtml(label || `Preset ${i + 1}`)}</span>`;
        btn.title = state.presetEditMode ? 'Click to rename this preset' : 'Recall preset';
        btn.disabled = !route.enabled && !state.presetEditMode;
        btn.addEventListener('click', () => {
          if (state.presetEditMode) renamePreset(route.index, i).catch((err) => alert(`Rename failed: ${err.message}`));
          else sendCommand(route.index, 'preset_recall', { preset: i + 1 });
        });
        buttons.appendChild(btn);
      });
      grid.appendChild(card);
    });
    $$('.preset-page-btn').forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.page) === state.presetPage));
    const editBtn = $('#btn-edit-presets');
    if (editBtn) editBtn.textContent = state.presetEditMode ? 'Done editing preset names' : 'Edit preset names';
  }

  function commandArgs(command) {
    const panSpeed = Number($('#pan-speed')?.value || 3);
    const tiltSpeed = Number($('#tilt-speed')?.value || 3);
    const zoomSpeed = Number($('#zoom-speed')?.value || 3);
    const args = {};
    if (command.startsWith('pan_') || command.startsWith('tilt_') || command === 'stop') {
      args.pan_speed = panSpeed;
      args.tilt_speed = tiltSpeed;
    }
    if (command.startsWith('zoom_')) args.zoom_speed = zoomSpeed;
    return args;
  }

  async function sendCommand(routeIndex, command, args = {}) {
    try {
      if (routeIndex === null || Number.isNaN(routeIndex)) return alert('Select a camera first.');
      showStatus(`Sending ${command}…`, true);
      const result = await request('/api/command', {
        method: 'POST', body: JSON.stringify({ route_index: routeIndex, command, args }),
      });
      if (command === 'menu_open') setOsdActive(true);
      if (command === 'menu_close') setOsdActive(false);
      showStatus(result.ok ? `${command} sent` : `${command} failed`, Boolean(result.ok));
      await loadDiagnostics();
      if (!result.ok) alert(`${command} failed: ${result.detail || 'unknown error'}`);
    } catch (err) {
      showStatus('Command failed', false);
      alert(`Command failed: ${err.message}`);
    }
  }

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
      try { await request('/api/bridge/restart', { method: 'POST', body: '{}' }); } catch (_) {}
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
      $('#event-log').textContent = `Diagnostics unavailable: ${err.message}`;
    }
  }

  async function resetDiagnostics() {
    await request('/api/diagnostics/reset', { method: 'POST', body: '{}' });
    showStatus('Diagnostics reset', true);
    await loadDiagnostics();
  }

  function bindEvents() {
    $$('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        $$('.tab-btn').forEach((b) => b.classList.remove('active'));
        $$('.tab-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
      });
    });

    $$('.preset-page-btn').forEach((btn) => btn.addEventListener('click', () => {
      state.presetPage = Number(btn.dataset.page);
      renderPresets();
    }));
    $('#btn-edit-presets')?.addEventListener('click', () => {
      state.presetEditMode = !state.presetEditMode;
      renderPresets();
    });

    $('#btn-save-bridge-ip')?.addEventListener('click', () => saveBridgeIP());

    $('#routes-table tbody')?.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      const row = button.closest('tr');
      const index = Number(row.dataset.index);
      if (button.dataset.action === 'save-route') saveRoute(index).catch((err) => {
        showStatus('Save failed', false);
        alert(`Save failed: ${err.message}`);
      });
      if (button.dataset.action === 'test-route') testRoute(index);
    });

    $$('.controls button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const routeIndex = Number($('#manual-camera-select')?.value);
        const command = btn.dataset.cmd === 'autofocus' ? 'autofocus_toggle' : btn.dataset.cmd;
        sendCommand(routeIndex, command, commandArgs(command));
      });
    });

    $('#btn-save-preset')?.addEventListener('click', () => {
      const routeIndex = Number($('#manual-camera-select')?.value);
      const preset = Number($('#save-preset-num').value);
      sendCommand(routeIndex, 'preset_save', { preset });
    });

    $('#btn-recall-preset')?.addEventListener('click', () => {
      const routeIndex = Number($('#manual-camera-select')?.value);
      const preset = Number($('#recall-preset-num').value);
      sendCommand(routeIndex, 'preset_recall', { preset });
    });

    $('#btn-export')?.addEventListener('click', () => exportConfig().catch((err) => {
      showStatus('Export failed', false);
      alert(`Export failed: ${err.message}`);
    }));
    $('#btn-import')?.addEventListener('click', () => $('#import-file')?.click());
    $('#import-file')?.addEventListener('change', (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      importConfig(file).catch((err) => {
        showStatus('Import failed', false);
        alert(`Import failed: ${err.message}`);
      }).finally(() => { event.target.value = ''; });
    });
    $('#btn-reset-routes')?.addEventListener('click', () => resetDiagnostics().catch((err) => {
      showStatus('Reset failed', false);
      alert(`Reset failed: ${err.message}`);
    }));
  }

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
})();
