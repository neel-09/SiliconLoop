#!/usr/bin/env node
// Headless simulation test, called by the Python backend (siliconloop/simulator.py).
// stdin: JSON spec {board, hex, parts, rules, telemetry_keys, types, defaults}; stdout: JSON result.
// Rules run in parallel worker threads (each one is an independent power-up).
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import os from 'node:os';
import { runRule, mergeProblems } from './runner.js';

if (!isMainThread) {
  const { spec, rule } = workerData;
  try { parentPort.postMessage(runRule(spec, rule)); }
  catch (e) { parentPort.postMessage({ id: rule.id, description: rule.description, ok: false, error: String(e.message || e), steps: [], health: { ok: true, problems: [] } }); }
} else {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const spec = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const t0 = Date.now();
  const width = Math.max(1, Math.min(spec.rules.length, Number(process.env.SIM_WORKERS) || os.availableParallelism?.() || os.cpus().length));
  const results = new Array(spec.rules.length);
  let next = 0;
  const runOne = () => new Promise((resolve) => {
    const i = next++;
    if (i >= spec.rules.length) return resolve();
    const w = new Worker(new URL(import.meta.url), { workerData: { spec, rule: spec.rules[i] } });
    w.once('message', (r) => { results[i] = r; });
    w.once('error', (e) => { results[i] = { id: spec.rules[i].id, ok: false, error: String(e.message || e), steps: [], health: { ok: true, problems: [] } }; });
    w.once('exit', () => resolve(runOne()));
  });
  await Promise.all(Array.from({ length: width }, runOne));
  const problems = mergeProblems(results.map((r) => r.health?.problems || []));
  process.stdout.write(JSON.stringify({
    ok: results.every((r) => r.ok) && problems.length === 0,
    passed: results.filter((r) => r.ok).length,
    total: results.length,
    rules: results,
    health: { ok: problems.length === 0, problems },
    wall_ms: Date.now() - t0,
  }));
}
