// ---------- volume levelling ----------------------------------------------

// Each track deck runs through its own gain, then a master gain for the
// volume slider. The audio graph is only built once levelling is turned
// on, and only from a click or key press (browsers keep audio made
// without one silent).
const LEVEL_TARGET = -16;            // LUFS every track is brought to
const MAX_BOOST_DB = 9;
const MAX_CUT_DB = -15;
const HEADROOM = 0.89;                // about -1 dB below full scale
// Browsers decode a whole file into memory to measure it, so only songs
// of ordinary length are measured; longer files need tags to be levelled.
const MEASURE_MAX_MS = 12 * 60 * 1000;
const MEASURE_MAX_BYTES = 60e6;
const L = {
  on: store("level") === "1",
  ctx: null,
  master: null,
  analyser: null,
  gains: [null, null],
  known: new Map(),      // track id -> {lufs, peak}, or null while unknown
  pending: new Map(),    // track id -> promise of the lookup
  measureQueue: [],
  measuring: false,
};

function ensureGraph() {
  if (!L.on) return;
  if (!L.ctx) {
    try {
      L.ctx = new AudioContext();
    } catch {
      return;
    }
    L.ctx.addEventListener("statechange", connectGraph);
  }
  if (L.ctx.state !== "running") L.ctx.resume().catch(() => {});
  connectGraph();
}

// The decks are only routed through the audio graph once it is running:
// routed into a context the browser holds suspended, they'd be silent.
function connectGraph() {
  const ctx = L.ctx;
  if (L.master) {
    if (ctx.state === "suspended" && S.mode === "track" && !audio.paused) ctx.resume().catch(() => {});
    return;
  }
  if (ctx.state !== "running") return;
  try {
    const master = ctx.createGain();
    master.connect(ctx.destination);
    L.analyser = ctx.createAnalyser();  // for the sound wave
    L.analyser.fftSize = 2048;
    master.connect(L.analyser);
    decks.forEach((d, i) => {
      const gain = ctx.createGain();
      ctx.createMediaElementSource(d).connect(gain).connect(master);
      L.gains[i] = gain;
    });
    L.master = master;
  } catch {
    return;
  }
  applyVolume();
  decks.forEach((d, i) => setDeckGain(i, d.dataset.trackId, false));
}

// ---------- sound wave ---------------------------------------------------

// A line beside the song title that moves with the sound. It listens to
// the radio relay's player (in an audio context of its own) and, with
// volume levelling on, to the library's audio graph; anything else, it
// rests flat.
const WAVE = { ctx: null, radio: null, buf: new Float32Array(2048), raf: 0, scale: 1 };
const calm = matchMedia("(prefers-reduced-motion: reduce)");

// Route the relay's player through the analyser, once the context runs
// (routed into a context the browser holds suspended, it would be silent).
function waveListen() {
  if (!WAVE.ctx) {
    try {
      WAVE.ctx = new AudioContext();
    } catch {
      return;
    }
    WAVE.ctx.addEventListener("statechange", routeRadio);
  }
  if (WAVE.ctx.state !== "running") WAVE.ctx.resume().catch(() => {});
  routeRadio();
}

function routeRadio() {
  const ctx = WAVE.ctx;
  if (WAVE.radio || ctx.state !== "running") return;
  try {
    const an = ctx.createAnalyser();
    an.fftSize = 2048;
    ctx.createMediaElementSource(radioRelay).connect(an).connect(ctx.destination);
    WAVE.radio = an;
  } catch { /* plays as it is; the wave rests */ }
}

function waveSource() {
  if (audio.paused || S.mode === "idle") return null;
  if (S.mode === "radio") return audio === radioRelay ? WAVE.radio : null;
  return L.master ? L.analyser : null;
}

