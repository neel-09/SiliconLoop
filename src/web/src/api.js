// Backend API calls. Every route lives under /api on the same origin as the site.

async function request(path, opts = {}) {
  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch {
    throw new Error('SiliconLoop could not reach its server. Check your connection and try again.');
  }
  const type = res.headers.get('content-type') || '';
  const body = type.includes('json') ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof body === 'object' ? body.detail : body;
    const msg = Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : detail;
    throw new Error(msg || `The server answered ${res.status}.`);
  }
  return body;
}

const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const api = {
  boards: () => request('/boards'),
  parts: () => request('/parts'),
  recognise(board, files) {
    const fd = new FormData();
    fd.append('board', board);
    files.slice(0, 3).forEach((f, i) => fd.append(`photo${i + 1}`, f, f.name || `photo${i + 1}.jpg`));
    return request('/builds', { method: 'POST', body: fd });
  },
  ideas: (id, parts) => request(`/builds/${id}/ideas`, json('POST', { parts })),
  build: (id) => request(`/builds/${id}`),
  twin: (id) => request(`/builds/${id}/twin`),
  report: (id) => request(`/builds/${id}/report`),
  reportMd: (id) => request(`/builds/${id}/report.md`),
  sketch: (id) => request(`/builds/${id}/sketch.ino`),
  hex: (id) => request(`/builds/${id}/firmware.hex`),
  chat: (id, messages) => request(`/builds/${id}/chat`, json('POST', { messages })),
  handoffCreate: () => request('/handoff', { method: 'POST' }),
  handoffStatus: (token) => request(`/handoff/${token}`),
  async handoffPhoto(token, i) {
    const res = await fetch(`/api/handoff/${token}/${i}`);
    if (!res.ok) throw new Error('The photo from your phone could not be loaded.');
    return res.blob();
  },
  handoffUpload(token, file) {
    const fd = new FormData();
    fd.append('photo', file, file.name || 'phone.jpg');
    return request(`/handoff/${token}`, { method: 'POST', body: fd });
  },

  /** Design + firmware as a stream of trace events. onEvent gets every parsed event; resolves on done/error. */
  async run(id, parts, wish, onEvent, signal) {
    let res;
    try {
      res = await fetch(`/api/builds/${id}/run`, { ...json('POST', { parts, wish }), signal });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      throw new Error('SiliconLoop could not reach its server. Check your connection and try again.');
    }
    if (!res.ok || !res.body) {
      const t = await res.text();
      let msg = t;
      try { msg = JSON.parse(t).detail; } catch { /* plain text */ }
      throw new Error(msg || `The server answered ${res.status}.`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', last = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const data = chunk.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim()).join('');
        if (!data) continue;
        const ev = JSON.parse(data);
        last = ev;
        onEvent(ev);
      }
    }
    if (!last || (last.event !== 'done' && last.event !== 'error')) {
      throw new Error('The connection to the build closed early. Press Try again.');
    }
    return last;
  },
};
