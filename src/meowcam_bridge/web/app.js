/* MeowCam Bridge — embedded web UI JavaScript

Wires the local operator UI to the FastAPI backend:
- editable route settings for up to eight cameras
- config import/export
- camera test buttons
- preset recall buttons generated from route labels
- manual PTZ/preset commands
- diagnostics polling and reset
- network interface dropdown for bridge IP selection
*/

(function () {
  const MAX_ROUTES = 8;
  const OUTPUT_PROFILES = [
    { value: 'sony_brc_h900_brbk_ip10', label: 'Sony BRC-H900 (BRBK-IP10)' },
  ];
  const DEFAULT_ROUTE = {
    enabled: false,
    label: 'Camera',
    incoming_port: 52380,
    input_profile: 'ptzoptics_pt_joy_g4_sony_visca_udp',
    output_profile: 'sony_brc_h900_brbk_ip10',
    camera_ip: '192.168.1.100',
    camera_port: 52381,
    status: 'unknown',
    preset_labels: Array.from({ length: 16 }, (_, i) => `Preset ${i + 1}`),
  };

  const state = {
    config: null,
    routes: [],
    selectedPresetRoute: null,
    selectedManualRoute: null,
    editing: false,  // true when user is actively editing a field
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function cloneDefaultRoute(index) {
    return {
      ...DEFAULT_ROUTE,
      label: `Camera ${index + 1}`,
      incoming_port: DEFAULT_ROUTE.incoming_port + index,
      preset_labels: [...DEFAULT_ROUTE.preset_labels],
    };
  }

  function showStatus(message, ok = true) {
    const badge = $('#status-indicator');
    if (!badge) return;
    badge.textContent = message;
    badge.classList.toggle('ok', ok);
    badge.classList.toggle('err', !ok);
  }

  function safeText(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  async function request(path, options = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      data = await res.text();
    }
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
      padded.push({ ...cloneDefaultRoute(i), ...(existing || {}), index: i });
    }
    return padded;
  }

  async function loadNetworkInterfaces() {
    try {
      const ifaces = await request('/api/network-interfaces');
      const sel = $('#bridge-ip-select');
      if (!sel) return;
      const currentVal = sel.value;
      sel.innerHTML = '<option value="0.0.0.0">0.0.0.0 (all interfaces)</option>';
      (ifaces || []).forEach((iface) => {
        const opt = document.createElement('option');
        opt.value = iface.ip;
        opt.textContent = `${iface.ip} (${iface.name})`;
        sel.appendChild(opt);
      });
      if (currentVal) sel.value = currentVal;
    } catch (e) {
      // endpoint may not exist yet — silently ignore
    }
  }

  async function loadConfig() {
    state.config = await request('/api/config');
    if (state.config.error) throw new Error(state.config.error);
    // Set bridge IP dropdown
    const bridgeSel = $('#bridge-ip-select');
    const bridgeManual = $('#bridge-ip-manual');
    const currentIP = state.config.bridge_ip || '0.0.0.0';
    if (bridgeSel) {
      // Check if current IP is in dropdown
      let found = false;
      for (const opt of bridgeSel.options) {
        if (opt.value === currentIP) { found = true; break; }
      }
      if (!found && currentIP !== '0.0.0.0') {
        // Add it as a custom option
        const opt = document.createElement('option');
        opt.value = currentIP;
        opt.textContent = currentIP;
        bridgeSel.appendChild(opt);
      }
      bridgeSel.value = currentIP;
    }
    if (bridgeManual) bridgeManual.value = '';
    // Set controller profile
    const firstRoute = (state.config.routes || [])[0] || DEFAULT_ROUTE;
    $('#controller-profile').value = firstRoute.input_profile || DEFAULT_ROUTE.input_profile;
    await loadRoutes();
  }

  async function loadRoutes() {
    // Don't reload routes while user is editing
    if (state.editing) return;
    const routes = await request('/api/routes');
    state.routes = normaliseRoutes(routes);
    renderRoutes();
    renderCameraSelects();
    renderPresets();
    showStatus('Ready', true);
  }

  function routeFromRow(row) {
    const presetLabels = state.routes[Number(row.dataset.index)]?.preset_labels || DEFAULT_ROUTE.preset_labels;
    return {
      enabled: row.querySelector('[data-field="enabled"]').checked,
      label: row.querySelector('[data-field="label"]').value.trim() || `Camera ${Number(row.dataset.index) + 1}`,
      incoming_port: Number(row.querySelector('[data-field="incoming_port"]').value),
      input_profile: row.querySelector('[data-field="input_profile"]').value.trim() || DEFAULT_ROUTE.input_profile,
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
    showStatus(`Saved ${payload.label}`, true);
    await loadRoutes();
  }

  async function testRoute(index) {
    try {
      showStatus(`Testing camera ${index + 1}…`, true);
      const result = await request(`/api/routes/${index}/test`, {
        method: 'POST',
        body: JSON.stringify({ type: 'version' }),
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
    const profileOptions = OUTPUT_PROFILES.map(
      (p) => `<option value="${p.value}">${p.label}</option>`
    ).join('');
    state.routes.forEach((r) => {
      const tr = document.createElement('tr');
      tr.dataset.index = r.index;
      tr.dataset.status = r.status || 'unknown';
      const selectedProfile = r.output_profile || DEFAULT_ROUTE.output_profile;
      tr.innerHTML = `
        <td>${r.index + 1}</td>
        <td><input data-field="enabled" type="checkbox" ${r.enabled ? 'checked' : ''}></td>
        <td><input data-field="label" type="text" value="${escapeHtml(r.label)}"></td>
        <td><input data-field="incoming_port" type="number" min="1" max="65535" value="${r.incoming_port}"></td>
        <td><select data-field="output_profile">${profileOptions}</select></td>
        <td><input data-field="camera_ip" type="text" value="${escapeHtml(r.camera_ip)}"></td>
        <td><input data-field="camera_port" type="number" min="1" max="65535" value="${r.camera_port}"></td>
        <td><span class="route-status ${escapeHtml(r.status || 'unknown')}">${escapeHtml(r.status || 'unknown')}</span></td>
        <td class="row-actions">
          <input data-field="input_profile" type="hidden" value="${escapeHtml(r.input_profile)}">
          <button data-action="save-route">Save</button>
          <button data-action="test-route">Test</button>
        </td>
      `;
      // Set the output profile dropdown value after innerHTML
      const sel = tr.querySelector('[data-field="output_profile"]');
      if (sel) sel.value = selectedProfile;
      // Mark editing on focus
      tr.querySelectorAll('input, select').forEach((el) => {
        el.addEventListener('focus', () => { state.editing = true; });
        el.addEventListener('change', () => { state.editing = true; });
      });
      tbody.appendChild(tr);
    });
  }

  function renderCameraSelects() {
    const enabled = state.routes.filter((r) => r.enabled);
    ['preset-camera-select', 'manual-camera-select'].forEach((id) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      const prior = sel.value;
      sel.innerHTML = '';
      const list = enabled.length ? enabled : state.routes;
      list.forEach((r) => {
        const opt = document.createElement('option');
        opt.value = r.index;
        opt.textContent = `${r.index + 1}: ${r.label} (${r.camera_ip})${r.enabled ? '' : ' (disabled)'}`;
        sel.appendChild(opt);
      });
      if (prior && list.some((r) => String(r.index) === String(prior))) sel.value = prior;
    });
  }

  function selectedRoute(selectId) {
    const sel = document.getElementById(selectId);
    if (!sel || sel.value === '') return null;
    return Number(sel.value);
  }

  function renderPresets() {
    const grid = $('#preset-grid');
    if (!grid) return;
    const index = selectedRoute('preset-camera-select');
    const route = state.routes.find((r) => r.index === index) || state.routes[0];
    grid.innerHTML = '';
    if (!route) {
      grid.textContent = 'No camera routes configured.';
      return;
    }
    (route.preset_labels || DEFAULT_ROUTE.preset_labels).slice(0, 16).forEach((label, i) => {
      const btn = document.createElement('button');
      btn.className = 'preset-btn';
      btn.dataset.preset = i + 1;
      btn.textContent = `${i + 1}. ${label || `Preset ${i + 1}`}`;
      btn.addEventListener('click', () => sendCommand(route.index, 'preset_recall', { preset: i + 1 }));
      grid.appendChild(btn);
    });
  }

  async function sendCommand(routeIndex, command, args = {}) {
    try {
      showStatus(`Sending ${command}…`, true);
      const result = await request('/api/command', {
        method: 'POST',
        body: JSON.stringify({ route_index: routeIndex, command, args }),
      });
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
      const routeSummary = (diag.routes || [])
        .map((r) => `${r.index + 1}:${r.label}=${r.status}${r.enabled ? '' : '(off)'}`)
        .join('  ');
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

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;',
    }[ch]));
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

    $('#btn-save-bridge-ip')?.addEventListener('click', () => saveBridgeIP());

    $('#controller-profile')?.addEventListener('change', async (event) => {
      $$('#routes-table tbody tr').forEach((row) => {
        const hidden = row.querySelector('[data-field="input_profile"]');
        if (hidden) hidden.value = event.target.value;
      });
      showStatus('Controller profile staged; save route rows to persist', true);
    });

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

    $('#preset-camera-select')?.addEventListener('change', renderPresets);

    $$('.controls button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const routeIndex = selectedRoute('manual-camera-select');
        if (routeIndex === null) return alert('Select a camera first.');
        const command = btn.dataset.cmd === 'autofocus' ? 'autofocus_toggle' : btn.dataset.cmd;
        sendCommand(routeIndex, command);
      });
    });

    $('#btn-save-preset')?.addEventListener('click', () => {
      const routeIndex = selectedRoute('manual-camera-select');
      const preset = Number($('#save-preset-num').value);
      sendCommand(routeIndex, 'preset_save', { preset });
    });

    $('#btn-recall-preset')?.addEventListener('click', () => {
      const routeIndex = selectedRoute('manual-camera-select');
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
  // Only poll diagnostics (not routes) to avoid wiping edits
  setInterval(loadDiagnostics, 5000);
  // Poll routes only if not editing
  setInterval(() => { if (!state.editing) loadRoutes(); }, 10000);
})();