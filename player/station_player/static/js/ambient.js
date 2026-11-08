// ---------- ambient mode ----------------------------------------------------
// A calm scene filling the screen, with its own sound: waves on a shore,
// rain on the window, a stream, birds at dawn, a summer night, a fire or a
// fish tank. A scene plays a real recording where the app has one
// (static/ambient/sounds.json lists them); otherwise, and until it has
// loaded, the sound is made here as it plays. It plays alone or quietly
// under the music, and the sleep timer fades it out with everything else.
const AMBIENT_SCENES = [["waves", "Waves"], ["rain", "Rain"], ["stream", "Stream"], ["birds", "Birds"],
  ["night", "Night"], ["fire", "Fire"], ["fish", "Fish tank"]];
const AMB = { scene: store("ambient-scene") || "waves", volume: Number(store("ambient-volume") ?? 0.6),
  ctx: null, out: null, sound: null, raf: 0, t0: 0, hideTimer: null, things: [], wake: null, buffers: {} };

function openAmbient() {
  const box = $("#ambient");
  const canvas = el("canvas", { class: "ambient-canvas", "aria-hidden": "true" });
  const scenes = el("div", { class: "segmented", role: "group", "aria-label": "Scene" },
    ...AMBIENT_SCENES.map(([key, label]) => el("button", {
      type: "button", class: "seg", "data-scene": key, "aria-pressed": String(AMB.scene === key),
      onclick: () => setAmbientScene(key),
    }, label)));
  const vol = el("input", {
    type: "range", min: 0, max: 1, step: 0.01, value: AMB.volume, "aria-label": "Ambient volume",
    oninput: (e) => { AMB.volume = Number(e.target.value); store("ambient-volume", String(AMB.volume)); fillRange(e.target); ambientLevel(); },
  });
  fillRange(vol);
  box.replaceChildren(canvas,
    el("div", { class: "ambient-controls" },
      el("h2", { class: "sr-only" }, "Ambient"),
      scenes,
      el("label", { class: "ambient-volume" }, icon("volume"), vol),
      el("button", { type: "button", class: "btn", id: "btn-ambient-close", onclick: () => closeAmbient() }, "Close"),
      el("p", { class: "ambient-credit", hidden: true })));
  box.hidden = false;
  document.body.classList.add("ambient-on");
  setAmbientScene(AMB.scene);
  ambientNudge();
  refocus(`#ambient .seg[aria-pressed="true"]`);
  if (navigator.wakeLock) navigator.wakeLock.request("screen").then((w) => { AMB.wake = w; }).catch(() => {});
}

function closeAmbient() {
  const box = $("#ambient");
  if (box.hidden) return;
  stopAmbientSound();
  AMB.buffers = {};
  cancelAnimationFrame(AMB.raf);
  AMB.raf = 0;
  clearTimeout(AMB.hideTimer);
  box.hidden = true;
  box.replaceChildren();
  document.body.classList.remove("ambient-on");
  if (AMB.wake) { AMB.wake.release().catch(() => {}); AMB.wake = null; }
  refocus("#btn-ambient");
}

function setAmbientScene(key) {
  if (!AMBIENT_SCENES.some(([k]) => k === key)) key = "waves";
  AMB.scene = key;
  store("ambient-scene", key);
  for (const b of document.querySelectorAll("#ambient .seg")) b.setAttribute("aria-pressed", String(b.dataset.scene === key));
  AMB.things = [];
  AMB.t0 = performance.now();
  AMB.last = 0;
  $("#ambient .ambient-credit").hidden = true;
  stopAmbientSound();
  startAmbientSound(key);
  cancelAnimationFrame(AMB.raf);
  AMB.raf = requestAnimationFrame(drawAmbient);
  announce(`Ambient: ${AMBIENT_SCENES.find(([k]) => k === key)[1]}`);
}

// The controls fade out after a few still seconds, and come back on a
// move of the mouse or a key.
function ambientNudge() {
  const box = $("#ambient");
  box.classList.remove("still");
  clearTimeout(AMB.hideTimer);
  AMB.hideTimer = setTimeout(() => box.classList.add("still"), 4000);
}
$("#ambient").addEventListener("pointermove", ambientNudge);
$("#ambient").addEventListener("keydown", (e) => {
  ambientNudge();
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeAmbient(); }
});
$("#btn-ambient").addEventListener("click", openAmbient);

// ---- the sound

function ambientLevel() {
  if (AMB.out) AMB.out.gain.setTargetAtTime(AMB.volume * volMul.sleep * 0.5, AMB.ctx.currentTime, 0.1);
}

function noiseBuffer(ctx, seconds, brown = false) {
  const buf = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * seconds), ctx.sampleRate);
  const d = buf.getChannelData(0);
  let last = 0;
  for (let i = 0; i < d.length; i++) {
    const white = Math.random() * 2 - 1;
    d[i] = brown ? (last = (last + 0.02 * white) / 1.02) * 3.5 : white;
  }
  return buf;
}

function loopNoise(ctx, brown) {
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(ctx, 4, brown);
  src.loop = true;
  src.start();
  return src;
}

// A short tone that slides between pitches: a bird's chirp, a bubble.
function chirp(ctx, out, at, { from, to, length, level, type = "sine", attack = Math.min(0.012, length / 4) }) {
  const osc = ctx.createOscillator();
  const env = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(from, at);
  osc.frequency.exponentialRampToValueAtTime(to, at + length);
  env.gain.setValueAtTime(0.0001, at);
  env.gain.exponentialRampToValueAtTime(level, at + attack);
  env.gain.exponentialRampToValueAtTime(0.0001, at + length);
  osc.connect(env).connect(out);
  osc.start(at);
  osc.stop(at + length + 0.02);
}

