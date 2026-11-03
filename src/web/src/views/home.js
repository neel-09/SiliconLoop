// Home: board choice, component photos (upload or phone via QR), About box and FAQ.
import QRCode from 'qrcode';
import { state, go } from '../main.js';
import { api } from '../api.js';
import { h, icon, button, modal, toast, shrinkImage } from '../ui.js';
import { ABOUT, FAQ } from '../content.js';

const MAX_PHOTOS = 3;
const FALLBACK_BOARDS = [
  { key: 'uno', name: 'Arduino Uno' }, { key: 'nano', name: 'Arduino Nano' },
  { key: 'nano_old', name: 'Arduino Nano (old bootloader)' }, { key: 'mega', name: 'Arduino Mega 2560' },
];

export function homeView(root) {
  const thumbs = h('ul.thumbs', { 'aria-label': 'Photos to identify' });
  const fileInput = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true,
    onchange: (e) => { addFiles([...e.target.files]); e.target.value = ''; } });
  const identify = button('Identify parts', { kind: 'primary', iconName: 'spark', onClick: () => run() });
  const errorBox = h('p.form-error', { role: 'alert' });

  const boardSelect = h('select#board', { onchange: (e) => { state.board = e.target.value; localStorage.setItem('sl.board', state.board); } });
  const fillBoards = () => {
    const list = state.boards.length ? state.boards : FALLBACK_BOARDS;
    boardSelect.replaceChildren(...list.map((b) => h('option', { value: b.key, selected: b.key === state.board }, b.name)));
  };
  fillBoards();
  document.addEventListener('sl:boards', fillBoards);

  const drop = h('div.dropzone', {
    tabindex: '0', role: 'button', 'aria-label': 'Add photos of your components',
    onclick: () => fileInput.click(),
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); },
    ondragleave: () => drop.classList.remove('over'),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); addFiles([...e.dataTransfer.files]); },
  },
  h('span.drop-icon', icon('upload')),
  h('strong', 'Drop photos of your components'),
  h('span.muted', 'or click to choose up to three. Shop photos work too.'));

  function renderThumbs() {
    thumbs.replaceChildren(...state.files.map((f, i) => {
      const url = URL.createObjectURL(f);
      return h('li.thumb', h('img', { src: url, alt: `Photo ${i + 1}`, onload: () => URL.revokeObjectURL(url) }),
        h('span.scan'),
        h('button.thumb-x', { type: 'button', 'aria-label': `Remove photo ${i + 1}`, onclick: () => { state.files.splice(i, 1); renderThumbs(); } }, icon('close')));
    }));
    identify.disabled = !state.files.length;
    drop.classList.toggle('has-files', state.files.length > 0);
  }

  async function addFiles(files) {
    const imgs = files.filter((f) => f.type.startsWith('image/'));
    if (files.length && !imgs.length) { toast('Those files are not images. Choose JPG, PNG or HEIC photos.', 'warn'); return; }
    const room = MAX_PHOTOS - state.files.length;
    if (imgs.length > room) toast(`Up to ${MAX_PHOTOS} photos per build. The first ${Math.max(room, 0)} were added.`, 'warn');
    for (const f of imgs.slice(0, Math.max(room, 0))) state.files.push(await shrinkImage(f));
    renderThumbs();
  }

  async function run() {
    if (!state.files.length) return;
    errorBox.textContent = '';
    panel.classList.add('busy');
    identify.disabled = true;
    identify.querySelector('span').textContent = 'Identifying parts';
    try {
      const build = await api.recognise(state.board, state.files);
      if (!build.recognised?.length) { noComponents(build); return; }
      state.build = build;
      state.confirmed = build.recognised.map((p) => ({ ...p }));
      state.ideas = null;
      go('confirm');
    } catch (e) {
      errorBox.textContent = e.message;
      panel.classList.remove('busy');
      identify.disabled = false;
      identify.querySelector('span').textContent = 'Identify parts';
    }
  }

  const panel = h('section.intake', { 'aria-labelledby': 'intake-h' },
    h('h2#intake-h', 'Start a build'),
    h('label.field', { for: 'board' }, h('span', 'Your board'), h('div.select-wrap', boardSelect)),
    drop, fileInput, thumbs,
    h('div.intake-actions',
      button('Use your phone camera', { kind: 'ghost', iconName: 'phone', onClick: () => openPhone(addFiles) }),
      identify),
    errorBox);
  renderThumbs();

  root.append(
    h('section.hero',
      h('div.hero-copy',
        h('h1.display', 'From a pile of parts to firmware that is proven before you wire it.'),
        h('p.lede', 'Photograph your components and say what the gadget should do. SiliconLoop designs the circuit, writes the Arduino code and runs it in a simulated Arduino until every behaviour you asked for passes.'),
        h('ol.flow',
          h('li', h('span.pad'), h('strong', 'Photograph'), h('span', 'the parts you have')),
          h('li', h('span.pad'), h('strong', 'Describe'), h('span', 'what it should do, or ask for ideas')),
          h('li', h('span.pad'), h('strong', 'Build'), h('span', 'from a verified twin, code and report')))),
      panel),
    faqSection(),
  );
  return () => document.removeEventListener('sl:boards', fillBoards);
}

