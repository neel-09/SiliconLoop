// Confirm: the recognised parts (edit, add, remove), then the wish, typed or chosen from three ideas.
import { state, go, catalogue } from '../main.js';
import { api } from '../api.js';
import { h, icon, button, modal, toast } from '../ui.js';

const IN_WORDS = { adc: 'an analog reading (0 to 1023)', distance: 'distance in cm', bool: 'an on/off signal', temp: 'temperature',
  pct: 'humidity', tilt: 'tilt angle', angle: 'an angle', fraction: 'a level' };
const OUT_WORDS = { on: 'turns on and off', brightness: 'dims', r: 'mixes colours', angle: 'moves to an angle',
  active: 'switches on and off', text_contains: 'shows text' };

function describe(part) {
  const ins = Object.entries(part.rules.in).map(([k, t]) => IN_WORDS[t] || k);
  const outs = [...new Set(Object.keys(part.rules.out).map((k) => OUT_WORDS[k] || (k === 'g' || k === 'b' ? OUT_WORDS.r : k)))];
  if (ins.length) return `Senses ${[...new Set(ins)].join(', ')}`;
  if (outs.length) return `${outs.join(', ').replace(/^./, (c) => c.toUpperCase())}`;
  return '';
}

function group(part) {
  if (Object.keys(part.rules.in).length) return 'Sensors and controls';
  if ('text_contains' in part.rules.out) return 'Displays';
  return 'Lights, sound and motion';
}

/** Browse the supported parts. With pick, clicking a part calls pick(part) and closes. */
export async function openCatalogue({ pick } = {}) {
  let parts;
  try { parts = await catalogue(); } catch (e) { toast(e.message, 'error'); return; }
  const search = h('input.search', { type: 'search', placeholder: 'Search parts, for example servo or OLED', 'aria-label': 'Search parts' });
  const body = h('div.cat-body');
  const render = () => {
    const q = search.value.trim().toLowerCase();
    const hits = parts.filter((p) => !q || p.name.toLowerCase().includes(q) || p.aliases.some((a) => a.includes(q)));
    const groups = {};
    for (const p of hits) (groups[group(p)] ||= []).push(p);
    body.replaceChildren(...(hits.length ? Object.entries(groups).map(([g, list]) => h('section.cat-group',
      h('h3', g, h('span.count', String(list.length))),
      h('ul.cat-grid', list.map((p) => h('li', h(pick ? 'button.cat-card.pickable' : 'div.cat-card', pick ? { type: 'button', onclick: () => { pick(p); close(); } } : {},
        h('span.cat-name', p.name),
        h('span.cat-does', describe(p)),
        h('span.cat-pins', Object.values(p.pins).join('  ')),
        p.generic ? h('span.tag', 'Generic module model') : null)))))) : [h('p.muted', 'No supported part matches. Unknown parts can still be confirmed; SiliconLoop looks them up on datasheet sites during the build.')]));
  };
  search.addEventListener('input', render);
  render();
  const { close } = modal(h('div.catalogue',
    h('h2', pick ? 'Add a part' : 'Supported parts'),
    h('p.muted', `${parts.length} curated parts with simulation models, plus simple digital, analog and switched modules found on approved datasheet sites.`),
    search, body), { label: 'Supported parts', className: 'wide' });
  setTimeout(() => search.focus(), 50);
}