// A short burst of filtered noise: a raindrop, a crackle in the fire.
function crackle(ctx, out, at, { freq, length, level, q = 1.5 }) {
  AMB.burst ??= noiseBuffer(ctx, 0.25);
  const src = ctx.createBufferSource();
  src.buffer = AMB.burst;
  const bp = ctx.createBiquadFilter();
  bp.type = "bandpass";
  bp.frequency.value = freq;
  bp.Q.value = q;
  const env = ctx.createGain();
  env.gain.setValueAtTime(level, at);
  env.gain.exponentialRampToValueAtTime(0.0001, at + length);
  src.connect(bp).connect(env).connect(out);
  src.start(at, Math.random() * 0.2);
  src.stop(at + length + 0.02);
}

// A filter on its own, for chaining.
function biquad(ctx, type, freq, q) {
  const f = ctx.createBiquadFilter();
  f.type = type;
  f.frequency.value = freq;
  if (q !== undefined) f.Q.value = q;
  return f;
}

function gainOf(ctx, level) {
  const g = ctx.createGain();
  g.gain.value = level;
  return g;
}

// Each scene's made-up sound: it starts its steady parts (added to
// `parts`, to be stopped later) and gives back a tick, called every tenth
// of a second, that schedules what comes and goes.
const AMBIENT_SOUNDS = {
  waves(ctx, out, parts) {
    // Noise through a low filter, rising and falling like the sea, with
    // the hiss of foam as each wave breaks.
    const src = loopNoise(ctx, false);
    const lp = biquad(ctx, "lowpass", 400);
    const swell = gainOf(ctx, 0);
    const foam = gainOf(ctx, 0);
    src.connect(lp).connect(swell).connect(out);
    src.connect(biquad(ctx, "highpass", 2800)).connect(foam).connect(out);
    parts.push(src);
    return () => {
      const s = ctx.currentTime;
      const wave = Math.max(0, Math.sin(s * 2 * Math.PI / 9) * 0.6 + Math.sin(s * 2 * Math.PI / 13.7) * 0.4);
      swell.gain.setTargetAtTime(0.08 + wave * 0.6, s, 0.25);
      lp.frequency.setTargetAtTime(380 + wave * 1400, s, 0.25);
      foam.gain.setTargetAtTime(wave ** 3 * 0.12, s, 0.4);
    };
  },
  rain(ctx, out, parts) {
    // A steady hiss that grows heavier and lighter, drops close by, and
    // now and then thunder far away.
    const src = loopNoise(ctx, false);
    const hiss = gainOf(ctx, 0.25);
    src.connect(biquad(ctx, "highpass", 500)).connect(biquad(ctx, "lowpass", 6500)).connect(hiss).connect(out);
    const low = loopNoise(ctx, true);
    const rumble = gainOf(ctx, 0);
    low.connect(biquad(ctx, "lowpass", 110)).connect(rumble).connect(out);
    parts.push(src, low);
    let next = ctx.currentTime;
    let thunder = ctx.currentTime + rand(25, 70);
    return () => {
      const s = ctx.currentTime;
      const heavy = 0.5 + 0.5 * Math.sin(s * 2 * Math.PI / 47);
      hiss.gain.setTargetAtTime(0.16 + heavy * 0.18, s, 1.5);
      next = Math.max(next, s);  // after a stall (a hidden window), from now, not a pile at once
      while (next < s + 0.3) {
        if (Math.random() < 0.3) {
          const f = rand(1800, 3200);
          chirp(ctx, out, next, { from: f, to: f * 0.55, length: rand(0.02, 0.05), level: rand(0.02, 0.06) });
        } else {
          crackle(ctx, out, next, { freq: rand(2500, 7000), length: rand(0.008, 0.03), level: rand(0.04, 0.16) });
        }
        next += rand(0.015, 0.2) * (1.4 - heavy * 0.8);
      }
      if (s > thunder) {
        rumble.gain.setTargetAtTime(rand(0.8, 1.6), s, 0.6);
        rumble.gain.setTargetAtTime(0, s + rand(1.5, 3), 2.5);
        thunder = s + rand(45, 140);
      }
    };
  },
  stream(ctx, out, parts) {
    // Water over stones: a low rush, a babble of narrow filters wandering
    // in pitch, and little gulps and blips.
    const src = loopNoise(ctx, false);
    const rush = loopNoise(ctx, true);
    rush.connect(biquad(ctx, "lowpass", 500)).connect(gainOf(ctx, 0.35)).connect(out);
    src.connect(biquad(ctx, "bandpass", 1100, 0.5)).connect(gainOf(ctx, 0.06)).connect(out);
    const babble = [0, 1, 2, 3].map(() => {
      const f = biquad(ctx, "bandpass", rand(400, 2000), 7);
      const g = gainOf(ctx, 0);
      src.connect(f).connect(g).connect(out);
      return { f, g };
    });
    parts.push(src, rush);
    let next = ctx.currentTime;
    return () => {
      const s = ctx.currentTime;
      for (const b of babble) {
        if (Math.random() < 0.5) continue;
        b.f.frequency.setTargetAtTime(rand(350, 2400), s, 0.04);
        b.g.gain.setTargetAtTime(rand(0.05, 0.4), s, 0.05);
      }
      next = Math.max(next, s);  // after a stall (a hidden window), from now, not a pile at once
      while (next < s + 0.3) {
        const f = rand(500, 1400);
        chirp(ctx, out, next, { from: f, to: f * rand(1.3, 2), length: rand(0.025, 0.07), level: rand(0.015, 0.05) });
        next += rand(0.04, 0.35);
      }
    };
  },
  birds(ctx, out, parts) {
    // A breeze, and birds: short phrases of chirps, each bird its own pitch.
    const air = loopNoise(ctx, false);
    air.connect(biquad(ctx, "bandpass", 900, 0.4)).connect(gainOf(ctx, 0.05)).connect(out);
    parts.push(air);
    const birds = [2600, 3400, 4300, 1900].map((pitch) => ({ pitch, next: ctx.currentTime + Math.random() * 3 }));
    return () => {
      for (const b of birds) {
        if (b.next > ctx.currentTime + 0.5) continue;
        let at = b.next;
        const notes = 2 + Math.floor(Math.random() * 6);
        for (let i = 0; i < notes; i++) {
          const up = Math.random() < 0.6;
          const f = b.pitch * (0.85 + Math.random() * 0.3);
          const length = 0.05 + Math.random() * 0.12;
          chirp(ctx, out, at, { from: up ? f * 0.7 : f * 1.3, to: up ? f * 1.25 : f * 0.8, length, level: 0.05 + Math.random() * 0.08 });
          at += length + 0.03 + Math.random() * 0.08;
        }
        b.next = at + 1.5 + Math.random() * 6;
      }
    };
  },
  night(ctx, out, parts) {
    // Warm still air, crickets each keeping their own time, and an owl
    // now and then.
    const air = loopNoise(ctx, true);
    air.connect(biquad(ctx, "lowpass", 280)).connect(gainOf(ctx, 0.18)).connect(out);
    parts.push(air);
    const crickets = [4400, 4750, 4100].map((pitch) => ({
      pitch, every: rand(0.45, 0.85), next: ctx.currentTime + rand(0, 1), rest: 0 }));
    let owl = ctx.currentTime + rand(8, 25);
    return () => {
      const s = ctx.currentTime;
      for (const c of crickets) {
        c.next = Math.max(c.next, s);
        while (c.next < s + 0.3) {
          if (c.next > c.rest) {
            for (let i = 0; i < 3; i++) {
              chirp(ctx, out, c.next + i * 0.045, { from: c.pitch, to: c.pitch * 0.97, length: 0.03, level: 0.025 });
            }
          }
          c.next += c.every * rand(0.95, 1.05);
          if (Math.random() < 0.02) c.rest = c.next + rand(2, 8);  // a pause
        }
      }
      if (s > owl) {
        chirp(ctx, out, s + 0.1, { from: 430, to: 400, length: 0.35, level: 0.07, attack: 0.08 });
        chirp(ctx, out, s + 0.75, { from: 440, to: 380, length: 0.7, level: 0.06, attack: 0.1 });
        owl = s + rand(20, 60);
      }
    };
  },
  fire(ctx, out, parts) {
    // The fire's low roar, flickering, and its crackles and pops.
    const low = loopNoise(ctx, true);
    const roar = gainOf(ctx, 0.5);
    low.connect(biquad(ctx, "lowpass", 380)).connect(roar).connect(out);
    const src = loopNoise(ctx, false);
    src.connect(biquad(ctx, "bandpass", 3200, 0.6)).connect(gainOf(ctx, 0.015)).connect(out);
    parts.push(low, src);
    let next = ctx.currentTime;
    return () => {
      const s = ctx.currentTime;
      roar.gain.setTargetAtTime(rand(0.35, 0.75), s, 0.12);
      next = Math.max(next, s);  // after a stall (a hidden window), from now, not a pile at once
      while (next < s + 0.3) {
        const burst = Math.random() < 0.2 ? Math.floor(rand(3, 7)) : 1;
        for (let i = 0; i < burst; i++) {
          crackle(ctx, out, next + i * rand(0.01, 0.03), { freq: rand(900, 4500), length: rand(0.004, 0.02), level: rand(0.1, 0.45), q: 0.8 });
        }
        if (Math.random() < 0.04) crackle(ctx, out, next, { freq: rand(500, 1200), length: 0.05, level: 0.7, q: 0.6 });
        next += rand(0.03, 0.45);
      }
    };
  },
  fish(ctx, out, parts) {
    // The tank's pump hum, and bubbles: quick upward pops.
    const hum = loopNoise(ctx, true);
    hum.connect(biquad(ctx, "lowpass", 260)).connect(gainOf(ctx, 0.5)).connect(out);
    parts.push(hum);
    let next = ctx.currentTime;
    return () => {
      next = Math.max(next, ctx.currentTime);
      while (next < ctx.currentTime + 0.3) {
        const stream = Math.random() < 0.15;
        for (let i = 0; i < (stream ? 6 : 1); i++) {
          const f = 380 + Math.random() * 700;
          chirp(ctx, out, next + i * 0.07, { from: f, to: f * 2.4, length: 0.05 + Math.random() * 0.05, level: 0.12 + Math.random() * 0.15 });
        }
        next += 0.12 + Math.random() * 0.6;
      }
    };
  },
};

