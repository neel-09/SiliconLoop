// Reusable simulation model types (declared in siliconloop/data/sim_models.json). Nothing here knows a part name or a
// part's pin names: a part card lists model instances, the backend binds their terminals to board pins and passes
// params, and rules use the card's rule keys, which map to each model's native keys.
// Model API: new Model(mcu, {pins: {terminal: boardPin|null}, params}); set(nativeKey, v); get(nativeKey); state().
import { GLYPHS_5X7 } from './fonts.js';

/** Tracks a digital output pin: level history for duty cycle and high-pulse widths (for servos). */
class PinMonitor {
  constructor(mcu, pin) {
    this.mcu = mcu; this.pin = pin;
    this.edges = [];                 // [timeMs, high]
    this.high = mcu.isHigh(pin);
    this.lastRise = null;
    this.pulses = [];                // [endMs, widthUs]
    mcu.onEdge(pin, () => this._update());
    mcu.onPinChange(pin, () => this._update());
  }
  _update() {
    const h = this.mcu.isHigh(this.pin), t = this.mcu.timeMs;
    if (h === this.high) return;
    this.high = h;
    this.edges.push([t, h]);
    if (this.edges.length > 4000) this.edges.splice(0, 2000);
    if (h) this.lastRise = t;
    else if (this.lastRise !== null) {
      this.pulses.push([t, (t - this.lastRise) * 1000]);
      if (this.pulses.length > 200) this.pulses.splice(0, 100);
    }
  }
  /** Fraction of the last `windowMs` the pin was driven high. */
  duty(windowMs = 20) {
    this._update();
    const now = this.mcu.timeMs, start = Math.max(0, now - windowMs);
    let level = this.high, t = now, highTime = 0;
    for (let i = this.edges.length - 1; i >= 0 && t > start; i--) {
      const [et, eh] = this.edges[i];
      const from = Math.max(et, start);
      if (level) highTime += t - from;
      t = from; level = !eh;
    }
    if (t > start && level) highTime += t - start;
    return now - start > 0 ? highTime / (now - start) : (this.high ? 1 : 0);
  }
}

/** Resistive input that follows the firmware's pull-up (buttons, joystick switch). */
class SwitchInput {
  constructor(mcu, pin) {
    this.mcu = mcu; this.pin = pin; this.closed = false;
    const refresh = () => {
      const st = mcu.pinState(pin);
      // Closed: shorted to GND. Open: pulled up only if the firmware enabled the pull-up; a floating pin reads LOW here
      // so firmware that forgets INPUT_PULLUP fails the test instead of passing by luck.
      mcu.setInput(pin, !this.closed && st === 'pullup');
    };
    this.refresh = refresh;
    mcu.onPinChange(pin, refresh);
    refresh();
  }
  set(closed) { this.closed = !!closed; this.refresh(); }
}

const need = (spec, t) => {
  const p = spec.pins[t];
  if (!p) throw new Error(`terminal '${t}' is not wired`);
  return p;
};
const level = (activeLevel, active) => ((activeLevel || 'HIGH') === 'HIGH' ? active : !active);

// ---------- generic digital / analog ----------

/** One output pin driving a load. state: active for more than `threshold` of the time; level: that fraction. */
class Load {
  constructor(mcu, spec) { this.mcu = mcu; this.p = spec.params; this.pin = need(spec, 'out'); this.mon = new PinMonitor(mcu, this.pin); }
  _activeFraction() {
    const st = this.mcu.pinState(this.pin);
    if (st !== 'low' && st !== 'high') return 0;                     // undriven: the load is off
    const d = this.mon.duty();
    return (this.p.active_level || 'HIGH') === 'HIGH' ? d : 1 - d;
  }
  get(k) {
    const f = this._activeFraction();
    if (k === 'state') return f > this.p.threshold;
    if (k === 'level') return +f.toFixed(3);
  }
  state() { return { state: this.get('state'), level: this.get('level') }; }
}

/** Passive contact to GND. */
class Contact {
  constructor(mcu, spec) { this.sw = new SwitchInput(mcu, need(spec, 'contact')); this.closed = false; }
  set(k, v) { if (k === 'closed') { this.closed = !!v; this.sw.set(this.closed); } }
  state() { return { closed: this.closed }; }
}

/** Module that drives its own digital output; optional analog output follows the same state. */
class DrivenDigital {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params; this.pin = need(spec, 'out'); this.analog = spec.pins.analog || null;
    this.active = false; this._apply();
  }
  _apply() {
    this.mcu.setInput(this.pin, level(this.p.active_level, this.active));
    if (this.analog) this.mcu.setAdc(this.analog, this.active ? this.p.analog_active : this.p.analog_idle);
  }
  set(k, v) { if (k === 'active') { this.active = !!v; this._apply(); } }
  state() { return { active: this.active }; }
}

