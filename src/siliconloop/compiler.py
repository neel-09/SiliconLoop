"""arduino-cli compile wrapper + header->library map builder (from library_index.json).

compile_sketch(code, board_key) -> {"ok", "errors", "flash", "ram", "hex", "log"}
  - Rejects #includes outside the allowlist before compiling (no hallucinated libraries).
  - Installs any allowed library that is missing, using the header->library map.
  - Treats flash or RAM over the board's safe limit as a failure the model must repair.
Build the map once with:  python -m siliconloop.compiler --build-map
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .allocator import BOARDS, PARTS

ARDUINO_CLI = os.getenv("ARDUINO_CLI", "arduino-cli")
HEADER_MAP_FILE = Path(__file__).resolve().parent / "data" / "header_map.json"

# Headers that ship with the AVR core: always allowed, never installed.
CORE_HEADERS = {"Arduino.h", "Wire.h", "SPI.h", "EEPROM.h", "SoftwareSerial.h", "math.h", "stdint.h", "string.h", "stdlib.h"}
CORE_PREFIXES = ("avr/", "util/")
# Fallback if the map has not been built yet: the Arduino library each part card names for its headers.
FALLBACK_MAP = {h: [lib] for c in PARTS.values() for h, lib in c.get("library_packages", {}).items()}
# Keep RAM headroom for the stack; above this the sketch may crash at runtime.
RAM_LIMIT_FRACTION = 0.85
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


def _run(args, timeout=180):
    return subprocess.run([ARDUINO_CLI, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def allowed_headers(libraries):
    """Headers a sketch may include: AVR core headers, the libraries of the parts in the design, and parts.h."""
    return CORE_HEADERS | set(libraries) | {"parts.h"}


def check_includes(code, libraries):
    bad = []
    for h in INCLUDE_RE.findall(code):
        if h in allowed_headers(libraries) or h.startswith(CORE_PREFIXES):
            continue
        bad.append(h)
    return bad


def header_map():
    if HEADER_MAP_FILE.exists():
        m = json.loads(HEADER_MAP_FILE.read_text())
        if m:
            return {**FALLBACK_MAP, **{h: m[h] for h in FALLBACK_MAP if h in m}, **m}
    return FALLBACK_MAP


def build_header_map():
    """Ranked header -> [library names] from arduino-cli's library_index.json.

    Several libraries ship the same header (e.g. Servo.h). Ranking: libraries maintained by
    Arduino first, then a library whose name matches the header, then the rest alphabetically.
    """
    _run(["lib", "update-index"], timeout=300)
    data_dir = _run(["config", "get", "directories.data"]).stdout.strip()
    index = json.loads((Path(data_dir) / "library_index.json").read_text(encoding="utf-8"))
    latest = {}
    for lib in index["libraries"]:
        latest[lib["name"]] = lib  # later entries are newer releases
    m = {}
    for name, lib in latest.items():
        for h in lib.get("providesIncludes") or lib.get("provides_includes") or []:
            m.setdefault(h, []).append(lib)

    def rank(h, lib):
        official = "arduino" in (lib.get("maintainer", "") + lib.get("author", "")).lower()
        same = lib["name"].replace(" ", "").lower() == h.rsplit(".", 1)[0].lower()
        return (not official, not same, lib["name"].lower())

    out = {h: [l["name"] for l in sorted(libs, key=lambda l: rank(h, l))] for h, libs in m.items()}
    HEADER_MAP_FILE.write_text(json.dumps(out, sort_keys=True))
    return len(out)


_installed = None


def ensure_libraries(headers):
    """Install the top-ranked library for each non-core header, if not already installed."""
    global _installed
    if _installed is None:
        r = _run(["lib", "list", "--json"])
        try:
            data = json.loads(r.stdout or "{}")
            libs = data.get("installed_libraries", data if isinstance(data, list) else [])
            _installed = {l["library"]["name"] for l in libs}
        except (ValueError, KeyError, TypeError):
            _installed = set()
    hm = header_map()
    log = []
    for h in headers:
        if h in CORE_HEADERS or h.startswith(CORE_PREFIXES):
            continue
        names = hm.get(h)
        if not names:
            log.append(f"No library provides {h}.")
            continue
        if names[0] not in _installed:
            r = _run(["lib", "install", names[0]], timeout=300)
            ok = r.returncode == 0 or "already exists" in r.stderr  # a manually installed copy counts
            log.append(f"Installed {names[0]}" if r.returncode == 0 else
                       f"Already present: {names[0]}" if ok else f"Failed to install {names[0]}: {r.stderr.strip()}")
            if ok:
                _installed.add(names[0])
    return log


# ---------- deterministic clean-up of the model's sketch ----------

def tidy_sketch(code, libraries):
    """Remove include lines that are duplicated, already provided (Arduino.h comes with the pin block) or not
    needed by this design. Returns (code, notes). Disallowed headers are left in place for lint/compile to report."""
    needed = set(libraries)
    seen, out, notes = set(), [], []
    for line in code.splitlines():
        m = INCLUDE_RE.match(line)
        if m:
            h = m.group(1)
            if h == "Arduino.h":
                notes.append("removed #include <Arduino.h> (already included above)")
                continue
            if h in seen:
                notes.append(f"removed duplicate #include <{h}>")
                continue
            if (h in CORE_HEADERS or h in {lib for c in PARTS.values() for lib in c["libraries"]}) and h not in needed:
                notes.append(f"removed #include <{h}> (not needed by this design)")
                continue
            seen.add(h)
        out.append(line)
    return "\n".join(out) + ("\n" if code.endswith("\n") else ""), notes


# ---------- pre-compile checks on the model's sketch ----------

def _body(code, name):
    """Text of the body of void name() { ... }, or None."""
    m = re.search(r"\bvoid\s+" + name + r"\s*\(\s*\)\s*\{", code)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(code) and depth:
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        i += 1
    return code[m.end():i - 1]


def lint_sketch(code, telemetry_keys):
    """Problems in the model-written code (without the generated pin constants), found before compiling."""
    p = []
    raw = code
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S)          # strip comments
    bare = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)                      # and string contents
    if re.search(r"\bconst\s+\w+\s+PIN_[A-Z0-9_]+\s*=|#\s*define\s+PIN_", bare):
        p.append("Do not declare pin constants; they are already declared above your code.")
    if re.search(r"\b(pinMode|digitalWrite|digitalRead|analogRead|analogWrite|pulseIn|attach)\s*\(\s*(\d+|A\d+)\s*[,)]", bare):
        p.append("Never write raw pin numbers; use the PIN_ constants.")
    for fn in ("setup", "loop"):
        if _body(bare, fn) is None:
            p.append(f"Define void {fn}().")
    if not re.search(r"Serial\s*\.\s*begin\s*\(\s*115200\s*\)", bare):
        p.append("Call Serial.begin(115200) in setup().")
    loop = _body(bare, "loop") or ""
    for m in re.finditer(r"\bdelay\s*\(\s*(\w+)\s*\)", loop):
        if not m.group(1).isdigit() or int(m.group(1)) > 20:
            p.append("No delay() in loop(); use millis() timers so the loop never blocks.")
            break
    if re.search(r"\bwhile\s*\(\s*(1|true)\s*\)", loop):
        p.append("No infinite loops inside loop().")
    for m in re.finditer(r"\bpulseIn\s*\(([^;]*?)\)\s*;", bare):
        if m.group(1).count(",") < 2:
            p.append("Every pulseIn() needs a timeout of 30000 microseconds or less as its third argument.")
            break
    if re.search(r"\bString\b", bare):
        p.append("Do not use the String class (it fragments the 2 KB of RAM); use char arrays.")
    if telemetry_keys:
        if '"T,' not in raw and "\"T," not in raw:
            p.append('Print the telemetry line starting with "T," every 250 ms.')
        missing = [k for k in telemetry_keys if f"{k}=" not in raw]
        if missing:
            p.append(f"The telemetry line is missing these keys: {', '.join(missing)}.")
    return p


def lint_cards(code, cards):
    """Part-specific code rules declared on the part cards ('lint': [{forbid|require: regex, message}])."""
    bare = re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S)
    p = []
    for card in cards:
        for rule in card.get("lint", []):
            if "forbid" in rule and re.search(rule["forbid"], bare):
                p.append(rule["message"])
            if "require" in rule and not re.search(rule["require"], bare):
                p.append(rule["message"])
    return list(dict.fromkeys(p))


def numbered(sketch):
    return "\n".join(f"{n:3d}| {line}" for n, line in enumerate(sketch.splitlines(), 1))


def _clean_errors(text, sketch_path=None):
    """Compiler/linker errors only, with every path to the temp sketch shortened to 'sketch.ino'."""
    lines = [re.sub(r"\S*sketch[\\/](sketch\.ino|parts\.h)", r"\1", l) for l in text.splitlines()]
    keys = ("error", "undefined reference", "multiple definition", "note:", "in function")
    keep = [l for l in lines if any(k in l.lower() for k in keys) or l.strip().startswith("^")]
    return "\n".join(keep[:40]) or "\n".join(lines[-20:])


def compile_sketch(code, board_key, libraries, extra_files=None):
    """code is sketch.ino; extra_files (e.g. {"parts.h": text}) are written next to it."""
    board = BOARDS[board_key]
    bad = check_includes(code, libraries)
    if bad:
        return {"ok": False, "stage": "includes",
                "errors": f"These #includes are not allowed: {', '.join(bad)}. "
                          f"Allowed: {', '.join(sorted(allowed_headers(libraries)))}."}
    if shutil.which(ARDUINO_CLI) is None:
        return {"ok": False, "stage": "setup", "errors": "arduino-cli is not installed on the server."}

    log = ensure_libraries([h for h in INCLUDE_RE.findall(code + "".join((extra_files or {}).values())) if h != "parts.h"])
    work = Path(tempfile.mkdtemp(prefix="sl_"))
    sketch_dir = work / "sketch"
    sketch_dir.mkdir()
    sketch = sketch_dir / "sketch.ino"
    sketch.write_text(code, encoding="utf-8")
    for name, text in (extra_files or {}).items():
        (sketch_dir / name).write_text(text, encoding="utf-8")
    out_dir = work / "out"
    try:
        r = _run(["compile", "--fqbn", board["fqbn"], "--output-dir", str(out_dir), "--json", str(sketch_dir)], timeout=300)
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {"success": False, "compiler_err": r.stdout + r.stderr}
        if not res.get("success", r.returncode == 0):
            err = res.get("compiler_err") or res.get("error") or r.stderr
            return {"ok": False, "stage": "compile", "errors": _clean_errors(err, sketch), "log": log}

        sizes = {s["name"]: s for s in res.get("builder_result", {}).get("executable_sections_size", []) or []}
        flash = sizes.get("text", {}).get("size", 0)
        ram = sizes.get("data", {}).get("size", 0)
        flash_max, ram_max = board["flash_bytes"], board["sram_bytes"]
        mem = {"flash": flash, "flash_max": flash_max, "ram": ram, "ram_max": ram_max}
        if flash > flash_max:
            return {"ok": False, "stage": "memory", "errors": f"Program uses {flash} bytes of flash; the board has {flash_max}.", **mem}
        if ram > ram_max * RAM_LIMIT_FRACTION:
            return {"ok": False, "stage": "memory",
                    "errors": f"Global variables use {ram} of {ram_max} bytes of RAM; keep it under "
                              f"{int(ram_max * RAM_LIMIT_FRACTION)} so the stack has room. Use F() for strings "
                              f"and avoid large buffers.", **mem}
        hex_file = out_dir / "sketch.ino.hex"
        return {"ok": True, "stage": "done", "errors": "", "hex": hex_file.read_text(), "log": log, **mem}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "compile", "errors": "Compilation timed out."}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def preinstall():
    """Install the AVR core and every library any part card allows (used in the Docker build)."""
    _run(["core", "update-index"], timeout=300)
    _run(["core", "install", "arduino:avr"], timeout=600)
    headers = sorted({h for c in PARTS.values() for h in c["libraries"]})
    return ensure_libraries(headers)


if __name__ == "__main__":
    if "--build-map" in sys.argv:
        print(f"{build_header_map()} headers mapped -> {HEADER_MAP_FILE}")
    elif "--preinstall" in sys.argv:
        print("\n".join(preinstall()))
    else:
        blink = "void setup(){pinMode(13,OUTPUT);}\nvoid loop(){digitalWrite(13,HIGH);delay(500);digitalWrite(13,LOW);delay(500);}\n"
        for b in BOARDS:
            r = compile_sketch(blink, b, [])  # single-file sketch
            print(b, r["ok"], r.get("flash"), r.get("ram"), r.get("errors", "")[:200])
