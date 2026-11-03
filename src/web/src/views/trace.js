// Execution trace: the design + firmware pipeline streamed live. Each stage is a pad on a copper trace; repairs
// send the signal back round the loop from the simulation test to the firmware writer.
import { state, go } from '../main.js';
import { api } from '../api.js';
import { h, icon, button } from '../ui.js';

const STAGES = [
  ['parts', 'Check the parts', 'Catalogue cards, or datasheet lookups for unknown parts'],
  ['ground', 'Ground in datasheets', 'Approved sites only; every fact must match a quote'],
  ['plan', 'Plan behaviour rules', 'Muse Spark Open writes rules a program can test'],
  ['allocate', 'Assign pins', 'Deterministic allocator, from the board facts'],
  ['safety', 'Electrical safety check', 'Current, power budget, voltages, mains refused'],
  ['firmware', 'Write the firmware', 'Muse Spark Open writes the Arduino sketch'],
  ['compile', 'Compile', 'arduino-cli, with flash and RAM limits'],
  ['simulate', 'Simulation test', 'The exact binary runs in avr8js against every rule'],
];
const LOOP_FROM = 'simulate', LOOP_TO = 'firmware';

export function traceView(root, { parts, wish } = {}) {
  const build = state.build;
  if (!build || !parts) { go(build ? 'confirm' : 'home'); return; }
  const ctrl = new AbortController();
  const t0 = performance.now();
  let timer = 0, finished = false, repairs = 0, lastActive = null;

  const clock = h('span.clock', '0.0 s');
  const title = h('h1.display.sm', 'Building your gadget');
  const log = h('ol.log', { 'aria-live': 'polite', 'aria-label': 'Build log' });
  const result = h('div.result');
  const loopTag = h('span.loop-tag', '');
  const rows = {};
  const rail = h('ol.rail', STAGES.map(([key, name, sub]) => {
    const detail = h('div.stage-detail');
    const status = h('span.stage-status', 'Waiting');
    const counter = h('span.attempt');
    const li = h('li.stage', { 'data-stage': key },
      h('span.pad', { 'aria-hidden': 'true' }),
      h('div.stage-body', h('div.stage-top', h('strong', name), counter, status), h('span.stage-sub', sub), detail));
    rows[key] = { li, detail, status, counter };
    return li;
  }));
  const loop = h('div.loopback', { 'aria-hidden': 'true' }, h('span.loop-pulse'));

  root.append(h('section.trace',
    h('header.trace-head',
      h('div', h('p.kicker', h('span.live'), 'Live execution trace'), title, h('p.wish-quote', `“${wish}”`)),
      h('div.trace-meta', h('span.model-chip', icon('spark'), 'Muse Spark Open'), clock)),
    h('div.trace-grid',
      h('div.rail-wrap', rail, loop, loopTag),
      h('div.console', h('div.console-head', h('span', 'Build log'), h('span.muted', build.id)), log)),
    result));

  const tick = () => { clock.textContent = `${((performance.now() - t0) / 1000).toFixed(1)} s`; if (!finished) timer = requestAnimationFrame(tick); };
  tick();

  function placeLoop() {
    const a = rows[LOOP_TO].li, b = rows[LOOP_FROM].li;
    const top = a.offsetTop + 14, bottom = b.offsetTop + 14;
    loop.style.top = `${top}px`; loop.style.height = `${bottom - top}px`;
    loopTag.style.top = `${(top + bottom) / 2}px`;
  }
  requestAnimationFrame(placeLoop);
  window.addEventListener('resize', placeLoop);

  function setState(key, st, text) {
    const r = rows[key];
    if (!r) return;
    const idx = STAGES.findIndex((s) => s[0] === key);
    // everything before the active stage is done
    STAGES.forEach(([k], i) => {
      const li = rows[k].li;
      if (i < idx && !li.classList.contains('fail') && !li.classList.contains('ok')) { li.classList.add('ok'); rows[k].status.textContent = 'Done'; }
    });
    r.li.classList.remove('active', 'ok', 'fail', 'retry');
    r.li.classList.add(st === 'start' || st === 'info' || st === 'retry' ? 'active' : st === 'warn' ? 'active' : st);
    if (st === 'retry') r.li.classList.add('retry');
    if (key !== lastActive) {           // keep the working stage in frame (the demo is recorded from this page)
      lastActive = key;
      const box = r.li.getBoundingClientRect();
      if (box.top < 90 || box.bottom > window.innerHeight - 40) r.li.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    r.status.textContent = text;
    rail.style.setProperty('--progress', String(idx + (st === 'ok' ? 1 : 0.5)));
    rail.style.setProperty('--n', String(STAGES.length));
    requestAnimationFrame(placeLoop);
  }

  function logLine(ev) {
    const cls = ev.state === 'fail' ? '.bad' : ev.state === 'ok' ? '.good' : ev.state === 'retry' || ev.state === 'warn' ? '.warn' : '';
    const li = h(`li${cls}`, h('span.t', `${(ev.t ?? 0).toFixed(1)}s`), h('span.s', ev.stage), h('span.m', ev.text));
    const extra = ev.errors || (ev.problems && ev.problems.join('\n'));
    if (extra && ev.state !== 'ok') li.append(h('pre.err', extra));
    log.append(li);
    log.scrollTop = log.scrollHeight;
  }

  const chipList = (items) => h('ul.chips', items.map((x) => h('li.chip', x)));

  function detail(ev) {
    const d = rows[ev.stage]?.detail;
    if (!d) return;
    switch (ev.stage) {
      case 'parts':
        if (ev.parts) d.replaceChildren(chipList(ev.parts.map((p) => (p.count > 1 ? `${p.count} x ${p.name}` : p.name))));
        break;
      case 'ground':
        if (ev.sources) d.replaceChildren(h('ul.sources', ev.sources.slice(0, 5).map((s) => h('li', h('a', { href: s.url, target: '_blank', rel: 'noopener' }, s.title || s.url),
          h('span.host', safeHost(s.url))))));
        break;
      case 'plan':
        if (ev.problems) d.append(h('p.retry-note', `Draft rejected by the validator: ${ev.problems[0]}`));
        if (ev.rules) {
          title.textContent = ev.title;
          d.replaceChildren(h('p.plan-sum', ev.summary), h('ol.rules', ev.rules.map((r) => h('li', { 'data-rule': r.id }, h('span.rid', r.id), h('span', r.description)))));
        }
        break;
      case 'allocate':
        if (ev.pin_map) d.replaceChildren(chipList([...Object.entries(ev.pin_map).map(([n, p]) => `${n} → ${p}`),
          ...(ev.extras || []).filter((e) => e.part === 'resistor').map((e) => `${e.id}: ${fmtOhms(e.ohms)}`)]));
        break;
      case 'safety':
        if (ev.errors?.length) d.replaceChildren(h('ul.notes.bad', ev.errors.map((x) => h('li', x))));
        else if (ev.warnings?.length) d.replaceChildren(h('ul.notes', ev.warnings.slice(0, 3).map((x) => h('li', x))));
        break;
      case 'compile':
        if (ev.flash) d.replaceChildren(meter('Flash', ev.flash, ev.flash_max), meter('RAM', ev.ram, ev.ram_max));
        break;
      case 'simulate':
        if (ev.rules) {
          d.replaceChildren(h('ul.rule-tiles', ev.rules.map((r) => h(`li.${r.ok ? 'pass' : 'fail'}`, { title: r.description }, h('span', r.id), icon(r.ok ? 'check' : 'cross')))));
          markPlanRules(ev.rules);
        }
        break;
      default:
    }
  }

  function markPlanRules(results) {
    for (const r of results) {
      const li = rows.plan.detail.querySelector(`[data-rule="${CSS.escape(r.id)}"]`);
      if (li) { li.classList.toggle('pass', r.ok); li.classList.toggle('fail', !r.ok); }
    }
  }

  function onEvent(ev) {
    if (ev.event !== 'stage') return;
    logLine(ev);
    if (ev.stage === 'repair') {
      repairs = ev.attempt || repairs + 1;
      loop.classList.remove('run'); void loop.offsetWidth; loop.classList.add('run');
      loopTag.textContent = `Repair round ${repairs}`;
      loopTag.classList.add('on');
      for (const k of ['firmware', 'compile', 'simulate']) {
        rows[k].li.classList.remove('ok', 'fail');
        rows[k].counter.textContent = `attempt ${repairs + 1}`;
      }
      setState('firmware', 'start', 'Repairing from the test report');
      return;
    }
    if (ev.stage === 'verify') return;
    setState(ev.stage, ev.state, ev.text);
    detail(ev);
  }

  async function start() {
    result.replaceChildren();
    try {
      const last = await api.run(build.id, parts, wish, onEvent, ctrl.signal);
      finished = true;
      if (last.event === 'error') return showError(last.message);
      state.build = last.build;
      showDone(last.build, last.t);
    } catch (e) {
      if (e.name === 'AbortError') return;
      finished = true;
      showError(e.message);
    }
  }

  function showDone(b, secs) {
    cancelAnimationFrame(timer);
    if (secs != null) clock.textContent = `${secs.toFixed(1)} s`;
    if (b.status === 'verified') {
      STAGES.forEach(([k]) => { rows[k].li.classList.remove('active'); rows[k].li.classList.add('ok'); });
      rail.style.setProperty('--progress', String(STAGES.length));
      const sim = b.simulation || {};
      result.className = 'result win';
      result.replaceChildren(
        h('div.win-seal', icon('check')),
        h('div.win-copy',
          h('h2', 'Proven in simulation'),
          h('p', `${sim.passed} of ${sim.total} behaviour rules passed on a simulated ${boardName(b.board)}, after ${b.attempts?.length || 1} attempt${(b.attempts?.length || 1) === 1 ? '' : 's'}.`)),
        button('Continue', { kind: 'primary', iconName: 'bolt', onClick: () => go('output') }));
      result.scrollIntoView({ behavior: 'smooth', block: 'center' });
      result.querySelector('.btn-primary').focus({ preventScroll: true });
      return;
    }
    if (b.status === 'refused') {
      result.className = 'result lose';
      result.replaceChildren(h('div.win-copy', h('h2', 'Not safe to build'),
        h('ul.notes.bad', (b.safety?.errors || []).map((x) => h('li', x)))),
      h('div.result-actions', button('Change the wish', { kind: 'primary', onClick: () => go('confirm', { wish }) })));
      return;
    }
    const lastA = (b.attempts || []).at(-1) || {};
    result.className = 'result lose';
    result.replaceChildren(
      h('div.win-copy', h('h2', 'Not verified, so no firmware was handed out'),
        h('p', `The firmware still failed at the ${lastA.stage || 'test'} stage after ${b.attempts?.length || 0} attempts. A clearer wish usually fixes this: give numbers for thresholds and say what happens in both cases.`),
        lastA.errors ? h('pre.err', lastA.errors.slice(0, 1200)) : null),
      h('div.result-actions', button('Change the wish', { kind: 'primary', onClick: () => go('confirm', { wish }) }),
        button('Try again', { kind: 'ghost', iconName: 'reset', onClick: () => go('trace', { parts, wish }) })));
  }

  function showError(msg) {
    cancelAnimationFrame(timer);
    for (const k in rows) if (rows[k].li.classList.contains('active')) { rows[k].li.classList.remove('active'); rows[k].li.classList.add('fail'); }
    result.className = 'result lose';
    result.replaceChildren(h('div.win-copy', h('h2', 'The build stopped'), h('p', msg)),
      h('div.result-actions', button('Try again', { kind: 'primary', iconName: 'reset', onClick: () => go('trace', { parts, wish }) }),
        button('Change the wish', { kind: 'ghost', onClick: () => go('confirm', { wish }) })));
  }

  start();
  return () => { ctrl.abort(); finished = true; cancelAnimationFrame(timer); window.removeEventListener('resize', placeLoop); };
}

function meter(label, used, max) {
  const f = max ? used / max : 0;
  return h('div.meter', { style: { '--v': f } }, h('span.meter-l', label), h('span.meter-bar'), h('span.meter-v', `${used.toLocaleString()} / ${max.toLocaleString()} B`));
}

const fmtOhms = (v) => (v >= 1000 ? `${v / 1000} kΩ` : `${v} Ω`);
const safeHost = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } };
export const boardName = (key) => (state.boards.find((b) => b.key === key) || {}).name || ({ uno: 'Arduino Uno', nano: 'Arduino Nano', nano_old: 'Arduino Nano', mega: 'Arduino Mega 2560' }[key] || key);
