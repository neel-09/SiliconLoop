"""The "simulate" tool: run the compiled hex headless in avr8js (src/sim, Node) against the plan's behaviour rules.

simulate(board_key, hex, wiring, plan, cards, telemetry_keys) -> {"ok", "passed", "total", "rules", "health", "report"}
  - Every rule starts from a fresh power-up with default inputs; part models are wired exactly as the allocator decided.
  - Health checks (baud, telemetry format and timing, serial flooding, resets) apply to every run.
  - "report" is a short plain-text failure description for the repair prompt.
Self-test:  python -m siliconloop.simulator --selftest
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import simspec

SIM_DIR = Path(os.getenv("SIM_DIR") or Path(__file__).resolve().parents[1] / "sim")
NODE = os.getenv("NODE") or shutil.which("node") or "node"
TIMEOUT_S = int(os.getenv("SIM_TIMEOUT_S", "120"))
# Boards whose chip configuration has passed the Mega blink and servo checks (see --selftest).
SIM_BOARDS = {"uno", "nano", "nano_old", "mega"}


class SimulatorError(RuntimeError):
    pass


def build_spec(board_key, hex_text, wiring, plan, cards, telemetry_keys):
    """Everything the Node runner needs: the binary, the model instances of every part (from its card, with card pins
    replaced by the board pins the allocator chose), rule key types and default inputs. Only the rules come from a model."""
    intents = {i["id"]: i for i in plan["intents"]}
    parts, types, defaults = [], {}, {}
    for p in wiring["parts"]:
        card, options = cards[p["part"]], intents.get(p["id"], {}).get("options") or {}
        insts = []
        for inst in simspec.resolve(card, options):
            insts.append({"model": inst["model"], "params": inst["params"],
                          "pins": {t: p["pins"].get(cp) if cp else None for t, cp in inst["pins"].items()},
                          "io": {k: e["native"] for k, e in inst["io"].items()
                                 if not e["requires_option"] or options.get(e["requires_option"])}})
            for k, e in inst["io"].items():
                types[f"{p['id']}.{k}"] = e["type"]
        parts.append({"id": p["id"], "part": p["part"], "models": insts})
        defaults[p["id"]] = simspec.defaults_of(card, options)
    return {"board": board_key, "hex": hex_text, "parts": parts, "rules": plan["rules"],
            "telemetry_keys": list(telemetry_keys), "types": types, "defaults": defaults}


def run_spec(spec):
    if spec["board"] not in SIM_BOARDS:
        raise SimulatorError(f"The simulator does not support {spec['board']} yet.")
    if not (SIM_DIR / "node_modules" / "avr8js").exists():
        raise SimulatorError(f"avr8js is not installed: run npm install in {SIM_DIR}")
    try:
        r = subprocess.run([NODE, str(SIM_DIR / "cli.mjs")], input=json.dumps(spec), capture_output=True,
                           text=True, encoding="utf-8", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SimulatorError(f"The simulation took longer than {TIMEOUT_S} s.")
    if r.returncode != 0:
        raise SimulatorError(f"The simulator failed: {r.stderr.strip()[-800:]}")
    return json.loads(r.stdout)


def simulate(board_key, hex_text, wiring, plan, cards, telemetry_keys):
    result = run_spec(build_spec(board_key, hex_text, wiring, plan, cards, telemetry_keys))
    result["report"] = report(result)
    return result


def _fmt(v):
    if isinstance(v, str):
        return '"' + " / ".join(x.rstrip() for x in v.split("\n") if x.strip()) + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]" if v else "nothing"
    return json.dumps(v)


def report(result):
    """Plain-language failure list for the repair prompt (and the build log). Empty when everything passed."""
    if result.get("ok"):
        return ""
    out = []
    for r in result["rules"]:
        if r.get("ok"):
            continue
        head = f"Rule {r['id']} FAILED ({r.get('description', '')})"
        if r.get("error"):
            out.append(f"{head}: {r['error']}.")
            continue
        for n, s in enumerate(r.get("steps", []), 1):
            if s.get("ok"):
                continue
            sets = ", ".join(f"{iid}.{k}={_fmt(v)}" for iid, vals in (s.get("set") or {}).items() for k, v in vals.items())
            lines = [f"{head}, step {n}: after setting {sets or 'nothing'}, waited {s.get('atMs', s.get('within_ms'))} ms "
                     f"(allowed {s.get('within_ms')} ms{', then hold ' + str(s['hold_ms']) + ' ms' if s.get('hold_ms') else ''})."]
            if s.get("crashed"):
                lines.append(f"  The board {s['crashed']['kind']} at {s['crashed']['atMs']} ms.")
            for f in s.get("failed") or []:
                lines.append(f"  {f['key']}: expected {_fmt(f['expected'])}, observed {_fmt(f['observed'])}")
            out.append("\n".join(lines))
            break
    for p in result.get("health", {}).get("problems", []):
        out.append(f"Health check FAILED: {p}.")
    return "\n".join(out)


# ---------- self-test: hand-written sketches with known behaviour ----------

def _selftest():
    """Compile known-good and known-bad sketches with arduino-cli and check the simulator agrees.
    Requires arduino-cli with the AVR core and the allowlisted libraries (as in the Docker image)."""
    from .compiler import compile_sketch
    from .allocator import BOARDS, PARTS, allocate
    from .prompts import PLAN_EXAMPLE, pin_block, telemetry_keys
    from . import selftest_sketches as T

    from .compiler import lint_cards
    from .simspec import check_cards

    ok = True
    bad = check_cards({**PARTS, **T.EXTRA_CARDS})
    print(f"[{'ok' if not bad else 'FAILED'}] part cards: {len(PARTS)} curated + {len(T.EXTRA_CARDS)} test cards resolve"
          + ("".join(f"\n   {b}" for b in bad)))
    ok &= not bad
    for name, keys, code, expect_pass in T.LINT_CASES:
        probs = lint_cards(code, [PARTS[k] for k in keys])
        good = (not probs) == expect_pass
        ok &= good
        print(f"[{'ok' if good else 'MISMATCH'}] lint {name}: {'; '.join(probs) or 'clean'}")
    for name, board, plan, code, expect_pass, *extra in T.CASES:
        extra = extra[0] if extra else {}
        cards = {**PARTS, **extra}
        wiring = allocate(board, plan["intents"], extra_cards=extra)
        sketch = pin_block(wiring) + code
        c = compile_sketch(sketch, board, wiring["libraries"])
        if not c["ok"]:
            print(f"[{name}] compile failed:\n{c.get('errors')}")
            ok = False
            continue
        res = simulate(board, c["hex"], wiring, plan, cards, telemetry_keys(plan, cards))
        good = res["ok"] == expect_pass
        ok &= good
        print(f"[{'ok' if good else 'MISMATCH'}] {name}: {res['passed']}/{res['total']} rules, health "
              f"{'ok' if res['health']['ok'] else 'failed'} ({res['wall_ms']} ms)")
        if not good or not res["ok"]:
            print("   " + res["report"].replace("\n", "\n   "))
    return ok


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print(__doc__)
