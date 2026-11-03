// The digital twin: the circuit diagram drawn from the allocator's wiring, with every part driven live by the
// verified binary running in avr8js and the same part models the backend's simulation test used.
import '@wokwi/elements';
import { MCU } from '@sim/mcu.js';
import { attach } from '@sim/parts.js';
import { parseTelemetry } from '@sim/runner.js';
import { h, icon } from './ui.js';

const BOARD_TAG = { uno: 'wokwi-arduino-uno', nano: 'wokwi-arduino-nano', nano_old: 'wokwi-arduino-nano', mega: 'wokwi-arduino-mega' };
const WIRE_HEX = { red: '#e5484d', black: '#30353a', orange: '#ff9a3c', yellow: '#f5c84c', green: '#3fbf6a', blue: '#4f8ff7',
  white: '#eceee8', purple: '#a77bf3', grey: '#9aa3a8', brown: '#b07444', pink: '#ef6ea8' };
const SIGNAL_ORDER = ['yellow', 'green', 'blue', 'white', 'purple', 'grey', 'brown', 'pink'];

// Card pin -> Wokwi element pin, plus how the element shows the part's live state.
const PARTS = {
  led: { tag: 'wokwi-led', pins: { anode: 'A', cathode: 'C' }, setup: (el, o) => { el.color = o.color || 'red'; },
    out: (el, p) => { const lv = p.instances[0].get('level'); el.value = lv > 0.04; el.brightness = lv > 0.04 && lv < 0.96 ? Math.max(0.2, lv) : 1; } },
  rgb_led: { tag: 'wokwi-rgb-led', pins: { r: 'R', g: 'G', b: 'B', cathode: 'COM' },
    out: (el, p) => { el.ledRed = p.get('r') || 0; el.ledGreen = p.get('g') || 0; el.ledBlue = p.get('b') || 0; } },
  push_button: { tag: 'wokwi-pushbutton', pins: { a: '1.l', b: '2.r' }, setup: (el, o, set) => {
    el.color = 'green';
    el.addEventListener('button-press', () => set('pressed', true));
    el.addEventListener('button-release', () => set('pressed', false));
  }, in: (el, v) => { el.pressed = !!v.pressed; } },
  potentiometer: { tag: 'wokwi-potentiometer', pins: { left: 'VCC', wiper: 'SIG', right: 'GND' },
    setup: (el, o, set) => { el.min = 0; el.max = 1023; el.addEventListener('input', () => set('adc', Math.round(el.value))); },
    in: (el, v) => { el.value = v.adc; } },
  joystick: { tag: 'wokwi-analog-joystick', pins: { vcc: 'VCC', vrx: 'HORZ', vry: 'VERT', sw: 'SEL', gnd: 'GND' },
    in: (el, v) => { el.xValue = ((v.x ?? 512) - 512) / 512; el.yValue = ((v.y ?? 512) - 512) / 512; el.pressed = !!v.pressed; } },
  active_buzzer: { tag: 'wokwi-buzzer', pins: { pos: '2', neg: '1' }, out: (el, p) => { el.hasSignal = !!p.get('on'); } },
  sg90_servo: { tag: 'wokwi-servo', pins: { signal: 'PWM', vcc: 'V+', gnd: 'GND' }, out: (el, p) => { el.angle = p.get('angle') ?? 0; } },
  hc_sr04: { tag: 'wokwi-hc-sr04', pins: { vcc: 'VCC', trig: 'TRIG', echo: 'ECHO', gnd: 'GND' } },
  pir: { tag: 'wokwi-pir-motion-sensor', pins: { vcc: 'VCC', out: 'OUT', gnd: 'GND' } },
  sound_sensor: { tag: 'wokwi-small-sound-sensor', pins: { ao: 'AOUT', gnd: 'GND', vcc: 'VCC', do: 'DOUT' },
    in: (el, v) => { el.ledPower = true; el.ledSignal = (v.adc ?? 0) > 600; } },
  flame_sensor: { tag: 'wokwi-flame-sensor', pins: { vcc: 'VCC', gnd: 'GND', do: 'DOUT', ao: 'AOUT' },
    in: (el, v) => { el.ledPower = true; el.ledSignal = !!v.active; } },
  dht11: { tag: 'wokwi-dht22', pins: { vcc: 'VCC', data: 'SDA', gnd: 'GND' } },
  dht22: { tag: 'wokwi-dht22', pins: { vcc: 'VCC', data: 'SDA', gnd: 'GND' } },
  mpu6050: { tag: 'wokwi-mpu6050', pins: { vcc: 'VCC', gnd: 'GND', scl: 'SCL', sda: 'SDA', int: 'INT' } },
  lcd1602_i2c: { tag: 'wokwi-lcd1602', pins: { gnd: 'GND', vcc: 'VCC', sda: 'SDA', scl: 'SCL' }, setup: (el) => { el.pins = 'i2c'; },
    out: (el, p) => {
      const m = p.instances[0];
      const text = m.on ? m.lines().join('') : ' '.repeat(32);
      el.characters = Array.from(text, (c) => c.charCodeAt(0));
      el.backlight = m.backlight;
    } },
};

