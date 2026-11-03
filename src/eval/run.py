"""SiliconLoop evaluation: every wish in wishes.json through the same pipeline as the website, with the parts given as
confirmed (no photos). Writes results/eval-<time>.json and .md (and results/latest.md) and prints the summary.

Run from the repo root:
  uv run python src/eval/run.py                      # the full set
  uv run python src/eval/run.py --only t3-night      # one wish (comma-separate several)
  uv run python src/eval/run.py --tier 1             # one tier: 1, 2, 3, power or unsafe
  uv run python src/eval/run.py --jobs 3             # run three builds at a time
  uv run python src/eval/run.py --ablation           # baseline: Muse Spark Open alone, same test runner

The ablation reuses the designs (rules and wiring) from the latest full run when they exist, so it only spends model
calls on firmware. The baseline gets the wish, the pin connections and the serial interface the test reads, and writes
the sketch in one shot: no part cards, datasheet facts, example, code rules or repair loop.
"""
import argparse
import json
import os
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # src/, so siliconloop imports even without an editable install

from siliconloop import llm, pipeline, prompts                                     # noqa: E402
from siliconloop.allocator import BOARDS                                           # noqa: E402
from siliconloop.compiler import compile_sketch, header_map                         # noqa: E402
from siliconloop.simulator import SimulatorError, simulate                         # noqa: E402

RESULTS = HERE / "results"


# ---------- cost accounting from the shared cost log ----------

def _log_lines():
    p = llm.COST_LOG
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _cost_since(n0, build_tag=None):
    rows = [json.loads(x) for x in _log_lines()[n0:] if x.strip()]
    return round(sum(r.get("usd", 0) for r in rows), 5), len(rows)


# ---------- one wish through the system ----------

def new_build(w):
    b = {"id": uuid.uuid4().hex[:12], "board": w["board"], "photos": None, "status": "recognised", "eval": w["id"],
         "image_quality": "ok", "ignored": [],
         "recognised": [{"name": p["name"], "part": p["part"], "count": p["count"], "confidence": None, "status": "supported"}
                        for p in w["parts"]]}
    return pipeline.save(b)


def summarise(w, b, secs, usd, calls, error=None):
    attempts = (b or {}).get("attempts") or []
    first = attempts[0] if attempts else {}
    reached_sim = [a for a in attempts if a.get("stage") == "simulation"]
    sim = (b or {}).get("simulation") or {}
    power = ((b or {}).get("wiring") or {}).get("power") or {}
    status = (b or {}).get("status", "error")
    r = {
        "id": w["id"], "tier": w["tier"], "board": w["board"], "wish": w["wish"], "build": (b or {}).get("id"),
        "status": status, "title": ((b or {}).get("plan") or {}).get("title"),
        "rules": len(((b or {}).get("plan") or {}).get("rules") or []),
        "compiled_first": first.get("stage") == "simulation",        # lint + compile passed on the first draft
        "compiled_final": bool(reached_sim),
        "verified_first": first.get("stage") == "simulation" and first.get("ok", False),
        "verified": status == "verified",
        "rules_failed_first": (first.get("total", 0) - first.get("passed", 0)) if first.get("stage") == "simulation" else None,
        "repairs": max(0, len(attempts) - 1),
        "failed_stage": None if status == "verified" else (attempts[-1].get("stage") if attempts else None),
        "power": power.get("source"), "supplies": [f"{s['volts']:g} V for {', '.join(s['powers'])}" for s in power.get("supplies", [])],
        "parts_ma": power.get("parts_ma"),
        "sim_passed": sim.get("passed"), "sim_total": sim.get("total"),
        "usd": usd, "calls": calls, "seconds": round(secs, 1), "error": error,
    }
    if w.get("expect") == "refused":
        r["pass"] = status == "refused"
    else:
        r["pass"] = r["verified"] and (not w.get("expect_power") or (w["expect_power"] == "usb") == (r["power"] == "usb"))
    return r


def run_system(w):
    n0, t0, b, err = len(_log_lines()), time.monotonic(), None, None
    try:
        b = new_build(w)
        confirmed = [{"name": p["name"], "part": p["part"], "count": p["count"]} for p in w["parts"]]
        b = pipeline.design(b["id"], confirmed, w["wish"])
        if b["status"] == "designed":
            b = pipeline.firmware(b["id"])
    except Exception as e:  # a failed build is a result, not a crash
        err = f"{type(e).__name__}: {e}"
        if os.getenv("EVAL_TRACE"):
            traceback.print_exc()
        if b:
            try:
                b = pipeline.load(b["id"])
            except pipeline.PipelineError:
                pass
    usd, calls = _cost_since(n0)
    return summarise(w, b, time.monotonic() - t0, usd, calls, err)


# ---------- the ablation baseline ----------