/** 0-5 V source; optional comparator output (like the DO pin of sound/soil modules). */
class AnalogSource {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params; this.pin = need(spec, 'out'); this.digital = spec.pins.digital || null;
    this.value = 512; this._apply();
  }
  _apply() {
    this.mcu.setAdc(this.pin, this.value);
    if (this.digital) {
      const tripped = this.p.digital_when === 'below' ? this.value < this.p.digital_threshold : this.value > this.p.digital_threshold;
      this.mcu.setInput(this.digital, level(this.p.digital_active_level, tripped));
    }
  }
  set(k, v) { if (k === 'value') { this.value = v; this._apply(); } }
  state() { return { value: this.value }; }
}

// ---------- servo ----------

class ServoPulse {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params; this.mon = new PinMonitor(mcu, need(spec, 'signal'));
    this.pos = this.p.start_angle; this.target = null; this.t = 0;
  }
  _step() {
    const { min_us, max_us, max_angle, deg_per_s } = this.p, now = this.mcu.timeMs;
    const fresh = this.mon.pulses.filter(([end, w]) => now - end < 60 && w >= min_us * 0.7 && w <= max_us * 1.1);
    if (fresh.length) {
      const w = fresh[fresh.length - 1][1];
      this.target = Math.max(0, Math.min(max_angle, ((w - min_us) / (max_us - min_us)) * max_angle));
    }
    if (this.target !== null) {
      const maxMove = ((now - this.t) * deg_per_s) / 1000, d = this.target - this.pos;
      this.pos += Math.abs(d) <= maxMove ? d : Math.sign(d) * maxMove;
    }
    this.t = now;
  }
  get(k) { if (k === 'angle') { this._step(); return +this.pos.toFixed(1); } }
  state() { return { angle: this.get('angle'), commanded: this.target === null ? null : +this.target.toFixed(1) }; }
}

// ---------- trigger / echo ranger ----------

class Ultrasonic {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params; this.distance = 200; this.busy = false;
    this.trig = need(spec, 'trig'); this.echo = need(spec, 'echo');
    let riseUs = null;
    mcu.setInput(this.echo, false);
    mcu.onEdge(this.trig, (high) => {
      const now = mcu.timeUs, p = this.p;
      if (high) { riseUs = now; return; }
      if (riseUs === null || now - riseUs < p.min_trigger_us || this.busy) return;
      this.busy = true;
      const width = this.distance > p.max_cm ? p.no_echo_us : this.distance * p.us_per_cm;
      mcu.schedule(p.delay_us, () => {
        mcu.setInput(this.echo, true);
        mcu.schedule(width, () => { mcu.setInput(this.echo, false); mcu.schedule(200, () => { this.busy = false; }); });
      });
    });
  }
  set(k, v) { if (k === 'distance') this.distance = v; }
  state() { return { distance: this.distance }; }
}

// ---------- DHT single-wire ----------

class Dht {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params; this.pin = need(spec, 'data');
    this.temperature = 25; this.humidity = 50; this.lowSince = null; this.busy = false; this.reads = 0;
    mcu.setInput(this.pin, true);                    // the module's pull-up holds the line high when idle
    mcu.onPinChange(this.pin, (us) => {
      const st = mcu.pinState(this.pin);
      if (st === 'low') { if (this.lowSince === null) this.lowSince = us; return; }
      if (this.lowSince === null) return;
      const held = us - this.lowSince;
      this.lowSince = null;
      if (held >= this.p.start_low_us && !this.busy && st !== 'high') this._respond();
    });
  }
  _bytes() {
    const [tlo, thi] = this.p.temp_range, [hlo, hhi] = this.p.humidity_range;
    const t = Math.max(tlo, Math.min(thi, this.temperature)), hum = Math.max(hlo, Math.min(hhi, this.humidity));
    if (this.p.encoding === 'int') {
      const h = Math.round(hum), ti = Math.floor(Math.abs(t)), td = Math.round((Math.abs(t) - ti) * 10);
      return [h, 0, ti, td | (t < 0 ? 0x80 : 0), (h + ti + (td | (t < 0 ? 0x80 : 0))) & 0xff];
    }
    const h = Math.round(hum * 10);
    let tt = Math.round(Math.abs(t) * 10);
    if (t < 0) tt |= 0x8000;
    const b = [h >> 8, h & 0xff, tt >> 8, tt & 0xff];
    return [...b, (b[0] + b[1] + b[2] + b[3]) & 0xff];
  }
  _respond() {
    this.busy = true; this.reads++;
    const m = this.mcu, pin = this.pin;
    const seq = [[30, null], [80, false], [80, true]];   // [duration us, level]: wait, ack low, ack high
    for (const byte of this._bytes()) for (let i = 7; i >= 0; i--) seq.push([50, false], [(byte >> i) & 1 ? 70 : 26, true]);
    seq.push([50, false]);
    let i = 0;
    const next = () => {
      if (i >= seq.length) { m.setInput(pin, true); this.busy = false; return; }
      const [us, lvl] = seq[i++];
      if (lvl !== null) m.setInput(pin, lvl);
      m.schedule(us, next);
    };
    next();
  }
  set(k, v) { if (k === 'temperature') this.temperature = v; if (k === 'humidity') this.humidity = v; }
  state() { return { temperature: this.temperature, humidity: this.humidity, reads: this.reads }; }
}

