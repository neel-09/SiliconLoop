// Output: the live twin and circuit diagram, the code, the build assistant (report + questions) and flashing.
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { state, go, catalogue } from '../main.js';
import { api } from '../api.js';
import { h, icon, button, modal, toast, download, slug } from '../ui.js';
import { highlight } from '../highlight.js';
import { flash, webSerialSupported } from '../flash.js';
import { boardName } from './trace.js';

const md = (text) => DOMPurify.sanitize(marked.parse(text || '', { breaks: false, gfm: true }));
const SUGGESTED = ['Which wire goes where first?', 'Why does it need these resistors?', 'Explain what the code does', 'My LED stays off. What should I check?'];

export function outputView(root) {
  const build = state.build;
  if (!build || build.status !== 'verified') { go('home'); return; }
  const sim = build.simulation || {};
  let twinObj = null, sketch = '', hexText = null, reportMd = null, reportData = null;
  const name = slug(build.plan?.title);

  // ---------- header ----------
  const mem = build.memory || {};
  root.append(h('section.output',
    h('header.out-head',
      h('div.out-title',
        h('p.kicker', h('span.seal', icon('check')), `Proven in simulation: ${sim.passed} of ${sim.total} behaviour rules`),
        h('h1.display.sm', build.plan.title),
        h('p.lede.sm', build.plan.summary),
        h('ul.facts',
          h('li', h('span', 'Board'), h('strong', boardName(build.board))),
          mem.flash ? h('li', h('span', 'Flash'), h('strong', `${Math.round((mem.flash / mem.flash_max) * 100)}%`)) : null,
          mem.ram ? h('li', h('span', 'RAM'), h('strong', `${Math.round((mem.ram / mem.ram_max) * 100)}%`)) : null,
          h('li', h('span', 'Attempts'), h('strong', String(build.attempts?.length || 1))))),
      h('div.out-actions',
        button('Flash to board', { kind: 'primary', iconName: 'bolt', onClick: () => openFlash() }),
        button('Sketch', { kind: 'ghost', iconName: 'download', title: 'Download sketch.ino', onClick: async () => download(`${name}.ino`, sketch || await api.sketch(build.id)) }),
        button('Firmware', { kind: 'ghost', iconName: 'download', title: 'Download firmware.hex', onClick: async () => download(`${name}.hex`, hexText || (hexText = await api.hex(build.id))) }),
        button('Share link', { kind: 'ghost', iconName: 'copy', title: 'Copy a link that opens this verified build', onClick: async () => {
          const url = `${location.origin}/?build=${build.id}`;
          try { await navigator.clipboard.writeText(url); toast('Link copied. Anyone with it can run this twin in their browser.'); }
          catch { toast(url); }
        } }),
        button('New build', { kind: 'ghost', iconName: 'reset', onClick: () => { state.files = []; history.replaceState({ view: 'home' }, '', '/'); go('home'); } }))),
    h('div.out-grid', mainPanel(), chatPanel())));

  // ---------- circuit / code ----------
  function mainPanel() {
    const twinHost = h('div.twin-host', h('div.twin-loading', h('span.pulse'), 'Starting the simulated board'));
    const controlsHost = h('div.controls-host');
    const serialLog = h('ol.serial', { 'aria-live': 'off' });
    const telBox = h('dl.telemetry');
    const clock = h('span.sim-clock', '0.0 s');
    const speed = h('span.sim-speed', '');
    const playBtn = h('button.icon-btn', { type: 'button', 'aria-label': 'Pause the simulation', onclick: () => {
      if (!twinObj) return;
      if (twinObj.running) { twinObj.stop(); playBtn.replaceChildren(icon('play')); playBtn.setAttribute('aria-label', 'Run the simulation'); }
      else { twinObj.start(); playBtn.replaceChildren(icon('pause')); playBtn.setAttribute('aria-label', 'Pause the simulation'); }
    } }, icon('pause'));
    const resetBtn = h('button.icon-btn', { type: 'button', 'aria-label': 'Power-cycle the board', onclick: () => { twinObj?.reset(); serialLog.replaceChildren(); } }, icon('reset'));

    const codeBox = h('pre.code', h('code', 'Loading the sketch'));
    const copyBtn = button('Copy', { kind: 'ghost', iconName: 'copy', onClick: async () => { await navigator.clipboard.writeText(sketch); toast('Sketch copied.'); } });

    const circuit = h('div.tabpanel#tp-circuit', { role: 'tabpanel', 'aria-labelledby': 'tab-circuit' },
      h('div.twin-bar', h('span.live'), h('span', 'Live twin running your exact firmware'), h('span.grow'), clock, speed, playBtn, resetBtn),
      twinHost,
      h('div.twin-lower',
        h('div.panel-box', h('h3', 'Change the inputs'), controlsHost),
        h('div.panel-box', h('h3', 'Serial monitor', h('span.muted', '115200 baud')), telBox, serialLog)));
    const code = h('div.tabpanel#tp-code', { role: 'tabpanel', 'aria-labelledby': 'tab-code', hidden: true },
      h('div.code-bar', h('span', `${name}.ino`), h('span.grow'), h('span.muted', 'Pin constants are generated; the rest was written by Muse Spark Open'), copyBtn),
      codeBox);
    const tabs = h('div.tabs', { role: 'tablist' },
      tab('circuit', 'Circuit and twin', true), tab('code', 'Code', false));
    function tab(key, label, sel) {
      return h(`button.tab${sel ? '.sel' : ''}#tab-${key}`, { type: 'button', role: 'tab', 'aria-selected': String(sel), 'aria-controls': `tp-${key}`,
        onclick: (e) => {
          for (const b of tabs.children) { b.classList.toggle('sel', b === e.currentTarget); b.setAttribute('aria-selected', String(b === e.currentTarget)); }
          circuit.hidden = key !== 'circuit'; code.hidden = key !== 'code';
        } }, label);
    }

    // load everything the twin needs
    Promise.all([api.twin(build.id), api.report(build.id), catalogue().catch(() => [])]).then(async ([twin, report, cat]) => {
      reportData = report;
      const { createTwin, twinControls } = await import('../twin.js');
      twinObj = await createTwin(twinHost, { build, twin, report, catalogue: cat });
      controlsHost.replaceChildren(twinControls(twinObj, twin));
      twinObj.on('serial', (line) => {
        serialLog.append(h('li', line));
        while (serialLog.children.length > 60) serialLog.firstChild.remove();
        serialLog.scrollTop = serialLog.scrollHeight;
      });
      twinObj.on('telemetry', (tel) => {
        telBox.replaceChildren(...Object.entries(tel).flatMap(([k, v]) => [h('dt', k), h('dd', v)]));
      });
      let lastPaint = 0;
      twinObj.on('tick', ({ ms, speed: s, crashed }) => {
        const now = performance.now();
        if (now - lastPaint < 120) return;
        lastPaint = now;
        clock.textContent = `${(ms / 1000).toFixed(1)} s`;
        speed.textContent = crashed ? 'stopped' : s > 0.95 ? 'real time' : `${Math.round(s * 100)}% speed`;
      });
      twinObj.start();
    }).catch((e) => { twinHost.replaceChildren(h('p.form-error', `The twin could not start: ${e.message}`)); });

    api.sketch(build.id).then((s) => { sketch = s; codeBox.firstChild.innerHTML = numbered(highlight(s)); })
      .catch((e) => { codeBox.firstChild.textContent = e.message; });

    return h('div.out-main', tabs, circuit, code);
  }

  // ---------- chat with the report ----------
  function chatPanel() {
    const list = h('ol.msgs', { 'aria-live': 'polite' });
    const history = [];
    const input = h('textarea', { rows: 2, placeholder: 'Ask about wiring, parts or the code', 'aria-label': 'Your question', maxlength: 1500,
      onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } } });
    const sendBtn = h('button.send', { type: 'submit', 'aria-label': 'Send' }, icon('send'));
    const chips = h('div.suggest', SUGGESTED.map((q) => h('button.chip', { type: 'button', onclick: () => { input.value = q; send(); } }, q)));
    const reportCard = h('div.report-card', h('p.muted', 'Preparing your build report'));

    list.append(h('li.msg.bot', h('div.bubble',
      h('p', 'Your gadget is designed, coded and proven. Here is the build report to follow when you wire the real thing.'),
      reportCard)));
    api.report(build.id).then((r) => {
      reportData = r;
      reportCard.replaceChildren(
        h('div.rc-head', h('span.rc-icon', icon('print')), h('div', h('strong', `${r.title} build report`),
          h('span.muted', `${r.parts.length} parts, ${r.steps.length} wiring steps, ${r.checklist.length} checks`))),
        h('ol.rc-steps', r.steps.slice(0, 4).map((s) => h('li', h('span.wire-dot', { style: { '--c': `var(--w-${s.colour || 'yellow'})` } }), s.text))),
        r.steps.length > 4 ? h('p.muted.sm', `and ${r.steps.length - 4} more steps in the full report`) : null,
        h('div.rc-actions',
          button('Open report', { kind: 'secondary', onClick: () => openReport() }),
          button('Markdown', { kind: 'ghost', iconName: 'download', onClick: async () => download(`${name}-report.md`, await getMd(), 'text/markdown') }),
          button('PDF', { kind: 'ghost', iconName: 'print', onClick: async () => printReport(await getMd(), build.plan.title) })));
    }).catch((e) => reportCard.replaceChildren(h('p.form-error', e.message)));

    async function send() {
      const q = input.value.trim();
      if (!q) return;
      input.value = '';
      chips.remove();
      list.append(h('li.msg.me', h('div.bubble', q)));
      history.push({ role: 'user', content: q });
      const pending = h('li.msg.bot.pending', h('div.bubble', h('span.dots', h('i'), h('i'), h('i'))));
      list.append(pending);
      list.scrollTop = list.scrollHeight;
      sendBtn.disabled = true;
      try {
        const { reply } = await api.chat(build.id, history);
        history.push({ role: 'assistant', content: reply });
        pending.replaceWith(h('li.msg.bot', h('div.bubble.md', { html: md(reply) })));
      } catch (e) {
        history.pop();
        pending.replaceWith(h('li.msg.bot.err', h('div.bubble', e.message)));
      } finally {
        sendBtn.disabled = false;
        list.scrollTop = list.scrollHeight;
        input.focus();
      }
    }

    return h('aside.chat', { 'aria-label': 'Build assistant' },
      h('div.chat-head', h('strong', 'Build assistant'), h('span.model-chip', icon('spark'), 'Muse Spark Open')),
      list, chips,
      h('form.chat-form', { onsubmit: (e) => { e.preventDefault(); send(); } }, input, sendBtn),
      h('p.chat-note', 'Answers come from this build\'s report, rules and code. AI can make mistakes; the report is the reference.'));
  }

  async function getMd() { return reportMd || (reportMd = await api.reportMd(build.id)); }

  async function openReport() {
    const text = await getMd();
    modal(h('div.report',
      h('div.report-bar',
        button('Markdown', { kind: 'ghost', iconName: 'download', onClick: () => download(`${name}-report.md`, text, 'text/markdown') }),
        button('PDF', { kind: 'secondary', iconName: 'print', onClick: () => printReport(text, build.plan.title) })),
      h('article.report-body.md', { html: md(text) })), { label: 'Build report', className: 'wide' });
  }

  // ---------- flashing ----------
  function openFlash() {
    const supported = webSerialSupported();
    const bar = h('div.progress', h('span'));
    const status = h('p.flash-status', { role: 'status' }, supported ? 'Plug the board in with a USB data cable, then press Choose port.' : '');
    const go_ = button('Choose port and flash', { kind: 'primary', iconName: 'bolt', onClick: async () => {
      go_.disabled = true;
      try {
        hexText ||= await api.hex(build.id);
        await flash(build.board, hexText, (stage, f) => {
          const pct = stage === 'connect' ? f * 8 : stage === 'write' ? 8 + f * 62 : stage === 'verify' ? 70 + f * 30 : 100;
          bar.firstChild.style.width = `${pct}%`;
          status.textContent = { connect: 'Talking to the bootloader', write: `Writing firmware ${Math.round(f * 100)}%`, verify: `Checking every byte ${Math.round(f * 100)}%`, done: 'Done' }[stage];
        });
        box.classList.add('done');
        status.textContent = 'Flashed and verified. Your board is now running the same firmware as the twin.';
      } catch (e) {
        status.textContent = e.name === 'NotFoundError' ? 'No port was chosen.' : e.message;
        box.classList.add('failed');
      } finally {
        go_.disabled = false;
      }
    } });
    const box = h('div.flash',
      h('h2', `Flash to your ${boardName(build.board)}`),
      supported ? h('ol.flash-steps',
        h('li', 'Wire the circuit from the report and check it against the pin table.'),
        h('li', 'Connect the board by USB and close any open serial monitor.'),
        h('li', 'Press the button below and choose the port named Arduino, USB Serial or CH340.'))
        : h('p.banner.warn', 'This browser cannot reach USB ports. Open SiliconLoop in Chrome or Edge on a computer, or upload the sketch with the Arduino IDE using the report\'s flashing steps.'),
      supported ? bar : null, status,
      h('div.flash-actions', supported ? go_ : null,
        button('Download sketch for the Arduino IDE', { kind: 'ghost', iconName: 'download', onClick: async () => download(`${name}.ino`, sketch || await api.sketch(build.id)) })));
    modal(box, { label: 'Flash to board' });
  }

  return () => twinObj?.destroy();
}

function numbered(html) {
  return html.split('\n').map((l, i) => `<span class="ln" data-n="${i + 1}">${l || ' '}</span>`).join('');
}

function printReport(text, title) {
  const w = window.open('', '_blank');
  if (!w) { toast('Allow pop-ups to save the report as PDF.', 'warn'); return; }
  w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title.replace(/</g, '')} build report</title>
<style>body{font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;color:#13201d;max-width:760px;margin:32px auto;padding:0 24px}
h1{font:600 30px/1.15 "Chakra Petch",system-ui,sans-serif;margin:0 0 8px}h2{font:600 19px "Chakra Petch",system-ui,sans-serif;margin:26px 0 8px;border-bottom:1.5px solid #c58a4f;padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #c9d1cc;padding:5px 8px;text-align:left}th{background:#eef1ea}
li{margin:3px 0}hr{border:0;border-top:1px solid #c9d1cc;margin:28px 0 10px}code{font-family:"IBM Plex Mono",monospace}
@media print{body{margin:0}h2{break-after:avoid}li,tr{break-inside:avoid}}</style></head><body>${md(text)}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 300);
}