// ---------- hand-drawn parts (no matching Wokwi element) ----------

function customPart(kind, label, pinNames, w0) {
  const n = pinNames.length;
  if (kind === 'ldr') {
    const el = h('div.cpart.ldr', { html: `<svg width="34" height="70" viewBox="0 0 34 70"><defs><radialGradient id="ldrg"><stop offset="0" stop-color="#fff6c2"/><stop offset="1" stop-color="#fff6c200"/></radialGradient></defs>
      <circle class="ldr-glow" cx="17" cy="16" r="17" fill="url(#ldrg)" opacity="0"/>
      <circle cx="17" cy="16" r="12" fill="#e9b04a" stroke="#8a5a1a"/><path d="M9 12h4v4h4v-4h4v4h4M9 20h4v-4" fill="none" stroke="#7a2f12" stroke-width="1.6"/>
      <path d="M12 28v40M22 28v40" stroke="#b8bcc0" stroke-width="1.6"/></svg>` });
    return { el, w: 34, h: 70, pins: [{ name: 'a', x: 12, y: 68 }, { name: 'b', x: 22, y: 68 }] };
  }
  if (kind === 'oled') {
    const W = 150, H = 112;
    const canvas = h('canvas.oled-screen', { width: 128, height: 64 });
    const el = h('div.cpart.oled', { style: { width: `${W}px`, height: `${H}px` } },
      h('div.oled-pins', ['GND', 'VCC', 'SCL', 'SDA'].map((p) => h('span', p))), canvas);
    const xs = [48, 66, 84, 102];
    return { el, w: W, h: H, canvas, pins: ['gnd', 'vcc', 'scl', 'sda'].map((p, i) => ({ name: p, x: xs[i], y: 4 })) };
  }
  const W = Math.max(w0 || 0, 30 + n * 22, Math.round(label.length * 6.6 + 40)), H = 64;
  const el = h('div.cpart.module', { style: { width: `${W}px`, height: `${H}px` } },
    h('span.mod-led'), h('span.mod-name', label),
    h('div.mod-pins', pinNames.map((p) => h('span', p.label))));
  const step = (W - 20) / n;
  return { el, w: W, h: H, pins: pinNames.map((p, i) => ({ name: p.key, x: 10 + step * (i + 0.5), y: H - 2 })) };
}

// ---------- geometry ----------

async function mount(stage, tag, setupFn) {
  const el = document.createElement(tag);
  setupFn?.(el);
  const wrap = h('div.tpart', el);
  stage.append(wrap);
  await customElements.whenDefined(tag);
  await el.updateComplete;
  return { el, wrap, w: el.offsetWidth || el.getBoundingClientRect().width, h: el.offsetHeight || el.getBoundingClientRect().height, pins: el.pinInfo || [] };
}

function pinMap(pins) { return Object.fromEntries(pins.map((p) => [p.name, p])); }

function sideOf(p, w, hgt) {
  if (p.y <= hgt * 0.3) return 'up';
  if (p.y >= hgt * 0.7) return 'down';
  return p.x >= w / 2 ? 'right' : 'left';
}

const DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };

function route(a, aDir, b, bDir, lane, box, bBox, fromBoard = false) {
  const s1 = 14 + lane * 5, s2 = 12 + (lane % 4) * 4;
  const A = [a.x + DIRS[aDir][0] * s1, a.y + DIRS[aDir][1] * s1];
  let B = [b.x + DIRS[bDir][0] * s2, b.y + DIRS[bDir][1] * s2];
  const tail = [];
  // The part's pin faces away from the wire's source: leave the pin, then go round the side of the part.
  if (bBox && (bDir === 'up' || bDir === 'down') && Math.sign(a.y - b.y) !== DIRS[bDir][1] && Math.abs(a.y - b.y) > 20) {
    const left = Math.abs(a.x - bBox.x) < Math.abs(a.x - (bBox.x + bBox.w));
    const sx = left ? bBox.x - 12 - lane * 4 : bBox.x + bBox.w + 12 + lane * 4;
    const sy = bDir === 'up' ? bBox.y + bBox.h + 14 : bBox.y - 14;
    tail.unshift(B, [sx, B[1]]);
    B = [sx, sy];
    bDir = bDir === 'up' ? 'down' : 'up';
  }
  const pts = [[a.x, a.y], A];
  const vertA = aDir === 'up' || aDir === 'down', vertB = bDir === 'up' || bDir === 'down';
  // Opposite sides of the board: go around its left edge so no wire crosses the board.
  const crosses = fromBoard && vertA && ((aDir === 'down' && B[1] < box.y) || (aDir === 'up' && B[1] > box.y + box.h));
  if (crosses) {
    const gx = box.x - 26 - lane * 7;
    pts.push([gx, A[1]], [gx, B[1]], B);
  } else if (vertA && vertB) {
    const my = (A[1] + B[1]) / 2 + (lane - 3) * 4;
    pts.push([A[0], my], [B[0], my], B);
  } else if (vertA && bBox && (a.y > bBox.y + bBox.h || a.y < bBox.y)) {
    // side pin (e.g. a servo lead): approach from below/above the part, never across it
    const yc = a.y > bBox.y + bBox.h ? bBox.y + bBox.h + 12 + lane * 4 : bBox.y - 12 - lane * 4;
    pts.push([A[0], yc], [B[0], yc], B);
  } else if (vertA) {
    pts.push([A[0], B[1]], B);
  } else if (vertB) {
    pts.push([B[0], A[1]], B);
  } else {
    const mx = (A[0] + B[0]) / 2 + (lane - 3) * 4;
    pts.push([mx, A[1]], [mx, B[1]], B);
  }
  pts.push(...tail.reverse(), [b.x, b.y]);
  return pts;
}

function pathD(pts) {
  // orthogonal polyline with small rounded corners
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
    const l1 = Math.hypot(x1 - x0, y1 - y0), l2 = Math.hypot(x2 - x1, y2 - y1);
    const r = Math.min(6, l1 / 2, l2 / 2);
    if (r < 0.5) { d += ` L${x1},${y1}`; continue; }
    const ax = x1 - ((x1 - x0) / l1) * r, ay = y1 - ((y1 - y0) / l1) * r;
    const bx = x1 + ((x2 - x1) / l2) * r, by = y1 + ((y2 - y1) / l2) * r;
    d += ` L${ax},${ay} Q${x1},${y1} ${bx},${by}`;
  }
  const e = pts[pts.length - 1];
  return `${d} L${e[0]},${e[1]}`;
}

const ohms = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

// ---------- the twin ----------

