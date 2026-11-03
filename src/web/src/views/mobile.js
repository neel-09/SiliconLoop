// Phone capture page, opened from the QR code: take photos of the parts and send them to the computer.
import { api } from '../api.js';
import { h, icon, button, shrinkImage } from '../ui.js';

export function mobileView(root, token) {
  let sent = 0;
  const preview = h('ul.m-thumbs');
  const status = h('p.m-status', { role: 'status' }, 'Lay your parts out on a plain surface in good light, then take a photo.');
  const input = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: (e) => upload([...e.target.files]) });
  const take = button('Take a photo', { kind: 'primary', iconName: 'phone', onClick: () => input.click() });

  async function upload(files) {
    input.value = '';
    for (const f of files) {
      if (sent >= 3) break;
      take.disabled = true;
      status.textContent = 'Sending to your computer';
      try {
        const small = await shrinkImage(f, 1600, 0.86);
        await api.handoffUpload(token, small);
        sent++;
        const url = URL.createObjectURL(small);
        preview.append(h('li', h('img', { src: url, alt: `Photo ${sent}` }), h('span.m-ok', icon('check'))));
        status.textContent = sent >= 3 ? 'Three photos sent. You can put your phone down; carry on on your computer.'
          : `Photo ${sent} sent. It is on your computer now. Take another, or carry on there.`;
      } catch (e) {
        status.textContent = e.message;
      } finally {
        take.disabled = sent >= 3;
      }
    }
  }

  root.replaceChildren(h('main.mobile',
    h('div.m-brand', icon('chip'), h('span', 'SiliconLoop')),
    h('h1', 'Photograph your parts'),
    h('p.muted', 'Each photo goes straight to the SiliconLoop page on your computer. Up to three photos.'),
    take, input, status, preview,
    h('p.m-foot', 'SiliconLoop is an AI-based product and can make mistakes. Built with Muse Spark Open.')));
}
