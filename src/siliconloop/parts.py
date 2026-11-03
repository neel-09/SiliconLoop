"""Part-card lookup + Tavily grounding + validated web cards for unknown parts.

Grounding exists to minimise hallucinations. Before any model plans or writes code, Tavily is searched for:
  - each part's pinout and sensor characteristics (range, timing, accuracy),
  - reference circuits for the wish on the chosen board,
  - usage of each allowed library.
Only raw search snippets are used (never Tavily's generated answer, which is not a Meta model).
Part and sensor facts are searched only on PART_DOMAINS (official manufacturer/distributor sites).
Curated cards stay authoritative for pins; web evidence that does not mention a card's pin labels is flagged.
Unknown parts are admitted only if a validated datasheet extraction fits a generic simulation class.
"""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from .allocator import PARTS
from . import llm, prompts

CACHE_DIR = Path(os.getenv("CACHE_DIR") or Path(__file__).resolve().parents[2] / ".cache")
_cache_lock = threading.Lock()
SNIPPET_CHARS = 700
# Sensor/part facts come only from official manufacturer and distributor sites.
PART_DOMAINS = [d.strip() for d in os.getenv("PART_SOURCE_DOMAINS", "adafruit.com,sparkfun.com,ti.com,arduino.cc,components101.com,github.com").split(",") if d.strip()]
PROTOCOL_WORDS = re.compile(r"\b(i2c|iic|sda|scl|spi|mosi|miso|sck|uart|rx|tx|one-wire|1-wire|onewire)\b", re.I)


# ---------- curated lookup ----------

def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve(name):
    """Curated part key for a free-text name, or None. Longest alias match wins."""
    n = f" {_norm(name)} "
    best, best_len = None, 0
    for key, card in PARTS.items():
        for alias in card.get("aliases", []) + [key.replace("_", " ")]:
            a = _norm(alias)
            if a and f" {a} " in n and len(a) > best_len:
                best, best_len = key, len(a)
    return best


# ---------- Tavily search with a disk cache ----------

def _client():
    key = os.getenv("tavily_api_key")
    if not key:
        return None
    from tavily import TavilyClient
    return TavilyClient(api_key=key)


def _readable(text):
    """False for binary or garbled extractions (e.g. a PDF read as bytes): too few real words."""
    if len(text) < 40:
        return False
    tokens = text.split()
    words = sum(1 for t in tokens if re.fullmatch(r"[A-Za-z][a-z]{2,}[.,:;)]?", t))
    non_ascii = sum(1 for ch in text if not ch.isascii() and ch not in "°±µΩ–—’“”…")
    return words / max(len(tokens), 1) >= 0.25 and non_ascii / len(text) < 0.03


