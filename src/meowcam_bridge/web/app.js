/* MeowCam Bridge — embedded web UI JavaScript (stub)

This is a minimal placeholder. Future iterations will:
- Poll /api/config and /api/routes
- Render routes dynamically
- Send manual control commands via POST /api/command
- Show diagnostics logs via WebSocket or SSE
*/

(function () {
  const tabs = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.tab-panel');

  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    });
  });

  async function loadRoutes() {
    try {
      const res = await fetch('/api/routes');
      const routes = await res.json();
      const tbody = document.querySelector('#routes-table tbody');
      if (!tbody) return;
      tbody.innerHTML = '';
      routes.forEach(r => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${r.index + 1}</td>
          <td><input type="checkbox" ${r.enabled ? 'checked' : ''} disabled></td>
          <td>${r.label}</td>
          <td>${r.incoming_port}</td>
          <td>${r.output_profile}</td>
          <td>${r.camera_ip}</td>
          <td>${r.camera_port}</td>
          <td>${r.status}</td>
          <td><button disabled>Test</button></td>
        `;
        tbody.appendChild(tr);
      });

      // Populate camera selects
      ['preset-camera-select', 'manual-camera-select'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        sel.innerHTML = '';
        routes.filter(r => r.enabled).forEach(r => {
          const opt = document.createElement('option');
          opt.value = r.index;
          opt.textContent = `${r.label} (${r.camera_ip})`;
          sel.appendChild(opt);
        });
      });
    } catch (e) {
      console.warn('Failed to load routes', e);
    }
  }

  // Initial load
  loadRoutes();
  setInterval(loadRoutes, 5000);
})();
