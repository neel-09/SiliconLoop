// Deterministic behaviour-rule runner. Every rule gets a fresh power-up of the exact compiled binary, the part models
// wired as the allocator decided, and the default inputs; then each step sets inputs and polls outputs.
// Health checks run on every rule's timeline: serial baud, telemetry format and timing, serial flooding, resets.
import { MCU } from './mcu.js';
export { MCU };
import { attach } from './parts.js';

const POLL_MS = 5;
const BOOT_MAX_MS = 5000;
// Tolerance for a bare number, by value type (prompts.SIM_IO). {min, max} is always taken literally.
const TOL = { fraction: 0.1, angle: 10, adc: 10, distance: 3, temp: 1, pct: 2, tilt: 3 };

export function parseTelemetry(line) {
  if (!line.startsWith('T,')) return null;
  const out = {};
  for (const kv of line.slice(2).split(',')) {
    const i = kv.indexOf('=');
    if (i < 0) return { _bad: line };
    out[kv.slice(0, i).trim()] = kv.slice(i + 1).trim();
  }
  return out;
}

function matches(type, expected, observed) {
  if (observed === undefined || observed === null) return false;
  if (type === 'bool') return !!observed === expected;
  if (type === 'str') return String(observed).includes(expected);
  const x = typeof observed === 'number' ? observed : parseFloat(observed);
  if (Number.isNaN(x)) return false;
  if (typeof expected === 'object') return (expected.min ?? -Infinity) <= x && x <= (expected.max ?? Infinity);
  return Math.abs(x - expected) <= (TOL[type] ?? 0);
}

/** Power up a board with its parts. Returns {mcu, parts: {id: model}, log: {lines, telemetry}}. */
export function boot(spec) {
  const mcu = new MCU(spec.board, spec.hex);
  const parts = {};
  for (const inst of spec.parts) parts[inst.id] = attach(mcu, inst);
  for (const inst of spec.parts) {
    for (const [k, v] of Object.entries(spec.defaults?.[inst.id] || {})) parts[inst.id].set(k, v);
  }
  const log = { lines: [], telemetry: [] };
  mcu.onLine((line, t) => {
    const tel = parseTelemetry(line);
    if (tel) log.telemetry.push({ t, values: tel, raw: line });
    else log.lines.push({ t, text: line });
  });
  return { mcu, parts, log };
}

function observe(ctx, spec, expect, sinceMs) {
  const { parts, log } = ctx;
  const obs = {}, fails = [];
  let lastTel = null;
  for (let i = log.telemetry.length - 1; i >= 0 && log.telemetry[i].t > sinceMs; i--) { lastTel = log.telemetry[i]; break; }
  const after = log.lines.filter((l) => l.t > sinceMs).map((l) => l.text);
  for (const [iid, vals] of Object.entries(expect)) {
    for (const [k, want] of Object.entries(vals)) {
      if (iid === 'board') {
        if (k === 'serial_contains') {
          const ok = after.some((l) => l.includes(want));
          obs[`board.serial_contains`] = ok ? want : after.slice(-5);
          if (!ok) fails.push({ key: 'board.serial_contains', expected: want, observed: after.slice(-5) });
        } else if (k === 'serial_excludes') {
          const hit = after.find((l) => l.includes(want));
          if (hit !== undefined) fails.push({ key: 'board.serial_excludes', expected: `"${want}" not printed`, observed: hit, final: true });
        } else if (k === 'telemetry') {
          for (const [tk, tv] of Object.entries(want)) {
            const raw = lastTel?.values[tk];
            const type = spec.types[tk] || 'adc';
            obs[`telemetry ${tk}`] = raw ?? null;
            if (!matches(type, tv, raw)) fails.push({ key: `telemetry ${tk}`, expected: tv, observed: raw ?? (lastTel ? 'missing' : 'no telemetry line yet') });
          }
        }
        continue;
      }
      const got = parts[iid].get(k);
      obs[`${iid}.${k}`] = got;
      if (!matches(spec.types[`${iid}.${k}`], want, got)) fails.push({ key: `${iid}.${k}`, expected: want, observed: got });
    }
  }
  return { obs, fails };
}

function runStep(ctx, spec, step) {
  const { mcu, parts } = ctx;
  for (const [iid, vals] of Object.entries(step.set || {})) for (const [k, v] of Object.entries(vals)) parts[iid].set(k, v);
  const t0 = mcu.timeMs, within = step.within_ms, hold = step.hold_ms || 0;
  const hasExcludes = 'serial_excludes' in (step.expect.board || {});
  let metAt = null, last = null;
  while (true) {
    mcu.run(POLL_MS);
    if (mcu.crashed) return { ok: false, crashed: mcu.crashed, observed: last?.obs, failed: last?.fails };
    const el = mcu.timeMs - t0;
    last = observe(ctx, spec, step.expect, t0);
    if (last.fails.some((f) => f.final)) return { ok: false, atMs: Math.round(el), observed: last.obs, failed: last.fails };
    if (last.fails.length === 0) { if (metAt === null) metAt = el; } else metAt = null;   // must hold continuously
    if (metAt === null && el > within) return { ok: false, atMs: Math.round(el), observed: last.obs, failed: last.fails };
    if (metAt !== null && el - metAt >= hold && (!hasExcludes || el >= within)) return { ok: true, metMs: Math.round(metAt), observed: last.obs };
  }
}