export function confirmView(root, opts = {}) {
  const build = state.build;
  if (!build) { go('home'); return; }
  let parts = [];
  catalogue().then((p) => { parts = p; renderRows(); }).catch(() => {});

  const rows = h('ul.part-rows');
  const wish = h('textarea#wish', { rows: 3, maxlength: 400, placeholder: 'For example: a night light that turns on when it gets dark and waves the servo when someone comes closer than 30 cm',
    oninput: (e) => { state.wish = e.target.value; } });
  wish.value = opts.wish ?? state.wish ?? '';
  const ideasBox = h('div.ideas', { 'aria-live': 'polite' });
  const err = h('p.form-error', { role: 'alert' });

  function renderRows() {
    rows.replaceChildren(...state.confirmed.map((p, i) => {
      const supported = !!p.part;
      const select = h('select', { 'aria-label': `Catalogue match for ${p.name}`,
        onchange: (e) => { p.part = e.target.value || null; p.status = p.part ? 'supported' : 'unknown'; renderRows(); } },
      h('option', { value: '', selected: !p.part }, 'Not in the catalogue: look it up'),
      ...parts.map((c) => h('option', { value: c.key, selected: c.key === p.part }, c.name)));
      if (!parts.length && p.part) select.append(h('option', { value: p.part, selected: true }, p.part));
      return h(`li.part-row${supported ? '' : '.unknown'}`,
        h('span.pad', { title: supported ? 'In the catalogue' : 'Not in the catalogue' }),
        h('div.part-main',
          h('input.part-name', { value: p.name, 'aria-label': 'Part name', onchange: (e) => { p.name = e.target.value.trim() || p.name; } }),
          h('div.select-wrap.small', select),
          !supported ? h('span.hint', 'Looked up on approved datasheet sites during the build. Left out if it cannot be simulated.') : null),
        p.confidence != null ? h('span.conf', { title: `Recognition confidence ${Math.round(p.confidence * 100)}%` },
          h('span.conf-bar', { style: { '--v': p.confidence } }), `${Math.round(p.confidence * 100)}%`) : h('span.conf.added', 'added'),
        h('div.stepper', { role: 'group', 'aria-label': `How many ${p.name}` },
          h('button', { type: 'button', 'aria-label': 'One fewer', onclick: () => { if (p.count > 1) { p.count--; renderRows(); } } }, icon('minus')),
          h('output', String(p.count)),
          h('button', { type: 'button', 'aria-label': 'One more', onclick: () => { if (p.count < 20) { p.count++; renderRows(); } } }, icon('plus'))),
        h('button.icon-btn', { type: 'button', 'aria-label': `Remove ${p.name}`, onclick: () => { state.confirmed.splice(i, 1); state.ideas = null; renderRows(); renderIdeas(); } }, icon('trash')));
    }));
    if (!state.confirmed.length) rows.append(h('li.empty', 'No parts yet. Add the parts you have from the catalogue.'));
  }

  function payload() {
    return state.confirmed.map((p) => ({ name: p.name, part: p.part || null, count: p.count }));
  }

  function renderIdeas() {
    if (!state.ideas) { ideasBox.replaceChildren(); return; }
    ideasBox.replaceChildren(
      h('h3', 'Three things these parts can build'),
      h('ul.idea-grid', state.ideas.map((idea, n) => h('li', h('button.idea', { type: 'button', style: { '--n': n }, onclick: () => start(idea.wish) },
        h('span.idea-title', idea.title),
        h('span.idea-wish', idea.wish),
        idea.why ? h('span.idea-why', idea.why) : null,
        h('span.idea-parts', idea.parts_used.map((x) => h('span.chip', x.count > 1 ? `${x.count} x ${x.name}` : x.name))),
        h('span.idea-go', 'Build this'))))));
  }

  const ideasBtn = button('Suggest ideas', { kind: 'ghost', iconName: 'spark', onClick: async () => {
    err.textContent = '';
    if (!state.confirmed.some((p) => p.part)) { err.textContent = 'Ideas need at least one part from the catalogue.'; return; }
    ideasBtn.disabled = true;
    ideasBtn.classList.add('loading');
    ideasBox.replaceChildren(h('div.ideas-loading', h('span.pulse'), 'Muse Spark Open is thinking up gadgets for your parts'));
    try {
      const res = await api.ideas(build.id, payload());
      state.ideas = res.ideas;
      renderIdeas();
    } catch (e) {
      ideasBox.replaceChildren();
      err.textContent = e.message;
    } finally {
      ideasBtn.disabled = false;
      ideasBtn.classList.remove('loading');
    }
  } });

  function start(text) {
    err.textContent = '';
    const w = (text ?? wish.value).trim();
    if (!state.confirmed.length) { err.textContent = 'Add at least one part first.'; return; }
    if (w.length < 8) { err.textContent = 'Describe what the gadget should do in a sentence, or pick one of the ideas.'; wish.focus(); return; }
    state.wish = w;
    wish.value = w;
    go('trace', { parts: payload(), wish: w });
  }

  const boardName = (state.boards.find((b) => b.key === build.board) || {}).name || build.board;
  root.append(
    h('section.confirm',
      h('div.confirm-head',
        h('button.back', { type: 'button', onclick: () => go('home') }, icon('back'), 'Photos'),
        h('h1.display.sm', 'Check your parts'),
        h('p.muted', `Muse Spark Open found these in your photo${state.files.length === 1 ? '' : 's'}. Fix anything it got wrong, then say what to build on your ${boardName}.`)),
      build.image_quality === 'unclear' ? h('p.banner.warn', 'The photo looks blurry or dark, so some parts may be missing. Add them below or go back and upload a clearer photo.') : null,
      h('div.confirm-grid',
        h('div.confirm-parts',
          rows,
          build.ignored?.length ? h('p.ignored', `Not counted as parts: ${build.ignored.join(', ')}. Resistors are added for you.`) : null,
          h('div.row-actions',
            button('Add a part', { kind: 'ghost', iconName: 'plus', onClick: () => openCatalogue({ pick: (c) => {
              const have = state.confirmed.find((x) => x.part === c.key);
              if (have) have.count++; else state.confirmed.push({ name: c.name, part: c.key, count: 1, confidence: null, status: 'supported' });
              state.ideas = null; renderRows(); renderIdeas();
            } }) }),
            button('Supported parts', { kind: 'ghost', onClick: () => openCatalogue() }))),
        h('div.confirm-wish',
          h('label.field', { for: 'wish' }, h('h2', 'What should it do?')),
          wish,
          h('div.wish-actions', ideasBtn, button('Build it', { kind: 'primary', iconName: 'bolt', onClick: () => start() })),
          err,
          ideasBox))));
  renderRows();
  renderIdeas();
}
