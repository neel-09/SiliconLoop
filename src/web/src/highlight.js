// Minimal C++/Arduino syntax highlighter: comments, strings, preprocessor, keywords, numbers, Arduino calls.
const KW = new Set(('alignas auto bool break case char class const constexpr continue default delete do double else enum ' +
  'extern false float for if inline int long namespace new nullptr private protected public return short signed sizeof static ' +
  'struct switch template this true typedef unsigned void volatile while uint8_t uint16_t uint32_t int8_t int16_t int32_t byte word size_t').split(' '));
const ARD = new Set(('pinMode digitalWrite digitalRead analogRead analogWrite millis micros delay delayMicroseconds pulseIn ' +
  'Serial begin print println write setup loop map constrain abs min max tone noTone attach detach Wire F HIGH LOW INPUT ' +
  'OUTPUT INPUT_PULLUP').split(' '));

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function highlight(code) {
  const re = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])')|(^[ \t]*#[^\n]*)|(\b0x[0-9a-fA-F]+\b|\b\d+(?:\.\d+)?[fFuUlL]*\b)|([A-Za-z_]\w*)/gm;
  let out = '', last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    const [tok, com, str, pre, num, id] = m;
    if (com) out += `<span class="c-com">${esc(tok)}</span>`;
    else if (str) out += `<span class="c-str">${esc(tok)}</span>`;
    else if (pre) out += `<span class="c-pre">${esc(tok)}</span>`;
    else if (num) out += `<span class="c-num">${esc(tok)}</span>`;
    else if (id && KW.has(id)) out += `<span class="c-kw">${tok}</span>`;
    else if (id && ARD.has(id)) out += `<span class="c-fn">${tok}</span>`;
    else if (id && /^[A-Z][A-Z0-9_]{2,}$/.test(id)) out += `<span class="c-const">${tok}</span>`;
    else out += esc(tok);
    last = m.index + tok.length;
  }
  return out + esc(code.slice(last));
}