// The real recordings: ambient/sounds.json names a file for a scene, with
// who recorded it and its licence. Decoded, one is tens of megabytes, so
// only the scene showing keeps its own.
let ambientFiles = null;
async function ambientRecording(ctx, key) {
  ambientFiles ??= fetch("/ambient/sounds.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  const info = (await ambientFiles)[key];
  if (!info || !info.file) return null;
  AMB.buffers = { [key]: AMB.buffers[key] };
  AMB.buffers[key] ??= fetch(`/ambient/${encodeURIComponent(info.file)}`)
    .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.arrayBuffer(); })
    .then((b) => ctx.decodeAudioData(b));
  return { buffer: await AMB.buffers[key], info };
}

// Plays a recording round and round, each time through fading into the
// next so the join can't be heard; the first time from somewhere in the
// middle, so it isn't always the same.
function loopRecording(ctx, buffer, out, parts) {
  const fade = Math.min(4, buffer.duration / 4);
  let at = ctx.currentTime + 0.05;
  let from = rand(0, buffer.duration / 2);
  return () => {
    // A few seconds ahead, as a window out of sight ticks only now and then.
    if (at < ctx.currentTime) at = ctx.currentTime;
    while (at < ctx.currentTime + 4) {
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      const env = ctx.createGain();
      const end = at + buffer.duration - from;
      env.gain.setValueAtTime(0, at);
      env.gain.linearRampToValueAtTime(1, at + fade);
      env.gain.setValueAtTime(1, end - fade);
      env.gain.linearRampToValueAtTime(0, end);
      src.connect(env).connect(out);
      src.start(at, from);
      src.stop(end + 0.05);
      src.onended = () => parts.splice(parts.indexOf(src) >>> 0, 1);
      parts.push(src);
      at = end - fade;
      from = 0;
    }
  };
}

