// Headless AVR microcontroller (avr8js) with Arduino pin names, shared by the Node test runner and the browser twin.
// Supports the ATmega328P (Uno, Nano) and the ATmega2560 (Mega).
import {
  CPU, avrInstruction, AVRTimer, timer0Config, timer1Config, timer2Config, AVRIOPort, AVRUSART, usart0Config,
  AVRADC, adcConfig, AVRTWI, twiConfig, AVREEPROM, EEPROMMemoryBackend, eepromConfig, AVRSPI, spiConfig, PinState,
  portAConfig, portBConfig, portCConfig, portDConfig, portEConfig, portFConfig, portGConfig, portHConfig,
  portJConfig, portKConfig, portLConfig, ADCMuxInputType,
} from 'avr8js';

export const F_CPU = 16e6;
const PORTS = { A: portAConfig, B: portBConfig, C: portCConfig, D: portDConfig, E: portEConfig, F: portFConfig,
  G: portGConfig, H: portHConfig, J: portJConfig, K: portKConfig, L: portLConfig };

// Arduino pin name -> [port letter, bit]. From the core's variants/*/pins_arduino.h.
const PINS_328 = {};
for (let i = 0; i < 8; i++) PINS_328[`D${i}`] = ['D', i];
for (let i = 8; i < 14; i++) PINS_328[`D${i}`] = ['B', i - 8];
for (let i = 0; i < 6; i++) PINS_328[`A${i}`] = ['C', i];
const MEGA_ORDER = ('E0 E1 E4 E5 G5 E3 H3 H4 H5 H6 B4 B5 B6 B7 J1 J0 H1 H0 D3 D2 D1 D0 A0 A1 A2 A3 A4 A5 A6 A7 ' +
  'C7 C6 C5 C4 C3 C2 C1 C0 D7 G2 G1 G0 L7 L6 L5 L4 L3 L2 L1 L0 B3 B2 B1 B0 ' +
  'F0 F1 F2 F3 F4 F5 F6 F7 K0 K1 K2 K3 K4 K5 K6 K7').split(' ');
const PINS_MEGA = {};
MEGA_ORDER.forEach((pb, i) => {
  const name = i >= 54 ? `A${i - 54}` : `D${i}`;
  PINS_MEGA[name] = [pb[0], +pb.slice(1)];
});

// ATmega2560 differs from the 328P in interrupt vectors, timer output pins and three extra 16-bit timers.
const v = (n) => n * 2; // vector number -> word address
const PORT_ADDR = { B: 0x25, D: 0x2b, E: 0x2e, G: 0x34, H: 0x102, L: 0x10b };
const mega16 = (base, tifr, timsk, vec, pins) => ({
  ...timer1Config, TCCRA: base, TCCRB: base + 1, TCCRC: base + 2, TCNT: base + 4, ICR: base + 6, OCRA: base + 8,
  OCRB: base + 10, OCRC: base + 12, TIFR: tifr, TIMSK: timsk, captureInterrupt: v(vec), compAInterrupt: v(vec + 1),
  compBInterrupt: v(vec + 2), compCInterrupt: v(vec + 3), ovfInterrupt: v(vec + 4), OCFC: 8, OCIEC: 8,
  compPortA: PORT_ADDR[pins[0]], compPinA: pins[1], compPortB: PORT_ADDR[pins[0]], compPinB: pins[2],
  compPortC: PORT_ADDR[pins[0]], compPinC: pins[3], externalClockPort: 0, externalClockPin: 0,
});
const MEGA = {
  timers: [
    { ...timer0Config, compAInterrupt: v(21), compBInterrupt: v(22), ovfInterrupt: v(23), compPortA: 0x25, compPinA: 7,
      compPortB: 0x34, compPinB: 5, externalClockPort: 0x2b, externalClockPin: 7 },
    { ...timer1Config, captureInterrupt: v(16), compAInterrupt: v(17), compBInterrupt: v(18), compCInterrupt: v(19),
      ovfInterrupt: v(20), OCRC: 0x8c, OCFC: 8, OCIEC: 8, compPortA: 0x25, compPinA: 5, compPortB: 0x25, compPinB: 6,
      compPortC: 0x25, compPinC: 7, externalClockPort: 0x2b, externalClockPin: 6 },
    { ...timer2Config, compAInterrupt: v(13), compBInterrupt: v(14), ovfInterrupt: v(15), compPortA: 0x25, compPinA: 4,
      compPortB: 0x102, compPinB: 6 },
    mega16(0x90, 0x38, 0x71, 31, ['E', 3, 4, 5]),
    mega16(0xa0, 0x39, 0x72, 41, ['H', 3, 4, 5]),
    mega16(0x120, 0x3a, 0x73, 46, ['L', 3, 4, 5]),
  ],
  usart: { ...usart0Config, rxCompleteInterrupt: v(25), dataRegisterEmptyInterrupt: v(26), txCompleteInterrupt: v(27) },
  adc: (() => {
    const mux = { ...adcConfig.muxChannels };
    for (let i = 0; i < 8; i++) mux[0x20 | i] = { type: ADCMuxInputType.SingleEnded, channel: 8 + i };
    return { ...adcConfig, adcInterrupt: v(29), numChannels: 16, muxChannels: mux };
  })(),
  twi: { ...twiConfig, twiInterrupt: v(39) },
  spi: { ...spiConfig, spiInterrupt: v(24) },
  eeprom: { ...eepromConfig, eepromReadyInterrupt: v(30) },
};

