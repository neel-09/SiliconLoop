// Flash a verified .hex onto a real board over Web Serial (Chrome or Edge on a computer).
// Uno and Nano: STK500 v1 (Optiboot 115200 baud; old Nano bootloader 57600). Mega 2560: STK500 v2 (115200 baud).
// Every page is read back and compared before the board is released.

export const webSerialSupported = () => 'serial' in navigator;

const BOARDS = {
  uno: { proto: 1, bauds: [115200], page: 128 },
  nano: { proto: 1, bauds: [115200, 57600], page: 128 },
  nano_old: { proto: 1, bauds: [57600, 115200], page: 128 },
  mega: { proto: 2, bauds: [115200], page: 256 },
};

/** Intel HEX -> {data: Uint8Array, length} (length rounded up to whole words). */
export function parseHex(text) {
  const mem = new Uint8Array(256 * 1024).fill(0xff);
  let base = 0, top = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line.startsWith(':')) continue;
    const n = parseInt(line.substr(1, 2), 16), addr = parseInt(line.substr(3, 4), 16), type = parseInt(line.substr(7, 2), 16);
    if (type === 0) {
      for (let i = 0; i < n; i++) mem[base + addr + i] = parseInt(line.substr(9 + i * 2, 2), 16);
      top = Math.max(top, base + addr + n);
    } else if (type === 2) base = parseInt(line.substr(9, 4), 16) << 4;
    else if (type === 4) base = parseInt(line.substr(9, 4), 16) << 16;
    else if (type === 1) break;
  }
  if (!top) throw new Error('The firmware file is empty.');
  return { data: mem, length: top + (top % 2) };
}