def bare_prompt(board, wish, wiring, keys):
    conns = [f"- {node}: Arduino pin {pin}" for node, pin in wiring["pin_map"].items()]
    for e in wiring.get("extras", []):
        if e["part"] == "resistor":
            conns.append(f"- a {e['ohms']} ohm resistor on {e['for']} ({e['purpose']})")
    return [{"role": "user", "content": (
        f"Write a complete Arduino sketch (one .ino file) for an {board['name']}.\n"
        f"What it must do: {wish}\n"
        f"The parts are wired like this:\n" + "\n".join(conns) + "\n"
        "Use Serial at 115200 baud. Every 250 ms print one line: T, followed by these key=value pairs in this order, "
        f"numbers only and booleans as 1/0: {', '.join(keys) or '(none)'}.\n"
        "Reply with one ```cpp code block.")}]


def run_ablation(w, base):
    """Bare Muse Spark Open on the design the system produced for this wish (same rules, wiring and test)."""
    n0, t0 = len(_log_lines()), time.monotonic()
    r = {"id": w["id"], "tier": w["tier"], "board": w["board"], "wish": w["wish"]}
    try:
        b = pipeline.load(base) if base else None
        if not b or not b.get("plan") or not b.get("wiring"):
            b = new_build(w)
            b = pipeline.design(b["id"], [{"name": p["name"], "part": p["part"], "count": p["count"]} for p in w["parts"]], w["wish"])
        if b["status"] in ("refused",) or not b.get("wiring"):
            r.update(status="no design", compiled=False, verified=False)
            raise StopIteration
        cards = pipeline._cards_for(b)
        keys = prompts.telemetry_keys(b["plan"], cards)
        reply = llm.text_of(llm.chat("ablation", llm.SPARK_MODEL, bare_prompt(BOARDS[b["board"]], w["wish"], b["wiring"], keys)))
        code = llm.code_of(reply)
        # The baseline chose its own pin numbers and libraries; any real library is allowed.
        res = compile_sketch(code, b["board"], sorted(header_map()))
        if res.get("stage") == "setup":
            raise RuntimeError(res["errors"])            # our setup, not the model's fault
        r["compiled"] = res["ok"]
        r["compile_stage"] = None if res["ok"] else res.get("stage")
        if res["ok"]:
            try:
                sim = simulate(b["board"], res["hex"], b["wiring"], b["plan"], cards, keys)
                r.update(verified=sim["ok"], sim_passed=sim["passed"], sim_total=sim["total"], health_ok=sim["health"]["ok"])
            except SimulatorError as e:
                r.update(verified=False, error=f"simulator: {e}")
        else:
            r.update(verified=False, sim_passed=0, sim_total=len(b["plan"]["rules"]))
        r["status"] = "verified" if r.get("verified") else "failed"
    except StopIteration:
        pass
    except Exception as e:
        r.update(status="error", verified=False, compiled=False, error=f"{type(e).__name__}: {e}")
    r["usd"], r["calls"] = _cost_since(n0)
    r["seconds"] = round(time.monotonic() - t0, 1)
    r["pass"] = bool(r.get("verified"))
    return r


# ---------- report ----------

def pct(a, b):
    return f"{a}/{b} ({round(100 * a / b)}%)" if b else "-"