function startAmbientSound(key) {
  if (!AMB.ctx) {
    try { AMB.ctx = new AudioContext(); } catch { return; }
  }
  const ctx = AMB.ctx;
  ctx.resume().catch(() => {});
  const out = gainOf(ctx, 0);
  out.connect(ctx.destination);
  AMB.out = out;
  const made = gainOf(ctx, 1);
  made.connect(out);
  const parts = [];
  const sound = { parts, out, ticks: [AMBIENT_SOUNDS[key](ctx, made, parts)], real: false };
  const tick = () => { for (const f of sound.ticks) f(); };
  tick();
  sound.timer = setInterval(tick, 100);
  AMB.sound = sound;
  ambientLevel();
  ambientRecording(ctx, key).then((rec) => {
    if (!rec || AMB.sound !== sound) return;
    // Swap the made-up sound for the recording, crossfading.
    const ownParts = parts.splice(0);
    const real = gainOf(ctx, 0);
    real.connect(out);
    sound.ticks = [loopRecording(ctx, rec.buffer, real, parts)];
    sound.ticks[0]();
    real.gain.setTargetAtTime(1, ctx.currentTime, 0.7);
    made.gain.setTargetAtTime(0, ctx.currentTime, 0.7);
    setTimeout(() => {
      for (const p of ownParts) { try { p.stop(); } catch { /* already */ } }
      made.disconnect();
    }, 5000);
    sound.real = true;
    const credit = $("#ambient .ambient-credit");
    const { title, by, licence } = rec.info;
    if (credit && (title || by)) {
      credit.textContent = [title || "Recording", by && `by ${by}`, licence && `(${licence})`].filter(Boolean).join(" ");
      credit.hidden = false;
    }
  }).catch(() => { /* it keeps the made-up sound */ });
}

function stopAmbientSound() {
  const sound = AMB.sound;
  if (!sound) return;
  AMB.sound = null;
  AMB.out = null;
  clearInterval(sound.timer);
  const ctx = AMB.ctx;
  sound.out.gain.setTargetAtTime(0, ctx.currentTime, 0.15);
  setTimeout(() => {
    for (const p of sound.parts.splice(0)) { try { p.stop(); } catch { /* already */ } }
    sound.out.disconnect();
  }, 900);
}

// ---- the picture

function drawAmbient(now) {
  AMB.raf = 0;
  const cv = document.querySelector("#ambient canvas");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  // Still frames for those who'd rather nothing moved.
  const t = calm.matches ? 0 : (now - AMB.t0) / 1000;
  ({ fish: drawFish, waves: drawSea, birds: drawDawn, rain: drawRain, fire: drawFire, night: drawNight, stream: drawStream })[AMB.scene](g, w, h, t, dpr);
  if (!calm.matches && !document.hidden) AMB.raf = requestAnimationFrame(drawAmbient);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !$("#ambient").hidden && !AMB.raf) AMB.raf = requestAnimationFrame(drawAmbient);
});

const rand = (a, b) => a + Math.random() * (b - a);

function drawSea(g, w, h, t) {
  const sky = g.createLinearGradient(0, 0, 0, h * 0.55);
  sky.addColorStop(0, "#1d2a52");
  sky.addColorStop(0.6, "#c4607a");
  sky.addColorStop(1, "#f6b06b");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  g.fillStyle = "rgba(255, 226, 170, .95)";
  g.beginPath();
  g.arc(w * 0.68, h * 0.5, Math.min(w, h) * 0.07, 0, Math.PI * 2);
  g.fill();
  const bands = [["#2a3f74", 0.5, 0.004, 0.25], ["#24366a", 0.58, 0.006, 0.4], ["#1c2c5c", 0.68, 0.008, 0.6], ["#16234d", 0.8, 0.011, 0.9]];
  for (const [color, top, amp, speed] of bands) {
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w; x += 8) {
      const y = h * top + Math.sin(x / w * 9 + t * speed) * h * amp * 4 + Math.sin(x / w * 23 - t * speed * 1.7) * h * amp;
      g.lineTo(x, y);
    }
    g.lineTo(w, h);
    g.fill();
  }
  // The sun's glitter on the water.
  g.fillStyle = "rgba(255, 220, 160, .3)";
  for (let i = 0; i < 60; i++) {
    const y = h * (0.52 + (i / 60) * 0.42);
    const spread = w * (0.015 + (i / 60) * 0.08);
    const x = w * 0.68 + Math.sin(i * 12.9 + t * 0.8) * spread;
    g.fillRect(x - spread * 0.4, y, spread * (0.3 + 0.5 * Math.abs(Math.sin(i * 7.3 + t))), 1.5);
  }
}