export const BOARD_CHIPS = { uno: 'atmega328p', nano: 'atmega328p', nano_old: 'atmega328p', mega: 'atmega2560' };

export function parseHex(text, size) {
  const bytes = new Uint8Array(size);
  let base = 0;
  for (const line of text.split(/\r?\n/)) {
    if (line[0] !== ':') continue;
    const n = parseInt(line.substr(1, 2), 16), addr = parseInt(line.substr(3, 4), 16), type = parseInt(line.substr(7, 2), 16);
    if (type === 0) for (let i = 0; i < n; i++) bytes[base + addr + i] = parseInt(line.substr(9 + i * 2, 2), 16);
    else if (type === 2) base = parseInt(line.substr(9, 4), 16) << 4;
    else if (type === 4) base = parseInt(line.substr(9, 4), 16) << 16;
  }
  return bytes;
}

/** A simple I2C bus: devices register by 7-bit address and get begin(write)/write(byte)/read()/stop(). */
class I2CBus {
  constructor(twi) { this.twi = twi; this.devices = new Map(); this.active = null; }
  start() { this.twi.completeStart(); }
  stop() { this.active?.stop?.(); this.active = null; this.twi.completeStop(); }
  connectToSlave(addr, write) {
    const d = this.devices.get(addr);
    this.active = d || null;
    if (d) d.begin?.(write);
    this.twi.completeConnect(!!d);
  }
  writeByte(value) { this.twi.completeWrite(this.active ? this.active.write(value) !== false : false); }
  readByte() { this.twi.completeRead(this.active ? (this.active.read() & 0xff) : 0xff); }
}

export class MCU {
  constructor(board, hexText) {
    this.board = board;
    this.chip = BOARD_CHIPS[board];
    if (!this.chip) throw new Error(`unsupported board ${board}`);
    const mega = this.chip === 'atmega2560';
    const flash = mega ? 0x40000 : 0x8000;
    const prog = parseHex(hexText, flash);
    this.progLen = prog.length;
    this.flashUsed = prog.reduce((last, b, i) => (b !== 0 ? i : last), 0);
    this.cpu = new CPU(new Uint16Array(prog.buffer), mega ? 8192 + 0x100 : 2048);
    this.pinMap = mega ? PINS_MEGA : PINS_328;
    this.ports = {};
    const letters = mega ? 'ABCDEFGHJKL' : 'BCD';
    for (const L of letters) this.ports[L] = new AVRIOPort(this.cpu, PORTS[L]);
    const cfg = mega ? MEGA : { timers: [timer0Config, timer1Config, timer2Config], usart: usart0Config, adc: adcConfig,
      twi: twiConfig, spi: spiConfig, eeprom: eepromConfig };
    this.timers = cfg.timers.map((t) => new AVRTimer(this.cpu, t));
    this.usart = new AVRUSART(this.cpu, cfg.usart, F_CPU);
    this.adc = new AVRADC(this.cpu, cfg.adc);
    this.twi = new AVRTWI(this.cpu, cfg.twi, F_CPU);
    this.spi = new AVRSPI(this.cpu, cfg.spi, F_CPU);
    this.eeprom = new AVREEPROM(this.cpu, new EEPROMMemoryBackend(mega ? 4096 : 1024), cfg.eeprom);
    this.i2c = new I2CBus(this.twi);
    this.twi.eventHandler = this.i2c;
    this.adc.channelValues.fill(0);

    this.serialText = '';          // everything printed, ever
    this.lineListeners = [];
    this._line = '';
    this.usart.onByteTransmit = (b) => {
      const c = String.fromCharCode(b);
      this.serialText += c;
      if (c === '\n') {
        const line = this._line.replace(/\r$/, '');
        this._line = '';
        for (const l of this.lineListeners) l(line, this.timeMs);
      } else this._line += c;
    };
    this.resets = 0;
    this.crashed = null;
    this.edgeListeners = new Map(); // pin name -> [fn(high, timeMs)]
    this.portChange = {};           // port letter -> [fn(timeUs)], on any PORT or DDR change
    for (const [L, port] of Object.entries(this.ports)) {
      port.addListener((value, old) => {
        const pcs = this.portChange[L];
        if (pcs) for (const fn of pcs) fn(this.timeUs);
        const diff = value ^ old;
        if (!diff) return;
        for (let bit = 0; bit < 8; bit++) {
          if (!(diff & (1 << bit))) continue;
          const name = this._nameOf(L, bit);
          const ls = name && this.edgeListeners.get(name);
          if (ls) for (const fn of ls) fn(!!(value & (1 << bit)), this.timeMs);
        }
      });
    }
    this._names = {};
    for (const [name, [L, bit]] of Object.entries(this.pinMap)) this._names[`${L}${bit}`] = name;
  }