/** Build the circuit diagram + simulator in `host`. twin = GET /twin, build = the build, report = GET /report. */
export async function createTwin(host, { build, twin, report, catalogue }) {
  const wiring = build.wiring, board = build.board;
  const cards = Object.fromEntries((catalogue || []).map((c) => [c.key, c]));
  const stage = h('div.cstage');
  const wires = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  wires.classList.add('wires');
  const viewport = h('div.stage-viewport', stage);
  host.replaceChildren(viewport);
  stage.append(wires);

  // board
  const B = await mount(stage, BOARD_TAG[board] || 'wokwi-arduino-uno');
  const bPins = pinMap(B.pins);
  const boardNode = (pin) => {
    if (pin === '5V') return bPins['5V'] || bPins['5V.1'];
    if (pin === 'GND') return null; // chosen per wire
    const name = pin.startsWith('D') ? pin.slice(1) : pin;
    return bPins[name];
  };
  const gnds = B.pins.filter((p) => p.name.startsWith('GND'));

  // parts
  const items = {};      // id -> {el, wrap, w, h, pins, kind, part}
  for (const p of wiring.parts) {
    const def = PARTS[p.part];
    const card = cards[p.part];
    const labels = card ? Object.entries(card.pins).map(([key, label]) => ({ key, label: label.replace(/ \(.*$/, '').slice(0, 6) })) : Object.keys(p.pins).map((k) => ({ key: k, label: k }));
    let it;
    if (def) {
      it = await mount(stage, def.tag, (el) => def.setup?.(el, p.options || {}, (k, v) => setInput(p.id, k, v)));
      it.map = def.pins;
    } else {
      const kind = p.part === 'ldr' ? 'ldr' : p.part === 'ssd1306' ? 'oled' : 'module';
      const c = customPart(kind, (card?.name || p.part).replace(/\s*\(.*\)/, '').slice(0, 18), labels);
      const wrap = h('div.tpart', c.el);
      stage.append(wrap);
      it = { ...c, wrap, map: Object.fromEntries((c.pins || []).map((x) => [x.name, x.name])) };
      it.pins = c.pins;
    }
    it.def = def; it.part = p; it.id = p.id;
    items[p.id] = it;
  }
  // extras: resistors and an external supply
  const extras = {};
  for (const e of wiring.extras || []) {
    if (e.part === 'resistor') {
      const it = await mount(stage, 'wokwi-resistor', (el) => { el.value = String(e.ohms); });
      extras[e.id] = { ...it, e, rot: true };
    } else if (e.part.startsWith('external')) {
      const v = e.volts ?? 5;
      const c = customPart('module', `${v} V supply`, [{ key: '+V', label: `+${v}V` }, { key: 'GND', label: 'GND' }]);
      const wrap = h('div.tpart', c.el); stage.append(wrap);
      extras[e.id] = { ...c, wrap, e, map: { '+V': '+V', '+5V': '+V', GND: 'GND' } };
    }
  }

  // ---- layout: parts above / below / right of the board, ordered by the board pins they use ----
  const GAP = 132;
  const bx = 60, by = 0;
  const groups = { up: [], down: [], right: [] };
  for (const it of Object.values(items)) {
    const sides = { up: 0, down: 0, right: 0, left: 0 };
    let meanX = 0, n = 0;
    for (const bp of Object.values(it.part.pins)) {
      const pin = boardNode(bp);
      if (!pin) continue;
      sides[sideOf(pin, B.w, B.h)]++; meanX += pin.x; n++;
    }
    let side = Object.entries(sides).sort((a, b) => b[1] - a[1])[0][0];
    if (side === 'left') side = 'up';
    it.side = side; it.meanX = n ? meanX / n : B.w / 2;
    groups[side].push(it);
  }
  const place = (it, x, y) => { it.x = x; it.y = y; };
  const rowLayout = (list, top) => {
    list.sort((a, b) => a.meanX - b.meanX);
    const total = list.reduce((s, it) => s + it.w, 0) + (list.length - 1) * 56;
    let x = bx + Math.max(0, (B.w - total) / 2);
    for (const it of list) {
      place(it, x, top ? by - GAP - it.h : by + B.h + GAP);
      x += it.w + 56;
    }
  };
  const NO_FLIP = new Set(['ssd1306', 'lcd1602_i2c', 'sg90_servo']);
  for (const it of Object.values(items)) {
    if (NO_FLIP.has(it.part.part) || it.side === 'right') continue;
    const sig = Object.keys(it.part.pins).map((cp) => it.pins.find((x) => x.name === (it.map[cp] ?? cp))).filter(Boolean);
    const dirs = sig.map((p) => sideOf(p, it.w, it.h));
    const away = it.side === 'up' ? 'up' : 'down';
    if (!(dirs.length && dirs.filter((d) => d === away).length > dirs.length / 2)) continue;
    if (it.el.classList?.contains('module')) {
      it.el.classList.add(away === 'down' ? 'pins-top' : 'pins-bottom');
      it.pins = it.pins.map((q) => ({ ...q, y: away === 'down' ? 2 : it.h - 2 }));
    } else it.flip = true;
  }
  rowLayout(groups.up, true);
  rowLayout(groups.down, false);
  let ry = by;
  for (const it of groups.right.sort((a, b) => a.meanX - b.meanX)) { place(it, bx + B.w + GAP, ry); ry += it.h + 40; }
  place(B, bx, by);

  // resistors sit in the wire channel next to the part pin they serve, rotated vertical
  const rList = Object.values(extras).filter((x) => x.e.part === 'resistor');
  for (const r of rList) {
    const [pid, ppin] = r.e.for.split('.');
    const it = items[pid];
    const local = it && (it.pins.find((p) => p.name === it.map[ppin]) || it.pins[0]);
    const px = it ? it.x + (local?.x ?? it.w / 2) : bx;
    const rw = r.h, rh = r.w; // rotated
    const sideways = r.e.purpose === 'pulldown' ? 26 : 0;
    const x = px - rw / 2 + sideways;
    const y = it?.side === 'down' ? it.y - GAP / 2 - rh / 2 : it ? it.y + it.h + GAP / 2 - rh / 2 : by - 80;
    r.x = x; r.y = y; r.rw = rw; r.rh = rh;
    r.ccw = it?.side !== 'down'; // part above the board: pin 1 (board side) at the bottom
  }
  for (const sd of [true, false]) {
    const row = rList.filter((r) => r.ccw === sd).sort((a, b) => a.x - b.x);
    for (let i = 1; i < row.length; i++) {
      const min = row[i - 1].x + row[i - 1].rw + 30;
      if (row[i].x < min && Math.abs(row[i].y - row[i - 1].y) < row[i].rh) row[i].x = min;
    }
  }
  Object.values(extras).filter((x) => x.e.part.startsWith('external')).forEach((psu, i) => {
    psu.x = bx - psu.w - 90; psu.y = by + B.h + GAP + i * (psu.h + 40);
  });

  // normalise so everything is in positive space
  const all = [B, ...Object.values(items), ...Object.values(extras)];
  const minX = Math.min(...all.map((it) => it.x)) - 70, minY = Math.min(...all.map((it) => it.y)) - 50;
  for (const it of all) { it.x -= minX; it.y -= minY; }
  const W = Math.max(...all.map((it) => it.x + (it.rw || it.w))) + 70;
  const H = Math.max(...all.map((it) => it.y + (it.rh || it.h))) + 40;
  for (const it of all) {
    it.wrap.style.left = `${it.x}px`; it.wrap.style.top = `${it.y}px`;
    if (it.rot) { it.wrap.style.transform = it.ccw ? `translate(0,${it.w}px) rotate(-90deg)` : `translate(${it.h}px,0) rotate(90deg)`; it.wrap.style.transformOrigin = '0 0'; }
    if (it.flip) { it.wrap.style.transform = 'rotate(180deg)'; it.wrap.style.transformOrigin = `${it.w / 2}px ${it.h / 2}px`; }
  }
  stage.style.width = `${W}px`; stage.style.height = `${H}px`;
  wires.setAttribute('width', W); wires.setAttribute('height', H); wires.setAttribute('viewBox', `0 0 ${W} ${H}`);

  // absolute pin positions
  const abs = (it, p) => (it.rot ? (it.ccw ? { x: it.x + p.y, y: it.y + (it.w - p.x) } : { x: it.x + (it.h - p.y), y: it.y + p.x })
    : it.flip ? { x: it.x + it.w - p.x, y: it.y + it.h - p.y } : { x: it.x + p.x, y: it.y + p.y });
  const boardAbs = (name) => abs(B, name);
  const box = { x: B.x, y: B.y, w: B.w, h: B.h };
  let gndTurn = 0;
  function nodePos(node, towards) {
    const [id, pin] = node.split('.');
    if (id === 'board') {
      let p;
      if (pin === 'GND') {
        const cands = gnds.length ? gnds : B.pins;
        p = towards ? cands.slice().sort((a, b) => Math.abs(B.y + a.y - towards.y) - Math.abs(B.y + b.y - towards.y))[gndTurn++ % Math.min(2, cands.length)] : cands[0];
      } else p = boardNode(pin);
      if (!p) return null;
      return { ...boardAbs(p), dir: sideOf(p, B.w, B.h), label: `Arduino ${pin}` };
    }
    const it = items[id] || extras[id];
    if (!it) return null;
    const ename = it.map ? (it.map[pin] ?? pin) : pin;
    const p = it.pins.find((x) => x.name === ename) || (it.rot ? it.pins.find((x) => x.name === pin) : null);
    if (!p) return null;
    const a = abs(it, p);
    const lw = it.rot ? it.h : it.w, lh = it.rot ? it.w : it.h;
    const rel = { x: a.x - it.x, y: a.y - it.y };
    return { ...a, dir: sideOf(rel, lw, lh), label: `${id} ${pin}`, item: it, box: { x: it.x, y: it.y, w: lw, h: lh } };
  }

  // colours: the report's net colours, so the diagram and the written steps always agree
  const colours = { ...(report?.net_colours || {}) };
  const resistorNet = {};
  for (const net of wiring.nets) for (const n of net.nodes) if (/^r\d+\.1$/.test(n)) resistorNet[n.split('.')[0]] = net.name;
  let spare = 0;
  const colourOf = (net) => {
    if (colours[net.name]) return colours[net.name];
    if (net.name.startsWith('EXT_')) return 'orange';
    const r = net.nodes.find((n) => /^r\d+\./.test(n));
    if (r && colours[resistorNet[r.split('.')[0]]]) return colours[resistorNet[r.split('.')[0]]];
    if (/^r\d+\.2$/.test(net.nodes[0] || '') && net.name !== 'GND') return colours[resistorNet[net.nodes[0].split('.')[0]]] || 'yellow';
    return (colours[net.name] = SIGNAL_ORDER[spare++ % SIGNAL_ORDER.length]);
  };

  let lane = 0;
  const NS = 'http://www.w3.org/2000/svg';
  const drawWire = (pts, colour, title, ends) => {
    const d = pathD(pts);
    const g = document.createElementNS(NS, 'g');
    g.classList.add('wire');
    const t = document.createElementNS(NS, 'title'); t.textContent = title;
    const under = document.createElementNS(NS, 'path'); under.setAttribute('d', d); under.setAttribute('class', 'w-under');
    const top = document.createElementNS(NS, 'path'); top.setAttribute('d', d); top.setAttribute('class', 'w-top'); top.setAttribute('stroke', colour);
    g.append(t, under, top);
    for (const e of ends) {
      const c = document.createElementNS(NS, 'circle'); c.setAttribute('cx', e[0]); c.setAttribute('cy', e[1]); c.setAttribute('r', 2.6); c.setAttribute('fill', colour);
      g.append(c);
    }
    wires.append(g);
  };

  // Breadboard power rails beside each row of parts, as in the report: the board feeds the rails, parts drop onto them.
  const sideOfItem = (id) => {
    const it = items[id] || extras[id];
    if (!it) return null;
    if (it.e?.for) return items[it.e.for.split('.')[0]]?.side === 'down' ? 'down' : 'up';
    return it.side === 'down' ? 'down' : 'up';
  };
  const railNets = wiring.nets.filter((n) => n.name === '5V' || n.name === 'GND');
  const railSides = new Set();
  for (const net of railNets) for (const n of net.nodes) if (!n.startsWith('board.')) { const sd = sideOfItem(n.split('.')[0]); if (sd) railSides.add(sd); }
  const rails = {};
  const rowItems = (sd) => Object.values(items).filter((it) => (sd === 'down' ? it.side === 'down' : it.side !== 'down'));
  for (const sd of railSides) {
    const list = rowItems(sd);
    const xs = [B.x - 46, B.x + B.w + 20, ...list.flatMap((it) => [it.x - 20, it.x + it.w + 20])];
    const edge = sd === 'up' ? Math.max(...list.map((it) => it.y + it.h), B.y - GAP) : Math.min(...list.map((it) => it.y), B.y + B.h + GAP);
    const dy = sd === 'up' ? 1 : -1;
    rails[sd] = { x0: Math.min(...xs), x1: Math.max(...xs), '5V': edge + dy * 24, GND: edge + dy * 38 };
  }
  for (const [sd, r] of Object.entries(rails)) {
    for (const net of ['5V', 'GND']) {
      const y = r[net], col = net === '5V' ? WIRE_HEX.red : '#59616a';
      const line = document.createElementNS(NS, 'path');
      line.setAttribute('d', `M${r.x0},${y} L${r.x1},${y}`);
      line.setAttribute('class', `rail rail-${net === '5V' ? 'plus' : 'minus'}`);
      line.setAttribute('stroke', col);
      wires.append(line);
      const lbl = document.createElementNS(NS, 'text');
      lbl.setAttribute('x', r.x1 + 6); lbl.setAttribute('y', y + 4);
      lbl.setAttribute('class', 'rail-label'); lbl.textContent = net === '5V' ? '+ 5V' : '− GND';
      wires.append(lbl);
    }
  }
  for (const net of railNets) {
    const colour = WIRE_HEX[net.name === '5V' ? 'red' : 'black'];
    const used = new Set();
    for (const n of net.nodes) {
      if (n.startsWith('board.')) continue;
      const sd = sideOfItem(n.split('.')[0]);
      const r = rails[sd];
      const b = nodePos(n);
      if (!r || !b) continue;
      used.add(sd);
      const y = r[net.name];
      const toward = sd === 'up' ? 'down' : 'up';
      let pts;
      if (b.dir === toward || b.dir === 'left' || b.dir === 'right') {
        pts = b.dir === toward ? [[b.x, b.y], [b.x, y]] : [[b.x, b.y], [b.x + DIRS[b.dir][0] * 12, b.y], [b.x + DIRS[b.dir][0] * 12, y]];
      } else {
        pts = route({ x: b.x, y, dir: toward }, sd === 'up' ? 'up' : 'down', b, b.dir, lane++ % 8, box, b.box);
      }
      drawWire(pts, colour, `${b.label} → ${net.name === '5V' ? '+' : '−'} rail`, [pts[0], pts[pts.length - 1]]);
    }
    // the board feeds each rail it is needed on
    for (const sd of used) {
      const r = rails[sd], y = r[net.name];
      const cands = net.name === '5V' ? B.pins.filter((p) => p.name === '5V' || p.name.startsWith('5V')) : gnds;
      const near = cands.find((p) => sideOf(p, B.w, B.h) === sd) || cands[0];
      if (!near) continue;
      const a = boardAbs(near), aDir = sideOf(near, B.w, B.h);
      let pts;
      if (aDir === sd) pts = [[a.x, a.y], [a.x, y]];
      else {
        const s1 = 16 + (net.name === '5V' ? 0 : 7);
        const gx = r.x0 + (net.name === '5V' ? 0 : 7);
        pts = [[a.x, a.y], [a.x, a.y + DIRS[aDir][1] * s1], [gx, a.y + DIRS[aDir][1] * s1], [gx, y]];
      }
      drawWire(pts, colour, `Arduino ${net.name} → ${net.name === '5V' ? '+' : '−'} rail`, [pts[0], pts[pts.length - 1]]);
    }
  }

  for (const net of wiring.nets) {
    if (net.name === '5V' || net.name === 'GND') continue;
    const nodes = net.nodes.filter((n) => nodePos(n));
    if (nodes.length < 2) continue;
    const hub = nodes.find((n) => n.startsWith('board.')) || nodes.find((n) => /^r\d+\./.test(n)) || nodes[0];
    const colour = WIRE_HEX[colourOf(net)] || WIRE_HEX.yellow;
    for (const n of nodes) {
      if (n === hub) continue;
      const b = nodePos(n);
      const a = nodePos(hub, b);
      if (!a || !b) continue;
      const pts = route(a, a.dir, b, b.dir, lane++ % 8, box, b.box, hub.startsWith('board.'));
      drawWire(pts, colour, `${a.label} → ${b.label}`, [[a.x, a.y], [b.x, b.y]]);
    }
  }

  // labels: part id and live reading, in silkscreen style
  for (const it of [...Object.values(items)]) {
    it.badge = h('div.tbadge', h('span.tb-id', it.id), h('span.tb-v'));
    it.badge.style.left = `${it.x + it.w / 2}px`;
    it.badge.style.top = it.side === 'down' ? `${it.y + it.h + 8}px` : `${it.y - 26}px`;
    stage.append(it.badge);
  }
  for (const r of rList) {
    const tag = h('div.rbadge', `${ohms(r.e.ohms)}Ω`);
    tag.style.left = `${r.x + r.rw + 4}px`; tag.style.top = `${r.y + r.rh / 2 - 8}px`;
    stage.append(tag);
  }

  // fit the stage into the viewport
  const fit = () => {
    const k = Math.min(1.25, (viewport.clientWidth - 8) / W, Math.max(360, Math.min(window.innerHeight * 0.74, 860)) / H);
    stage.style.transform = `scale(${k})`;
    viewport.style.height = `${H * k}px`;
  };
  const ro = new ResizeObserver(fit); ro.observe(viewport); fit();

  // ---------------- simulation ----------------
  const spec = twin.spec;
  let mcu, models, inputs = {}, running = false, raf = 0, lastFrame = 0, speed = 1, simStart = 0;
  const listeners = { serial: [], telemetry: [], tick: [] };

  function bootSim() {
    mcu = new MCU(spec.board, spec.hex);
    models = {};
    for (const inst of spec.parts) models[inst.id] = attach(mcu, inst);
    for (const [id, vals] of Object.entries(spec.defaults || {})) {
      for (const [k, v] of Object.entries(vals)) if (inputs[id]?.[k] === undefined) (inputs[id] ||= {})[k] = v;
    }
    for (const [id, vals] of Object.entries(inputs)) for (const [k, v] of Object.entries(vals)) models[id]?.set(k, v);
    mcu.onLine((line) => {
      const tel = parseTelemetry(line);
      if (tel) listeners.telemetry.forEach((f) => f(tel));
      else listeners.serial.forEach((f) => f(line));
    });
    simStart = 0;
  }

  function setInput(id, k, v) {
    (inputs[id] ||= {})[k] = v;
    models?.[id]?.set(k, v);
    const it = items[id];
    it?.def?.in?.(it.el, inputs[id]);
    refreshBadge(id);
  }

  const UNITS = { distance_cm: ' cm', temperature_c: ' °C', humidity_pct: ' %', pitch_deg: '°', roll_deg: '°' };
  function refreshBadge(id) {
    const it = items[id];
    if (!it?.badge) return;
    const io = twin.io[id] || { in: {}, out: {} };
    const parts = [];
    for (const k of Object.keys(io.in)) {
      const v = inputs[id]?.[k];
      if (v === undefined) continue;
      parts.push(typeof v === 'boolean' ? `${k} ${v ? 'on' : 'off'}` : `${Math.round(v * 10) / 10}${UNITS[k] || ''}`);
    }
    for (const [k, t] of Object.entries(io.out)) {
      if (t === 'str') continue;
      const v = models?.[id]?.get(k);
      if (v === undefined) continue;
      parts.push(t === 'bool' ? (v ? 'on' : 'off') : t === 'angle' ? `${Math.round(v)}°` : t === 'fraction' ? `${Math.round(v * 100)}%` : String(v));
    }
    it.badge.lastChild.textContent = parts.join('  ');
  }

  function render() {
    for (const it of Object.values(items)) {
      const m = models[it.id];
      if (!m) continue;
      it.def?.out?.(it.el, m);
      if (it.part.part === 'ssd1306' && it.canvas) drawOled(it.canvas, m.instances[0]);
      if (it.part.part === 'ldr') it.el.querySelector('.ldr-glow')?.setAttribute('opacity', String(Math.min(1, (inputs[it.id]?.adc ?? 512) / 1023)));
      if (!it.def && it.el.classList.contains('module')) {
        const io = twin.io[it.id] || {};
        const on = io.in && 'active' in io.in ? inputs[it.id]?.active : io.out && 'active' in io.out ? m.get('active') : false;
        it.el.classList.toggle('on', !!on);
      }
      refreshBadge(it.id);
    }
  }

  function frame(now) {
    if (!running) return;
    const dt = Math.min(50, now - (lastFrame || now));
    lastFrame = now;
    const t0 = performance.now();
    let sim = 0;
    while (sim < dt && performance.now() - t0 < 14 && !mcu.crashed) { mcu.run(2); sim += 2; }
    speed = dt > 0 ? sim / dt : 1;
    render();
    listeners.tick.forEach((f) => f({ ms: mcu.timeMs, speed, crashed: mcu.crashed }));
    raf = requestAnimationFrame(frame);
  }

  function start() { if (running) return; running = true; lastFrame = 0; raf = requestAnimationFrame(frame); }
  function stop() { running = false; cancelAnimationFrame(raf); }
  function reset() { const was = running; stop(); bootSim(); render(); if (was) start(); }

  bootSim();
  for (const [id] of Object.entries(items)) items[id].def?.in?.(items[id].el, inputs[id] || {});
  render();

  return {
    start, stop, reset, setInput, inputs: () => inputs,
    get running() { return running; },
    on: (ev, fn) => listeners[ev].push(fn),
    destroy() { stop(); ro.disconnect(); },
  };
}

function drawOled(canvas, m) {
  const g = canvas.getContext('2d');
  const img = g.createImageData(128, 64);
  const on = m.on && m.pump;
  for (let page = 0; page < 8; page++) {
    const row = m.ram[page];
    for (let x = 0; x < 128; x++) {
      const b = on ? row[x] : 0;
      for (let bit = 0; bit < 8; bit++) {
        const i = ((page * 8 + bit) * 128 + x) * 4;
        const lit = b & (1 << bit);
        img.data[i] = lit ? 150 : 8; img.data[i + 1] = lit ? 220 : 14; img.data[i + 2] = lit ? 255 : 26; img.data[i + 3] = 255;
      }
    }
  }
  g.putImageData(img, 0, 0);
}

/** Input controls for every simulated sensor or control: sliders for readings, switches for on/off signals. */
export function twinControls(twinObj, twinSpec) {
  const RANGE = { adc: [0, 1023, 1], distance: [2, 400, 1], temp: [-40, 80, 0.5], pct: [0, 100, 1], tilt: [-90, 90, 1], angle: [0, 180, 1], fraction: [0, 1, 0.01] };
  const NAMES = { adc: 'Reading', distance_cm: 'Distance', pressed: 'Pressed', active: 'Triggered', temperature_c: 'Temperature',
    humidity_pct: 'Humidity', pitch_deg: 'Pitch', roll_deg: 'Roll', x: 'X axis', y: 'Y axis' };
  const UNIT = { distance: 'cm', temp: '°C', pct: '%', tilt: '°', angle: '°' };
  const rows = [];
  const inputs = twinObj.inputs();
  for (const [id, io] of Object.entries(twinSpec.io)) {
    for (const [k, t] of Object.entries(io.in)) {
      const v = inputs[id]?.[k];
      const label = h('span.ctl-label', h('strong', id), ` ${NAMES[k] || k}`);
      if (t === 'bool') {
        const sw = h('button.switch', { type: 'button', role: 'switch', 'aria-checked': String(!!v), 'aria-label': `${id} ${NAMES[k] || k}`,
          onclick: () => { const nv = sw.getAttribute('aria-checked') !== 'true'; sw.setAttribute('aria-checked', String(nv)); twinObj.setInput(id, k, nv); } },
        h('span.knob'));
        rows.push(h('div.ctl', label, sw));
      } else {
        const [min, max, step] = RANGE[t] || [0, 1023, 1];
        const out = h('output', `${v}${UNIT[t] ? ` ${UNIT[t]}` : ''}`);
        const range = h('input', { type: 'range', min, max, step, value: v ?? min, 'aria-label': `${id} ${NAMES[k] || k}`,
          oninput: (e) => { const nv = +e.target.value; out.textContent = `${nv}${UNIT[t] ? ` ${UNIT[t]}` : ''}`; twinObj.setInput(id, k, nv); } });
        rows.push(h('div.ctl', label, range, out));
      }
    }
  }
  return rows.length ? h('div.controls', rows) : h('p.muted', 'This gadget has no inputs to change. Watch it run.');
}

export { icon };