function drawFish(g, w, h, t, dpr) {
  const water = g.createLinearGradient(0, 0, 0, h);
  water.addColorStop(0, "#0f4f6e");
  water.addColorStop(1, "#06192b");
  g.fillStyle = water;
  g.fillRect(0, 0, w, h);
  // Light from above, swaying.
  g.fillStyle = "rgba(160, 230, 255, .05)";
  for (let i = 0; i < 5; i++) {
    const x = w * (0.1 + i * 0.2) + Math.sin(t * 0.3 + i) * w * 0.03;
    g.beginPath();
    g.moveTo(x - w * 0.02, 0);
    g.lineTo(x + w * 0.02, 0);
    g.lineTo(x + w * 0.09, h);
    g.lineTo(x - w * 0.03, h);
    g.fill();
  }
  // Weed on the bottom.
  g.strokeStyle = "#1f6b4a";
  g.lineWidth = 6 * dpr;
  g.lineCap = "round";
  for (let i = 0; i < 14; i++) {
    const x = w * (i + 0.5) / 14;
    g.beginPath();
    g.moveTo(x, h);
    g.quadraticCurveTo(x + Math.sin(t * 0.8 + i) * 30 * dpr, h * 0.86, x + Math.sin(t * 0.6 + i * 2) * 18 * dpr, h * (0.7 + (i % 3) * 0.05));
    g.stroke();
  }
  if (!AMB.things.length) {
    const colors = ["#ff8a3d", "#ffd23f", "#4fd1c5", "#f25f5c", "#a78bfa", "#ffb4a2"];
    for (let i = 0; i < 9; i++) {
      AMB.things.push({ kind: "fish", x: rand(0, w), y: rand(h * 0.15, h * 0.75), size: rand(14, 34) * dpr,
        speed: rand(20, 60) * dpr * (Math.random() < 0.5 ? -1 : 1), color: colors[i % colors.length], phase: rand(0, 6) });
    }
    for (let i = 0; i < 40; i++) AMB.things.push({ kind: "bubble", x: rand(0, w), y: rand(0, h), r: rand(1.5, 5) * dpr, speed: rand(30, 80) * dpr });
  }
  const dt = AMB.last ? Math.min(0.1, t - AMB.last) : 0;
  AMB.last = t;
  for (const f of AMB.things) {
    if (f.kind === "bubble") {
      f.y -= f.speed * dt;
      if (f.y < -10) { f.y = h + 10; f.x = rand(0, w); }
      g.strokeStyle = "rgba(200, 240, 255, .55)";
      g.lineWidth = 1.2 * dpr;
      g.beginPath();
      g.arc(f.x + Math.sin(t * 2 + f.r) * 4 * dpr, f.y, f.r, 0, Math.PI * 2);
      g.stroke();
      continue;
    }
    f.x += f.speed * dt;
    if (f.x > w + 60 * dpr) f.x = -60 * dpr;
    if (f.x < -60 * dpr) f.x = w + 60 * dpr;
    const y = f.y + Math.sin(t * 0.9 + f.phase) * 10 * dpr;
    const dir = Math.sign(f.speed);
    const s = f.size;
    const wag = Math.sin(t * 6 + f.phase) * 0.25;
    g.fillStyle = f.color;
    g.beginPath();
    g.ellipse(f.x, y, s, s * 0.55, 0, 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.moveTo(f.x - dir * s * 0.8, y);
    g.lineTo(f.x - dir * s * 1.6, y - s * (0.5 + wag));
    g.lineTo(f.x - dir * s * 1.6, y + s * (0.5 - wag));
    g.fill();
    g.fillStyle = "#0b1a26";
    g.beginPath();
    g.arc(f.x + dir * s * 0.5, y - s * 0.12, s * 0.1, 0, Math.PI * 2);
    g.fill();
  }
}

function drawDawn(g, w, h, t, dpr) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#2b2d5c");
  sky.addColorStop(0.45, "#a45a7a");
  sky.addColorStop(0.75, "#f4a261");
  sky.addColorStop(1, "#f9d38a");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // The sun, rising a little over a few minutes.
  const rise = Math.min(1, t / 300);
  g.fillStyle = "rgba(255, 236, 190, .95)";
  g.beginPath();
  g.arc(w * 0.3, h * (0.78 - rise * 0.12), Math.min(w, h) * 0.08, 0, Math.PI * 2);
  g.fill();
  const hills = [["#5b3a5e", 0.72, 0.05, 2.3], ["#3d2a4a", 0.8, 0.06, 1.6], ["#251a33", 0.9, 0.04, 3.1]];
  for (const [color, top, amp, freq] of hills) {
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w; x += 10) g.lineTo(x, h * top + Math.sin(x / w * Math.PI * freq + top * 9) * h * amp);
    g.lineTo(w, h);
    g.fill();
  }
  if (!AMB.things.length) {
    for (let i = 0; i < 11; i++) {
      AMB.things.push({ x: rand(-w * 0.2, w), y: rand(h * 0.12, h * 0.55), size: rand(7, 15) * dpr, speed: rand(25, 55) * dpr, phase: rand(0, 6) });
    }
  }
  const dt = AMB.last ? Math.min(0.1, t - AMB.last) : 0;
  AMB.last = t;
  g.strokeStyle = "#1d1426";
  g.lineWidth = 2.2 * dpr;
  g.lineCap = "round";
  for (const b of AMB.things) {
    b.x += b.speed * dt;
    if (b.x > w + 40 * dpr) { b.x = -40 * dpr; b.y = rand(h * 0.12, h * 0.55); }
    const y = b.y + Math.sin(t * 0.7 + b.phase) * 8 * dpr;
    const flap = Math.sin(t * 7 + b.phase) * 0.6;
    g.beginPath();
    g.moveTo(b.x - b.size, y - b.size * flap);
    g.quadraticCurveTo(b.x - b.size * 0.4, y - b.size * 0.3, b.x, y);
    g.quadraticCurveTo(b.x + b.size * 0.4, y - b.size * 0.3, b.x + b.size, y - b.size * flap);
    g.stroke();
  }
}