const addr = (a, dflt) => (typeof a === 'number' ? a : parseInt(a || dflt, 16));

// ---------- I2C devices ----------

/** MPU6050 register model. pitch_deg / roll_deg set the board's attitude; the sensor rotates there at 200 deg/s and
 * reports gravity on the accelerometer and the rotation rate on the gyro while it moves, like a real board being tilted.
 * Convention (matches the part card): roll = rotation about X (MPU6050_light getAngleX), pitch = about Y (getAngleY). */
class ImuMpu6050 {
  constructor(mcu, spec) {
    this.mcu = mcu; this.p = spec.params;
    this.reg = new Uint8Array(128);
    this.reg[0x75] = addr(this.p.who_am_i, '0x68'); this.reg[0x6b] = 0x40;   // WHO_AM_I, PWR_MGMT_1 (asleep at power-up)
    this.ptr = 0; this.first = false;
    this.target = { pitch: 0, roll: 0 }; this.cur = { pitch: 0, roll: 0 }; this.t = 0;
    mcu.addI2C(addr(this.p.address, '0x68'), this);
  }
  begin(write) { this.first = write; if (!write) this._snapshot(); }
  write(b) {
    if (this.first) { this.ptr = b & 0x7f; this.first = false; }
    else { if (this.ptr === 0x6b && (b & 0x80)) { const w = this.reg[0x75]; this.reg.fill(0); this.reg[0x75] = w; this.reg[0x6b] = 0x40; } else this.reg[this.ptr] = b; this.ptr = (this.ptr + 1) & 0x7f; }
    return true;
  }
  read() { const v = this.reg[this.ptr]; this.ptr = (this.ptr + 1) & 0x7f; return v; }
  _put16(addr, v) { v = Math.max(-32768, Math.min(32767, Math.round(v))); this.reg[addr] = (v >> 8) & 0xff; this.reg[addr + 1] = v & 0xff; }
  _snapshot() {
    const now = this.mcu.timeMs, dt = Math.max(1e-6, (now - this.t) / 1000);
    this.t = now;
    const rate = {};
    for (const k of ['pitch', 'roll']) {
      const d = this.target[k] - this.cur[k], max = this.p.deg_per_s * dt;
      const step = Math.abs(d) <= max ? d : Math.sign(d) * max;
      this.cur[k] += step; rate[k] = step / dt;
    }
    if (this.reg[0x6b] & 0x40) { for (let a = 0x3b; a < 0x49; a++) this.reg[a] = 0; return; }   // asleep: no data
    const r = (this.cur.roll * Math.PI) / 180, p = (this.cur.pitch * Math.PI) / 180;
    const ay = Math.sin(r), ax = -Math.sin(p), az = Math.sqrt(Math.max(0, 1 - ax * ax - ay * ay));
    const accLsb = 16384 / (1 << ((this.reg[0x1c] >> 3) & 3)), gyroLsb = 131 / (1 << ((this.reg[0x1b] >> 3) & 3));
    this._put16(0x3b, ax * accLsb); this._put16(0x3d, ay * accLsb); this._put16(0x3f, az * accLsb);
    this._put16(0x41, (25 - 36.53) * 340);
    this._put16(0x43, rate.roll * gyroLsb); this._put16(0x45, rate.pitch * gyroLsb); this._put16(0x47, 0);
  }
  set(k, v) { if (k === 'pitch') this.target.pitch = v; if (k === 'roll') this.target.roll = v; }
  state() { return { pitch: this.target.pitch, roll: this.target.roll, awake: !(this.reg[0x6b] & 0x40) }; }
}

