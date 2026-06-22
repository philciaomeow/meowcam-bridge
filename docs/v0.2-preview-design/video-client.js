/* ============================================================================
   MeowCam Bridge — v0.2 browser video client (reference implementation)
   ----------------------------------------------------------------------------
   Two interchangeable transports for one job: show a live camera pane in the UI.

   1. MjpegImgView  — MJPEG multipart/x-mixed-replace in an <img>. Zero-dep,
                       the v0.2 baseline. Has a watchdog because <img> never fires
                       onload on replace frames and stalls silently.

   2. WsFrameView   — WebSocket binary frames drawn to <canvas>. The upgrade
                       path: one multiplexed connection for all cameras, per-pane
                       fps/quality control, real stall detection. Drop-in later.

   Both render into the SAME .video-pane markup used by mockup.html, so swapping
   the mockup's test-pattern generator for a live feed is a one-line change:

       const view = new MjpegImgView(pane, 0);   // cam index 0
       view.start();
       // ...later
       view.stop();

   See RESEARCH_FINDINGS.md §2 and §4 for the why behind each transport.
   ============================================================================ */

/* ----------------------------- shared base -------------------------------- */
class CameraView {
  /** @param {HTMLElement} pane  .video-pane element (owns canvas/img/overlays)
   *  @param {number} camIndex   route index (0-7) */
  constructor(pane, camIndex) {
    this.pane = pane;
    this.cam = camIndex;
    this.stalled = false;
    this._watchdog = null;
    this._lastFrame = performance.now();
  }

  /** Called by subclasses whenever a real frame lands. */
  _onFrame() {
    this._lastFrame = performance.now();
    if (this.stalled) {
      this.stalled = false;
      this.pane.classList.remove('stalled');
    }
  }

  /** Start the stall watchdog. If no frame arrives within `timeoutMs`, flip the
   *  pane into its 'stalled' overlay state. This is the single most important
   *  piece for MJPEG-in-<img>, which freezes silently on disconnect. */
  _startWatchdog(timeoutMs = 3000) {
    this._stopWatchdog();
    this._watchdog = setInterval(() => {
      const idle = performance.now() - this._lastFrame;
      if (idle > timeoutMs && !this.stalled) {
        this.stalled = true;
        this.pane.classList.add('stalled');
        this._onStall();
      }
    }, 1000);
  }
  _stopWatchdog() {
    if (this._watchdog) { clearInterval(this._watchdog); this._watchdog = null; }
  }
  _onStall() { /* subclasses override to attempt reconnect */ }

  /** Ensure the pane is allowed to show video (not in 'off' state). */
  _ensureOn() { this.pane.classList.remove('off'); }

  start() {}
  stop() { this._stopWatchdog(); }
}

/* ==========================================================================
   1. MJPEG in <img>  — the v0.2 baseline
   --------------------------------------------------------------------------
   - Point an <img> at /api/video/feed/{index}?fps=&quality=&width=
   - Append a cache-busting query on (re)load so the browser opens a fresh
     connection rather than reusing a dead keep-alive socket.
   - The watchdog reloads the src if no frame lands for N seconds.
   - Limitation (by design): cannot set Authorization headers on <img src>.
     The bridge is local/trusted; rely on same-origin or a path token (?t=).
   ========================================================================== */
class MjpegImgView extends CameraView {
  constructor(pane, camIndex, opts = {}) {
    super(pane, camIndex);
    this.opts = { fps: 8, quality: 60, width: 480, ...opts };
    this._img = null;
    this._pollTimer = null;
  }

  start() {
    this._ensureOn();
    // Inject an <img> if the pane only has a canvas (mockup layout).
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

    // Cross-browser 'first frame arrived' detection.
    // Chrome never fires onload on multipart replace parts; Firefox does on the
    // first. Either way we arm the watchdog and let a tiny interval poll the
    // naturalWidth to confirm pixels are actually flowing.
    img.addEventListener('load', () => this._onFrame());
    img.addEventListener('error', () => this._scheduleReconnect());

    this._loadSrc();
    this._startWatchdog(3000);

    // Cheap frame-presence poll: when the decoded image has real dimensions and
    // is not the broken-image icon, count it as a frame.
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

  _onStall() {
    // Stall -> force a fresh connection. The old src URL may be on a dead socket.
    this._scheduleReconnect();
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnect);
    this._reconnect = setTimeout(() => {
      console.warn(`[MjpegImgView] cam ${this.cam} reconnecting`);
      this._loadSrc();
    }, 1200);
  }

  stop() {
    super.stop();
    clearTimeout(this._reconnect);
    clearInterval(this._pollTimer);
    if (this._img) {
      // Tearing down: set src to '' then '' again forces Chrome to abort the
      // underlying multipart connection (just nulling .src can leave it open).
      this._img.src = '';
      this._img.removeAttribute('src');
    }
  }
}