function drawWave() {
  WAVE.raf = 0;
  const cv = $("#wave");
  if (!cv) return;
  cv.hidden = S.mode === "idle";
  if (cv.hidden) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.strokeStyle = getComputedStyle(cv).color;
  g.lineWidth = 1.5 * dpr;
  g.lineJoin = "round";
  const an = calm.matches ? null : waveSource();
  g.globalAlpha = an ? 1 : 0.4;
  g.beginPath();
  if (an) {
    const buf = WAVE.buf;
    an.getFloatTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i]));
    // Quiet or loud, the line fills its space: the scale eases towards
    // the level, so it doesn't jump on every beat.
    const want = Math.min(12, 0.9 / Math.max(peak, 0.02));
    WAVE.scale += (want - WAVE.scale) * 0.08;
    for (let x = 0; x <= w; x += dpr) {
      const v = buf[Math.floor((x / w) * (buf.length - 1))] * WAVE.scale;
      const y = h / 2 - Math.max(-1, Math.min(1, v)) * (h / 2 - g.lineWidth);
      x ? g.lineTo(x, y) : g.moveTo(x, y);
    }
  } else {
    g.moveTo(0, h / 2);
    g.lineTo(w, h / 2);
  }
  g.stroke();
  if (an && !document.hidden) WAVE.raf = requestAnimationFrame(drawWave);
}

function waveWake() {
  if (!WAVE.raf) WAVE.raf = requestAnimationFrame(drawWave);
}
document.addEventListener("visibilitychange", waveWake);
calm.addEventListener("change", waveWake);

function setLevelling(on) {
  L.on = on;
  store("level", on ? "1" : "0");
  if (on) ensureGraph();
  decks.forEach((d, i) => setDeckGain(i, d.dataset.trackId, true));
  if (on && S.mode === "track") {
    lookupLoudness(current());
    preloadNext();
  }
}

// The gain that brings a track to LEVEL_TARGET without clipping it.
function levelGain(trackId) {
  const l = L.on && trackId ? L.known.get(trackId) : null;
  if (!l || typeof l.lufs !== "number") return 1;
  const db = Math.max(MAX_CUT_DB, Math.min(MAX_BOOST_DB, LEVEL_TARGET - l.lufs));
  const gain = 10 ** (db / 20);
  if (gain <= 1) return gain;
  // A quiet track is only raised as far as its loudest peak allows (with
  // a little headroom: peaks measured at a low sample rate read low).
  // Without a known peak it is raised by 1 dB at most.
  const ceiling = l.peak ? Math.max(1, HEADROOM / l.peak) : 10 ** (1 / 20);
  return Math.min(gain, ceiling);
}

// `smooth`: true eases into the new level, a number is the easing time.
function setDeckGain(i, trackId, smooth) {
  const node = L.gains[i];
  if (!node || !L.master) return;
  const value = levelGain(trackId) * fadeMul[i];
  const now = L.ctx.currentTime;
  const ease = smooth === true ? 0.4 : smooth || 0;
  node.gain.cancelScheduledValues(now);
  if (ease) node.gain.setTargetAtTime(value, now, ease);
  else node.gain.setValueAtTime(value, now);
}

// Loudness from the player's server (tags, or an earlier measurement);
// unknown tracks are measured in the background.
function lookupLoudness(track) {
  if (!L.on || !track || !track.available) return Promise.resolve();
  if (L.known.has(track.id)) return Promise.resolve();
  if (L.pending.has(track.id)) return L.pending.get(track.id);
  const job = (async () => {
    try {
      const found = await api(`/api/loudness/${track.id}`);
      if (typeof found.lufs === "number") {
        loudnessKnown(track.id, found);
      } else if (track.duration_ms && track.duration_ms <= MEASURE_MAX_MS) {
        L.measureQueue.push(track);
        runMeasurements();
      } else {
        L.known.set(track.id, null);  // can't be measured: plays as it is
      }
    } catch { /* levelling is best effort */ } finally {
      L.pending.delete(track.id);
    }
  })();
  L.pending.set(track.id, job);
  return job;
}

function loudnessKnown(id, l) {
  L.known.set(id, { lufs: l.lufs, peak: l.peak || null });
  decks.forEach((d, i) => { if (d.dataset.trackId === id) setDeckGain(i, id, true); });
}