const SSD_ARGS = { 0x20: 1, 0x21: 2, 0x22: 2, 0x26: 6, 0x27: 6, 0x29: 5, 0x2a: 5, 0x81: 1, 0x8d: 1, 0xa3: 2, 0xa8: 1,
  0xd3: 1, 0xd5: 1, 0xd9: 1, 0xda: 1, 0xdb: 1 };

/** SSD1306 128x64 controller: decodes the I2C command/data stream into GDDRAM, then reads the text back with the
 * 5x7 font table (1x and 2x scale). Text only counts as shown while the display and its charge pump are on. */
class Ssd1306 {
  constructor(mcu, spec) {
    const p = spec.params;
    this.W = p.width; this.P = p.height / 8;
    this.ram = Array.from({ length: this.P }, () => new Uint8Array(this.W));
    this.mode = 2; this.col = 0; this.page = 0; this.c0 = 0; this.c1 = this.W - 1; this.p0 = 0; this.p1 = this.P - 1;
    this.on = false; this.pump = false; this.cmd = []; this.need = 0; this.ctrl = null;
    mcu.addI2C(addr(p.address, '0x3C'), this);
  }
  begin() { this.ctrl = null; }
  write(b) {
    if (this.ctrl === null) { this.ctrl = b; return true; }
    if (this.ctrl & 0x40) this._data(b);
    else this._command(b);
    if (this.ctrl & 0x80) this.ctrl = null;   // Co bit: one byte per control byte
    return true;
  }
  _command(b) {
    if (this.need) { this.cmd.push(b); if (--this.need === 0) this._exec(this.cmd); return; }
    if (SSD_ARGS[b]) { this.cmd = [b]; this.need = SSD_ARGS[b]; return; }
    this._exec([b]);
  }
  _exec([c, a, b]) {
    if (c === 0xae) this.on = false;
    else if (c === 0xaf) this.on = true;
    else if (c === 0x8d) this.pump = !!(a & 0x04);
    else if (c === 0x20) this.mode = a & 3;
    else if (c === 0x21) { this.c0 = a % this.W; this.c1 = b % this.W; this.col = this.c0; }
    else if (c === 0x22) { this.p0 = a % this.P; this.p1 = b % this.P; this.page = this.p0; }
    else if (c >= 0xb0 && c <= 0xb7) this.page = (c & 7) % this.P;
    else if (c <= 0x0f) this.col = (this.col & 0xf0) | c;
    else if (c >= 0x10 && c <= 0x1f) this.col = ((c & 0x0f) << 4) | (this.col & 0x0f);
  }
  _data(b) {
    this.ram[this.page][this.col % this.W] = b;
    if (this.mode === 2) { this.col = (this.col + 1) % this.W; return; }
    if (this.mode === 0) {
      if (this.col >= this.c1) { this.col = this.c0; this.page = this.page >= this.p1 ? this.p0 : this.page + 1; } else this.col++;
    } else if (this.page >= this.p1) { this.page = this.p0; this.col = this.col >= this.c1 ? this.c0 : this.col + 1; } else this.page++;
  }
  _decodeRow(cols) {
    let out = '', blank = 0;
    for (let x = 0; x < cols.length;) {
      const key = `${cols[x]},${cols[x + 1]},${cols[x + 2]},${cols[x + 3]},${cols[x + 4]}`;
      const ch = cols[x] || cols[x + 1] || cols[x + 2] ? GLYPHS_5X7[key] : undefined;
      if (ch) { if (blank >= 4 && out) out += ' '.repeat(Math.max(1, Math.round(blank / 6))); out += ch; blank = 0; x += 5; continue; }
      if (cols[x] === 0) blank++; else blank = 0;
      x++;
    }
    return out;
  }
  lines() {
    const rows = this.ram.map((p) => this._decodeRow(Array.from(p)));
    for (let pg = 0; pg < this.P - 1; pg++) {                  // 2x text: every glyph pixel is 2x2 across two pages
      const cols = [];
      for (let x = 0; x < this.W; x += 2) {
        const w = this.ram[pg][x] | (this.ram[pg + 1][x] << 8);
        let b = 0;
        for (let i = 0; i < 8; i++) if (w & (1 << (i * 2))) b |= 1 << i;
        cols.push(b);
      }
      const t = this._decodeRow(cols);
      if (t.length >= 2) rows.push(t);
    }
    return rows.filter((r) => r.trim());
  }
  get(k) { if (k === 'text') return this.on && this.pump ? this.lines().join('\n') : ''; }
  state() { return { on: this.on && this.pump, lines: this.lines(), pixels: this.ram.map((p) => Array.from(p)) }; }
}