// The same "random" number for the same i, for things that mustn't
// jump about from frame to frame (trees on a hill).
const steady = (i) => { const x = Math.sin(i * 12.9898) * 43758.5453; return x - Math.floor(x); };

// A soft round glow.
function glow(g, x, y, r, color, alpha) {
  const grad = g.createRadialGradient(x, y, 0, x, y, r);
  grad.addColorStop(0, `rgba(${color}, ${alpha})`);
  grad.addColorStop(1, `rgba(${color}, 0)`);
  g.fillStyle = grad;
  g.fillRect(x - r, y - r, r * 2, r * 2);
}

function stepOf(t) {
  const dt = AMB.last ? Math.min(0.1, t - AMB.last) : 0;
  AMB.last = t;
  return dt;
}

// Rain on the window at night, the town's lights blurred behind it.
function drawRain(g, w, h, t, dpr) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#0b1220");
  sky.addColorStop(1, "#1d2a3c");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  if (!AMB.things.length) {
    const lights = ["255, 196, 120", "255, 160, 90", "150, 200, 255", "255, 230, 170", "255, 120, 110"];
    for (let i = 0; i < 34; i++) {
      AMB.things.push({ kind: "light", x: rand(0, w), y: rand(h * 0.35, h * 0.95), r: rand(20, 70) * dpr,
        color: lights[i % lights.length], phase: rand(0, 6) });
    }
    for (let i = 0; i < 260; i++) {
      AMB.things.push({ kind: "drop", x: rand(0, w), y: rand(0, h), len: rand(10, 26) * dpr, speed: rand(500, 900) * dpr });
    }
    for (let i = 0; i < 45; i++) {
      AMB.things.push({ kind: "bead", x: rand(0, w), y: rand(0, h), r: rand(1.5, 4.5) * dpr, slide: Math.random() < 0.3 ? rand(8, 40) * dpr : 0 });
    }
  }
  const dt = stepOf(t);
  for (const l of AMB.things) {
    if (l.kind === "light") glow(g, l.x, l.y, l.r, l.color, 0.22 + 0.08 * Math.sin(t * 0.5 + l.phase));
  }
  g.strokeStyle = "rgba(190, 210, 235, .32)";
  g.lineWidth = 1.1 * dpr;
  g.beginPath();
  for (const d of AMB.things) {
    if (d.kind !== "drop") continue;
    d.y += d.speed * dt;
    d.x += d.speed * dt * 0.12;
    if (d.y > h + d.len) { d.y = -d.len; d.x = rand(-w * 0.1, w); }
    g.moveTo(d.x, d.y);
    g.lineTo(d.x - d.len * 0.12, d.y - d.len);
  }
  g.stroke();
  // Drops on the glass, a few running down.
  for (const b of AMB.things) {
    if (b.kind !== "bead") continue;
    if (b.slide) {
      b.y += b.slide * dt;
      if (b.y > h + 10) { b.y = -10; b.x = rand(0, w); }
    }
    g.fillStyle = "rgba(200, 220, 245, .18)";
    g.beginPath();
    g.arc(b.x, b.y, b.r, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "rgba(255, 255, 255, .45)";
    g.beginPath();
    g.arc(b.x - b.r * 0.3, b.y - b.r * 0.35, b.r * 0.3, 0, Math.PI * 2);
    g.fill();
  }
}