async function runMeasurements() {
  if (L.measuring) return;
  L.measuring = true;
  try {
    while (L.measureQueue.length && L.on) {
      const track = L.measureQueue.shift();
      if (L.known.has(track.id)) continue;
      try {
        const result = await measureTrack(track);
        if (result) {
          loudnessKnown(track.id, result);
          api("/api/loudness", { id: track.id, lufs: result.lufs, peak: result.peak }, { quiet: true }).catch(() => {});
        } else {
          L.known.set(track.id, null);  // silent or too short: don't try again
        }
      } catch {
        L.known.set(track.id, null);  // undecodable: plays unlevelled
      }
    }
  } finally {
    L.measuring = false;
  }
}

async function measureTrack(track) {
  const res = await fetch(`/audio/${track.id}`);
  if (!res.ok) return null;
  if (Number(res.headers.get("Content-Length")) > MEASURE_MAX_BYTES) return null;
  const bytes = await res.arrayBuffer();
  // Decoded at a low sample rate: plenty for loudness, a fraction of the memory.
  const decoded = await new OfflineAudioContext(1, 1, 22050).decodeAudioData(bytes);
  return integratedLoudness(decoded);
}

// Biquad coefficients of the ITU-R BS.1770 "K" weighting at rate `fs`
// (as in libebur128): a high shelf, then a high-pass.
function kWeighting(fs) {
  let f0 = 1681.974450955533;
  const G = 3.999843853973347;
  let Q = 0.7071752369554196;
  let K = Math.tan(Math.PI * f0 / fs);
  const Vh = 10 ** (G / 20);
  const Vb = Vh ** 0.4996667741545416;
  let a0 = 1 + K / Q + K * K;
  const shelf = {
    b: [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0],
    a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0],
  };
  f0 = 38.13547087602444;
  Q = 0.5003270373238773;
  K = Math.tan(Math.PI * f0 / fs);
  a0 = 1 + K / Q + K * K;
  const highpass = { b: [1, -2, 1], a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0] };
  return [shelf, highpass];
}

// Gated integrated loudness (LUFS) and sample peak of an AudioBuffer.
async function integratedLoudness(buffer) {
  const fs = buffer.sampleRate;
  const hop = Math.round(fs / 10);           // 100 ms
  const bins = Math.floor(buffer.length / hop);
  if (bins < 4) return null;
  const power = new Float64Array(bins);
  let peak = 0;
  const [f1, f2] = kWeighting(fs);
  const channels = Math.min(buffer.numberOfChannels, 2);
  for (let c = 0; c < channels; c++) {
    const x = buffer.getChannelData(c);
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0, z1 = 0, z2 = 0;
    const end = bins * hop;
    for (let start = 0; start < end; start += hop * 600) {  // a minute at a time
      const stop = Math.min(end, start + hop * 600);
      for (let i = start; i < stop; i++) {
        const v = x[i];
        const a = v < 0 ? -v : v;
        if (a > peak) peak = a;
        const y = f1.b[0] * v + f1.b[1] * x1 + f1.b[2] * x2 - f1.a[0] * y1 - f1.a[1] * y2;
        x2 = x1; x1 = v;
        const z = f2.b[0] * y + f2.b[1] * y1 + f2.b[2] * y2 - f2.a[0] * z1 - f2.a[1] * z2;
        y2 = y1; y1 = y;
        z2 = z1; z1 = z;
        power[(i / hop) | 0] += z * z;
      }
      await new Promise((r) => setTimeout(r, 0));  // keep the page responsive
    }
  }
  // 400 ms blocks overlapping by 75%, gated at -70 LUFS, then at 10 LU
  // below the loudness of what passed.
  const blocks = [];
  for (let j = 0; j + 4 <= bins; j++) {
    blocks.push((power[j] + power[j + 1] + power[j + 2] + power[j + 3]) / (4 * hop));
  }
  const lufs = (z) => -0.691 + 10 * Math.log10(z);
  const mean = (list) => list.reduce((s, z) => s + z, 0) / list.length;
  const loud = blocks.filter((z) => z > 0 && lufs(z) > -70);
  if (!loud.length) return null;
  const gate = lufs(mean(loud)) - 10;
  const kept = loud.filter((z) => lufs(z) > gate);
  const value = lufs(mean(kept));
  if (!isFinite(value)) return null;
  return { lufs: Math.round(value * 100) / 100, peak: peak > 0 ? Math.round(peak * 1e6) / 1e6 : null };
}