/** HD44780 character LCD behind a PCF8574 I2C expander, 4-bit mode. Expander bit assignment, size and row
 * addresses come from params, so 16x2, 20x4 and other backpack wirings are the same model. */
class Hd44780I2c {
  constructor(mcu, spec) {
    const p = spec.params;
    this.p = p; this.b = p.bits;
    this.ddram = new Array(128).fill(' '); this.addr = 0; this.inc = 1; this.four = false; this.half = null;
    this.on = false; this.cgram = false; this.last = 0; this.backlight = false;
    mcu.addI2C(addr(p.address, '0x27'), this);
  }
  write(v) {
    const en = 1 << this.b.en;
    this.backlight = !!(v & (1 << this.b.bl));
    if ((this.last & en) && !(v & en)) this._latch((this.last >> this.b.d4) & 0x0f, (this.last >> this.b.rs) & 1);
    this.last = v;
    return true;
  }
  read() { return this.last | (0x0f << this.b.d4); }
  _latch(nib, rs) {
    if (!this.four) { const b = nib << 4; if (!rs) this._command(b); return; }
    if (this.half === null) { this.half = nib; return; }
    const b = (this.half << 4) | nib; this.half = null;
    rs ? this._char(b) : this._command(b);
  }
  _command(b) {
    if (b & 0x80) { this.addr = b & 0x7f; this.cgram = false; }
    else if (b & 0x40) this.cgram = true;
    else if (b & 0x20) { this.four = !(b & 0x10); this.half = null; }
    else if (b & 0x10) { /* cursor/display shift: not modelled */ }
    else if (b & 0x08) this.on = !!(b & 0x04);
    else if (b & 0x04) this.inc = b & 0x02 ? 1 : -1;
    else if (b & 0x02) this.addr = 0;
    else if (b & 0x01) { this.ddram.fill(' '); this.addr = 0; this.inc = 1; }
  }
  _char(b) {
    if (this.cgram) return;
    this.ddram[this.addr] = b >= 32 && b < 127 ? String.fromCharCode(b) : '?';
    this.addr = (this.addr + this.inc) & 0x7f;
  }
  lines() {
    const { cols, rows, row_offsets: off } = this.p;
    return Array.from({ length: rows }, (_, r) => this.ddram.slice(off[r], off[r] + cols).join(''));
  }
  get(k) { if (k === 'text') return this.on ? this.lines().join('\n') : ''; }
  state() { return { on: this.on, backlight: this.backlight, lines: this.lines() }; }
}

// ---------- registry ----------

export const MODELS = {
  load: Load,
  contact: Contact,
  driven_digital: DrivenDigital,
  analog_source: AnalogSource,
  servo_pulse: ServoPulse,
  ultrasonic: Ultrasonic,
  dht: Dht,
  imu_mpu6050: ImuMpu6050,
  ssd1306: Ssd1306,
  hd44780_i2c: Hd44780I2c,
};

/** One physical part = one or more model instances. Routes rule keys to the instance and native key behind them.
 * part: {id, models: [{model, pins: {terminal: boardPin|null}, params, io: {rule key: native key}}]} */
export class Part {
  constructor(mcu, part) {
    this.id = part.id;
    this.routes = {};
    this.instances = part.models.map((m) => {
      const M = MODELS[m.model];
      if (!M) throw new Error(`${part.id}: no simulation model type '${m.model}'`);
      let inst;
      try { inst = new M(mcu, m); } catch (e) { throw new Error(`${part.id} (${m.model}): ${e.message}`); }
      for (const [rk, nk] of Object.entries(m.io)) this.routes[rk] = [inst, nk];
      return inst;
    });
  }
  set(k, v) {
    const r = this.routes[k];
    if (!r) throw new Error(`${this.id} has no input '${k}'`);
    r[0].set?.(r[1], v);
  }
  get(k) { const r = this.routes[k]; return r ? r[0].get?.(r[1]) : undefined; }
  /** Rule-key view of the part for the twin, plus each model's raw state. */
  state() {
    const out = {};
    for (const [rk, [inst, nk]] of Object.entries(this.routes)) out[rk] = inst.get?.(nk) ?? inst.state?.()[nk];
    out._models = this.instances.map((i) => i.state?.());
    return out;
  }
}

export function attach(mcu, part) { return new Part(mcu, part); }
