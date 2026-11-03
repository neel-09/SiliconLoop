// Opening sequence: a cloud of copper sparks gathers, the sparks settle as vias, copper traces route
// themselves into a chip, a signal runs the loop around it, and the camera dives into the die where the
// wordmark appears. Canvas 2D, about 7 s, skippable with any key, click or the Skip button.
import { h } from './ui.js';

const C = { bg: '#050607', grid: 'rgba(237,240,228,0.06)', copper: '#d4894a', copperHi: '#ffc58f', gold: '#e3c06a',
  ink: '#edf0e4', die: '#0a0b0d' };
const T = { burst: 550, fly1: 1600, trace0: 1350, trace1: 2500, chip0: 2100, chip1: 2700,
  loop0: 2550, loop1: 3700, zoom0: 3600, zoom1: 4400, mark0: 4100, exit0: 5500, exit1: 6100 };

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const prog = (t, a, b) => clamp((t - a) / (b - a));
const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);
const easeOut = (x) => 1 - (1 - x) ** 3;
const easeIn = (x) => x * x * x;

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function glowSprite(color, r) {
  const c = document.createElement('canvas');
  c.width = c.height = r * 2;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, '#ffffff');
  grad.addColorStop(0.18, color);
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, r * 2, r * 2);
  return c;
}

function polyLength(pts) {
  const acc = [0];
  for (let i = 1; i < pts.length; i++) acc.push(acc[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return acc;
}

function pointAt(pts, acc, d) {
  if (d <= 0) return pts[0];
  for (let i = 1; i < pts.length; i++) {
    if (acc[i] >= d) {
      const f = (d - acc[i - 1]) / (acc[i] - acc[i - 1] || 1);
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f];
    }
  }
  return pts[pts.length - 1];
}

function strokePartial(ctx, pts, acc, upto) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) {
    if (acc[i] <= upto) ctx.lineTo(pts[i][0], pts[i][1]);
    else { const p = pointAt(pts, acc, upto); ctx.lineTo(p[0], p[1]); break; }
  }
  ctx.stroke();
}

/** Build the scene for a w x h viewport. */
function layout(w, h) {
  const r = rng(20261103);
  const cx = w / 2, cy = h * 0.47;
  const S = clamp(Math.min(w, h) * 0.2, 110, 210);
  const chip = { x: cx - S / 2, y: cy - S / 2, s: S };
  const N = 7, pinLen = S * 0.09, margin = S * 0.34;
  const loop = { x: chip.x - margin, y: chip.y - margin, s: S + margin * 2, r: margin * 0.9 };
  const traces = [];
  const sides = [[0, -1], [1, 0], [0, 1], [-1, 0]];
  sides.forEach(([dx, dy], side) => {
    for (let i = 0; i < N; i++) {
      const f = (i + 1) / (N + 1);
      const along = (f - 0.5) * S * 0.86;
      // pin tip on the chip edge
      const px = cx + dx * (S / 2 + pinLen) + (dy !== 0 ? along : 0);
      const py = cy + dy * (S / 2 + pinLen) + (dx !== 0 ? along : 0);
      const out1 = margin + S * 0.18 + r() * S * 0.25 + Math.abs(along) * 0.25;
      const bend = (along >= 0 ? 1 : -1) * (0.35 + r() * 0.9) * (Math.abs(along) / (S * 0.43) + 0.2);
      const d2 = (40 + r() * 120) * (Math.min(w, h) / 800 + 0.4);
      const d3 = (30 + r() * 170) * (Math.min(w, h) / 800 + 0.4);
      const p1 = [px + dx * out1, py + dy * out1];
      const perp = dx !== 0 ? [0, 1] : [1, 0];
      const p2 = [p1[0] + dx * d2 * 0.7 + perp[0] * d2 * bend, p1[1] + dy * d2 * 0.7 + perp[1] * d2 * bend];
      let p3 = [p2[0] + dx * d3, p2[1] + dy * d3];
      p3 = [clamp(p3[0], 24, w - 24), clamp(p3[1], 24, h - 24)];
      const pts = [p3, p2, p1, [px, py]]; // routed from the via inwards to the pin
      traces.push({ pts, acc: polyLength(pts), via: p3, pin: [px, py], side, i, delay: r() * 0.45, hits: 0, packets: [r(), r() * 0.5 + 0.5] });
    }
  });
  return { w, h, cx, cy, chip, loop, traces, pinLen, N };
}