/** Recognition found nothing to build with: say why, clear the photos and start again on the home page. */
function noComponents(build) {
  const seen = build.ignored || [];
  const why = build.image_quality === 'unclear'
    ? 'The photo looks too blurry or dark to make out any parts.'
    : seen.length
      ? `Muse Spark Open could only see ${seen.join(', ')}. Boards, breadboards, wires and loose resistors are not counted as parts.`
      : 'These photos do not seem to show any electronic components.';
  modal((close) => h('div.nofind',
    h('span.nofind-icon', icon('cross')),
    h('h2', 'No components found'),
    h('p', why),
    h('ul',
      h('li', 'Lay the parts out on a plain surface, in good light, with nothing on top of each other.'),
      h('li', 'Make sure each part fills a good part of the photo and is in focus.'),
      h('li', 'Shop or datasheet photos of the parts work too.')),
    button('Try other photos', { kind: 'primary', iconName: 'upload', onClick: close })),
  { label: 'No components found', onClose: () => {
    state.files = [];
    state.build = null;
    go('home', { noHistory: true });
    requestAnimationFrame(() => document.querySelector('.intake')?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  } });
}

function faqSection() {
  return h('section.faq', { 'aria-labelledby': 'faq-h' },
    h('div.faq-head', h('h2#faq-h', 'Questions'), h('p.muted', 'Short answers about boards, parts, safety and flashing.')),
    h('div.faq-list', FAQ.map(([q, a]) => h('details.faq-item', h('summary', h('span', q), h('span.chev')), h('p', a)))));
}

// ---------- About: a scrollable datasheet with a copper trace that follows your reading ----------

export function openAbout() {
  const sections = ABOUT.map((s, i) => h('section.ds-sec', { 'data-i': i },
    h('span.ds-pad', { 'aria-hidden': 'true' }),
    h('h3', s.title),
    ...(s.body || []).map((p) => h('p', p)),
    s.steps ? h('ol.ds-steps', s.steps.map(([t, d]) => h('li', h('strong', t), h('span', d)))) : null,
    s.list ? h('ul.ds-list', s.list.map((x) => h('li', x))) : null));
  const fill = h('span.ds-fill');
  const scroller = h('div.ds-scroll', { tabindex: '0' },
    h('header.ds-head',
      h('p.ds-doc', 'SiliconLoop datasheet, revision 3'),
      h('h2', 'Imagination to silicon, proven in simulation'),
      h('p.ds-sub', 'A build system that turns a photo and a sentence into Arduino firmware that has already passed its test.')),
    h('div.ds-body', h('span.ds-rail', { 'aria-hidden': 'true' }, fill), sections),
    h('footer.ds-foot', 'SiliconLoop is an AI-based product and can make mistakes. Built with Muse Spark Open.'));
  const { el } = modal(h('div.ds', scroller), { label: 'About SiliconLoop', className: 'datasheet' });
  const onScroll = () => {
    const max = scroller.scrollHeight - scroller.clientHeight;
    fill.style.transform = `scaleY(${max > 0 ? scroller.scrollTop / max : 1})`;
    const mid = scroller.getBoundingClientRect().top + scroller.clientHeight * 0.55;
    for (const s of sections) s.classList.toggle('lit', s.getBoundingClientRect().top < mid);
  };
  scroller.addEventListener('scroll', onScroll, { passive: true });
  requestAnimationFrame(onScroll);
  return el;
}

// ---------- phone camera hand-off ----------

function openPhone(addFiles) {
  let timer = 0, seen = 0, closed = false;
  const qrBox = h('div.qr', h('span.muted', 'Making a QR code'));
  const status = h('p.qr-status', { role: 'status' }, 'Waiting for photos from your phone.');
  const local = /^(localhost|127\.|\[::1\])/.test(location.hostname);
  const { close } = modal(h('div.phone',
    h('h2', 'Take the photos with your phone'),
    h('p.muted', 'Scan the code with your phone camera, take one to three photos of your parts and press Send. They appear here automatically.'),
    qrBox, status,
    local ? h('p.note', 'Your phone cannot open "localhost". Open SiliconLoop on this computer through its network address (for example http://192.168.1.20:8000) or use the deployed site, then scan again.') : null),
  { label: 'Use your phone camera', onClose: () => { closed = true; clearTimeout(timer); } });

  api.handoffCreate().then(async ({ token }) => {
    if (closed) return;
    const url = `${location.origin}/?m=${token}`;
    const svgText = await QRCode.toString(url, { type: 'svg', margin: 1, color: { dark: '#050607', light: '#eceef0' } });
    qrBox.innerHTML = svgText;
    qrBox.append(h('a.qr-url', { href: url, target: '_blank', rel: 'noopener' }, url.replace(/^https?:\/\//, '')));
    const poll = async () => {
      if (closed) return;
      try {
        const s = await api.handoffStatus(token);
        for (; seen < s.count; seen++) {
          const blob = await api.handoffPhoto(token, seen);
          await addFiles([new File([blob], `phone-${seen + 1}.jpg`, { type: blob.type || 'image/jpeg' })]);
        }
        if (s.count) status.textContent = `${s.count} photo${s.count === 1 ? '' : 's'} received. Close this when you are done.`;
        if (s.count >= MAX_PHOTOS) { status.textContent = 'Three photos received.'; setTimeout(close, 900); return; }
      } catch (e) {
        status.textContent = e.message;
        return;
      }
      timer = setTimeout(poll, 1500);
    };
    poll();
  }).catch((e) => { qrBox.replaceChildren(h('p.form-error', e.message)); });
}