// A stream through the woods.
function drawStream(g, w, h, t, dpr) {
  const sky = g.createLinearGradient(0, 0, 0, h * 0.6);
  sky.addColorStop(0, "#a9cdb8");
  sky.addColorStop(1, "#e7dfb6");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // Two rows of trees, the far one paler.
  for (const [color, base, tall, n, seed] of [["#6f9a7f", 0.5, 0.22, 26, 1], ["#3f6b52", 0.58, 0.3, 18, 50]]) {
    g.fillStyle = color;
    g.fillRect(0, h * base, w, h * 0.2);
    for (let i = 0; i < n; i++) {
      const x = (i + steady(i + seed) * 0.8) / n * w;
      const th = h * tall * (0.6 + steady(i + seed + 7) * 0.4);
      const tw = th * 0.32;
      g.beginPath();
      g.moveTo(x - tw, h * base + 2);
      g.lineTo(x, h * base - th);
      g.lineTo(x + tw, h * base + 2);
      g.fill();
    }
  }
  g.fillStyle = "#2f5a3f";
  g.fillRect(0, h * 0.62, w, h);
  // The water, with banks that wobble a little.
  const top = (x) => h * 0.66 + Math.sin(x / w * 5) * h * 0.015;
  const bottom = (x) => h * 0.86 + Math.sin(x / w * 4 + 1) * h * 0.02;
  const water = g.createLinearGradient(0, h * 0.64, 0, h * 0.88);
  water.addColorStop(0, "#5f95a4");
  water.addColorStop(1, "#2e5f72");
  g.fillStyle = water;
  g.beginPath();
  for (let x = 0; x <= w; x += 10) g.lineTo(x, top(x));
  for (let x = w; x >= 0; x -= 10) g.lineTo(x, bottom(x));
  g.fill();
  if (!AMB.things.length) {
    for (let i = 0; i < 90; i++) {
      AMB.things.push({ kind: "ripple", x: rand(0, w), d: Math.random(), len: rand(14, 50) * dpr, speed: rand(40, 110) * dpr, phase: rand(0, 6) });
    }
    for (let i = 0; i < 5; i++) AMB.things.push({ kind: "rock", x: rand(0.08, 0.92) * w, d: rand(0.25, 0.8), r: rand(18, 40) * dpr });
  }
  const dt = stepOf(t);
  g.lineCap = "round";
  for (const r of AMB.things) {
    if (r.kind !== "ripple") continue;
    r.x += r.speed * dt;
    if (r.x > w + r.len) r.x = -r.len;
    const y = top(r.x) + (bottom(r.x) - top(r.x)) * r.d;
    g.strokeStyle = `rgba(225, 242, 250, ${0.25 + 0.3 * Math.abs(Math.sin(t * 1.5 + r.phase))})`;
    g.lineWidth = 1.6 * dpr;
    g.beginPath();
    g.moveTo(r.x, y);
    g.quadraticCurveTo(r.x + r.len / 2, y - 2 * dpr * Math.sin(t * 3 + r.phase), r.x + r.len, y);
    g.stroke();
  }
  for (const r of AMB.things) {
    if (r.kind !== "rock") continue;
    const y = top(r.x) + (bottom(r.x) - top(r.x)) * r.d;
    g.fillStyle = "#6c6a62";
    g.beginPath();
    g.ellipse(r.x, y, r.r, r.r * 0.55, 0, Math.PI, 0);
    g.fill();
    g.strokeStyle = "rgba(255, 255, 255, .7)";
    g.lineWidth = 2 * dpr;
    g.beginPath();
    g.ellipse(r.x - r.r * 0.15, y, r.r * (1.05 + 0.05 * Math.sin(t * 4 + r.x)), r.r * 0.3, 0, Math.PI * 0.55, Math.PI * 1.25);
    g.stroke();
  }
  // Grass in front, swaying.
  g.strokeStyle = "#24472f";
  g.lineWidth = 3 * dpr;
  for (let i = 0; i < 60; i++) {
    const x = steady(i + 99) * w;
    const tall = h * (0.06 + steady(i + 3) * 0.08);
    g.beginPath();
    g.moveTo(x, h);
    g.quadraticCurveTo(x, h - tall / 2, x + Math.sin(t * 0.9 + i) * 10 * dpr + 6 * dpr, h - tall);
    g.stroke();
  }
}

// A summer night: stars, the moon over the trees, fireflies.
function drawNight(g, w, h, t, dpr) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#04060f");
  sky.addColorStop(0.6, "#121b38");
  sky.addColorStop(1, "#22305a");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  if (!AMB.things.length) {
    for (let i = 0; i < 220; i++) {
      AMB.things.push({ kind: "star", x: rand(0, w), y: rand(0, h * 0.7), r: rand(0.4, 1.4) * dpr, speed: rand(0.5, 2.5), phase: rand(0, 6) });
    }
    for (let i = 0; i < 22; i++) {
      AMB.things.push({ kind: "fly", x: rand(0, w), y: rand(h * 0.6, h * 0.95), ax: rand(20, 70) * dpr, ay: rand(10, 30) * dpr,
        sx: rand(0.1, 0.35), sy: rand(0.15, 0.4), phase: rand(0, 6) });
    }
  }
  for (const s of AMB.things) {
    if (s.kind !== "star") continue;
    g.fillStyle = `rgba(255, 255, 240, ${0.35 + 0.65 * Math.abs(Math.sin(t * s.speed + s.phase))})`;
    g.fillRect(s.x, s.y, s.r * 2, s.r * 2);
  }
  const mr = Math.min(w, h) * 0.05;
  glow(g, w * 0.78, h * 0.2, mr * 5, "220, 225, 255", 0.18);
  g.fillStyle = "#f2eed8";
  g.beginPath();
  g.arc(w * 0.78, h * 0.2, mr, 0, Math.PI * 2);
  g.fill();
  // Hills, with pines along the nearer one.
  g.fillStyle = "#0d1428";
  g.beginPath();
  g.moveTo(0, h);
  for (let x = 0; x <= w; x += 10) g.lineTo(x, h * 0.72 + Math.sin(x / w * 3.3 + 1) * h * 0.04);
  g.lineTo(w, h);
  g.fill();
  const ridge = (x) => h * 0.8 + Math.sin(x / w * 2.4) * h * 0.035;
  g.fillStyle = "#060912";
  g.beginPath();
  g.moveTo(0, h);
  for (let x = 0; x <= w; x += 10) g.lineTo(x, ridge(x));
  g.lineTo(w, h);
  g.fill();
  for (let i = 0; i < 34; i++) {
    const x = (i + steady(i) * 0.9) / 34 * w;
    const th = h * (0.08 + steady(i + 40) * 0.12);
    const y = ridge(x) + 4;
    g.beginPath();
    g.moveTo(x - th * 0.22, y);
    g.lineTo(x, y - th);
    g.lineTo(x + th * 0.22, y);
    g.fill();
  }
  for (const f of AMB.things) {
    if (f.kind !== "fly") continue;
    const x = f.x + Math.sin(t * f.sx + f.phase) * f.ax;
    const y = f.y + Math.sin(t * f.sy * 1.3 + f.phase * 2) * f.ay;
    const on = Math.max(0, Math.sin(t * 0.9 + f.phase * 3));
    glow(g, x, y, 14 * dpr, "220, 255, 140", 0.15 + on * 0.6);
  }
}

