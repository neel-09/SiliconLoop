"""Orchestration: recognise -> confirm -> ground -> plan -> allocate -> safety -> firmware -> lint/compile/simulate-repair loop (max 3).

Each stage is a function on a stored build, so the API (and Swagger) can drive it step by step.
Builds are saved as JSON under BUILD_DIR, which on Cloud Run lives in the mounted bucket.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from . import llm, parts, prompts, simspec
from .allocator import BOARDS, PARTS, AllocationError, allocate
from .compiler import compile_sketch, lint_cards, lint_sketch, numbered, tidy_sketch
from .safety import MAINS, check, plan_power
from .simulator import SimulatorError, simulate

BUILD_DIR = Path(os.getenv("BUILD_DIR") or Path(__file__).resolve().parents[2] / ".cache" / "builds")
MAX_REPAIRS = 3
_lock = threading.Lock()
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
TYPE_CHECK = {
    "bool": lambda v: isinstance(v, bool),
    "fraction": lambda v: _num_or_range(v, 0, 1),
    "adc": lambda v: _num_or_range(v, 0, 1023),
    "angle": lambda v: _num_or_range(v, 0, 180),
    "distance": lambda v: _num_or_range(v, 2, 400),
    "temp": lambda v: _num_or_range(v, -40, 80),
    "pct": lambda v: _num_or_range(v, 0, 100),
    "tilt": lambda v: _num_or_range(v, -90, 90),
    "str": lambda v: isinstance(v, str) and 0 < len(v) <= 40,
}


class PipelineError(Exception):
    pass


def _quiet(*_a, **_k):
    """Default trace sink: the execution trace is optional (Swagger and the eval runner ignore it)."""


def _short(text, n=600):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + " ..."


def _num_or_range(v, lo, hi):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return lo <= v <= hi
    if isinstance(v, dict) and set(v) <= {"min", "max"} and v:
        return all(isinstance(x, (int, float)) and lo <= x <= hi for x in v.values())
    return False


# ---------- build storage ----------

def _path(build_id):
    if not re.fullmatch(r"[a-f0-9]{12}", build_id):
        raise PipelineError("Unknown build.")
    return BUILD_DIR / f"{build_id}.json"


def load(build_id):
    p = _path(build_id)
    if not p.exists():
        raise PipelineError("Unknown build.")
    return json.loads(p.read_text(encoding="utf-8"))


def save(build):
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    build["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with _lock:
        _path(build["id"]).write_text(json.dumps(build), encoding="utf-8")
    return build


def _cards_for(build):
    base = {**PARTS, **build.get("web_cards", {})}
    return {**base, **parts.apply_facts(base, build.get("facts", {}))}


# ---------- 1. recognise ----------

def _validate_recognition(obj):
    p = []
    if not isinstance(obj.get("parts"), list):
        return ["'parts' must be a list"]
    for x in obj["parts"]:
        if not isinstance(x.get("name"), str):
            p.append("every part needs a name")
        if x.get("part") is not None and x["part"] not in PARTS:
            p.append(f"'{x['part']}' is not a catalogue key; use null if nothing matches")
        if not isinstance(x.get("count"), int) or x["count"] < 1:
            p.append(f"{x.get('name')}: count must be a positive integer")
    return p


def recognise(board_key, images):
    """images: [(bytes, mime)]. Returns a new build with the recognised parts list (cached by image hash)."""
    if board_key not in BOARDS:
        raise PipelineError(f"Choose a board: {', '.join(BOARDS)}.")
    if not images:
        raise PipelineError("Upload at least one photo of your components.")
    digest = hashlib.sha256(b"".join(hashlib.sha256(d).digest() for d, _ in images)).hexdigest()[:20]
    cache = BUILD_DIR.parent / f"recog_{digest}.json"
    if cache.exists():
        rec = json.loads(cache.read_text(encoding="utf-8"))
    else:
        msgs = prompts.recognise([llm.image_part(d, m) for d, m in images], PARTS)
        rec = llm.chat_json("recognise", llm.SPARK_MODEL, msgs, validate=_validate_recognition)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rec), encoding="utf-8")

    found = []
    for x in rec["parts"]:
        key = x.get("part") or parts.resolve(x["name"])
        found.append({"name": x["name"], "part": key, "count": x["count"],
                      "confidence": x.get("confidence"), "status": "supported" if key else "unknown"})
    build = {"id": uuid.uuid4().hex[:12], "board": board_key, "photos": digest, "recognised": found,
             "ignored": rec.get("ignored", []), "image_quality": rec.get("image_quality", "ok"), "status": "recognised"}
    return save(build)


# ---------- 2. design: ground -> plan -> allocate -> safety ----------

def validate_plan(plan, available, cards, board_key):
    p = []
    for k in ("title", "summary", "intents", "rules"):
        if k not in plan:
            p.append(f"missing '{k}'")
    if p:
        return p
    ids, used = {}, {}
    for i in plan["intents"]:
        iid, part = i.get("id", ""), i.get("part")
        if not ID_RE.match(iid or ""):
            p.append(f"intent id '{iid}' must be short lowercase letters/digits")
        if iid in ids:
            p.append(f"intent id '{iid}' is used twice")
        if part not in available:
            p.append(f"'{part}' is not one of the available parts: {sorted(available)}")
            continue
        used[part] = used.get(part, 0) + 1
        ids[iid] = part
        card, opts = cards[part], i.setdefault("options", {})
        for k, v in opts.items():
            if k == "use":
                optional = {pp for pp, s in card["pins"].items() if s.get("optional")}
                if not isinstance(v, list) or not set(v) <= optional:
                    p.append(f"{iid}: options.use may only list {sorted(optional) or 'nothing'}")
            elif k in card.get("options", {}):
                if v not in card["options"][k]:
                    p.append(f"{iid}: {k} must be one of {card['options'][k]}")
            else:
                p.append(f"{iid}: unknown option '{k}'")
    for part, n in used.items():
        if n > available[part]:
            p.append(f"uses {n} x {part} but only {available[part]} available")

    def io_of(iid):
        if iid == "board":
            return simspec.BOARD_IO
        return simspec.io_of(cards[ids[iid]], plan_intent(plan, iid).get("options") or {})

    rules = plan["rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 12:
        p.append("rules must be a list of 1 to 12 rules")
        return p
    set_ids, checked_ids = set(), set()
    for r in rules:
        rid = r.get("id", "?")
        steps = r.get("steps")
        if not isinstance(steps, list) or not steps:
            p.append(f"{rid}: needs at least one step")
            continue
        for s in steps:
            for kind, field in (("in", "set"), ("out", "expect")):
                for iid, vals in (s.get(field) or {}).items():
                    if iid != "board" and iid not in ids:
                        p.append(f"{rid}: '{iid}' in {field} is not an intent id")
                        continue
                    allowed = io_of(iid).get(kind, {})
                    if not isinstance(vals, dict) or not vals:
                        p.append(f"{rid}: {field}.{iid} must be an object")
                        continue
                    for k, v in vals.items():
                        if k == "telemetry" and iid == "board":
                            p += _check_telemetry(rid, v, ids, io_of)
                            continue
                        need = io_of(iid).get("conditional", {}).get(k) if iid != "board" else None
                        if k not in allowed and need:
                            p.append(f"{rid}: {field}.{iid}.{k} needs options.{need} = true on {iid}")
                        elif k not in allowed:
                            p.append(f"{rid}: {field}.{iid}.{k} is not allowed; use {sorted(allowed) or 'nothing'}")
                        elif not TYPE_CHECK[allowed[k]](v):
                            p.append(f"{rid}: {field}.{iid}.{k} has an invalid value {v!r}")
                    (set_ids if kind == "in" else checked_ids).add(iid)
            if not s.get("expect"):
                p.append(f"{rid}: every step needs an expect")
            w = s.get("within_ms")
            if not isinstance(w, int) or not 100 <= w <= 10000:
                p.append(f"{rid}: within_ms must be an integer 100-10000")
            if "hold_ms" in s and (not isinstance(s["hold_ms"], int) or not 0 <= s["hold_ms"] <= 10000):
                p.append(f"{rid}: hold_ms must be an integer 0-10000")
    for iid, part in ids.items():
        io = io_of(iid)
        if io.get("in") and iid not in set_ids:
            p.append(f"input {iid} is never set in any rule")
        if io.get("out") and iid not in checked_ids:
            p.append(f"output {iid} is never checked in any rule")
    p += _coverage(rules, ids, io_of)
    p += _thresholds(plan, rules, ids, io_of)
    if not p:
        try:
            allocate(board_key, plan["intents"], extra_cards={k: v for k, v in cards.items() if k not in PARTS})
        except AllocationError as e:
            p.append(f"the design does not fit the board: {e} Use fewer parts.")
    return p


def _norm_val(v):
    return json.dumps(v, sort_keys=True)


def _coverage(rules, ids, io_of):
    """Every input set to 2+ values, every output checked in 2+ states, every serial_contains also excluded, and every
    output seen changing state inside one rule (a power-up default alone never proves the firmware switches it)."""
    sets, checks, contains, excludes, changes = {}, {}, set(), set(), set()
    for r in rules:
        per_rule = {}
        for s in r.get("steps") or []:
            for iid, vals in (s.get("expect") or {}).items():
                if iid != "board" and isinstance(vals, dict):
                    for k, v in vals.items():
                        per_rule.setdefault((iid, k), set()).add(_norm_val(v))
        changes |= {key for key, vals in per_rule.items() if len(vals) >= 2}
        for s in r.get("steps") or []:
            for iid, vals in (s.get("set") or {}).items():
                if isinstance(vals, dict):
                    sets.setdefault(iid, set()).add(_norm_val(vals))
            for iid, vals in (s.get("expect") or {}).items():
                if not isinstance(vals, dict):
                    continue
                if iid == "board":
                    if isinstance(vals.get("serial_contains"), str):
                        contains.add(vals["serial_contains"])
                    if isinstance(vals.get("serial_excludes"), str):
                        excludes.add(vals["serial_excludes"])
                    continue
                for k, v in vals.items():
                    checks.setdefault((iid, k), set()).add(_norm_val(v))
    p = []
    for iid in ids:
        io = io_of(iid)
        if io.get("in") and len(sets.get(iid, ())) < 2:
            p.append(f"input {iid} must be set to at least two different values across the rules")
        outs = {k: t for k, t in io.get("out", {}).items() if t != "str"}
        if outs and not any(len(checks.get((iid, k), ())) >= 2 for k in outs):
            p.append(f"output {iid} must be checked in at least two different states (e.g. on and off)")
        elif outs and not any((iid, k) in changes for k in outs):
            p.append(f"output {iid} must change state within one rule: add a rule whose steps first bring it to one state "
                     f"and then, after changing the inputs, check it in the other (e.g. on, then off)")
    for text in sorted(contains - excludes):
        p.append(f"'{text}' is only checked with serial_contains; add a rule where it must NOT be printed (serial_excludes)")
    return p


# One test band per numeric input type: the firmware's hysteresis is capped below it (prompts.firmware).
BAND = {"adc": 25, "distance": 5, "angle": 5, "tilt": 5, "temp": 2, "pct": 5, "fraction": 0.05}


def _thresholds(plan, rules, ids, io_of):
    """Every numeric rule input declares a threshold (or is proportional), and the rules test each threshold
    closely: one value on the active side within one band, one on the other side one to two bands past it.
    Without this, firmware with the threshold in the wrong place could still pass."""
    th = plan.get("thresholds")
    if not isinstance(th, list):
        return ["missing 'thresholds' (a list, empty only if no rule input is numeric)"]
    p, declared = [], {}
    for t in th:
        inp = t.get("input") if isinstance(t, dict) else None
        iid, _, k = (inp or "").partition(".")
        typ = io_of(iid).get("in", {}).get(k) if iid in ids else None
        if typ not in BAND:
            p.append(f"thresholds: '{inp}' is not a numeric rule input")
            continue
        if inp in declared:
            p.append(f"thresholds: '{inp}' is listed twice")
        declared[inp] = t
        if t.get("proportional") is True:
            continue
        if not isinstance(t.get("value"), (int, float)) or isinstance(t.get("value"), bool) or t.get("active") not in ("below", "above"):
            p.append(f"thresholds: '{inp}' needs a numeric value and active 'below' or 'above'")
            continue
        if not TYPE_CHECK[typ](t["value"]):
            p.append(f"thresholds: '{inp}' value {t['value']} is out of range")
            continue
        b, v, sign = BAND[typ], t["value"], (-1 if t["active"] == "below" else 1)
        tested = [x for r in rules for st in (r.get("steps") or [])
                  for x in [((st.get("set") or {}).get(iid) or {}).get(k)] if isinstance(x, (int, float)) and not isinstance(x, bool)]
        near_on = [x for x in tested if 0 < (x - v) * sign <= b or (x == v)]
        near_off = [x for x in tested if b <= (v - x) * sign <= 2 * b]
        lo_on, hi_on = sorted([v, v + sign * b])
        lo_off, hi_off = sorted([v - sign * b, v - sign * 2 * b])
        if not near_on:
            p.append(f"thresholds: test {inp} on the active side close to {v}: set it to a value from {_fmtn(lo_on)} to {_fmtn(hi_on)} in some step")
        if not near_off:
            p.append(f"thresholds: test {inp} just past {v} on the other side: set it to a value from {_fmtn(lo_off)} to {_fmtn(hi_off)} in some step")
    for iid in ids:
        for k, typ in io_of(iid).get("in", {}).items():
            if typ in BAND and f"{iid}.{k}" not in declared:
                p.append(f"thresholds: add an entry for {iid}.{k} (its threshold, or proportional: true)")
    return p


def _fmtn(x):
    return int(x) if float(x).is_integer() else round(x, 3)


def _check_telemetry(rid, tel, ids, io_of):
    if not isinstance(tel, dict) or not tel:
        return [f"{rid}: board.telemetry must map '<id>.<key>' to a number or {{min, max}}"]
    p = []
    for key, v in tel.items():
        iid, _, k = key.partition(".")
        io = io_of(iid) if iid in ids else {}
        kinds = {**io.get("in", {}), **io.get("out", {})}
        if k not in kinds or kinds[k] in ("str", "bool"):
            p.append(f"{rid}: telemetry key '{key}' must be <intent id>.<numeric rule key>")
        elif not TYPE_CHECK[kinds[k]](v):
            p.append(f"{rid}: telemetry {key} has an invalid value {v!r}")
    return p


def plan_intent(plan, iid):
    return next((i for i in plan["intents"] if i.get("id") == iid), {})


def design(build_id, confirmed, wish, emit=_quiet):
    """confirmed: [{name, part (key or null), count}] after the user's review.
    emit(stage, state, text, **data) reports progress for the live execution trace."""
    build = load(build_id)
    board = BOARDS[build["board"]]
    wish = (wish or "").strip()
    if not wish:
        raise PipelineError("Describe what the gadget should do.")
    if MAINS.search(wish):  # refuse before spending anything on grounding or planning
        build.update(wish=wish, status="refused", safety=check({"board": build["board"], "parts": []}, wish))
        emit("safety", "fail", "Refused: the wish involves mains electricity.", errors=build["safety"]["errors"])
        return save(build)
    emit("parts", "start", f"Checking {len(confirmed)} confirmed part type(s) against the catalogue")
    web_cards, unsupported, available, names = {}, [], {}, {}
    for x in confirmed:
        key = x.get("part") or parts.resolve(x["name"])
        if key and key in PARTS:
            available[key] = available.get(key, 0) + int(x.get("count", 1))
            names.setdefault(key, []).append(x["name"])
            continue
        emit("parts", "info", f"No curated card for '{x['name']}': searching approved datasheet sites")
        wkey, card = parts.unknown_part(x["name"])
        if wkey:
            emit("parts", "info", f"Admitted '{x['name']}' from web datasheets (unverified)")
            web_cards[wkey] = card
            available[wkey] = available.get(wkey, 0) + int(x.get("count", 1))
            names.setdefault(wkey, []).append(x["name"])
        else:
            unsupported.append({"name": x["name"], "reason": card})
            emit("parts", "warn", f"'{x['name']}' cannot be simulated yet and was left out", reason=card)
    if not available:
        raise PipelineError("None of the confirmed parts can be simulated yet.")
    build.update(confirmed=confirmed, wish=wish, web_cards=web_cards, unsupported=unsupported)
    cards = _cards_for(build)
    used_cards = {k: cards[k] for k in available}
    emit("parts", "ok", f"{sum(available.values())} part(s) ready for {board['name']}",
         parts=[{"key": k, "name": cards[k]["name"], "count": n} for k, n in available.items()])

    emit("ground", "start", "Searching approved datasheet sites for pinouts, power figures and library usage")
    src = parts.ground(board, used_cards, wish)
    emit("ground", "info", f"{len(src.items)} source(s) found",
         sources=[{"title": x["title"], "url": x["url"]} for x in src.items[:12]])
    # Structured fact sheet: every field verified against an approved-site quote. Verified power figures
    # (supply voltage, typical and peak current) override the card defaults; raw snippets never reach a prompt.
    fact_sheet, warnings = parts.facts(used_cards, src)
    eff = parts.apply_facts(cards, fact_sheet)
    cards = {**cards, **eff}
    n_facts = sum(len(x.get("pins", [])) + len(x.get("specs", [])) for x in (fact_sheet or {}).get("parts", {}).values())
    emit("ground", "ok", f"Fact sheet verified: {n_facts} quoted fact(s), {len(warnings)} warning(s)", warnings=warnings[:6])

    emit("plan", "start", "Muse Spark Open is turning the wish into machine-checkable behaviour rules")
    plan_obj = llm.chat_json(
        "plan", llm.SPARK_MODEL, prompts.plan(board, {k: cards[k] for k in used_cards}, wish, fact_sheet, names),
        validate=lambda o: validate_plan(o, available, cards, build["board"]),
        on_problems=lambda n, probs: emit("plan", "retry", f"Validator rejected draft {n}: {len(probs)} problem(s), asking for a fix",
                                          problems=[_short(x, 200) for x in probs[:5]]),
    )
    emit("plan", "ok", f"{plan_obj['title']}: {len(plan_obj['rules'])} rules, {len(plan_obj['intents'])} part(s)",
         title=plan_obj["title"], summary=plan_obj.get("summary", ""),
         rules=[{"id": r.get("id"), "description": r.get("description", "")} for r in plan_obj["rules"]],
         thresholds=plan_obj.get("thresholds", []))
    extra = {k: v for k, v in cards.items() if k not in PARTS or k in eff}
    emit("allocate", "start", "Assigning real pins from the board facts")
    wiring = plan_power(allocate(build["board"], plan_obj["intents"], extra_cards=extra), extra_cards=extra)
    emit("allocate", "ok", f"{len(wiring['pin_map'])} signal pin(s), {sum(1 for e in wiring['extras'] if e['part'] == 'resistor')} resistor(s) added",
         pin_map=wiring["pin_map"], extras=wiring["extras"], libraries=wiring["libraries"])
    safety = check(wiring, wish, extra_cards=extra)
    if safety["safe"]:
        emit("safety", "ok", f"Safe to build: 5 V load about {safety['budget']['rail_5v_ma']} mA of {safety['budget']['usb_limit_ma']} mA",
             warnings=safety["warnings"], budget=safety["budget"])
    else:
        emit("safety", "fail", "Refused by the electrical safety check", errors=safety["errors"])
    build.update(sources=[{"n": x["n"], "topic": x["topic"], "title": x["title"], "url": x["url"]} for x in src.items],
                 facts=fact_sheet, grounding_warnings=warnings, plan=plan_obj, wiring=wiring, safety=safety,
                 status="designed" if safety["safe"] else "refused")
    return save(build)


# ---------- 3. firmware: write -> compile -> simulate -> repair ----------

def _strip_pin_block(code):
    return re.sub(r"// ---- SiliconLoop pin map.*?// -{10,}\n?", "", code, flags=re.S)


def _sim_summary(res):
    """What the build keeps from a simulation run: per-rule and per-step outcome, no raw serial logs."""
    rules = [{"id": r["id"], "ok": r["ok"], "error": r.get("error"), "sim_ms": r.get("sim_ms"),
              "steps": [{k: s.get(k) for k in ("ok", "metMs", "atMs", "failed")} for s in r.get("steps", [])]}
             for r in res["rules"]]
    return {"ok": res["ok"], "passed": res["passed"], "total": res["total"], "health": res["health"],
            "rules": rules, "wall_ms": res.get("wall_ms"), "report": res.get("report", "")}


def firmware(build_id, emit=_quiet):
    """Spark Open writes the sketch; it must pass lint, compile (with memory limits) and the simulation test.
    Every failure goes back to the model with the exact report, at most MAX_REPAIRS times."""
    build = load(build_id)
    if build.get("status") not in ("designed", "compiled", "verified", "failed"):
        raise PipelineError("Design the gadget (and pass the safety check) first.")
    board, cards, wiring, plan = BOARDS[build["board"]], _cards_for(build), build["wiring"], build["plan"]
    header = prompts.pin_block(wiring)
    keys = prompts.telemetry_keys(plan, cards)
    msgs = prompts.firmware(board, wiring, plan, cards, build.get("facts", {}))

    attempts, result, sketch, sim = [], {"ok": False}, "", None
    emit("firmware", "start", "Muse Spark Open is writing the firmware from the rules and the pin map")
    reply = llm.text_of(llm.chat("firmware", llm.SPARK_MODEL, msgs))
    for attempt in range(MAX_REPAIRS + 1):
        code, _ = tidy_sketch(_strip_pin_block(llm.code_of(reply)), wiring["libraries"])
        sketch = header + code
        emit("firmware", "ok", f"{'Draft' if attempt == 0 else 'Repair ' + str(attempt)}: {len(sketch.splitlines())} lines of C++",
             attempt=attempt)
        problems = lint_sketch(code, keys)  # check only the model's part, not the generated pin constants
        problems += lint_cards(code, [cards[i["part"]] for i in plan["intents"]])
        if problems:
            result = {"ok": False, "stage": "lint", "errors": "\n".join(problems)}
            emit("compile", "fail", f"Code rules: {len(problems)} problem(s)", errors=_short(result["errors"]), attempt=attempt)
        else:
            emit("compile", "start", "Compiling with arduino-cli", attempt=attempt)
            result = compile_sketch(sketch, build["board"], wiring["libraries"])
            if result["ok"]:
                emit("compile", "ok", f"Compiled: flash {result.get('flash')} / {result.get('flash_max')} B, RAM {result.get('ram')} / {result.get('ram_max')} B",
                     attempt=attempt, **{k: result.get(k) for k in ("flash", "flash_max", "ram", "ram_max")})
                emit("simulate", "start", f"Running the binary in a simulated {board['name']} against {len(plan['rules'])} rules",
                     attempt=attempt)
                try:
                    sim = simulate(build["board"], result["hex"], wiring, plan, cards, keys)
                except SimulatorError as e:  # our fault, not the model's: stop without spending a repair
                    attempts.append({"stage": "simulator", "ok": False, "errors": str(e)})
                    result = {**result, "ok": False, "stage": "simulator"}
                    emit("simulate", "fail", "The simulator itself failed", errors=_short(str(e)), attempt=attempt)
                    break
                result = {**result, "ok": sim["ok"], "stage": "simulation", "errors": sim["report"]}
                emit("simulate", "ok" if sim["ok"] else "fail",
                     f"{sim['passed']} / {sim['total']} rules passed" + ("" if sim["health"]["ok"] else ", health check failed"),
                     attempt=attempt, rules=[{"id": r["id"], "ok": r["ok"], "description": r.get("description", "")} for r in sim["rules"]],
                     errors=_short(sim["report"]) if not sim["ok"] else "")
            else:
                emit("compile", "fail", f"Compiler {result.get('stage', 'compile')} error", errors=_short(result.get("errors", "")), attempt=attempt)
        attempts.append({"stage": result["stage"], "ok": result["ok"], "errors": result.get("errors", ""),
                         **({"passed": sim["passed"], "total": sim["total"]} if result["stage"] == "simulation" else {})})
        if result["ok"] or result["stage"] == "setup" or attempt == MAX_REPAIRS:
            break
        msgs += [{"role": "assistant", "content": reply}, prompts.repair(result["stage"], result["errors"], numbered(sketch))]
        emit("repair", "start", f"Repair round {attempt + 1} of {MAX_REPAIRS}: sending the {result['stage']} report back to Muse Spark Open",
             attempt=attempt + 1)
        reply = llm.text_of(llm.chat("repair", llm.SPARK_MODEL, msgs))

    verified = result["ok"] and result["stage"] == "simulation"
    build.update(sketch=sketch, attempts=attempts, telemetry_keys=keys,
                 hex=result.get("hex") if verified else None,  # never hand out a binary that has not passed the test
                 memory={k: result.get(k) for k in ("flash", "flash_max", "ram", "ram_max")},
                 simulation=_sim_summary(sim) if sim else None,
                 status="verified" if verified else "failed")
    build.pop("parts_h", None)
    if verified:
        emit("verify", "ok", f"Verified: every behaviour rule passed in simulation after {len(attempts)} attempt(s)")
    else:
        emit("verify", "fail", "Not verified: the firmware did not pass every gate, so no binary is handed out")
    return save(build)


# ---------- ideas: three buildable gadgets for the confirmed parts ----------

MAX_IDEA_WISH = 220
UNTESTABLE = re.compile(r"\b(wi-?fi|bluetooth|internet|online|cloud|app|phone|sms|e-?mail|alexa|voice|camera|sd card|"
                        r"time of day|o'clock|calendar|music|song|melody|speaker|gps)\b", re.I)


def _available(confirmed, build):
    """Confirmed parts -> {catalogue key: count}. Only parts the simulator supports (curated or already-admitted web cards)."""
    cards = _cards_for(build)
    out = {}
    for x in confirmed:
        key = x.get("part") or parts.resolve(x.get("name", ""))
        if key and key in cards:
            out[key] = out.get(key, 0) + int(x.get("count", 1))
    return out


def validate_ideas(obj, available, board_key, cards):
    p = []
    ideas_ = obj.get("ideas")
    if not isinstance(ideas_, list) or len(ideas_) != 3:
        return ["'ideas' must be a list of exactly 3 ideas"]
    seen = set()
    for n, idea in enumerate(ideas_, 1):
        title, wish, used = idea.get("title"), idea.get("wish"), idea.get("parts_used")
        if not isinstance(title, str) or not 3 <= len(title) <= 48:
            p.append(f"idea {n}: title must be 3-48 characters")
        if not isinstance(wish, str) or not 20 <= len(wish) <= MAX_IDEA_WISH:
            p.append(f"idea {n}: wish must be one sentence of 20-{MAX_IDEA_WISH} characters")
        elif MAINS.search(wish):
            p.append(f"idea {n}: never involve mains electricity")
        elif UNTESTABLE.search(wish):
            p.append(f"idea {n}: '{UNTESTABLE.search(wish).group(0)}' cannot be built or tested with these parts; "
                     "use only the listed parts' inputs and outputs")
        if not isinstance(used, list) or not used:
            p.append(f"idea {n}: parts_used must list the part keys it uses")
            continue
        counts = {}
        for k in used:
            if k not in available:
                p.append(f"idea {n}: '{k}' is not one of the available parts {sorted(available)}")
            counts[k] = counts.get(k, 0) + 1
        for k, c in counts.items():
            if k in available and c > available[k]:
                p.append(f"idea {n}: uses {c} x {k} but only {available[k]} available")
        if any(k not in available for k in used):
            continue
        io = [simspec.io_of(cards[k]) for k in used]
        if not any(x["out"] for x in io) and not any(x["in"] for x in io):
            p.append(f"idea {n}: must use at least one sensor or output part")
        key = tuple(sorted(used)), (wish or "").lower()[:40]
        if key in seen:
            p.append(f"idea {n}: repeats another idea; make the three ideas clearly different")
        seen.add(key)
        intents = [{"id": f"{k[:10]}{i}", "part": k, "options": {}} for i, k in enumerate(used, 1)]
        try:
            allocate(board_key, intents, extra_cards={k: v for k, v in cards.items() if k not in PARTS})
        except AllocationError as e:
            p.append(f"idea {n}: does not fit the board ({e}); use fewer parts")
    return p


def ideas(build_id, confirmed):
    """Spark Open proposes three gadgets that use only the confirmed, simulatable parts. Each is pre-checked by the
    pin allocator, so every idea can go straight into design()."""
    build = load(build_id)
    available = _available(confirmed, build)
    if not available:
        raise PipelineError("None of the confirmed parts can be simulated yet, so there is nothing to suggest.")
    board, cards = BOARDS[build["board"]], _cards_for(build)
    obj = llm.chat_json("ideas", llm.SPARK_MODEL, prompts.ideas(board, {k: cards[k] for k in available}, available),
                        validate=lambda o: validate_ideas(o, available, build["board"], cards))
    out = []
    for idea in obj["ideas"]:
        used = {}
        for k in idea["parts_used"]:
            used[k] = used.get(k, 0) + 1
        out.append({"title": idea["title"].strip(), "wish": idea["wish"].strip(),
                    "why": (idea.get("why") or "").strip()[:160],
                    "parts_used": [{"key": k, "name": cards[k]["name"], "count": c} for k, c in used.items()]})
    build.update(ideas=out, confirmed=confirmed)
    save(build)
    return out


# ---------- the browser twin ----------

def twin_spec(build_id):
    """Everything the browser twin needs to run the verified binary with the same part models as the test."""
    from .simulator import build_spec
    build = load(build_id)
    if build.get("status") != "verified" or not build.get("hex"):
        raise PipelineError("Only a verified build can run in the twin.")
    cards = _cards_for(build)
    spec = build_spec(build["board"], build["hex"], build["wiring"], build["plan"], cards, build.get("telemetry_keys") or [])
    intents = {i["id"]: i for i in build["plan"]["intents"]}
    io = {}
    for p in build["wiring"]["parts"]:
        x = simspec.io_of(cards[p["part"]], intents.get(p["id"], {}).get("options") or {})
        io[p["id"]] = {"in": x["in"], "out": x["out"], "name": cards[p["part"]]["name"]}
    return {"spec": spec, "io": io}


# ---------- chat about a finished build ----------

def chat(build_id, messages):
    """Spark Open answers basic questions about one build, grounded only in that build's report, rules and sketch."""
    from . import report
    build = load(build_id)
    if not build.get("plan"):
        raise PipelineError("Design the gadget first.")
    history = [{"role": m["role"], "content": str(m["content"])[:2000]}
               for m in messages[-10:] if m.get("role") in ("user", "assistant") and m.get("content")]
    if not history or history[-1]["role"] != "user":
        raise PipelineError("Ask a question.")
    msgs = prompts.chat(BOARDS[build["board"]], build, report.markdown(build, _cards_for(build))) + history
    return llm.text_of(llm.chat("chat", llm.SPARK_MODEL, msgs, max_tokens=900))
