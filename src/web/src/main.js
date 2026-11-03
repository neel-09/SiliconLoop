// SiliconLoop single-page app: intro -> intake -> confirm -> trace -> output. A phone opened from the QR code
// gets the capture page instead (?m=<token>).
import '@fontsource/chakra-petch/500.css';
import '@fontsource/chakra-petch/600.css';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import './style.css';

import { h, icon, reducedMotion } from './ui.js';
import { playIntro } from './intro.js';
import { api } from './api.js';
import { homeView, openAbout } from './views/home.js';
import { confirmView, openCatalogue } from './views/confirm.js';
import { traceView } from './views/trace.js';
import { outputView } from './views/output.js';
import { mobileView } from './views/mobile.js';

export const state = {
  board: localStorage.getItem('sl.board') || 'uno',
  boards: [],
  files: [],          // File objects waiting to be identified
  build: null,        // the build object from the API
  confirmed: [],      // [{name, part, count, confidence, status}]
  ideas: null,
  wish: '',
  catalogue: null,    // GET /api/parts
};

const VIEWS = { home: homeView, confirm: confirmView, trace: traceView, output: outputView };
const STEP = { home: 0, confirm: 1, trace: 2, output: 3 };
let current = null, cleanup = null;

export async function catalogue() {
  if (!state.catalogue) state.catalogue = await api.parts();
  return state.catalogue;
}

export function go(name, opts = {}) {
  const swap = () => {
    cleanup?.();
    cleanup = null;
    const main = document.getElementById('view');
    main.replaceChildren();
    main.dataset.view = name;
    const res = VIEWS[name](main, opts);
    cleanup = typeof res === 'function' ? res : null;
    current = name;
    renderSteps();
    window.scrollTo({ top: 0, behavior: 'instant' });
    if (!opts.noHistory) history.pushState({ view: name }, '', name === 'home' ? '/' : `#${name}`);
  };
  if (document.startViewTransition && !reducedMotion() && current) document.startViewTransition(swap);
  else swap();
}

function renderSteps() {
  const el = document.getElementById('steps');
  if (!el) return;
  const names = ['Parts', 'Confirm', 'Build', 'Your gadget'];
  el.replaceChildren(...names.map((n, i) => h(`li${i === STEP[current] ? '.now' : i < STEP[current] ? '.done' : ''}`,
    { 'aria-current': i === STEP[current] ? 'step' : null }, h('span.pad'), h('span.lbl', n))));
}

function shell() {
  const app = document.getElementById('app');
  app.replaceChildren(
    h('header.topbar',
      h('a.brand', { href: '/', onclick: (e) => { e.preventDefault(); if (current !== 'trace') go('home'); } },
        icon('chip'), h('span', 'SiliconLoop')),
      h('ol.steps#steps', { 'aria-label': 'Progress' }),
      h('nav.topnav',
        h('button.linkish', { type: 'button', onclick: () => openCatalogue() }, 'Supported parts'),
        h('button.linkish', { type: 'button', onclick: () => openAbout() }, 'About'))),
    h('main#view'),
    h('footer.footer',
      h('p.disclaimer', 'SiliconLoop is an AI-based product and can make mistakes. Check your wiring against the build report and follow its safety notes before you power a circuit.'),
      h('p.built', 'Built with Muse Spark Open, a Meta open-weight model. Simulation by avr8js.')),
  );
}

async function boot() {
  const params = new URLSearchParams(location.search);
  const token = params.get('m');
  if (token) {
    document.body.classList.add('mobile-capture');
    mobileView(document.getElementById('app'), token);
    return;
  }
  shell();
  const shared = params.get('build');
  if (shared) {           // a shared link to a verified build opens straight on its twin
    try {
      const b = await api.build(shared);
      if (b.status === 'verified') {
        state.build = b; state.board = b.board;
        api.boards().then((x) => { state.boards = x; }).catch(() => {});
        go('output', { noHistory: true });
        history.replaceState({ view: 'output' }, '', `/?build=${shared}`);
        document.body.classList.add('ready');
        return;
      }
    } catch { /* fall through to the home page */ }
  }
  api.boards().then((b) => { state.boards = b; document.dispatchEvent(new Event('sl:boards')); }).catch(() => {});
  const skipIntro = params.get('intro') === '0' || reducedMotion();
  go('home', { noHistory: true });
  history.replaceState({ view: 'home' }, '', '/');
  if (!skipIntro) await playIntro();
  document.body.classList.add('ready');
}

window.addEventListener('popstate', (e) => {
  const v = e.state?.view;
  // Going back during a build would orphan it; only allow moving back to views whose data still exists.
  if (current === 'trace') { history.pushState({ view: 'trace' }, '', '#trace'); return; }
  if (v === 'home' || (v === 'confirm' && state.build) || (v === 'output' && state.build?.status === 'verified')) go(v, { noHistory: true });
  else go('home', { noHistory: true });
});

boot();