def search(query, max_results=4, domains=None):
    """Raw web snippets for a query: [{title, url, content}], optionally limited to domains.
    Cached on disk so repeat builds cost no credits."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = query + "|" + ",".join(sorted(domains or []))
    f = CACHE_DIR / f"tavily_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
    if f.exists():
        return [r for r in json.loads(f.read_text(encoding="utf-8")) if _readable(r["content"])]
    client = _client()
    if client is None:
        return []
    try:
        extra = {"include_domains": domains} if domains else {}
        res = client.search(query=query, search_depth="basic", max_results=max_results,
                            include_answer=False, include_raw_content=False, **extra)
    except Exception:
        return []
    out = [{"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:SNIPPET_CHARS]}
           for r in res.get("results", [])]
    with _cache_lock:
        f.write_text(json.dumps(out), encoding="utf-8")
    return [r for r in out if _readable(r["content"])]


class Sources:
    """Numbered, de-duplicated source list shared by the plan and firmware prompts."""

    def __init__(self):
        self.items, self._urls = [], {}

    def add(self, results, topic):
        nums = []
        for r in results:
            if r["url"] in self._urls:
                nums.append(self._urls[r["url"]])
                continue
            n = len(self.items) + 1
            self.items.append({"n": n, "topic": topic, **r})
            self._urls[r["url"]] = n
            nums.append(n)
        return nums

    def pick(self, topics):
        return [s for s in self.items if s["topic"] in topics]


def ground(board, cards, wish):
    """Search approved sites for each part's datasheet facts and each library's usage. Returns Sources.
    The raw snippets never reach the plan or firmware prompts: facts() turns them into a verified fact sheet."""
    src = Sources()
    for key, card in cards.items():
        src.add(search(f"{card['name']} pinout datasheet specifications", max_results=5, domains=PART_DOMAINS), f"part:{key}")
        src.add(search(f"{card['name']} operating voltage current consumption", max_results=3, domains=PART_DOMAINS), f"part:{key}")
    for lib in sorted({h for c in cards.values() for h in c.get("libraries", []) if h != "Wire.h"}):
        src.add(search(f"Arduino {lib} library reference example", max_results=3, domains=PART_DOMAINS), f"lib:{lib}")
    return src


# ---------- unknown parts -> validated generic cards ----------

def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "pin"


def validate_extraction(x, source_text):
    """Problems that stop an extracted part from being admitted. Empty list = admissible."""
    p = []
    kind = x.get("kind")
    if kind not in ("digital_sensor", "analog_sensor", "digital_and_analog_sensor", "switched_module"):
        return [f"kind '{kind}' cannot be simulated generically"]
    pins = x.get("pins") or []
    funcs = [pin.get("function") for pin in pins]
    if funcs.count("vcc") != 1:
        p.append("needs exactly one VCC pin")
    if funcs.count("gnd") < 1:
        p.append("needs a GND pin")
    if "other" in funcs:
        p.append("has pins whose function is unknown")
    need = {"digital_sensor": {"digital_out": 1}, "analog_sensor": {"analog_out": 1},
            "digital_and_analog_sensor": {"digital_out": 1, "analog_out": 1}, "switched_module": {"control_in": 1}}[kind]
    for f, n in need.items():
        if funcs.count(f) != n:
            p.append(f"a {kind} needs exactly {n} {f} pin")
    extra = [f for f in funcs if f not in ("vcc", "gnd") and f not in need]
    if extra:
        p.append(f"unexpected signal pins: {extra}")
    lo, hi = x.get("vcc_min"), x.get("vcc_max")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or not (0 < lo <= hi <= 24):
        p.append("its supply range must be stated (at most 24 V)")
    elif lo > 5.0 and kind != "switched_module":
        # A sensor powered above 5 V can output more than 5 V into an Arduino pin. A switched module only receives the
        # Arduino's signal, so it may have its own higher-voltage supply (safety.plan_power adds one).
        p.append("a sensor must run on 5 V so its output is safe for an Arduino pin")
    elif hi < 5.0:
        p.append("it cannot run on 5 V or more, and 3.3 V parts are not supported yet")
    cur = x.get("current_ma")
    if cur is not None and not (0 <= cur <= 2000):
        p.append("current draw out of range")
    if kind != "analog_sensor" and x.get("active_level") not in ("HIGH", "LOW"):
        p.append("active level (HIGH or LOW) must be stated")
    text = source_text.lower()
    for pin in pins:
        if _norm(pin.get("label", "")) not in _norm(text):
            p.append(f"pin label '{pin.get('label')}' does not appear in the sources")
    if PROTOCOL_WORDS.search(" ".join(pin.get("label", "") for pin in pins)):
        p.append("pin labels suggest a data protocol")
    return p


def card_from_extraction(x, urls):
    kind = x["kind"]
    sim = {"digital_sensor": "generic_digital_in", "analog_sensor": "generic_analog_in",
           "digital_and_analog_sensor": "generic_analog_in", "switched_module": "generic_digital_out"}[kind]
    pins = {}
    for pin in x["pins"]:
        key = _slug(pin["label"])
        while key in pins:
            key += "_2"
        f = pin["function"]
        if f == "vcc":
            pins[key] = {"rail": "5V", "label": pin["label"]}
        elif f == "gnd":
            pins[key] = {"rail": "GND", "label": pin["label"]}
        elif f == "analog_out":
            pins[key] = {"role": "analog_in", "label": pin["label"]}
        elif f == "digital_out":
            pins[key] = {"role": "digital_in", "label": pin["label"], **({"optional": True} if kind == "digital_and_analog_sensor" else {})}
        elif f == "control_in":
            pins[key] = {"role": "digital_out", "label": pin["label"]}
    current = x.get("current_ma") or 20
    return {
        "name": x["name"], "aliases": [x["name"].lower()], "sim": sim, "pins": pins, "extra": {},
        "active_level": x.get("active_level"), "pin_ma": 5 if kind == "switched_module" else 0,
        "vcc_range": [x["vcc_min"], x["vcc_max"]], "rail_ma": current, "libraries": [],
        "notes": x.get("notes", [])[:3], "unverified": True, "source_urls": urls,
    }


def unknown_part(name):
    """Try to admit a part with no curated card. Returns (key, card) or (None, reason)."""
    src = Sources()
    src.add(search(f"{name} module pinout datasheet operating voltage", domains=PART_DOMAINS), "part")
    src.add(search(f"{name} Arduino wiring pins", 3, domains=PART_DOMAINS), "part")
    results = src.items
    if not results:
        return None, f"No datasheet was found on the approved sites ({', '.join(PART_DOMAINS)})."
    try:
        x = llm.chat_json("extract_part", llm.SPARK_MODEL, prompts.extract_part(name, results))
    except ValueError:
        return None, "The datasheet could not be read reliably."
    problems = validate_extraction(x, " ".join(r["content"] + " " + r["title"] for r in results))
    if problems:
        return None, "Not supported yet: " + "; ".join(problems) + "."
    return f"web_{_slug(x['name'])}", card_from_extraction(x, [r["url"] for r in results])


# ---------- structured, verified fact sheet from the sources ----------

def _in_text(quote, text):
    q = _norm(quote or "")
    return bool(q) and q in _norm(text)


def _quote_ok(quote, value, text):
    """The quote appears verbatim (ignoring spacing/case) in the sources and contains the value."""
    if not isinstance(quote, str) or value is None or not _in_text(quote, text):
        return False
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", quote)]
    return any(abs(n - float(value)) < 1e-6 or abs(n * 1000 - float(value)) < 1e-6 for n in nums)


def _squash(s):
    return re.sub(r"\s+", "", s or "")


def facts(cards, src):
    """One structured fact sheet per part and per library, with every field backed by a verbatim quote from an
    approved-site source. Unverifiable fields are dropped, so nothing unchecked reaches the model.
    Returns (facts, warnings)."""
    part_src = [s for s in src.items if s["topic"].startswith("part:")]
    lib_src = [s for s in src.items if s["topic"].startswith("lib:")]
    if not part_src and not lib_src:
        return {"parts": {}, "libraries": {}}, ["No approved-site sources were found; the curated part cards were used as-is."]
    libs = sorted({h for c in cards.values() for h in c.get("libraries", []) if h != "Wire.h"})
    try:
        res = llm.chat_json("facts", llm.SPARK_MODEL, prompts.facts(cards, libs, part_src + lib_src))
    except ValueError:
        return {"parts": {}, "libraries": {}}, ["The datasheet facts could not be read reliably; the curated part cards were used as-is."]

    out, warnings = {"parts": {}, "libraries": {}}, []
    for key, x in (res.get("parts") or {}).items():
        if key not in cards or not isinstance(x, dict):
            continue
        mine = [s for s in part_src if s["topic"] == f"part:{key}"]
        text = " ".join(s["title"] + " " + s["content"] for s in mine)
        f, dropped = {"pins": [], "specs": []}, 0
        for pin in x.get("pins") or []:
            label, quote = pin.get("label"), pin.get("quote")
            if isinstance(label, str) and _in_text(quote, text) and _norm(label) in _norm(quote):
                f["pins"].append({"label": label, "function": pin.get("function")})
            else:
                dropped += 1
        lo, hi = x.get("vcc_min"), x.get("vcc_max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 1 <= lo <= hi <= 30 and _quote_ok(x.get("vcc_quote"), hi, text):
            f["vcc_range"] = [lo, hi]
        elif lo is not None or hi is not None:
            dropped += 1
        for field, qfield, limit, name in (("typical_ma", "typical_quote", 3000, "rail_ma"), ("peak_ma", "peak_quote", 5000, "stall_ma")):
            v = x.get(field)
            if isinstance(v, (int, float)) and 0 <= v <= limit and _quote_ok(x.get(qfield), v, text):
                f[name] = v
            elif v is not None:
                dropped += 1
        for sp in x.get("specs") or []:
            nums = re.findall(r"\d+(?:\.\d+)?", str(sp.get("value", "")))
            q = sp.get("quote")
            if isinstance(sp.get("name"), str) and _in_text(q, text) and nums and all(n in q for n in nums):
                f["specs"].append({"name": sp["name"], "value": sp["value"]})
            else:
                dropped += 1
        # A part with no supply pin (bare LED, LDR, button) has no supply voltage or rail current: a datasheet voltage
        # there is a forward voltage or rating. Drop such figures so they never reach a prompt as supply facts.
        if not any(pin.get("rail") == "5V" for pin in cards[key]["pins"].values()):
            dropped += sum(1 for k in ("vcc_range", "rail_ma", "stall_ma") if f.pop(k, None) is not None)
        f["sources"] = sorted({s["url"] for s in mine})
        f["dropped_unverified"] = dropped
        out["parts"][key] = f
        # Cross-check the curated card's printed pin names against the verified pins (card stays authoritative).
        card = cards[key]
        if f["pins"] and not card.get("unverified"):
            labels = [sp.get("label", pn).split(" ")[0] for pn, sp in card["pins"].items()
                      if not re.search(r"\b(wire|leg)\b", sp.get("label", ""))]
            seen = {_norm(pp["label"]) for pp in f["pins"]}
            missing = [l for l in labels if _norm(l) and not any(_norm(l) in s_ or s_ in _norm(l) for s_ in seen)]
            if missing:
                warnings.append(f"{card['name']}: the sources' pin list does not show {', '.join(missing)}; "
                                "the curated wiring was used.")
    for header, x in (res.get("libraries") or {}).items():
        if header not in libs or not isinstance(x, dict):
            continue
        mine = [s for s in lib_src if s["topic"] == f"lib:{header}"]
        text = " ".join(s["content"] for s in mine)
        calls = []
        for c in x.get("calls") or []:
            code, q = c.get("code"), c.get("quote")
            if isinstance(code, str) and _in_text(q, text) and _squash(code) in _squash(q):
                calls.append(code.strip())
        if calls:
            out["libraries"][header] = {"calls": calls[:8], "sources": sorted({s["url"] for s in mine})}
    return out, warnings


def apply_facts(cards, fact_sheet):
    """Copies of the cards with verified power figures applied (only the parts that have any)."""
    eff = {}
    for key, f in (fact_sheet or {}).get("parts", {}).items():
        if key not in cards or not any(k in f for k in ("vcc_range", "rail_ma", "stall_ma")):
            continue
        # Only modules powered from a supply rail have a supply voltage and rail current. For bare LEDs, LDRs, buttons
        # and potentiometers a datasheet "voltage" is a forward voltage or rating, not a supply requirement.
        powered = cards[key].get("vcc_range") is not None and any(p.get("rail") == "5V" for p in cards[key]["pins"].values())
        if not powered:
            continue
        c = dict(cards[key])
        verified = [k for k in ("vcc_range", "rail_ma", "stall_ma") if k in f]
        for k in verified:
            c[k] = f[k]
        # Current figures may only raise the budget: a datasheet quote can describe a different current (trigger input,
        # standby, per channel), and under-counting would keep a power-hungry build on the Arduino's 5 V pin.
        for k in ("rail_ma", "stall_ma"):
            if k in verified and isinstance(cards[key].get(k), (int, float)) and isinstance(f[k], (int, float)):
                c[k] = max(cards[key][k], f[k])
        c["electrical_source"] = {"verified": verified, "sources": f["sources"]}
        eff[key] = c
    return eff