function loopPath(loop) {
  const { x, y, s, r } = loop;
  const pts = [];
  const seg = (ax, ay, bx, by) => { for (let k = 0; k <= 12; k++) pts.push([ax + (bx - ax) * k / 12, ay + (by - ay) * k / 12]); };
  const arc = (ox, oy, a0) => { for (let k = 0; k <= 10; k++) { const a = a0 + (Math.PI / 2) * k / 10; pts.push([ox + Math.cos(a) * r, oy + Math.sin(a) * r]); } };
  seg(x + r, y, x + s - r, y); arc(x + s - r, y + r, -Math.PI / 2);
  seg(x + s, y + r, x + s, y + s - r); arc(x + s - r, y + s - r, 0);
  seg(x + s - r, y + s, x + r, y + s); arc(x + r, y + s - r, Math.PI / 2);
  seg(x, y + s - r, x, y + r); arc(x + r, y + r, Math.PI);
  return { pts, acc: polyLength(pts) };
}

/** A soft cloud of sparks around the centre, where the chip will appear. */
function sparkCloud(w, h) {
  const r = rng(7), out = [];
  const n = Math.round(clamp((w * h) / 1100, 600, 1400));
  for (let i = 0; i < n; i++) {
    const a = r() * Math.PI * 2, d = Math.sqrt(-2 * Math.log(r() + 1e-6)) * 0.5;
    out.push([w / 2 + Math.cos(a) * d * w * 0.16, h * 0.47 + Math.sin(a) * d * h * 0.14]);
  }
  return out;
}