  _nameOf(L, bit) { return this._names[`${L}${bit}`]; }
  get timeMs() { return this.cpu.cycles / (F_CPU / 1000); }
  get timeUs() { return this.cpu.cycles / (F_CPU / 1e6); }
  usToCycles(us) { return Math.round(us * (F_CPU / 1e6)); }

  _pin(name) {
    const p = this.pinMap[name];
    if (!p) throw new Error(`pin ${name} does not exist on ${this.board}`);
    return [this.ports[p[0]], p[1]];
  }
  /** 'low' | 'high' | 'input' | 'pullup' as driven by the firmware. */
  pinState(name) {
    const [port, bit] = this._pin(name);
    return ['low', 'high', 'input', 'pullup'][port.pinState(bit)];
  }
  isHigh(name) { return this.pinState(name) === 'high'; }
  /** Drive a pin from outside (what a sensor outputs). Only visible while the firmware keeps it as an input. */
  setInput(name, high) { const [port, bit] = this._pin(name); port.setPin(bit, !!high); }
  /** Analog voltage on an analog pin (A0..A15). Nano-only A6/A7 are ADC channels 6 and 7. */
  setVoltage(name, volts) {
    const ch = +name.slice(1);
    this.adc.channelValues[ch] = Math.max(0, Math.min(5, volts));
  }
  setAdc(name, counts) { this.setVoltage(name, ((counts + 0.5) * 5) / 1024); }
  onEdge(name, fn) {
    this._pin(name);
    if (!this.edgeListeners.has(name)) this.edgeListeners.set(name, []);
    this.edgeListeners.get(name).push(fn);
  }
  /** Called whenever the firmware changes the direction, pull-up or level of any pin on this pin's port. */
  onPinChange(name, fn) {
    const L = this.pinMap[name][0];
    (this.portChange[L] ||= []).push(fn);
  }
  /** Repeat fn every `us` microseconds of simulated time. */
  every(us, fn) {
    const c = Math.max(1, this.usToCycles(us));
    const tick = () => { fn(this.timeUs); this.cpu.addClockEvent(tick, c); };
    this.cpu.addClockEvent(tick, c);
  }
  onLine(fn) { this.lineListeners.push(fn); }
  addI2C(addr, device) { this.i2c.devices.set(addr, device); }
  schedule(us, fn) { this.cpu.addClockEvent(fn, Math.max(1, this.usToCycles(us))); }
  get baud() { return this.usart.baudRate; }

  /** Run for ms of simulated time. Stops early (and records why) on a reset or a jump outside the program. */
  run(ms) {
    const cpu = this.cpu;
    const end = cpu.cycles + ms * (F_CPU / 1000);
    const words = this.flashUsed / 2 + 2;
    while (cpu.cycles < end) {
      avrInstruction(cpu);
      cpu.tick();
      if (cpu.pc === 0) {
        this.resets++;
        if (!this.crashed) this.crashed = { kind: 'reset', atMs: +this.timeMs.toFixed(1) };
      } else if (cpu.pc > words) {
        this.crashed = { kind: 'runaway', atMs: +this.timeMs.toFixed(1) };
        return;
      }
    }
  }
}

export { PinState };