// A log fire in the dark.
function drawFire(g, w, h, t, dpr) {
  g.fillStyle = "#070404";
  g.fillRect(0, 0, w, h);
  const cx = w / 2, base = h * 0.66, s = Math.min(w, h);
  const flick = 0.85 + 0.15 * Math.sin(t * 7) * Math.sin(t * 3.1);
  glow(g, cx, base - s * 0.12, s * 0.8 * flick, "255, 110, 30", 0.3);
  // The ground it sits on, lit by it.
  g.fillStyle = "#1a0e08";
  g.beginPath();
  g.ellipse(cx, base + s * 0.05, s * 0.42, s * 0.07, 0, 0, Math.PI * 2);
  g.fill();
  glow(g, cx, base, s * 0.3, "255, 120, 30", 0.55);
  // Flames: tongues in three colours, rounded at the foot, each swaying
  // and stretching to its own beat.
  g.globalCompositeOperation = "lighter";
  g.shadowBlur = 24 * dpr;
  for (const [color, tall, wide, alpha, n] of [["255, 60, 15", 0.4, 0.22, 0.32, 9], ["255, 130, 25", 0.3, 0.16, 0.45, 7], ["255, 215, 110", 0.18, 0.1, 0.6, 5]]) {
    g.fillStyle = `rgba(${color}, ${alpha})`;
    g.shadowColor = `rgba(${color}, .8)`;
    for (let i = 0; i < n; i++) {
      const k = i / (n - 1) - 0.5;
      const x = cx + k * wide * s * 1.6;
      const height = s * tall * (1 - Math.abs(k) * 1.1) * (0.7 + 0.3 * Math.sin(t * (4 + i * 0.7) + i * 1.7) * Math.sin(t * 2.3 + i));
      const lean = Math.sin(t * 1.6 + i) * s * 0.025;
      const half = wide * s * 0.2;
      const foot = base + half * 0.2;
      g.beginPath();
      g.moveTo(x - half, foot);
      g.bezierCurveTo(x - half * 1.1, foot - height * 0.45, x + lean - half * 0.2, foot - height * 0.75, x + lean, foot - height);
      g.bezierCurveTo(x + lean + half * 0.2, foot - height * 0.75, x + half * 1.1, foot - height * 0.45, x + half, foot);
      g.quadraticCurveTo(x, foot + half * 0.8, x - half, foot);
      g.fill();
    }
  }
  g.shadowBlur = 0;
  g.globalCompositeOperation = "source-over";
  // The logs, in front, their edges glowing.
  for (const [angle, dx] of [[-0.16, -0.06], [0.18, 0.06]]) {
    g.save();
    g.translate(cx + dx * s, base + s * 0.035);
    g.rotate(angle);
    const log = g.createLinearGradient(0, -s * 0.035, 0, s * 0.035);
    log.addColorStop(0, `rgba(190, 80, 25, ${0.7 * flick})`);
    log.addColorStop(0.35, "#3a2216");
    log.addColorStop(1, "#1c100a");
    g.fillStyle = log;
    g.beginPath();
    if (g.roundRect) g.roundRect(-s * 0.25, -s * 0.035, s * 0.5, s * 0.07, s * 0.035);
    else g.rect(-s * 0.25, -s * 0.035, s * 0.5, s * 0.07);
    g.fill();
    g.restore();
  }
  // Embers drifting up.
  if (!AMB.things.length) {
    for (let i = 0; i < 40; i++) AMB.things.push({ life: 1 });
  }
  const dt = stepOf(t);
  for (const e of AMB.things) {
    e.life += dt / (e.ttl || 1);
    if (e.life >= 1) {
      // Spread out to begin with, then each from the fire.
      Object.assign(e, { x: cx + rand(-0.12, 0.12) * s, y: base - rand(0, 0.1) * s, vx: rand(-20, 20) * dpr, vy: -rand(40, 110) * dpr,
        life: e.ttl ? 0 : rand(0, 1), ttl: rand(2, 5) });
    }
    e.x += (e.vx + Math.sin(t * 2 + e.ttl * 5) * 15 * dpr) * dt;
    e.y += e.vy * dt;
    g.fillStyle = `rgba(255, ${150 + Math.round(80 * (1 - e.life))}, 60, ${(1 - e.life) * 0.9})`;
    g.fillRect(e.x, e.y, 2 * dpr, 2 * dpr);
  }
}