/* ==========================================================================
   2. WebSocket frames -> <canvas>  — the upgrade path (not wired in v0.2)
   --------------------------------------------------------------------------
   - ONE multiplexed ws connection carries all cameras (sidesteps the 6-stream
     HTTP/1.1 connection cap entirely).
   - Each binary message is a small header + JPEG. Header layout (little-endian):
        u8  cam_index
        u16 width, u16 height
        u32 timestamp_ms
        ...then JPEG bytes
   - Client draws only frames for the camera it owns.
   - On connect, send a JSON control message to subscribe + throttle:
        { cam: 0, fps: 8, quality: 60 }
   ========================================================================== */
class WsFrameView extends CameraView {
  // Shared singleton socket per page — all WsFrameView instances multiplex here.
  static _socket = null;
  static _subscribers = new Map(); // cam -> Set<WsFrameView>

  constructor(pane, camIndex, opts = {}) {
    super(pane, camIndex);
    this.opts = { fps: 8, quality: 60, ...opts };
    this._ctx = null;
  }

  start() {
    this._ensureOn();
    let canvas = this.pane.querySelector('canvas');
    if (!canvas) {
      const img = this.pane.querySelector('img.mjpeg');
      if (img) img.remove();
      canvas = document.createElement('canvas');
      this.pane.prepend(canvas);
    }
    this._ctx = canvas.getContext('2d');
    WsFrameView._subscribe(this);
    this._startWatchdog(3000);
  }

  stop() {
    super.stop();
    WsFrameView._unsubscribe(this);
  }

  static _subscribe(view) {
    const subs = WsFrameView._subscribers;
    if (!subs.has(view.cam)) subs.set(view.cam, new Set());
    subs.get(view.cam).add(view);
    WsFrameView._ensureSocket();
    WsFrameView._send({ type: 'subscribe', cam: view.cam, fps: view.opts.fps, quality: view.opts.quality });
  }
  static _unsubscribe(view) {
    const subs = WsFrameView._subscribers;
    subs.get(view.cam)?.delete(view);
    if (subs.get(view.cam)?.size === 0) {
      subs.delete(view.cam);
      WsFrameView._send({ type: 'unsubscribe', cam: view.cam });
    }
  }

  static _ensureSocket() {
    if (WsFrameView._socket && WsFrameView._socket.readyState <= 1) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/video`);
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') return; // ignore server text messages
      const buf = new DataView(ev.data);
      const cam = buf.getUint8(0);
      const subs = WsFrameView._subscribers.get(cam);
      if (!subs) return;
      // skip header, draw the JPEG onto each subscribed canvas
      const headerLen = 1 + 2 + 2 + 4;
      const blob = new Blob([ev.data.slice(headerLen)], { type: 'image/jpeg' });
      createImageBitmap(blob).then((bmp) => {
        subs.forEach((v) => {
          const c = v._ctx.canvas;
          if (c.width !== bmp.width) c.width = bmp.width;
          if (c.height !== bmp.height) c.height = bmp.height;
          v._ctx.drawImage(bmp, 0, 0);
          v._onFrame();
        });
        bmp.close();
      });
    };
    ws.onopen = () => {
      // re-subscribe everything after a reconnect
      WsFrameView._subscribers.forEach((set, cam) => {
        set.forEach((v) => WsFrameView._send({ type: 'subscribe', cam, fps: v.opts.fps, quality: v.opts.quality }));
      });
    };
    ws.onclose = () => {
      // mark all panes stalled; _ensureSocket will reconnect on next subscribe
      WsFrameView._subscribers.forEach((set) => set.forEach((v) => v.pane.classList.add('stalled')));
      WsFrameView._socket = null;
    };
    WsFrameView._socket = ws;
  }
  static _send(obj) {
    const ws = WsFrameView._socket;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }
}

/* ----------------------------- export ------------------------------------- */
window.MeowCamVideo = { CameraView, MjpegImgView, WsFrameView };

/* ---- minimal usage example (drop into mockup.html in place of attachFeed) ----
   function attachFeed(camIndex, pane) {
     pane.__view = new MjpegImgView(pane, camIndex, { fps: 8, quality: 60, width: 480 });
     pane.__view.start();
   }
   function detachFeed(camIndex) {
     // find the pane owning this view — in the mockup, pane.dataset.cam === String(camIndex)
     document.querySelectorAll(`.video-pane[data-cam="${camIndex}"]`).forEach(p => {
       if (p.__view) { p.__view.stop(); delete p.__view; }
     });
   }
--------------------------------------------------------------------------- */