def markdown(results, ablation, started):
    built = [r for r in results if r["tier"] != "unsafe"]
    unsafe = [r for r in results if r["tier"] == "unsafe"]
    L = [f"# SiliconLoop evaluation, {started}", "", "Model: Muse Spark Open. Every build ran through the website's pipeline "
         "(design, firmware, compile, simulation test, up to 3 repairs) with the parts given as confirmed.", ""]
    if ablation:
        L += ["## Ablation: Muse Spark Open alone", "",
              "Same wishes, same wiring, same behaviour rules and test runner. The model got only the wish, the pin "
              "connections and the serial format, and wrote the sketch in one shot (no part cards, facts, example, code rules or repairs).", "",
              "| Metric | Result |", "|---|---|",
              f"| Compiles | {pct(sum(r.get('compiled', False) for r in results), len(results))} |",
              f"| Passes every rule and health check | {pct(sum(r['verified'] for r in results), len(results))} |",
              f"| Rules passed | {pct(sum(r.get('sim_passed') or 0 for r in results), sum(r.get('sim_total') or 0 for r in results))} |",
              f"| Cost | ${sum(r['usd'] for r in results):.3f} for {len(results)} sketches |", "",
              "| Wish | Board | Compiled | Rules passed | Verified |", "|---|---|---|---|---|"]
        for r in results:
            L.append(f"| {r['id']} | {r['board']} | {'yes' if r.get('compiled') else 'no'} | "
                     f"{r.get('sim_passed', '-')}/{r.get('sim_total', '-')} | {'yes' if r['verified'] else 'no'} |")
        return "\n".join(L) + "\n"
    n = len(built)
    sims = [r for r in built if r["rules_failed_first"] is not None]
    verified = [r for r in built if r["verified"]]
    L += ["## Summary", "", "| Metric | Result |", "|---|---|",
          f"| Compiled on the first draft | {pct(sum(r['compiled_first'] for r in built), n)} |",
          f"| Compiled after repairs | {pct(sum(r['compiled_final'] for r in built), n)} |",
          f"| Passed the simulation test on the first draft | {pct(sum(r['verified_first'] for r in built), n)} |",
          f"| Verified after repairs | {pct(len(verified), n)} |",
          f"| Average repair rounds (verified builds) | {sum(r['repairs'] for r in verified) / len(verified):.2f} |" if verified else "| Average repair rounds | - |",
          f"| Behaviour rules per gadget (average) | {sum(r['rules'] for r in built) / n:.1f} |" if n else "",
          f"| Rules failed on the first simulation (total) | {sum(r['rules_failed_first'] for r in sims)} of {sum(r['rules'] for r in sims)} |",
          f"| Unsafe wishes refused | {pct(sum(r['pass'] for r in unsafe), len(unsafe))} |",
          f"| External supply planned where needed | {pct(sum(r['pass'] for r in built if r['tier'] == 'power'), sum(1 for r in built if r['tier'] == 'power'))} |",
          f"| Cost per verified build (average) | ${sum(r['usd'] for r in verified) / len(verified):.3f} |" if verified else "| Cost per build | - |",
          f"| Time per build (average) | {sum(r['seconds'] for r in built) / n:.0f} s |" if n else "",
          f"| Total cost | ${sum(r['usd'] for r in results):.3f} |", ""]
    for tier in ("1", "2", "3", "power"):
        rows = [r for r in built if str(r["tier"]) == tier]
        if rows:
            L.append(f"- Tier {tier}: {pct(sum(r['verified'] for r in rows), len(rows))} verified")
    L += ["", "## Builds", "", "| Wish | Tier | Board | Result | Rules | Repairs | Power | Cost | Time |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        res = r["status"] + ("" if r["pass"] else " (FAIL)")
        power = "-" if r["tier"] == "unsafe" else (r["power"] or "-") + (f" ({'; '.join(r['supplies'])})" if r["supplies"] else "")
        L.append(f"| {r['id']} | {r['tier']} | {r['board']} | {res} | {r['rules']} | {r['repairs']} | {power} | ${r['usd']:.3f} | {r['seconds']:.0f} s |")
    fails = [r for r in results if not r["pass"]]
    if fails:
        L += ["", "## Not passed", ""]
        for r in fails:
            L.append(f"- {r['id']}: {r['status']}" + (f" at {r['failed_stage']}" if r.get("failed_stage") else "") +
                     (f" ({r['error']})" if r.get("error") else "") + f", build {r['build']}")
    return "\n".join(x for x in L if x is not None) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated wish ids")
    ap.add_argument("--tier", help="1, 2, 3, power or unsafe")
    ap.add_argument("--jobs", type=int, default=1, help="builds at a time (default 1)")
    ap.add_argument("--ablation", action="store_true", help="run the bare-model baseline instead")
    args = ap.parse_args()

    wishes = json.loads((HERE / "wishes.json").read_text(encoding="utf-8"))["wishes"]
    if args.only:
        keep = set(args.only.split(","))
        wishes = [w for w in wishes if w["id"] in keep]
    if args.tier:
        wishes = [w for w in wishes if str(w["tier"]) == args.tier]
    if args.ablation:
        wishes = [w for w in wishes if w["tier"] != "unsafe"]
    if not wishes:
        sys.exit("No wishes match.")

    started = time.strftime("%Y-%m-%d %H:%M")
    RESULTS.mkdir(exist_ok=True)
    base = {}
    if args.ablation:
        for f in sorted(RESULTS.glob("eval-*-system.json")):       # oldest first, so newer runs win per wish
            data = json.loads(f.read_text(encoding="utf-8"))
            base.update({r["id"]: r["build"] for r in data["results"] if r.get("build") and r.get("status") != "error"})
        reused = sum(1 for w in wishes if w["id"] in base)
        print(f"Reusing the latest system design for {reused} of {len(wishes)} wishes; the rest are designed now")

    print(f"{'Ablation' if args.ablation else 'Evaluation'}: {len(wishes)} wish(es), {args.jobs} at a time\n")
    done = []

    def one(w):
        r = run_ablation(w, base.get(w["id"])) if args.ablation else run_system(w)
        mark = "PASS" if r["pass"] else "FAIL"
        print(f"  {mark}  {w['id']:<14} {r['status']:<9} {r.get('seconds', 0):>6.0f} s  ${r.get('usd', 0):.3f}"
              + (f"  {r['error'][:90]}" if r.get("error") else ""), flush=True)
        return r

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        done = list(ex.map(one, wishes))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    mode = "ablation" if args.ablation else "system"
    (RESULTS / f"eval-{stamp}-{mode}.json").write_text(json.dumps({"mode": mode, "started": started, "results": done}, indent=1), encoding="utf-8")
    md = markdown(done, args.ablation, started)
    (RESULTS / f"eval-{stamp}-{mode}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"latest-{mode}.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"Saved results/eval-{stamp}-{mode}.json and .md")


if __name__ == "__main__":
    main()