class Port {
  constructor(port) { this.port = port; this.buf = []; this.reader = null; this.writer = null; this.closed = false; }
  async open(baud) {
    await this.port.open({ baudRate: baud, bufferSize: 4096 });
    this.writer = this.port.writable.getWriter();
    this.reader = this.port.readable.getReader();
    this.pump = (async () => {
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          for (const b of value) this.buf.push(b);
        }
      } catch { /* closed */ }
    })();
  }
  async close() {
    if (this.closed) return;
    this.closed = true;
    try { await this.reader?.cancel(); } catch { /* ignore */ }
    try { this.reader?.releaseLock(); } catch { /* ignore */ }
    try { this.writer?.releaseLock(); } catch { /* ignore */ }
    try { await this.port.close(); } catch { /* ignore */ }
  }
  async write(bytes) { await this.writer.write(new Uint8Array(bytes)); }
  drain() { this.buf.length = 0; }
  async read(n, timeout = 400) {
    const end = performance.now() + timeout;
    while (this.buf.length < n) {
      if (performance.now() > end) throw new Error('timeout');
      await sleep(4);
    }
    return this.buf.splice(0, n);
  }
  /** Pulse DTR/RTS: the auto-reset capacitor restarts the board into its bootloader. */
  async reset() {
    await this.port.setSignals({ dataTerminalReady: false, requestToSend: false });
    await sleep(120);
    await this.port.setSignals({ dataTerminalReady: true, requestToSend: true });
    await sleep(60);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- STK500 v1 (Optiboot) ----------
const OK = 0x10, INSYNC = 0x14, EOP = 0x20;

async function v1cmd(p, bytes, replyLen = 0, timeout = 400) {
  await p.write([...bytes, EOP]);
  const head = await p.read(1, timeout);
  if (head[0] !== INSYNC) throw new Error('not in sync');
  const data = replyLen ? await p.read(replyLen, timeout) : [];
  const tail = await p.read(1, timeout);
  if (tail[0] !== OK) throw new Error('command failed');
  return data;
}

async function v1sync(p) {
  for (let i = 0; i < 8; i++) {
    p.drain();
    try { await v1cmd(p, [0x30], 0, 200); return true; } catch { await sleep(30); }
  }
  return false;
}

async function flashV1(p, fw, pageSize, progress) {
  await v1cmd(p, [0x50]);                                   // enter program mode
  const pages = Math.ceil(fw.length / pageSize);
  for (let i = 0; i < pages; i++) {
    const addr = i * pageSize, chunk = fw.data.slice(addr, addr + pageSize);
    const w = addr >> 1;
    await v1cmd(p, [0x55, w & 0xff, (w >> 8) & 0xff]);     // load address (words)
    await v1cmd(p, [0x64, (chunk.length >> 8) & 0xff, chunk.length & 0xff, 0x46, ...chunk], 0, 1000);
    progress('write', (i + 1) / pages);
  }
  for (let i = 0; i < pages; i++) {
    const addr = i * pageSize, chunk = fw.data.slice(addr, addr + pageSize);
    const w = addr >> 1;
    await v1cmd(p, [0x55, w & 0xff, (w >> 8) & 0xff]);
    const got = await v1cmd(p, [0x74, (chunk.length >> 8) & 0xff, chunk.length & 0xff, 0x46], chunk.length, 1000);
    for (let k = 0; k < chunk.length; k++) if (got[k] !== chunk[k]) throw new Error(`verify failed at byte ${addr + k}`);
    progress('verify', (i + 1) / pages);
  }
  await v1cmd(p, [0x51]);                                   // leave program mode: the sketch starts
}

// ---------- STK500 v2 (Mega 2560 "wiring" bootloader) ----------
let seq = 1;
async function v2cmd(p, body, timeout = 600) {
  const s = seq; seq = (seq + 1) & 0xff;
  const msg = [0x1b, s, (body.length >> 8) & 0xff, body.length & 0xff, 0x0e, ...body];
  let ck = 0; for (const b of msg) ck ^= b;
  await p.write([...msg, ck]);
  const head = await p.read(5, timeout);
  if (head[0] !== 0x1b || head[4] !== 0x0e) throw new Error('bad reply');
  const len = (head[2] << 8) | head[3];
  const rest = await p.read(len + 1, timeout);
  const reply = rest.slice(0, len);
  if (reply[0] !== body[0] || reply[1] !== 0x00) throw new Error('command failed');
  return reply;
}

async function v2sync(p) {
  for (let i = 0; i < 8; i++) {
    p.drain();
    try { await v2cmd(p, [0x01], 250); return true; } catch { await sleep(40); }
  }
  return false;
}

async function flashV2(p, fw, pageSize, progress) {
  await v2cmd(p, [0x10, 200, 100, 25, 32, 0, 0x53, 3, 0xac, 0x53, 0x00, 0x00]);       // enter progmode ISP
  const pages = Math.ceil(fw.length / pageSize);
  const load = (addr) => { const w = (addr >> 1) | 0x80000000; return v2cmd(p, [0x06, (w >>> 24) & 0xff, (w >> 16) & 0xff, (w >> 8) & 0xff, w & 0xff]); };
  for (let i = 0; i < pages; i++) {
    const addr = i * pageSize, chunk = [...fw.data.slice(addr, addr + pageSize)];
    await load(addr);
    await v2cmd(p, [0x13, (chunk.length >> 8) & 0xff, chunk.length & 0xff, 0xc1, 0x0a, 0x40, 0x4c, 0x20, 0x00, 0x00, ...chunk], 2000);
    progress('write', (i + 1) / pages);
  }
  for (let i = 0; i < pages; i++) {
    const addr = i * pageSize, chunk = fw.data.slice(addr, addr + pageSize);
    await load(addr);
    const r = await v2cmd(p, [0x14, (chunk.length >> 8) & 0xff, chunk.length & 0xff, 0x20], 2000);
    const got = r.slice(2, 2 + chunk.length);
    for (let k = 0; k < chunk.length; k++) if (got[k] !== chunk[k]) throw new Error(`verify failed at byte ${addr + k}`);
    progress('verify', (i + 1) / pages);
  }
  await v2cmd(p, [0x11, 0x01, 0x01]);                                                  // leave progmode
}

/** progress(stage, fraction); stage: 'connect' | 'write' | 'verify' | 'done'. Resolves when the board runs the firmware. */
export async function flash(boardKey, hexText, progress = () => {}) {
  if (!webSerialSupported()) throw new Error('This browser cannot talk to USB serial ports. Use Chrome or Edge on a computer.');
  const cfg = BOARDS[boardKey] || BOARDS.uno;
  const fw = parseHex(hexText);
  const port = await navigator.serial.requestPort({});
  let lastErr = null;
  for (const baud of cfg.bauds) {
    const p = new Port(port);
    try {
      progress('connect', 0);
      await p.open(baud);
      await p.reset();
      const ok = cfg.proto === 2 ? await v2sync(p) : await v1sync(p);
      if (!ok) { lastErr = new Error('no bootloader answer'); await p.close(); continue; }
      progress('connect', 1);
      if (cfg.proto === 2) await flashV2(p, fw, cfg.page, progress); else await flashV1(p, fw, cfg.page, progress);
      await p.close();
      progress('done', 1);
      return { baud };
    } catch (e) {
      lastErr = e;
      await p.close();
      if (e.message.startsWith('verify')) break;
    }
  }
  const hint = boardKey.startsWith('nano')
    ? ' If this is a Nano clone, choose the other Nano option (old bootloader) and try again, and check the CH340 driver.'
    : ' Close the Arduino IDE serial monitor if it is open, check the cable carries data, and try again.';
  if (lastErr?.message?.startsWith('verify')) throw new Error(`The board did not store the firmware correctly (${lastErr.message}). Try again.`);
  throw new Error(`The board's bootloader did not answer.${hint}`);
}