function waitForBoot(ctx, spec) {
  const { mcu, log } = ctx;
  const need = spec.telemetry_keys.length > 0;
  while (mcu.timeMs < BOOT_MAX_MS) {
    mcu.run(10);
    if (mcu.crashed) return false;
    if (!need && mcu.timeMs > 300) return true;
    if (need && log.telemetry.length) return true;
  }
  return !need;
}

export function runRule(spec, rule) {
  const ctx = boot(spec);
  const res = { id: rule.id, description: rule.description, ok: true, steps: [] };
  if (!waitForBoot(ctx, spec)) {
    res.ok = false;
    res.error = ctx.mcu.crashed ? `the board ${ctx.mcu.crashed.kind === 'reset' ? 'reset' : 'crashed'} during setup() at ${ctx.mcu.crashed.atMs} ms`
      : `no telemetry line within ${BOOT_MAX_MS / 1000} s of power-up (setup() blocks or Serial is not started)`;
  } else {
    for (const step of rule.steps) {
      const r = runStep(ctx, spec, step);
      res.steps.push({ set: step.set, expect: step.expect, within_ms: step.within_ms, hold_ms: step.hold_ms, ...r });
      if (!r.ok) { res.ok = false; if (r.crashed) res.error = `the board ${r.crashed.kind === 'reset' ? 'reset' : 'crashed'} at ${r.crashed.atMs} ms`; break; }
    }
  }
  // A little extra run time so timing health checks see several telemetry intervals.
  if (!ctx.mcu.crashed && ctx.mcu.timeMs - (ctx.log.telemetry[0]?.t ?? 0) < 1200) ctx.mcu.run(800);
  res.health = health(ctx, spec);
  res.sim_ms = Math.round(ctx.mcu.timeMs);
  return res;
}

export function health(ctx, spec) {
  const { mcu, log } = ctx;
  const p = [];
  const baud = mcu.baud;
  if (log.telemetry.length || log.lines.length) {
    if (Math.abs(baud - 115200) / 115200 > 0.03) p.push(`serial runs at ${Math.round(baud)} baud; use Serial.begin(115200)`);
  }
  if (mcu.crashed) p.push(mcu.crashed.kind === 'reset' ? `the board reset at ${mcu.crashed.atMs} ms (out of RAM, stack overflow or a bad pointer)`
    : `the program ran outside its code at ${mcu.crashed.atMs} ms (stack corruption)`);
  const keys = spec.telemetry_keys;
  if (keys.length) {
    const tel = log.telemetry;
    if (tel.length >= 3) {
      const gaps = tel.slice(1).map((x, i) => x.t - tel[i].t);
      const sorted = [...gaps].sort((a, b) => a - b), median = sorted[Math.floor(sorted.length / 2)], max = sorted[sorted.length - 1];
      if (median < 200 || median > 300) p.push(`telemetry lines arrive every ${Math.round(median)} ms; they must come every 250 ms`);
      if (max > 750) p.push(`telemetry stopped for ${Math.round(max)} ms once: loop() is blocking (delay(), a slow read or a long loop)`);
    } else if (tel.length === 1 && mcu.timeMs - tel[0].t > 1000) p.push('telemetry is printed only once or twice; it must be printed every 250 ms');
    for (const x of tel) {
      const got = Object.keys(x.values);
      if (x.values._bad || got.join(',') !== keys.join(',')) { p.push(`telemetry line "${x.raw.slice(0, 120)}" must have exactly these keys in order: ${keys.join(',')}`); break; }
      const bad = got.find((k) => !/^-?\d+(\.\d+)?$/.test(x.values[k]));
      if (bad) { p.push(`telemetry value ${bad}=${x.values[bad]} is not a plain number`); break; }
    }
  }
  // Flooding: any message printed more than 20 times in one second.
  const L = log.lines;
  for (let i = 0, j = 0; i < L.length; i++) {
    while (L[i].t - L[j].t > 1000) j++;
    if (i - j + 1 > 20) { p.push(`more than 20 serial messages in one second (e.g. "${L[i].text.slice(0, 60)}"); print a status line only when it changes`); break; }
  }
  return { ok: p.length === 0, problems: p };
}

/** One message per kind of health problem across all rule runs, keeping the worst (largest) number.
 * Messages of the same kind differ only in their numbers, e.g. "telemetry stopped for 918 ms" vs "913 ms". */
export function mergeProblems(lists) {
  const byKind = new Map();
  const num = (t) => Math.max(0, ...(t.match(/\d+/g) || ['0']).map(Number));
  for (const p of lists.flat()) {
    const kind = p.replace(/\d+/g, '#');
    const cur = byKind.get(kind);
    if (cur === undefined || num(p) > num(cur)) byKind.set(kind, p);
  }
  return [...byKind.values()];
}

export function runAll(spec) {
  const t0 = Date.now();
  const rules = spec.rules.map((r) => runRule(spec, r));
  const problems = mergeProblems(rules.map((r) => r.health.problems));
  return {
    ok: rules.every((r) => r.ok) && problems.length === 0,
    passed: rules.filter((r) => r.ok).length,
    total: rules.length,
    rules,
    health: { ok: problems.length === 0, problems },
    wall_ms: Date.now() - t0,
  };
}