export function playIntro() {
  return new Promise((resolve) => {
    const canvas = h('canvas.intro-canvas', { 'aria-hidden': 'true' });
    const letters = [...'SiliconLoop'].map((ch, i) => h('span', { style: { '--i': i } }, ch));
    const mark = h('div.intro-mark', h('h1.intro-word', { 'aria-label': 'SiliconLoop' }, letters),
      h('p.intro-tag', 'Imagination to silicon, proven in simulation.'),
      h('p.intro-built', 'Built with Muse Spark Open'));
    const skip = h('button.intro-skip', { type: 'button' }, 'Skip intro');
    const root = h('div.intro', { role: 'presentation' }, canvas, mark, skip);
    document.body.append(root);
    document.body.classList.add('intro-on');

    const ctx = canvas.getContext('2d');
    let scene, particles, loopP, dpr;
    const sprites = { copper: glowSprite(C.copper, 24), gold: glowSprite(C.gold, 40), white: glowSprite('#fff3dd', 60) };

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth, hgt = window.innerHeight;
      canvas.width = w * dpr; canvas.height = hgt * dpr;
      canvas.style.width = `${w}px`; canvas.style.height = `${hgt}px`;
      scene = layout(w, hgt);
      loopP = loopPath(scene.loop);
      const pr = rng(11);
      particles = sparkCloud(w, hgt).map(([x, y]) => {
        const t = scene.traces[Math.floor(pr() * scene.traces.length)];
        const ang = pr() * Math.PI * 2;
        return { x0: x, y0: y, t, sw: (pr() - 0.5) * 260, ang, d: 0.55 + pr() * 0.45, done: false };
      });
    }
    resize();
    window.addEventListener('resize', resize);

    let start = null, raf = 0, finished = false, skipAt = null;
    const finish = () => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      document.removeEventListener('keydown', onSkip);
      root.classList.add('gone');
      document.body.classList.remove('intro-on');
      setTimeout(() => { root.remove(); }, 650);
      resolve();
    };
    const onSkip = () => {
      if (skipAt === null) { skipAt = performance.now(); root.classList.add('skipping'); setTimeout(finish, 380); }
    };
    skip.addEventListener('click', onSkip);
    root.addEventListener('click', (e) => { if (e.target !== skip) onSkip(); });
    document.addEventListener('keydown', onSkip);

    function frame(now) {
      if (start === null) start = now;
      const t = now - start;
      const { w, h: H, cx, cy, chip, loop, traces } = scene;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = C.bg;
      ctx.fillRect(0, 0, w, H);

      // camera dive into the die
      const z = easeIn(prog(t, T.zoom0, T.zoom1));
      const zoom = 1 + z * (Math.max(w, H) / (chip.s * 0.42)) * 1.25;
      ctx.save();
      ctx.translate(cx, cy); ctx.scale(zoom, zoom); ctx.translate(-cx, -cy);

      // PCB lattice
      const gridA = prog(t, T.burst - 300, T.fly1) * (1 - prog(t, T.zoom0, T.zoom0 + 400));
      if (gridA > 0) {
        ctx.fillStyle = `rgba(237,240,228,${0.07 * gridA})`;
        for (let gx = 12; gx < w; gx += 24) for (let gy = 12; gy < H; gy += 24) ctx.fillRect(gx, gy, 1.2, 1.2);
      }

      ctx.globalCompositeOperation = 'lighter';

      // sparks fade in, drift, then fly to the vias
      if (t < T.fly1 + 400) {
        const appear = easeOut(prog(t, 0, T.burst));
        for (const p of particles) {
          const f = easeInOut(clamp((t - T.burst) / ((T.fly1 - T.burst) * p.d)));
          if (f >= 1) { if (!p.done) { p.done = true; p.t.hits++; } continue; }
          const [vx, vy] = p.t.via;
          const mx = (p.x0 + vx) / 2 + Math.cos(p.ang) * p.sw, my = (p.y0 + vy) / 2 + Math.sin(p.ang) * p.sw;
          const x = (1 - f) * (1 - f) * p.x0 + 2 * (1 - f) * f * mx + f * f * vx;
          const y = (1 - f) * (1 - f) * p.y0 + 2 * (1 - f) * f * my + f * f * vy;
          const drift = (1 - f) * 6 * Math.sin(t / 260 + p.ang * 3);
          const s = 7 + 6 * Math.sin(f * Math.PI);
          ctx.globalAlpha = (0.55 + 0.45 * (1 - f)) * appear;
          ctx.drawImage(sprites.copper, x - s + drift, y - s, s * 2, s * 2);
        }
        ctx.globalAlpha = 1;
      }

      // vias, traces, packets
      const traceSpan = T.trace1 - T.trace0;
      for (const tr of traces) {
        const viaA = clamp(tr.hits / 18) * prog(t, T.burst + 300, T.fly1);
        if (viaA > 0) {
          ctx.globalAlpha = viaA;
          ctx.drawImage(sprites.gold, tr.via[0] - 9, tr.via[1] - 9, 18, 18);
          ctx.globalAlpha = 1;
          ctx.globalCompositeOperation = 'source-over';
          ctx.strokeStyle = `rgba(227,192,106,${viaA})`; ctx.lineWidth = 1.6;
          ctx.beginPath(); ctx.arc(tr.via[0], tr.via[1], 4.2, 0, Math.PI * 2); ctx.stroke();
          ctx.globalCompositeOperation = 'lighter';
        }
        const g = easeOut(clamp((t - T.trace0 - tr.delay * traceSpan * 0.5) / (traceSpan * 0.6)));
        if (g <= 0) continue;
        const L = tr.acc[tr.acc.length - 1], upto = L * g;
        ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.strokeStyle = 'rgba(212,137,74,0.22)'; ctx.lineWidth = 7;
        strokePartial(ctx, tr.pts, tr.acc, upto);
        ctx.strokeStyle = C.copper; ctx.lineWidth = 1.8;
        strokePartial(ctx, tr.pts, tr.acc, upto);
        if (g < 1) {
          const [hx, hy] = pointAt(tr.pts, tr.acc, upto);
          ctx.drawImage(sprites.white, hx - 14, hy - 14, 28, 28);
        } else if (t > T.trace1 - 200 && t < T.zoom1) {
          for (const ph of tr.packets) {
            const f = ((t - T.trace1) / 900 + ph) % 1;
            const [px, py] = pointAt(tr.pts, tr.acc, L * f);
            ctx.globalAlpha = 0.85 * Math.sin(f * Math.PI);
            ctx.drawImage(sprites.copper, px - 8, py - 8, 16, 16);
          }
          ctx.globalAlpha = 1;
        }
      }

      // the chip
      const chipA = easeOut(prog(t, T.chip0, T.chip1));
      if (chipA > 0) {
        ctx.globalCompositeOperation = 'source-over';
        const { x, y, s } = chip;
        // pins
        ctx.fillStyle = `rgba(227,192,106,${chipA})`;
        for (const tr of traces) {
          const lit = easeOut(clamp((t - T.trace0 - tr.delay * traceSpan * 0.5) / (traceSpan * 0.6)));
          ctx.globalAlpha = chipA * (0.4 + 0.6 * lit);
          const [px, py] = tr.pin;
          const horiz = tr.side === 1 || tr.side === 3;
          const len = scene.pinLen + 1;
          if (horiz) ctx.fillRect(Math.min(px, px - Math.sign(px - cx) * len), py - 2.4, len, 4.8);
          else ctx.fillRect(px - 2.4, Math.min(py, py - Math.sign(py - cy) * len), 4.8, len);
        }
        ctx.globalAlpha = 1;
        // body
        ctx.fillStyle = `rgba(14,15,18,${0.96 * chipA})`;
        roundRect(ctx, x, y, s, s, s * 0.06); ctx.fill();
        ctx.strokeStyle = `rgba(227,192,106,${0.9 * chipA})`; ctx.lineWidth = 1.4;
        roundRect(ctx, x, y, s, s, s * 0.06); ctx.stroke();
        // die with its lattice
        const d = s * 0.42, dx0 = cx - d / 2, dy0 = cy - d / 2;
        const lit = prog(t, T.chip1 - 200, T.loop1);
        ctx.fillStyle = `rgba(10,11,13,${chipA})`;
        ctx.fillRect(dx0, dy0, d, d);
        ctx.strokeStyle = `rgba(227,192,106,${0.25 + 0.55 * lit})`; ctx.lineWidth = 0.6;
        const cells = 10;
        for (let k = 1; k < cells; k++) {
          ctx.beginPath(); ctx.moveTo(dx0 + d * k / cells, dy0); ctx.lineTo(dx0 + d * k / cells, dy0 + d); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(dx0, dy0 + d * k / cells); ctx.lineTo(dx0 + d, dy0 + d * k / cells); ctx.stroke();
        }
        const r = rng(3);
        ctx.fillStyle = `rgba(255,197,143,${0.15 + 0.6 * lit})`;
        for (let k = 0; k < 40; k++) {
          const ix = Math.floor(r() * cells), iy = Math.floor(r() * cells);
          ctx.fillRect(dx0 + d * ix / cells + 1, dy0 + d * iy / cells + 1, d / cells - 2, d / cells - 2);
        }
        ctx.strokeStyle = `rgba(227,192,106,${chipA})`; ctx.lineWidth = 1;
        ctx.strokeRect(dx0, dy0, d, d);
        ctx.globalCompositeOperation = 'lighter';
        if (lit > 0) {
          ctx.globalAlpha = 0.5 * lit * (1 - z);
          ctx.drawImage(sprites.gold, cx - s * 0.7, cy - s * 0.7, s * 1.4, s * 1.4);
          ctx.globalAlpha = 1;
        }
        // pin-1 dot
        ctx.globalCompositeOperation = 'source-over';
        ctx.fillStyle = `rgba(237,240,228,${0.7 * chipA})`;
        ctx.beginPath(); ctx.arc(x + s * 0.1, y + s * 0.1, s * 0.025, 0, Math.PI * 2); ctx.fill();
        ctx.globalCompositeOperation = 'lighter';
      }

      // the loop and its signal
      const lp = prog(t, T.loop0, T.loop0 + 450);
      if (lp > 0) {
        const L = loopP.acc[loopP.acc.length - 1];
        ctx.strokeStyle = 'rgba(227,192,106,0.18)'; ctx.lineWidth = 8;
        strokePartial(ctx, loopP.pts, loopP.acc, L * easeOut(lp));
        ctx.strokeStyle = C.gold; ctx.lineWidth = 1.6;
        strokePartial(ctx, loopP.pts, loopP.acc, L * easeOut(lp));
        const run = prog(t, T.loop0 + 250, T.loop1);
        if (run > 0 && run < 1) {
          const head = easeInOut(run) * 2 * L;
          for (let k = 0; k < 26; k++) {
            const d = head - k * 7;
            if (d < 0) break;
            const [px, py] = pointAt(loopP.pts, loopP.acc, d % L);
            const s = (1 - k / 26) * 22 + 4;
            ctx.globalAlpha = (1 - k / 26) * 0.9;
            ctx.drawImage(k === 0 ? sprites.white : sprites.gold, px - s, py - s, s * 2, s * 2);
          }
          ctx.globalAlpha = 1;
        }
      }
      ctx.restore();

      // flash at the end of the dive
      const flash = Math.sin(Math.PI * prog(t, T.zoom1 - 350, T.zoom1 + 350));
      if (flash > 0) {
        ctx.globalCompositeOperation = 'lighter';
        ctx.fillStyle = `rgba(255,214,160,${0.22 * flash})`;
        ctx.fillRect(0, 0, w, H);
      }
      ctx.globalCompositeOperation = 'source-over';
      if (t > T.zoom1 - 250) {
        ctx.fillStyle = `rgba(5,6,7,${0.84 * prog(t, T.zoom1 - 250, T.zoom1 + 350)})`;
        ctx.fillRect(0, 0, w, H);
      }

      if (t >= T.mark0) root.classList.add('mark-in');
      if (t >= T.exit0) { root.classList.add('leaving'); document.body.classList.add('ready'); }
      if (t >= T.exit1) { finish(); return; }
      raf = requestAnimationFrame(frame);
    }

    const fontsReady = document.fonts?.load
      ? document.fonts.load(`600 80px "Chakra Petch"`).catch(() => {})
      : Promise.resolve();
    Promise.race([fontsReady, new Promise((r) => setTimeout(r, 900))]).then(() => {
      if (!finished) { resize(); raf = requestAnimationFrame(frame); }
    });
  });
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
